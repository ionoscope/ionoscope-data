#!/usr/bin/env python3
"""Continuously process queued prepared-data update tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Delay between queue checks.")
    parser.add_argument("--limit", type=int, default=1, help="Maximum queued tasks to process per check.")
    parser.add_argument("--dry-run", action="store_true", help="Preview commands without changing task status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "run_update_tasks.py"),
            "--limit",
            str(args.limit),
        ]
        if args.dry_run:
            command.append("--dry-run")

        completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if completed.returncode != 0:
            print(f"update worker iteration failed with exit code {completed.returncode}", file=sys.stderr)
        time.sleep(max(args.interval_seconds, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
