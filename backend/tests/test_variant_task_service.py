import tempfile
import unittest
from unittest.mock import patch, Mock
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
    def test_fused_fallback_warning_is_visible_without_second_transform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = str(Path(temp_dir) / 'fallback.mp4')
            Path(output_path).write_bytes(b'base-preserved')
            service = TaskService()
            service.variant_processor = UnexpectedVariantProcessor()
            status = TaskStatus(task_id='fused-fallback', task_name='demo', status='running', created_at=datetime.now())
            core = FakeCore(output_path)
            core.output_configs = {1: {**core.config, '_variant_applied': {'skipped': True},
                                       '_variant_warnings': ['变换失败，已回退基础混剪']}}
            self.assertTrue(service._render_job(core, 1, status))
            self.assertIn('回退基础混剪', status.output_warnings[output_path])
            self.assertFalse(any('最终完成' in message for message in core.messages))

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
            self.assertTrue(any('最终完成' in message for message in core.messages))

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
            self.assertIn('成品变换未完成', status.output_warnings[output_path])
            self.assertFalse(any('最终完成' in item for item in core.messages))

    def test_stop_during_cover_registers_preserved_video_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = str(Path(temp_dir) / 'base.mp4')
            Path(output).write_bytes(b'preserved-base-video')
            service = TaskService()
            status = TaskStatus(task_id='cover-stop', task_name='demo', status='running', created_at=datetime.now())
            service.tasks[status.task_id] = status
            core = FakeCore(output, enabled=False)
            core.config['enable_random_cover'] = True

            def stop(*_args, **_kwargs):
                self.assertEqual(status.output_files, [], 'Processing video must not be listed as finished')
                status.status = 'stopped'
                core.is_running = False
                return False, '已停止', {}

            with patch.object(service.cover_processor, 'process', side_effect=stop):
                self.assertTrue(service._render_job(core, 1, status))
            self.assertEqual(status.output_files, [output])
            self.assertIn('随机封面未完成', status.output_warnings[output])
            self.assertEqual(Path(output).read_bytes(), b'preserved-base-video')
            self.assertFalse(any('最终完成' in message for message in core.messages))

    def test_final_completion_is_announced_only_after_cover_finishes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = str(Path(temp_dir) / 'finished.mp4')
            Path(output).write_bytes(b'video')
            service = TaskService()
            status = TaskStatus(task_id='cover-ok', task_name='demo', status='running', created_at=datetime.now())
            core = FakeCore(output, enabled=False)
            core.config['enable_random_cover'] = True

            def complete(*_args, **_kwargs):
                self.assertEqual(status.output_files, [])
                self.assertFalse(any('最终完成' in message for message in core.messages))
                return True, None, {'mode': 'replace', 'sample_time': 1, 'zoom': 1.1}

            with patch.object(service.cover_processor, 'process', side_effect=complete):
                self.assertTrue(service._render_job(core, 1, status))
            self.assertEqual(status.output_files, [output])
            self.assertFalse(status.output_warnings)
            self.assertIn('最终完成', core.messages[-1])

    def test_task_outcome_distinguishes_complete_partial_and_total_failure(self):
        for outputs, warnings, expected in [
            ([], {}, 'failed'),
            (['a'], {}, 'partial'),
            (['a', 'b'], {'b': '封面未完成'}, 'partial'),
            (['a', 'b'], {}, 'completed'),
        ]:
            with self.subTest(expected=expected, outputs=outputs):
                status = TaskStatus(task_id='outcome', task_name='demo', status='running',
                                    created_at=datetime.now(), total=2, output_files=outputs, output_warnings=warnings)
                TaskService._finalize_status(status)
                self.assertEqual(status.status, expected)

    def test_stop_before_start_or_during_probe_stays_stopped(self):
        for stop_before_start in (True, False):
            with self.subTest(stop_before_start=stop_before_start):
                service = TaskService()
                status = TaskStatus(task_id='startup-stop', task_name='demo',
                                    status='stopped' if stop_before_start else 'pending', created_at=datetime.now())
                service.tasks[status.task_id] = status
                session = Mock()

                def probe(*_args, **_kwargs):
                    service.stop_task(status.task_id)
                    return session

                with patch('app.services.task_service.HardwareSession', side_effect=probe) as factory:
                    service._run_pipeline(status.task_id, {})
                self.assertEqual(status.status, 'stopped')
                if stop_before_start:
                    factory.assert_not_called()
                else:
                    session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
