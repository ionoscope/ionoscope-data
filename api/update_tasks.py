from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .schemas import UpdateRequest, UpdateTask


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = Path(os.getenv("IONOSCOPE_UPDATE_TASK_DIR", str(PROJECT_ROOT / "update_tasks")))


class UpdateTaskNotFoundError(ValueError):
    pass


class UpdateTaskStoreError(RuntimeError):
    pass


TERMINAL_STATUSES = {"completed", "failed"}


def create_update_task(request: UpdateRequest) -> UpdateTask:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    task = UpdateTask(
        task_id=uuid4().hex,
        status="queued",
        station=request.station.upper(),
        start=request.start,
        end=request.end,
        sources=request.sources,
        created_at=datetime.now(UTC),
        message="Prepared data is not available yet. The update worker can process this task.",
    )
    save_update_task(task)
    return task


def list_update_tasks(status: str | None = None) -> list[UpdateTask]:
    if not TASK_DIR.exists():
        return []
    tasks = []
    for path in sorted(TASK_DIR.glob("*.json")):
        task = UpdateTask.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if status is None or task.status == status:
            tasks.append(task)
    return sorted(tasks, key=lambda task: (task.created_at, task.task_id))


def get_update_task(task_id: str) -> UpdateTask:
    path = _task_path(task_id)
    if not path.exists():
        raise UpdateTaskNotFoundError(f"Update task {task_id} was not found.")
    try:
        return UpdateTask.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise UpdateTaskNotFoundError(f"Update task {task_id} was not found.") from exc
    except (OSError, ValueError) as exc:
        raise UpdateTaskStoreError("Update task could not be read") from exc


def mark_task_running(task: UpdateTask) -> UpdateTask:
    updated = task.model_copy(
        update={
            "status": "running",
            "started_at": datetime.now(UTC),
            "message": "Prepared data update is running.",
            "error": None,
        }
    )
    save_update_task(updated)
    return updated


def mark_task_completed(task: UpdateTask) -> UpdateTask:
    updated = task.model_copy(
        update={
            "status": "completed",
            "finished_at": datetime.now(UTC),
            "message": "Prepared data update completed.",
            "error": None,
        }
    )
    save_update_task(updated)
    return updated


def mark_task_failed(task: UpdateTask, error: str) -> UpdateTask:
    updated = task.model_copy(
        update={
            "status": "failed",
            "finished_at": datetime.now(UTC),
            "message": "Prepared data update failed.",
            "error": error,
        }
    )
    save_update_task(updated)
    return updated


def save_update_task(task: UpdateTask) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    destination = _task_path(task.task_id)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=TASK_DIR, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(task.model_dump_json(indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _task_path(task_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise UpdateTaskNotFoundError("Invalid update task identifier.")
    return TASK_DIR / f"{task_id}.json"
