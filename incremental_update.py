"""Download new observations, retain immutable batches and rebuild local features."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from api.worker_lock import worker_lock
from collect_hf_data import parse_dt, write_csv, write_records_json
from update_prepared_data import prepare_collection_config, run_command


def utc(value: str) -> datetime:
    parsed = parse_dt(value)
    if parsed is None:
        raise ValueError(f"Invalid UTC timestamp: {value}")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def collection_start(end: datetime, previous_end: datetime | None, overlap_hours: float) -> datetime:
    return min(previous_end, end) - timedelta(hours=overlap_hours) if previous_end else end - timedelta(days=8)


def merge_batches(batches: list[Path], destination: Path) -> None:
    """Newer batches replace overlapping timestamps; empty batches never erase history."""
    processed = destination / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    names = sorted({p.name for batch in batches for p in (batch / "processed").glob("*.csv")})
    for name in names:
        records = {}
        for batch in batches:
            path = batch / "processed" / name
            if not path.exists():
                continue
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    try:
                        timestamp = parse_dt(row.get("time_utc"))
                    except (ValueError, TypeError):
                        continue
                    if timestamp is None or row.get("error"):
                        continue
                    key = tuple(row.get(k, "") for k in ("source", "product", "station", "index", "energy")) + (timestamp.isoformat(),)
                    records[key] = row
        rows = sorted(records.values(), key=lambda row: utc(row["time_utc"]))
        write_csv(processed / name, rows)
        write_records_json((processed / name).with_suffix(".json"), Path(name).stem, rows)

    # Canonical raw files preserve the original tokens needed by normalization.
    stations: dict[str, dict[datetime, str]] = {}
    for batch in batches:
        for path in sorted((batch / "raw" / "giro").rglob("*.txt")):
            station = next((part.split("=", 1)[1] for part in path.parts if part.startswith("station=")), None)
            if station is None:
                continue
            records = stations.setdefault(station, {})
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                try:
                    timestamp = parse_dt(line.split()[0])
                except (ValueError, TypeError):
                    continue
                if timestamp is not None:
                    records[timestamp] = line
    for station, records in stations.items():
        path = destination / "raw" / "giro" / f"station={station}" / "history.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(records[key] for key in sorted(records)) + "\n", encoding="utf-8")


def validate_generation(generation: Path, stations: list[str], end: datetime) -> None:
    """Never replace working features with an empty or stale collection."""
    from api.services import _read_latest_row
    for station in stations:
        row = _read_latest_row(generation / 'features' / 'stations' / f'{station}_features.csv')
        observation = parse_dt(row.get('foF2_source_timestamp'))
        value = row.get('foF2')
        if (not observation or value in (None, '') or not math.isfinite(float(value))
                or not timedelta(0) <= end - observation <= timedelta(hours=2)):
            raise ValueError(f'No recent verified foF2 for {station}; retaining previous generation')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", required=True, help="Comma-separated stations; fixed for this workspace.")
    parser.add_argument("--dynamic-stations", action="store_true", help="Allow the operational station set to change.")
    parser.add_argument("--workspace", default="live_data")
    parser.add_argument("--seed-dir", help="Existing collector output to use as initial history (first run only).")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--sources", default="all", help="Keep the same source selection for this workspace.")
    parser.add_argument("--end", help="UTC end; defaults to now.")
    parser.add_argument("--overlap-hours", type=float, default=48, help="Recheck delayed/revised records; default 48h.")
    parser.add_argument("--initial-hours", type=float, default=192, help="History to collect on first run.")
    parser.add_argument("--prepare-only", action="store_true", help="Rebuild from stored history without network calls.")
    parser.add_argument("--cs-filter", choices=["threshold", "off"],
                        help="Defaults to saved mode, or threshold for a new workspace.")
    parser.add_argument("--min-cs", type=float, help="Defaults to saved cutoff, or 50.")
    parser.add_argument('--input-profile', choices=['legacy', 'candidate-cs50-period-end-v1'],
                        default=os.getenv('IONOSCOPE_INPUT_PROFILE', 'legacy'))
    args = parser.parse_args()
    if args.overlap_hours <= 0 or args.initial_hours <= 0:
        parser.error("Collection windows must be positive")
    stations = sorted({s.strip().upper() for s in args.stations.split(",") if s.strip()})
    if not stations or any(not s.replace("_", "").replace("-", "").isalnum() for s in stations):
        parser.error("Station codes must contain only letters, digits, underscores or hyphens")
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # Share the existing update-worker exclusion to avoid competing preparation jobs.
    with worker_lock() as acquired:
        if not acquired:
            raise RuntimeError("Another data update is running")
        state_path = workspace / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
            "stations": stations, "sources": args.sources, "batches": [], "last_end": None,
        }
        old_sources = set(state['sources'].split(',')) if state['sources'] != 'all' else {'giro', 'gfz', 'omni', 'noaa'}
        new_sources = set(args.sources.split(',')) if args.sources != 'all' else {'giro', 'gfz', 'omni', 'noaa'}
        source_upgrade = (args.input_profile == 'candidate-cs50-period-end-v1' and old_sources <= new_sources)
        if (state["stations"] != stations and not args.dynamic_stations) or (state["sources"] != args.sources and not source_upgrade):
            raise ValueError("Use a separate workspace when changing stations or sources")
        cs_filter = args.cs_filter or state.get("cs_filter", "threshold")
        min_cs = args.min_cs if args.min_cs is not None else state.get("min_cs", 50.0)
        batches = [workspace / name for name in state["batches"]]
        if args.seed_dir:
            if batches:
                raise ValueError("--seed-dir is only allowed for an empty workspace")
            seed = Path(args.seed_dir).resolve()
            manifest = json.loads((seed / "run_manifest.json").read_text(encoding="utf-8"))
            # A seed must cover the requested scope before its watermark is trusted.
            if not set(stations).issubset({p.name.split("=", 1)[1] for p in (seed / "raw/giro").glob("station=*")}):
                raise ValueError("Seed does not contain every requested station")
            if not set(args.sources.split(",") if args.sources != "all" else ["giro", "gfz", "omni", "noaa"]).issubset(manifest["sources"]):
                raise ValueError("Seed does not cover requested sources")
            batch = workspace / "batches" / ("seed_" + uuid4().hex)
            shutil.copytree(seed, batch)
            batches.append(batch)
            state["last_end"] = manifest["interval"]["end_utc"]
        # GFZ rejects fractional seconds in its query dates.
        end = (utc(args.end) if args.end else datetime.now(UTC)).replace(microsecond=0)
        if state["last_end"] and end < utc(state["last_end"]):
            raise ValueError("End must not move the collection watermark backwards")
        if not args.prepare_only:
            start = collection_start(end, utc(state["last_end"]) if state["last_end"] else None, args.overlap_hours)
            if not state["last_end"]:
                start = end - timedelta(hours=args.initial_hours)
            if args.input_profile == 'candidate-cs50-period-end-v1' and (
                    state.get('input_profile') != args.input_profile or state['sources'] != args.sources):
                start = min(start, end - timedelta(days=8))
            batch = workspace / "batches" / (end.strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:8])
            batch.mkdir(parents=True)
            config = batch / "collection.toml"
            config.write_text(prepare_collection_config(Path(args.config), stations, batch), encoding="utf-8")
            print(f"Downloading {start.isoformat()} through {end.isoformat()}", flush=True)
            run_command([sys.executable, "collect_hf_data.py", "--config", str(config), "--sources", args.sources,
                         "--start", start.isoformat(), "--end", end.isoformat()])
            batches.append(batch)
            state["last_end"] = end.isoformat()
        if not batches:
            raise ValueError("No stored history; provide --seed-dir or allow collection")
        from source_rate import release_priority
        release_priority()
        generation = workspace / "generations" / uuid4().hex
        merge_batches(batches, generation / "data")
        prepare_command = [sys.executable, "update_prepared_data.py", "--skip-collection", "--stations", ",".join(stations),
                     "--cs-filter", cs_filter, "--min-cs", str(min_cs),
                     "--data-dir", str(generation / "data"), "--cleaned-dir", str(generation / "cleaned"),
                     "--normalized-dir", str(generation / "normalized"), "--features-dir", str(generation / "features")]
        if args.dynamic_stations:
            selection = generation / 'station_set.json'
            selection.write_text(json.dumps({'stations': stations}), encoding='utf-8')
            # Build existing normalized stations together; a missing station must
            # not abort preparation for all the others.
            prepare_command.extend(['--station-set', str(selection)])
        if args.input_profile == 'candidate-cs50-period-end-v1':
            from data_preparation.candidate_inputs import prepare_live
            prepare_live(generation / 'data', generation / 'normalized', generation / 'features', stations, end)
            cs_filter, min_cs = 'threshold', 50.0
        else:
            run_command(prepare_command)
        usable = stations
        if not args.prepare_only:
            if args.dynamic_stations:
                usable = []
                for station in stations:
                    try:
                        validate_generation(generation, [station], end)
                        usable.append(station)
                    except (OSError, ValueError) as exc:
                        print(f'Skipping {station}: {exc}', flush=True)
                if not usable:
                    raise ValueError('No stations have usable current features; retaining previous generation')
            else:
                validate_generation(generation, stations, end)
        state['stations'] = stations
        state['sources'] = args.sources
        state['available_stations'] = usable
        state["batches"] = [str(batch.relative_to(workspace)) for batch in batches]
        state["generation"] = str(generation.relative_to(workspace))
        state["cs_filter"] = cs_filter
        state["min_cs"] = min_cs
        state['input_profile'] = args.input_profile
        state['prepared_at'] = datetime.now(UTC).isoformat()
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(state_path)
        # Produce a diagnostic response directly from the same Data API reader.
        from api import services
        services.DEFAULT_FEATURE_DIR = generation / "features"
        services.DEFAULT_FEATURE_STATION_DIR = generation / "features/stations"
        for station in usable:
            latest = services.get_latest_features(station)
            payload = latest.model_dump(mode="json", exclude={"features"})
            print(json.dumps(payload), flush=True)
        print(f"Prepared generation: {generation}; active pointer: {state_path}", flush=True)


if __name__ == "__main__":
    main()
