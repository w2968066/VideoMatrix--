import random
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.core.video_matrix import SharedMediaCache
from app.core.output_settings import resolve_output_config
from app.models.schemas import TaskStatus, VideoConfig
from app.services import task_service as module


class BenchmarkSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name).resolve()
        self.output = self.root / 'output'
        self.output.mkdir()
        self.sentinel = self.output / 'existing-video.mp4'
        self.sentinel.touch()
        self.cache = SharedMediaCache(str(self.root / 'production-state'))
        self.cache.usage_history = {'production-history'}
        self.cache.media_cache = {'source': {'mtime': 123}}
        self.cache.save_state()
        self.history_bytes = Path(self.cache.usage_history_file).read_bytes()
        self.media_bytes = Path(self.cache.media_cache_file).read_bytes()
        self.service = module.TaskService(self.cache)
        self.config = VideoConfig(hook_dir='hooks', body_dirs=['bodies'],
                                  base_out_dir=str(self.output), hook_full_duration=True, enable_random_cover=True)
        self.sessions, self.manifests = [], {}
        self.guard = threading.Lock()
        self.active = 0
        outer = self

        class Core:
            def __init__(inner, config, log, shared):
                inner.config, inner.log, inner.shared = config, log, shared
                inner.task_name, inner.is_running = config['task_name'], True
                inner.rng = random.Random(config['_selection_seed'])
                inner.hook_pool = []
                inner.body_pool = [{'file': 'b1'}, {'file': 'b2'}, {'file': 'b3'}]
                inner.body_group_pools = []
                inner.bgm_pool = [{'file': 'm1'}, {'file': 'm2'}]
                inner.bgm_track_pools = {}
                inner.voice_pool = [{'file': 'v1'}, {'file': 'v2'}]

            def pre_flight_check(inner):
                inner.hook_pool = [{'file': f'{inner.task_name}-h{i}', 'id': f'h{i}'} for i in range(4)]
                inner.rng.shuffle(inner.hook_pool)
                inner.shared.media_cache['prepared'] = {'duration': 4}
                return True, 'ok'

            def stop(inner):
                inner.is_running = False

        class Session:
            def __init__(inner, config, log, update, cancelled):
                inner.limit, inner.closed = config['concurrent_tasks'], False
                inner.log, inner.update = log, update
                outer.sessions.append(inner)
                update({'effective_concurrency': inner.limit, 'acceleration': 'NVIDIA'})
                log('>>> [加速] 混剪：NVIDIA (h264_nvenc)')

            def close(inner):
                outer.assertEqual(outer.active, 0, 'must join workers before closing')
                inner.closed = True

        self.Core, self.Session = Core, Session
        tasks = [{'name': name, 'hook_dir': name, 'body_dirs': [name], 'out': str(self.output / name)}
                 for name in ('A', 'B')]
        for target, options in [
            ((self.service, '_get_tasks_from_config'), {'return_value': tasks}),
            ((module, 'VideoMatrixCore'), {'side_effect': Core}),
            ((module, 'HardwareSession'), {'side_effect': Session}),
        ]:
            patcher = patch.object(*target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def render(self, core, index, status):
        session = core.config['_hardware_session']
        with self.guard:
            self.active += 1
        try:
            self.assertFalse(session.closed)
            self.assertEqual(core.config['concurrent_tasks'], session.limit)
            self.assertTrue(Path(core.config['out_dir']).is_relative_to(self.output))
            manifest = (core.task_name, index, core.hook_pool[0]['file'],
                        core.rng.choice(core.body_pool)['file'], core.rng.choice(core.bgm_pool)['file'],
                        core.rng.choice(core.voice_pool)['file'], core.config['_cover_seed'])
            path = str(Path(core.config['out_dir']) / f'{index}.mp4')
            with self.guard:
                self.manifests.setdefault(status.task_name, []).append(manifest)
                status.output_files.append(path)
                status.output_elapsed[path] = 10
                core.shared.usage_history.add('test-only-history')
            return True
        finally:
            with self.guard:
                self.active -= 1

    def run_benchmark(self, render=None, durations=None):
        # Seven trials: warmup, four initial passes, two finalist retests.
        durations = durations or [1, 12, 8, 9, 7.8, 7.8, 8]
        stamps = [value for duration in durations for value in (0, duration)]
        with patch.object(self.service, '_render_job', side_effect=render or self.render), \
                patch.object(module.time, 'perf_counter', side_effect=stamps):
            return self.service.get_benchmark(self.config)

    def assert_preserved_and_clean(self):
        self.assertEqual(self.cache.usage_history, {'production-history'})
        self.assertEqual(self.cache.media_cache, {'source': {'mtime': 123}})
        self.assertEqual(Path(self.cache.usage_history_file).read_bytes(), self.history_bytes)
        self.assertEqual(Path(self.cache.media_cache_file).read_bytes(), self.media_bytes)
        self.assertEqual(list(self.output.iterdir()), [self.sentinel])
        self.assertIsNone(self.service._benchmark_task_id)
        self.assertFalse(self.service.active_cores)
        self.assertTrue(all(session.closed for session in self.sessions))
        self.assertFalse(next(iter(self.service.tasks.values())).output_files)

    def test_same_eight_jobs_output_disk_snapshot_and_conservative_retest(self):
        result = self.run_benchmark()
        self.assertEqual(result['sample_count'], 8)
        self.assertEqual(result['best_concurrent'], 2, '2 is within 5% of 4, prefer lower')
        self.assertEqual(set(result['verification_results']), {2, 4})
        baseline = sorted(self.manifests['初测-1路'])
        for label, manifest in self.manifests.items():
            if label != '预热':
                self.assertEqual(sorted(manifest), baseline, label)
        self.assertEqual({manifest[0] for manifest in baseline}, {'A', 'B'})
        self.assertEqual([session.limit for session in self.sessions], [1, 1, 2, 3, 4, 4, 2])
        for item in result['results'].values():
            self.assertEqual(item['sample_count'], 8)
            self.assertEqual(item['completed_count'], 8)
            self.assertTrue(item['stable'])
            self.assertEqual(item['avg_video_elapsed'], 10)
        status = next(iter(self.service.tasks.values()))
        self.assertEqual((status.current, status.total, status.progress, status.status), (49, 49, 100, 'completed'))
        self.assert_preserved_and_clean()

    def test_preserved_base_video_with_cover_warning_cannot_win(self):
        def warning(core, index, status):
            ok = self.render(core, index, status)
            if status.task_name == '初测-4路':
                status.output_warnings[status.output_files[-1]] = '封面未完成'
            return ok
        result = self.run_benchmark(warning, [1, 12, 8, 9, 1, 9, 8])
        self.assertFalse(result['results'][4]['stable'])
        self.assertLess(result['results'][4]['completed_count'], 8)
        self.assertNotIn(4, result['verification_results'])
        self.assertEqual(result['best_concurrent'], 2)
        self.assert_preserved_and_clean()

    def test_random_output_settings_are_identical_across_concurrency_trials(self):
        self.config.random_resolution_enabled = True
        self.config.random_bitrate_enabled = True
        output_manifests, output_seeds = {}, set()

        def render(core, index, status):
            config = resolve_output_config(core.config, core.task_name, index, core.config['_output_seed'])
            with self.guard:
                output_seeds.add(core.config['_output_seed'])
                output_manifests.setdefault(status.task_name, []).append(
                    (core.task_name, index, config['resolution'], config['bitrate']))
            return self.render(core, index, status)

        result = self.run_benchmark(render)
        baseline = sorted(output_manifests['初测-1路'])
        self.assertEqual(len(baseline), result['sample_count'])
        self.assertEqual(len(output_seeds), 1)
        self.assertGreater(len({item[2:] for item in baseline}), 1)
        for label, manifest in output_manifests.items():
            if label != '预热':
                self.assertEqual(sorted(manifest), baseline, label)
        self.assertEqual(set(result['verification_results']), {2, 4})
        self.assert_preserved_and_clean()

    def test_recovery_or_effective_limit_drop_is_unstable(self):
        def recovery(core, index, status):
            if status.task_name == '初测-4路':
                core.log('>>> [加速恢复] isolated retry')
                core.config['_hardware_session'].update({'effective_concurrency': 2})
            return self.render(core, index, status)
        result = self.run_benchmark(recovery, [1, 12, 8, 9, 1, 9, 8])
        self.assertFalse(result['results'][4]['stable'])
        self.assertEqual(result['results'][4]['effective_concurrency'], 2)
        self.assertNotIn(4, result['verification_results'])
        self.assert_preserved_and_clean()

    def test_stop_cancels_later_trials_and_busy_guard_lasts_through_cleanup(self):
        def stop(core, index, status):
            self.assertTrue(self.service.stop_task(status.task_id))
            with self.assertRaises(module.TaskBusyError):
                self.service.create_task(self.config)
            return False
        result = self.run_benchmark(stop, [1])
        self.assertTrue(result['cancelled'])
        self.assertIn('停止', result['error'])
        self.assertEqual(result['results'], {})
        self.assertEqual(next(iter(self.service.tasks.values())).status, 'stopped')
        self.assert_preserved_and_clean()
        with patch.object(module.threading, 'Thread') as thread:
            self.service.create_task(self.config)
            thread.return_value.start.assert_called_once()

    def test_active_production_or_draining_core_refuses_benchmark(self):
        running = TaskStatus(task_id='production', task_name='P', status='running', created_at=datetime.now())
        self.service.tasks[running.task_id] = running
        with self.assertRaises(module.TaskBusyError):
            self.service.get_benchmark(self.config)
        running.status = 'stopped'
        self.service.active_cores[running.task_id] = [object()]
        with self.assertRaises(module.TaskBusyError):
            self.service.get_benchmark(self.config)
        self.assertEqual(list(self.service.tasks), ['production'])

    def test_not_enough_samples_keeps_current_setting(self):
        self.config.hook_full_duration = False
        self.config.hook_r = 0
        original = self.Core.pre_flight_check

        def limited(core):
            original(core)
            core.hook_pool = core.hook_pool[:2]
            return True, 'ok'
        with patch.object(self.Core, 'pre_flight_check', limited):
            result = self.run_benchmark()
        self.assertEqual(result['sample_count'], 4)
        self.assertIsNone(result['best_concurrent'])
        self.assertIn('不足', result['note'])
        self.assert_preserved_and_clean()

    def test_cleanup_error_does_not_leave_permanent_busy_flag(self):
        with patch.object(module.shutil, 'rmtree', side_effect=PermissionError('temporary lock')):
            self.run_benchmark()
        self.assertIsNone(self.service._benchmark_task_id)
        self.assertTrue(all(session.closed for session in self.sessions))


if __name__ == '__main__':
    unittest.main()
