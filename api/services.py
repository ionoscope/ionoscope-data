from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import FeatureResponse, Station


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATION_METADATA_PATH = PROJECT_ROOT / "feature_engineering" / "configs" / "stations_metadata.csv"
DEFAULT_FEATURE_DIR = PROJECT_ROOT / "features"
TIME_COLUMNS = ("time_utc", "timestamp", "datetime", "time")
IGNORED_FEATURE_COLUMNS = {"station", "interval"}


class StationNotFoundError(ValueError):
    pass


class FeatureNotFoundError(ValueError):
    pass


@lru_cache(maxsize=1)
def list_stations() -> list[Station]:
    if not STATION_METADATA_PATH.exists():
        return []

    stations: list[Station] = []
    with STATION_METADATA_PATH.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = _clean_text(row.get("station"))
            if not code:
                continue
            stations.append(
                Station(
                    code=code,
                    country=_clean_text(row.get("country")),
                    location=_clean_text(row.get("location")),
                    system_type=_clean_text(row.get("system_type")),
                    latitude=_to_float(row.get("latitude")),
                    longitude=_to_float(row.get("longitude")),
                    longitude_180=_to_float(row.get("longitude_180")),
                    data_available=_feature_file(code).exists(),
                )
            )
    return stations


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
    time_column = _first_existing_column(latest, TIME_COLUMNS)
    if time_column is None or not latest.get(time_column):
        raise FeatureNotFoundError(f"Feature table for station {station_info.code} has no timestamp column.")

    return FeatureResponse(
        station=station_info.code,
        timestamp=latest[time_column],
        features=_extract_numeric_features(latest, time_column),
    )


def _feature_file(station: str) -> Path:
    return DEFAULT_FEATURE_DIR / f"{station}_features.csv"


def _read_latest_row(path: Path) -> dict[str, str]:
    latest: dict[str, str] | None = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            latest = row
    if latest is None:
        raise FeatureNotFoundError(f"Feature table {path.name} is empty.")
    return latest


def _extract_numeric_features(row: dict[str, str], time_column: str) -> dict[str, float]:
    features: dict[str, float] = {}
    ignored = IGNORED_FEATURE_COLUMNS | {time_column}
    for key, value in row.items():
        if key in ignored or "_target_" in key:
            continue
        numeric = _to_float(value)
        if numeric is not None:
            features[key] = numeric
    return features


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
        return float(text)
    except ValueError:
        return None
