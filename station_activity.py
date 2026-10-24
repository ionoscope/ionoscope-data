"""Select operational stations using recent, verified GIRO foF2 observations."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import math
import re
import csv
import os
from contextlib import nullcontext
from pathlib import Path

from collect_hf_data import GIRO_DATA_URL, fetch_url, parse_dt, parse_giro_table, resolve_giro_stations


def activity_map(workspace: Path) -> dict | None:
    path = workspace / 'station_registry.json'
    if not path.exists():
        return None
    try:
        registry = json.loads(path.read_text(encoding='utf-8'))
        checked = parse_dt(registry['checked_at'])
        now = datetime.now(UTC)
        fresh_check = checked and timedelta(0) <= now - checked <= timedelta(minutes=30)
        result = {}
        for row in registry['stations']:
            observation = parse_dt(row.get('last_observation'))
            active = bool(row['active'] and fresh_check and observation and
                          timedelta(0) <= now - observation <= timedelta(minutes=registry['max_age_minutes']))
            result[row['station']] = {**row, 'active': active,
                'reason': row['reason'] if active or not row['active'] else 'activity_check_expired'}
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def assess_rows(rows: list[dict], now: datetime, max_age_minutes: float = 60) -> dict:
    completed_before = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    valid = []
    physical, completed_physical, recent_physical = [], [], []
    measurement_rows = 0
    invalid_values = 0
    for row in rows:
        try:
            timestamp = parse_dt(row.get('time_utc'))
            if timestamp is None:
                continue
            measurement_rows += 1
            try:
                value = float(row.get('foF2', 'nan'))
            except (ValueError, TypeError):
                value = math.nan
            try:
                cs = float(row.get('CS', 'nan'))
            except (ValueError, TypeError):
                cs = math.nan
            if math.isfinite(value) and 0.5 <= value <= 25:
                physical.append(timestamp)
                if timestamp < completed_before:
                    completed_physical.append(timestamp)
                    if timedelta(0) <= now - timestamp <= timedelta(minutes=max_age_minutes):
                        recent_physical.append((timestamp, cs))
            elif math.isfinite(value):
                invalid_values += 1
            if (timestamp and timestamp < completed_before and math.isfinite(value)
                    and 0.5 <= value <= 25 and math.isfinite(cs) and cs >= 50):
                valid.append(timestamp)
        except (ValueError, TypeError):
            continue
    latest = max(valid) if valid else None
    active = latest is not None and timedelta(0) <= now - latest <= timedelta(minutes=max_age_minutes)
    if active:
        diagnosis = 'fresh_fof2'
    elif recent_physical:
        diagnosis = 'low_cs' if any(math.isfinite(cs) and cs >= 0 for _, cs in recent_physical) else 'missing_cs'
    elif completed_physical:
        diagnosis = 'stale_fof2'
    elif physical:
        diagnosis = 'incomplete_interval'
    elif invalid_values:
        diagnosis = 'invalid_fof2'
    elif measurement_rows:
        diagnosis = 'fof2_missing'
    else:
        diagnosis = 'no_measurements_in_window'
    return {'active': active, 'reason': 'fresh_fof2' if active else 'no_recent_verified_fof2',
            'last_observation': latest.isoformat() if latest else None,
            'diagnosis': diagnosis, 'measurement_rows': measurement_rows,
            'recent_physical_fof2_rows': len(recent_physical),
            'recent_low_cs_rows': sum(math.isfinite(cs) and 0 <= cs < 50 for _, cs in recent_physical),
            'recent_missing_cs_rows': sum(not math.isfinite(cs) or cs < 0 for _, cs in recent_physical),
            'last_physical_fof2': max(physical).isoformat() if physical else None}


def check_station(station: str, now: datetime, max_age_minutes: float) -> dict:
    start = now - timedelta(hours=3)
    params = {'query_submit': 'Search', 'date_start': start.strftime('%Y-%m-%d %H:%M'),
              'date_end': now.strftime('%Y-%m-%d %H:%M'), 'location': station,
              'DMUF': '3000', 'chosenchars[]': [0]}
    try:
        raw = fetch_url(GIRO_DATA_URL, data=params, timeout=15, attempts=1)
        rows = parse_giro_table(raw.decode('latin-1'), station, '')
        result = assess_rows(rows, now, max_age_minutes)
        location = re.search(r'Location: GEO\s*\(\s*([-\d.]+)\s*([NS])\s+([-\d.]+)\s*([EW])\s*\).*?URSI-Code\s+\w+,\s*([^\r\n]+)', raw.decode('latin-1'))
        if location:
            latitude = abs(float(location[1])) * (-1 if location[2] == 'S' else 1)
            longitude = abs(float(location[3])) * (-1 if location[4] == 'W' else 1)
            result['metadata'] = {'station': station, 'latitude': latitude,
                                  'longitude': longitude, 'longitude_180': (longitude + 180) % 360 - 180,
                                  'location': location[5].strip(), 'metadata_source': 'GIRO response'}
    except Exception as exc:
        result = {'active': False, 'reason': 'source_unavailable',
                  'diagnosis': 'source_unavailable',
                  'last_observation': None, 'error': str(exc)}
    return {'station': station, **result}


def discover(workspace: Path, catalog_codes: list[str], max_age_minutes: float = 60, include_uncatalogued: bool = False,
             *, selected_only: bool = False, publish_progress: bool = False, known_codes: list[str] | None = None) -> dict:
    now = datetime.now(UTC)
    available = set(catalog_codes) if selected_only else set(resolve_giro_stations('all', timeout=15))
    candidates = sorted(available if include_uncatalogued else available.intersection(catalog_codes))
    if not candidates:
        raise RuntimeError('GIRO station discovery returned no known candidates')
    try:
        previous = json.loads((workspace / 'station_registry.json').read_text(encoding='utf-8'))
        cached = {row['station']: {**row, 'checked_at': row.get('checked_at', previous['checked_at'])}
                  for row in previous['stations']}
    except (OSError, ValueError, KeyError, TypeError):
        cached = {}
    reuse, pending = [], []
    for code in candidates:
        row = cached.get(code)
        try:
            recent = row and timedelta(0) <= now - parse_dt(row['checked_at']) < timedelta(minutes=10)
        except (ValueError, TypeError):
            recent = False
        if recent and row.get('active'):
            observation = parse_dt(row.get('last_observation'))
            recent = observation is not None and timedelta(0) <= now - observation <= timedelta(minutes=max_age_minutes)
        if recent and 'diagnosis' in row and row['reason'] in {'fresh_fof2', 'no_recent_verified_fof2'}:
            reuse.append(row)
        else:
            pending.append(code)
    def probe(code):
        checked = datetime.now(UTC)
        return {**check_station(code, checked, max_age_minutes), 'checked_at': checked.isoformat()}

    def publish(rows):
        registry = {'checked_at': datetime.now(UTC).isoformat(), 'max_age_minutes': max_age_minutes,
                    'active_stations': sorted(row['station'] for row in rows if row['active']),
                    'stations': sorted(rows, key=lambda row: row['station']),
                    'uncatalogued_stations': sorted(available-set(catalog_codes))}
        workspace.mkdir(parents=True, exist_ok=True)
        temporary = workspace/'station_registry.tmp'
        temporary.write_text(json.dumps(registry, indent=2), encoding='utf-8')
        temporary.replace(workspace/'station_registry.json')
        return registry

    results = []
    if publish_progress:
        seed = dict(cached)
        for code in (set(catalog_codes) | set(cached))-available:
            seed[code] = {'station':code, 'active':False, 'reason':'not_in_current_giro_catalog',
                          'diagnosis':'not_in_current_giro_catalog', 'last_observation':None,
                          'checked_at':now.isoformat()}
        if seed:
            cached = seed
            publish(list(seed.values()))
    executor = nullcontext(None) if os.getenv('IONOSCOPE_GIRO_BACKGROUND') == '1' else ThreadPoolExecutor(max_workers=4)
    with executor as pool:
        for row in (map(probe, pending) if pool is None else pool.map(probe, pending)):
            results.append(row)
            if publish_progress:
                updated = {r['station']: r for r in list(cached.values())+reuse+results}
                publish(list(updated.values()))
    results += reuse
    for row in results:
        if row['active'] and row['station'] not in (known_codes if known_codes is not None else catalog_codes) and not row.get('metadata'):
            row.update(active=False, reason='station_metadata_unavailable', diagnosis='station_metadata_unavailable')
    results += [{'station': code, 'active': False, 'reason': 'not_in_current_giro_catalog',
                 'diagnosis': 'not_in_current_giro_catalog',
                 'last_observation': None} for code in sorted(set(catalog_codes) - available)]
    if selected_only:
        results += [{**row, 'active': False, 'reason': 'not_selected'}
                    for code, row in cached.items() if code not in available]
    return publish(results)


def write_metadata(workspace: Path, source: Path, registry: dict) -> None:
    with source.open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        records = {row['station']: row for row in reader if row.get('station')}
    for row in registry['stations']:
        if row.get('metadata'):
            records[row['station']] = {**records.get(row['station'], {}), **row['metadata']}
    temporary = workspace / 'station_metadata.tmp'
    with temporary.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records[code] for code in sorted(records))
    temporary.replace(workspace / 'station_metadata.csv')
