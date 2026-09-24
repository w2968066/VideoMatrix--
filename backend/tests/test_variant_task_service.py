import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.models.schemas import TaskStatus
from app.services.task_service import TaskService


class FakeCore:
    def __init__(self, output_path: str, enabled=True):
        self.output_path = output_path
        self.config = {"enable_variants": enabled, "variant_seed": 7}
        self.task_name = "demo"
        self.is_running = True
        self.messages = []

    def render_single_video(self, _index, return_result=False):
        return True, self.output_path, 2.0

    def log(self, message):
        self.messages.append(message)


class FailingVariantProcessor:
    def process(self, *_args, **_kwargs):
        raise RuntimeError("variant failed")


class UnexpectedVariantProcessor:
    def process(self, *_args, **_kwargs):
        raise AssertionError("disabled output transformer must not be called")


class VariantTaskServiceTests(unittest.TestCase):
    def test_fused_variant_is_not_applied_twice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / "fused.mp4")
            Path(output_path).write_bytes(b"already-transformed")
            service = TaskService()
            service.variant_processor = UnexpectedVariantProcessor()
            status = TaskStatus(
                task_id="task-fused", task_name="demo", status="running",
                created_at=datetime.now(),
            )
            service.tasks[status.task_id] = status
            core = FakeCore(output_path)
            core.output_configs = {1: {**core.config, "_variant_applied": {"seed": 7}}}
            self.assertTrue(service._render_job(core, 1, status))
            self.assertEqual(Path(output_path).read_bytes(), b"already-transformed")
            self.assertEqual(status.output_files, [output_path])
            self.assertEqual(core.messages, [])

    def test_disabled_variant_does_not_change_original_render_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / "base.mp4")
            Path(output_path).write_bytes(b"base-video")
            service = TaskService()
            service.variant_processor = UnexpectedVariantProcessor()
            status = TaskStatus(
                task_id="task-disabled",
                task_name="demo",
                status="running",
                created_at=datetime.now(),
            )
            service.tasks[status.task_id] = status

            self.assertTrue(service._render_job(FakeCore(output_path, enabled=False), 1, status))
            self.assertEqual(Path(output_path).read_bytes(), b"base-video")
            self.assertEqual(status.output_files, [output_path])
            self.assertEqual(status.output_elapsed[output_path], 2.0)

    def test_variant_failure_keeps_base_output_successful(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / "base.mp4")
            Path(output_path).write_bytes(b"base-video")
            service = TaskService()
            service.variant_processor = FailingVariantProcessor()
            status = TaskStatus(
                task_id="task-1",
                task_name="demo",
                status="running",
                created_at=datetime.now(),
            )
            service.tasks[status.task_id] = status
            core = FakeCore(output_path)

            self.assertTrue(service._render_job(core, 1, status))
            self.assertEqual(Path(output_path).read_bytes(), b"base-video")
            self.assertEqual(status.output_files, [output_path])
            self.assertGreaterEqual(status.output_elapsed[output_path], 2.0)
            self.assertTrue(any("已保留原成片" in item for item in core.messages))


if __name__ == "__main__":
    unittest.main()
