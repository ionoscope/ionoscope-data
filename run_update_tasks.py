#!/usr/bin/env python3
"""Run queued prepared-data update tasks."""

from __future__ import annotations

import argparse
import subprocess
import sys

from api.schemas import UpdateTask
from api.update_tasks import PROJECT_ROOT, get_update_task, list_update_tasks, mark_task_completed, mark_task_failed, mark_task_running
from api.worker_lock import worker_lock


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=1, help="Maximum queued tasks to process.")
    parser.add_argument("--dry-run", action="store_true", help="Preview commands without changing task status.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with worker_lock() as acquired:
        if not acquired:
            print("Another update worker is active.")
            return 0
        return process_queue(args.limit, args.dry_run)


def process_queue(limit: int, dry_run: bool = False) -> int:
    if not dry_run:
        for task in list_update_tasks(status="running"):
            mark_task_failed(task, "Previous worker stopped before completion; submit a new update task.")
    queued_tasks = list_update_tasks(status="queued")
    if limit > 0:
        queued_tasks = queued_tasks[:limit]

    if not queued_tasks:
        print("No queued update tasks.")
        return 0

    failed = False
    for task in queued_tasks:
        if not run_task(task, dry_run=dry_run):
            failed = True
    return 1 if failed else 0


def run_task(task: UpdateTask, dry_run: bool = False) -> bool:
    task = get_update_task(task.task_id)
    if task.status != "queued":
        return True
    running = task if dry_run else mark_task_running(task)
    command = [
        sys.executable,
        str(PROJECT_ROOT / "update_prepared_data.py"),
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
            return True
        else:
            print("Running:", " ".join(command))
            subprocess.run(command, cwd=PROJECT_ROOT, check=True)
    except Exception as exc:
        mark_task_failed(running, str(exc))
        print(f"Task {running.task_id} failed: {exc}", file=sys.stderr)
        return False
    mark_task_completed(running)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
