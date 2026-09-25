from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .services import TIME_COLUMNS, _extract_numeric_features, _feature_file, _first_existing_column


@dataclass(frozen=True)
class FeatureCatalogEntry:
    station: str
    available_from: datetime
    available_to: datetime
    feature_file: Path


class DataUnavailableError(ValueError):
    pass


def get_feature_catalog_entry(station: str) -> FeatureCatalogEntry | None:
    station_code = station.upper()
    path = _feature_file(station_code)
    if not path.exists():
        return None

    first_time: datetime | None = None
    latest_time: datetime | None = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            time_column = _first_existing_column(row, TIME_COLUMNS)
            if time_column is None:
                continue
            timestamp = parse_datetime(row.get(time_column, ""))
            if timestamp is None:
                continue
            first_time = timestamp if first_time is None else min(first_time, timestamp)
            latest_time = timestamp if latest_time is None else max(latest_time, timestamp)

    if first_time is None or latest_time is None:
        return None
    return FeatureCatalogEntry(
        station=station_code,
        available_from=first_time,
        available_to=latest_time,
        feature_file=path,
    )


def has_feature_range(station: str, start: datetime, end: datetime) -> bool:
    entry = get_feature_catalog_entry(station)
    return entry is not None and entry.available_from <= start and entry.available_to >= end


def read_feature_range(station: str, start: datetime, end: datetime) -> list[dict[str, object]]:
    station_code = station.upper()
    if not has_feature_range(station_code, start, end):
        raise DataUnavailableError(f"Prepared features for {station_code} do not cover the requested period.")

    path = _feature_file(station_code)
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            time_column = _first_existing_column(row, TIME_COLUMNS)
            if time_column is None:
                continue
            timestamp = parse_datetime(row.get(time_column, ""))
            if timestamp is None or timestamp < start or timestamp > end:
                continue
            records.append(
                {
                    "station": station_code,
                    "timestamp": timestamp,
                    "features": _extract_numeric_features(row, time_column),
                }
            )
    return records


def parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
