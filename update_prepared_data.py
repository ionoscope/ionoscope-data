#!/usr/bin/env python3
"""Update prepared IonoScope feature tables before API requests arrive."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml", help="Base collection config.")
    parser.add_argument("--start", help="UTC start, for example 2025-01-01T00:00:00Z.")
    parser.add_argument("--end", help="UTC end, for example 2025-02-01T00:00:00Z.")
    parser.add_argument("--station-set", default="", help="JSON file with a stations list.")
    parser.add_argument("--stations", default="", help="Comma-separated station codes.")
    parser.add_argument("--sources", default="all", help="Comma-separated sources: all, giro, gfz, omni, noaa.")
    parser.add_argument("--data-dir", default="data", help="Collected data output directory.")
    parser.add_argument("--cleaned-dir", default="cleaned", help="Cleaned data output directory.")
    parser.add_argument("--normalized-dir", default="normalized", help="Normalized data output directory.")
    parser.add_argument("--features-dir", default="features", help="Feature table output directory used by the API.")
    parser.add_argument("--skip-collection", action="store_true", help="Reuse existing collected files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    station_codes = resolve_station_codes(args.stations, args.station_set)

    data_dir = Path(args.data_dir)
    cleaned_dir = Path(args.cleaned_dir)
    normalized_dir = Path(args.normalized_dir)
    features_dir = Path(args.features_dir)

    if not args.skip_collection:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False) as handle:
            collection_config = prepare_collection_config(Path(args.config), station_codes, data_dir)
            handle.write(collection_config)
            temp_config_path = Path(handle.name)
        try:
            run_command(
                [
                    sys.executable,
                    "collect_hf_data.py",
                    "--config",
                    str(temp_config_path),
                    "--sources",
                    args.sources,
                    *optional_arg("--start", args.start),
                    *optional_arg("--end", args.end),
                ]
            )
        finally:
            temp_config_path.unlink(missing_ok=True)

    run_command(
        [
            sys.executable,
            "data_preparation/clean_collected_data.py",
            "--input-dir",
            str(data_dir),
            "--giro-raw-dir",
            str(data_dir / "raw" / "giro"),
            "--output-dir",
            str(cleaned_dir),
        ]
    )

    normalize_command = [
        sys.executable,
        "data_preparation/normalize_time_grid.py",
        "--giro-raw-dir",
        str(data_dir / "raw" / "giro"),
        "--processed-dir",
        str(cleaned_dir / "processed"),
        "--output-dir",
        str(normalized_dir),
        "--time-config",
        "data_preparation/configs/time_normalization.json",
        "--split-by-station",
    ]
    if args.station_set:
        normalize_command.extend(["--station-set", args.station_set])
    elif station_codes:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump({"stations": station_codes}, handle)
            temp_station_set_path = Path(handle.name)
        try:
            run_command([*normalize_command, "--station-set", str(temp_station_set_path)])
        finally:
            temp_station_set_path.unlink(missing_ok=True)
    else:
        run_command(normalize_command)

    if args.station_set or not station_codes:
        run_command(
            [
                sys.executable,
                "feature_engineering/build_features.py",
                "--input-dir",
                str(normalized_dir),
                "--config",
                "feature_engineering/configs/feature_engineering.json",
                "--output-dir",
                str(features_dir),
            ]
        )
    else:
        for station in station_codes:
            run_command(
                [
                    sys.executable,
                    "feature_engineering/build_features.py",
                    "--input-dir",
                    str(normalized_dir),
                    "--config",
                    "feature_engineering/configs/feature_engineering.json",
                    "--output-dir",
                    str(features_dir),
                    "--station",
                    station,
                ]
            )


def optional_arg(name: str, value: str | None) -> list[str]:
    return [name, value] if value else []


def resolve_station_codes(stations: str, station_set: str) -> list[str]:
    if stations.strip():
        return [station.strip().upper() for station in stations.split(",") if station.strip()]
    if station_set:
        payload = json.loads(Path(station_set).read_text(encoding="utf-8"))
        return [str(station).strip().upper() for station in payload.get("stations", []) if str(station).strip()]
    return []


def prepare_collection_config(config_path: Path, station_codes: list[str], output_dir: Path) -> str:
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config.setdefault("run", {})["output_dir"] = str(output_dir)
    if station_codes:
        config.setdefault("giro", {})["stations"] = station_codes
    return dumps_toml(config)


def dumps_toml(config: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, values in config.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {format_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(format_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {format_toml_value(item)}" for key, item in value.items()) + " }"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def run_command(command: list[str]) -> None:
    print("Running:", " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    main()
