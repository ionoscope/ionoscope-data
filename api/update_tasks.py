from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .schemas import UpdateRequest, UpdateTask


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = PROJECT_ROOT / "update_tasks"


class UpdateTaskNotFoundError(ValueError):
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
    _task_path(task.task_id).write_text(task.model_dump_json(indent=2), encoding="utf-8")
    return task


def list_update_tasks(status: str | None = None) -> list[UpdateTask]:
    if not TASK_DIR.exists():
        return []
    tasks = []
    for path in sorted(TASK_DIR.glob("*.json")):
        task = UpdateTask.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if status is None or task.status == status:
            tasks.append(task)
    return tasks


def get_update_task(task_id: str) -> UpdateTask:
    path = _task_path(task_id)
    if not path.exists():
        raise UpdateTaskNotFoundError(f"Update task {task_id} was not found.")
    return UpdateTask.model_validate(json.loads(path.read_text(encoding="utf-8")))


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
    _task_path(task.task_id).write_text(task.model_dump_json(indent=2), encoding="utf-8")


def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"
