import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from api.main import app
from api.schemas import UpdateRequest
from api import update_tasks as queue
from api.worker_lock import worker_lock
import run_update_tasks as worker


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(queue, "TASK_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self):
        return queue.create_update_task(UpdateRequest(station="TR169", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"))

    def test_success_and_command(self):
        task = self.create()
        def execute(command, **kwargs):
            self.assertEqual(queue.get_update_task(task.task_id).status, "running")
            self.assertIn("TR169", command)
            self.assertIn(task.start.isoformat().replace("+00:00", "Z"), command)
            self.assertEqual(kwargs["cwd"], queue.PROJECT_ROOT)
        with patch.object(worker.subprocess, "run", side_effect=execute):
            self.assertEqual(worker.process_queue(1), 0)
        result = queue.get_update_task(task.task_id)
        self.assertEqual(result.status, "completed")
        self.assertIsNotNone(result.started_at)
        self.assertIsNotNone(result.finished_at)

    def test_failure_does_not_block_next_task(self):
        first, second = self.create(), self.create()
        with patch.object(worker.subprocess, "run", side_effect=[subprocess.CalledProcessError(1, "pipeline"), None]):
            self.assertEqual(worker.process_queue(0), 1)
        self.assertEqual(queue.get_update_task(first.task_id).status, "failed")
        self.assertTrue(queue.get_update_task(first.task_id).error)
        self.assertEqual(queue.get_update_task(second.task_id).status, "completed")

    def test_dry_run_preserves_queue(self):
        task = self.create()
        with patch.object(worker.subprocess, "run") as run:
            worker.process_queue(1, True)
            run.assert_not_called()
        self.assertEqual(queue.get_update_task(task.task_id), task)

    def test_limit_and_fifo(self):
        first, second = self.create(), self.create()
        self.assertEqual(queue.list_update_tasks()[0].task_id, first.task_id)
        with patch.object(worker.subprocess, "run"):
            worker.process_queue(1)
        self.assertEqual(queue.get_update_task(second.task_id).status, "queued")

    def test_exclusive_lock_and_release(self):
        with worker_lock() as first:
            self.assertTrue(first)
            with worker_lock() as second:
                self.assertFalse(second)
        with worker_lock() as third:
            self.assertTrue(third)

    def test_interrupted_task(self):
        task = queue.mark_task_running(self.create())
        worker.process_queue(1)
        self.assertEqual(queue.get_update_task(task.task_id).status, "failed")

    def test_invalid_id(self):
        with self.assertRaises(queue.UpdateTaskNotFoundError):
            queue.get_update_task("../config")

    def test_api_queues_without_processing(self):
        client = TestClient(app)
        with patch("api.main.get_station"), patch.object(worker.subprocess, "run") as run:
            response = client.post("/api/v1/updates", json={"station": "TR169", "start": "2026-01-01", "end": "2026-01-02T00:00:00Z"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["status"], "queued")
            run.assert_not_called()
            task_id = response.json()["task_id"]
            self.assertEqual(client.get(f"/api/v1/updates/{task_id}").status_code, 200)
            self.assertEqual(client.post("/api/v1/updates", json={"station": "TR169", "start": "2026-01-03", "end": "2026-01-02"}).status_code, 400)
            self.assertEqual(client.post("/api/v1/updates", json={"station": "TR169", "start": "2026-01-01", "end": "2026-01-02", "sources": "bad"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
