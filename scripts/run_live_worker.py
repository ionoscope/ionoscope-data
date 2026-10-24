"""Run queued jobs and the live collection/preparation/forecast cycle."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_job(command: list[str], timeout: float) -> None:
    # A timeout must stop the collector's children too, releasing the worker lock.
    with subprocess.Popen(command, cwd=ROOT, start_new_session=(os.name != 'nt')) as process:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def save_status(path: Path, status: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(status, indent=2), encoding='utf-8')
    temporary.replace(path)


class ForecastBatchError(RuntimeError):
    def __init__(self, errors: dict, forecasts: list[dict]):
        super().__init__(json.dumps(errors))
        self.forecasts = forecasts


def refresh_forecasts(api_url: str, stations: list[str], timeout: float) -> list[dict]:
    results = []
    errors = {}
    for station in stations:
        request = Request(api_url.rstrip('/') + '/api/v1/forecast/refresh',
                          data=json.dumps({'station': station}).encode(),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if payload.get('station') != station or not payload.get('current_fof2_available'):
                raise ValueError(f'Invalid forecast response for {station}')
            results.append(payload)
        except Exception as exc:
            errors[station] = str(exc)
    if errors:
        raise ForecastBatchError(errors, results)
    return results


def run_cycle(args, stations: list[str]) -> list[dict]:
    command = [sys.executable, str(ROOT / 'incremental_update.py'),
                    '--stations', ','.join(stations), '--workspace', args.workspace,
                    '--sources', args.sources, '--initial-hours', str(args.initial_hours),
                    '--overlap-hours', str(args.overlap_hours)]
    if getattr(args, 'stations', '').lower() == 'auto':
        command.append('--dynamic-stations')
    run_job(command, args.job_timeout)
    if getattr(args, 'stations', '').lower() == 'auto':
        state = json.loads((Path(args.workspace) / 'state.json').read_text())
        stations = state.get('available_stations', stations)
        from station_activity import activity_map
        activity = activity_map(Path(args.workspace)) or {}
        stations = [station for station in stations if activity.get(station, {}).get('active')]
        from api.services import forecast_ready
        stations = [station for station in stations if forecast_ready(
            Path(args.workspace) / state['generation'] / 'features/stations' / f'{station}_features.csv')]
        if not stations:
            raise RuntimeError('Station observations expired during preparation; no forecasts scheduled')
    return refresh_forecasts(args.api_url, stations, args.http_timeout)


def select_candidates(path: Path, workspace: Path) -> list[str]:
    """Catalog hints select work; only fresh foreground probes authorize serving."""
    from collect_hf_data import parse_dt
    now = datetime.now(UTC)
    rows = {}
    try:
        registry = json.loads(path.read_text(encoding='utf-8'))
        for row in registry['stations']:
            checked = parse_dt(row.get('checked_at', registry['checked_at']))
            if checked and 0 <= (now-checked).total_seconds() <= 7200:
                rows[row['station']] = row
    except (OSError, ValueError, KeyError, TypeError):
        pass
    candidates = {code for code, row in rows.items() if row.get('active')}
    for name, key in [('state.json', 'available_stations'), ('station_registry.json', 'active_stations')]:
        try:
            previous = json.loads((workspace/name).read_text(encoding='utf-8'))
            for code in previous.get(key, []):
                # An unavailable source is uncertainty, not confirmation of closure.
                if code not in rows or rows[code].get('reason') == 'source_unavailable':
                    candidates.add(code)
        except (OSError, ValueError, TypeError):
            pass
    return sorted(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stations', default='auto', help='auto checks all known GIRO stations each cycle')
    parser.add_argument('--max-station-age-minutes', type=float, default=60)
    parser.add_argument('--candidate-registry', help='Background catalog snapshot; avoids a full scan in each live cycle')
    parser.add_argument('--workspace', default='/app/live_data')
    parser.add_argument('--api-url', default='http://api:8000')
    parser.add_argument('--sources', default='giro,gfz,noaa')
    parser.add_argument('--interval-seconds', type=float, default=900)
    parser.add_argument('--retry-seconds', type=float, default=60)
    parser.add_argument('--initial-hours', type=float, default=24)
    parser.add_argument('--overlap-hours', type=float, default=3)
    parser.add_argument('--job-timeout', type=float, default=1200)
    parser.add_argument('--http-timeout', type=float, default=30)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    stations = sorted({s.strip().upper() for s in args.stations.split(',') if s.strip()})
    if not stations or min(args.interval_seconds, args.retry_seconds, args.initial_hours,
                           args.overlap_hours, args.job_timeout, args.http_timeout) <= 0:
        parser.error('Stations and positive intervals/timeouts are required')
    status_path = Path(args.workspace) / 'worker_status.json'
    try:
        status = json.loads(status_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        status = {}
    next_run = 0.0
    while True:
        # Queue and live preparation share the same OS lock and run sequentially.
        try:
            run_job([sys.executable, str(ROOT / 'run_update_tasks.py'), '--limit', '1'], args.job_timeout)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f'Queued update failed: {exc}', flush=True)
        if time.monotonic() >= next_run:
            cycle_started = time.monotonic()
            status.update(started_at=datetime.now(UTC).isoformat(), phase='running', error=None)
            save_status(status_path, status)
            try:
                from station_activity import discover, write_metadata
                from api.services import list_stations, STATION_METADATA_PATH
                from source_rate import foreground
                with foreground(args.job_timeout+300):
                    known = [station.code for station in list_stations()]
                    selected = bool(args.candidate_registry) or args.stations.lower() != 'auto'
                    candidates = (select_candidates(Path(args.candidate_registry), Path(args.workspace))
                                  if args.candidate_registry and args.stations.lower() == 'auto'
                                  else known if args.stations.lower() == 'auto' else stations)
                    if not candidates:
                        raise RuntimeError('Waiting for background catalog to find eligible station candidates')
                    registry = discover(Path(args.workspace), candidates, args.max_station_age_minutes,
                                        include_uncatalogued=args.stations.lower() == 'auto' and not selected,
                                        selected_only=selected, known_codes=known)
                    metadata_source = Path(args.workspace)/'station_metadata.csv'
                    write_metadata(Path(args.workspace), metadata_source if metadata_source.exists() else STATION_METADATA_PATH, registry)
                    os.environ['IONOSCOPE_STATION_METADATA'] = str(Path(args.workspace) / 'station_metadata.csv')
                    stations = registry['active_stations']
                    status['active_stations'] = stations
                    status['excluded_stations'] = [row for row in registry['stations'] if not row['active']]
                    status['candidate_count'] = len(candidates)
                    save_status(status_path, status)
                    if not stations:
                        raise RuntimeError('No stations have recent verified foF2; no forecasts scheduled')
                    forecasts = run_cycle(args, stations)
                status.update(phase='ok', last_success=datetime.now(UTC).isoformat(),
                              successful_cycles=status.get('successful_cycles', 0) + 1,
                              consecutive_failures=0, forecasts=forecasts)
                delay = args.interval_seconds
                print(f'Live cycle OK: {stations}', flush=True)
            except Exception as exc:
                status.update(phase='failed', error=str(exc),
                              consecutive_failures=status.get('consecutive_failures', 0) + 1)
                delay = args.retry_seconds
                if isinstance(exc, ForecastBatchError):
                    status['forecasts'] = exc.forecasts
                print(f'Live cycle failed; retry in {delay}s: {exc}', flush=True)
            status['finished_at'] = datetime.now(UTC).isoformat()
            status['duration_seconds'] = round(time.monotonic()-cycle_started, 3)
            status['next_run_at'] = datetime.fromtimestamp(time.time() + (
                max(0.1, delay-status['duration_seconds']) if status['phase'] == 'ok' else delay), UTC).isoformat()
            save_status(status_path, status)
            if args.once:
                if status['phase'] != 'ok':
                    raise SystemExit(1)
                return
            next_run = (max(time.monotonic() + 0.1, cycle_started + delay)
                        if status['phase'] == 'ok' else time.monotonic() + delay)
        time.sleep(min(10, max(0.1, next_run - time.monotonic())))


if __name__ == '__main__':
    main()
