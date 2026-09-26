#!/usr/bin/env python3
"""Run queued prepared-data update tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys

from api.schemas import UpdateTask
from api.update_tasks import list_update_tasks, mark_task_completed, mark_task_failed, mark_task_running


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1, help="Maximum queued tasks to process.")
    parser.add_argument("--dry-run", action="store_true", help="Mark tasks completed without running collection.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    queued_tasks = list_update_tasks(status="queued")
    if args.limit > 0:
        queued_tasks = queued_tasks[: args.limit]

    if not queued_tasks:
        print("No queued update tasks.")
        return

    for task in queued_tasks:
        run_task(task, dry_run=args.dry_run)


def run_task(task: UpdateTask, dry_run: bool = False) -> None:
    running = mark_task_running(task)
    command = [
        sys.executable,
        "update_prepared_data.py",
        "--stations",
        running.station,
        "--start",
        running.start.isoformat().replace("+00:00", "Z"),
        "--end",
        running.end.isoformat().replace("+00:00", "Z"),
        "--sources",
        running.sources,
    ]
    try:
        if dry_run:
            print("Dry run:", " ".join(command))
        else:
            print("Running:", " ".join(command))
            subprocess.run(command, check=True)
    except Exception as exc:
        mark_task_failed(running, str(exc))
        raise
    mark_task_completed(running)


if __name__ == "__main__":
    main()
