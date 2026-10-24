from __future__ import annotations

import csv
import math
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .schemas import FeatureResponse, Station


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATION_METADATA_PATH = PROJECT_ROOT / "feature_engineering" / "configs" / "stations_metadata.csv"
DEFAULT_FEATURE_DIR = PROJECT_ROOT / "features"
DEFAULT_FEATURE_STATION_DIR = DEFAULT_FEATURE_DIR / "stations"
TIME_COLUMNS = ("time_utc", "timestamp", "datetime", "time")
IGNORED_FEATURE_COLUMNS = {"station", "interval", "foF2_source_timestamp", "_latest_fof2_timestamp"}


class StationNotFoundError(ValueError):
    pass


class FeatureNotFoundError(ValueError):
    pass


def list_stations() -> list[Station]:
    metadata_path = Path(os.getenv('IONOSCOPE_STATION_METADATA', str(STATION_METADATA_PATH)))
    configured_workspace = os.getenv('IONOSCOPE_DATA_WORKSPACE')
    if configured_workspace and (Path(configured_workspace) / 'station_metadata.csv').exists():
        metadata_path = Path(configured_workspace) / 'station_metadata.csv'
    if not metadata_path.exists():
        return []

    from station_activity import activity_map
    workspace = os.getenv('IONOSCOPE_DATA_WORKSPACE')
    activity = activity_map(Path(workspace)) if workspace else None
    state = {}
    if workspace:
        try:
            state = json.loads((Path(workspace) / 'state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if not isinstance(state, dict):
            state = {}
    stations: list[Station] = []
    with metadata_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = _clean_text(row.get("station"))
            if not code:
                continue
            operational = activity.get(code, {'active': False, 'reason': 'not_checked'}) if activity is not None else {}
            if workspace:
                feature_path = Path(workspace) / state.get('generation', 'not_prepared') / 'features/stations' / f'{code}_features.csv'
                available = (feature_path.exists() and
                             ('available_stations' not in state or code in state['available_stations']))
            else:
                feature_path = _feature_file(code)
                available = feature_path.exists()
            available = available and operational.get('active') is not False
            if activity is not None and available:
                available = forecast_ready(feature_path)
            stations.append(
                Station(
                    code=code,
                    country=_clean_text(row.get("country")),
                    location=_clean_text(row.get("location")),
                    system_type=_clean_text(row.get("system_type")),
                    latitude=_to_float(row.get("latitude")),
                    longitude=_to_float(row.get("longitude")),
                    longitude_180=_to_float(row.get("longitude_180")),
                    data_available=available,
                    is_active=operational.get('active'),
                    activity_reason=operational.get('reason'),
                    last_observation=operational.get('last_observation'),
                )
            )
    return stations


def forecast_ready(path: Path) -> bool:
    """Current forecast selection also requires a target interval in the future."""
    try:
        row = _read_latest_row(path)
        value = _to_float(row.get('foF2'))
        start = parse_timestamp(row[_first_existing_column(row, TIME_COLUMNS)])
        observation = parse_timestamp(row.get('foF2_source_timestamp'))
        return (value is not None and math.isfinite(value)
                and start <= observation < start + timedelta(minutes=15)
                and start + timedelta(minutes=15) <= datetime.now(UTC)
                and start + timedelta(minutes=30) > datetime.now(UTC))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def get_station(station: str) -> Station:
    station_code = station.upper()
    for item in list_stations():
        if item.code.upper() == station_code:
            return item
    raise StationNotFoundError(f"Station {station_code} was not found.")


def get_latest_features(station: str) -> FeatureResponse:
    station_info = get_station(station)
    path = _feature_file(station_info.code)
    if not path.exists():
        raise FeatureNotFoundError(f"Feature table for station {station_info.code} was not found.")

    latest = _read_latest_row(path)
    observation = _clean_text(latest.get("foF2_source_timestamp"))
    fof2 = _to_float(latest.get("foF2"))
    current_available = fof2 is not None and math.isfinite(fof2)
    time_column = _first_existing_column(latest, TIME_COLUMNS)
    if time_column is None or not latest.get(time_column):
        raise FeatureNotFoundError(f"Feature table for station {station_info.code} has no timestamp column.")
    try:
        observation = parse_timestamp(observation) if observation else None
        start = parse_timestamp(latest[time_column])
        if observation and not start <= observation < start + timedelta(minutes=15):
            observation = None
    except (ValueError, TypeError):
        observation = None

    return FeatureResponse(
        station=station_info.code,
        timestamp=latest[time_column],
        source_interval_end=parse_timestamp(latest[time_column]) + timedelta(minutes=15),
        observation_timestamp=observation if current_available else None,
        latest_fof2_timestamp=latest.get("_latest_fof2_timestamp"),
        current_fof2_available=current_available,
        features=_extract_numeric_features(latest, time_column),
    )


def _feature_file(station: str) -> Path:
    workspace = os.getenv("IONOSCOPE_DATA_WORKSPACE")
    if workspace:
        root = Path(workspace)
        from station_activity import activity_map
        activity = activity_map(root)
        if activity is not None and not activity.get(station, {}).get('active', False):
            return root / 'inactive' / f'{station}_features.csv'
        try:
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            if 'available_stations' in state and station not in state['available_stations']:
                return root / 'not_prepared' / f'{station}_features.csv'
            return root / state["generation"] / "features" / "stations" / f"{station}_features.csv"
        except (OSError, ValueError, KeyError, TypeError):
            return root / "not_prepared" / f"{station}_features.csv"
    configured = os.getenv("IONOSCOPE_FEATURES_DIR")
    if configured:
        return Path(configured) / "stations" / f"{station}_features.csv"
    station_file = DEFAULT_FEATURE_STATION_DIR / f"{station}_features.csv"
    if station_file.exists():
        return station_file
    return DEFAULT_FEATURE_DIR / f"{station}_features.csv"


def _read_latest_row(path: Path) -> dict[str, str]:
    latest: dict[str, str] | None = None
    last_observation = None
    latest_time = None
    now = datetime.now(UTC)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            time_column = _first_existing_column(row, TIME_COLUMNS)
            try:
                timestamp = parse_timestamp(row.get(time_column, ""))
            except (ValueError, TypeError):
                continue
            if timestamp + timedelta(minutes=15) > now:
                continue
            if latest_time is None or timestamp > latest_time:
                latest, latest_time = row, timestamp
            value = _to_float(row.get("foF2"))
            if value is not None and math.isfinite(value):
                observation = _clean_text(row.get("foF2_source_timestamp"))
                if observation:
                    try:
                        parsed = parse_timestamp(observation)
                    except (ValueError, TypeError):
                        continue
                    if timestamp <= parsed < timestamp + timedelta(minutes=15):
                        last_observation = max(last_observation, parsed) if last_observation else parsed
    if latest is None:
        raise FeatureNotFoundError(f"Feature table {path.name} has no completed intervals.")
    latest["_latest_fof2_timestamp"] = last_observation
    return latest


def _extract_numeric_features(row: dict[str, str], time_column: str) -> dict[str, float]:
    features: dict[str, float] = {}
    ignored = IGNORED_FEATURE_COLUMNS | {time_column}
    for key, value in row.items():
        if key in ignored or "_target_" in key:
            continue
        numeric = _to_float(value)
        if numeric is not None and math.isfinite(numeric):
            features[key] = numeric
    return features


def parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp is missing")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)


def _first_existing_column(row: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in row:
            return candidate
    return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "na", "n/a", "null", "none"}:
        return None
    try:
        value = float(text)
        return value if math.isfinite(value) else None
    except ValueError:
        return None
