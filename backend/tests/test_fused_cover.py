"""Single-encode covers and direct unique output, with real FFmpeg media."""
import concurrent.futures
import hashlib
import subprocess
import tempfile
import unittest
from array import array
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.core.ffmpeg import FFMPEG, probe_media, render_video, run_process
from app.core.video_cover import prepare_cover_frame
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import TaskStatus
from app.services.task_service import TaskService
from tests.test_grouped_body_and_cover import audio_hash, frame_bytes, assert_frame_close


class FusedCoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / 'source.mp4'
        subprocess.run([
            FFMPEG, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=160x96:r=30000/1001:d=4',
            '-f', 'lavfi', '-i', 'sine=frequency=600:sample_rate=44100:d=4',
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(cls.source),
        ], check=True, capture_output=True)
        cls.digest = hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def core(self, name, **overrides):
        out = self.root / name
        out.mkdir(exist_ok=True)
        config = dict(task_name='same', out_dir=str(out), t_hook=1.001, t_body=.5005,
                      total_clips=3, hook_r=1., body_r=1., bgm_r=1., resolution='160*96',
                      fps='24', bitrate='400k', enable_gpu=False, vol_orig=100,
                      vol_bgm=0, vol_voice=0, enable_variants=False, _selection_seed=7,
                      enable_random_cover=True, _cover_seed=37)
        config.update(overrides)
        core = VideoMatrixCore(config, lambda _: None, SharedMediaCache(str(out / 'state')))
        self.addCleanup(core.temp_dir.cleanup)
        core.hook_pool = [dict(file=str(self.source), start=0., duration=1.001, has_audio=True)]
        core.body_pool = [dict(file=str(self.source), start=1.2, duration=.5005, has_audio=True),
                          dict(file=str(self.source), start=2.4, duration=.5005, has_audio=True)]
        return core

    def render(self, core, failures=0):
        commands = []

        def capture(command, *args, **kwargs):
            commands.append(command[:])
            if len(commands) <= failures:
                return False, 'simulated filter failure'
            return render_video(command, *args, **kwargs)

        with patch('app.core.video_matrix.render_video', side_effect=capture):
            ok, path, _ = core.render_single_video(1, True)
        self.assertTrue(ok, core.output_configs.get(1))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.digest)
        return path, commands

    @staticmethod
    def stream(path):
        return next(s for s in probe_media(path)['streams'] if s['codec_type'] == 'video')

    @staticmethod
    def samples(path):
        result = subprocess.check_output([FFMPEG, '-v', 'error', '-i', path,
                                          '-vn', '-ac', '1', '-ar', '44100', '-f', 's16le', '-'])
        data = array('h')
        data.frombytes(result)
        return data

    def test_replace_insert_at_integer_and_rational_rates(self):
        for fps in ('24', '30000/1001'):
            base_core = self.core('base-' + fps.replace('/', '-'), fps=fps, enable_random_cover=False)
            baseline, _ = self.render(base_core)
            before = self.stream(baseline)
            base_samples = self.samples(baseline)
            fps_value = 30000 / 1001 if '/' in fps else float(fps)
            for mode in ('replace', 'insert'):
                with self.subTest(fps=fps, mode=mode):
                    core = self.core(f'{mode}-{fps}'.replace('/', '-'), fps=fps, random_cover_mode=mode)
                    output, commands = self.render(core)
                    after = self.stream(output)
                    self.assertEqual(len(commands), 1)
                    self.assertNotIn('split=', commands[0][commands[0].index('-filter_complex') + 1])
                    self.assertEqual(int(after['nb_frames']), int(before['nb_frames']) + (mode == 'insert'))
                    self.assertAlmostEqual(float(after['duration']) - float(before['duration']),
                                           (mode == 'insert') / fps_value, delta=.001)
                    assert_frame_close(self, frame_bytes(output, 1), frame_bytes(baseline, 0 if mode == 'insert' else 1))
                    if mode == 'replace':
                        self.assertEqual(audio_hash(output), audio_hash(baseline))
                    else:
                        samples = self.samples(output)
                        shift = round(44100 / fps_value)
                        # AAC may ring near the boundary; the first 80% is silent.
                        self.assertLess(max(abs(n) for n in samples[:int(shift * .8)]), 80)
                        left = base_samples[4000:20000]
                        right = samples[4000 + shift:20000 + shift]
                        correlation = sum(a*b for a, b in zip(left, right)) / (
                            sum(a*a for a in left) * sum(b*b for b in right)) ** .5
                        self.assertGreater(correlation, .99)
                        audio = next(s for s in probe_media(output)['streams'] if s['codec_type'] == 'audio')
                        self.assertLess(abs(float(audio['duration']) - float(after['duration'])), .05)
                    self.assertTrue(core.output_configs[1]['_cover_applied'])
                    self.assertFalse(list(Path(core.config['out_dir']).glob('.vm-*')))

    def test_disabled_cover_does_no_extraction_or_probe(self):
        core = self.core('disabled', enable_random_cover=False)
        with patch('app.core.video_cover.prepare_cover_frame', side_effect=AssertionError('must bypass')), \
             patch('app.core.video_cover.RandomCoverProcessor._probe_media', side_effect=AssertionError('must bypass')):
            _, commands = self.render(core)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].count('-i'), 6)  # 3 video-only + 3 audio-only readers
        self.assertNotIn('_cover_applied', core.output_configs[1])
        # Separate demuxers prevent audio demand buffering decoded 4K Body frames.
        command = commands[0]
        readers = []
        start = 0
        for index, value in enumerate(command):
            if value == '-i':
                readers.append(command[start:index + 2])
                start = index + 2
        for index in range(3):
            self.assertIn('-an', readers[index])
            self.assertIn('-vn', readers[index + 3])
            self.assertEqual(readers[index][-1], readers[index + 3][-1])
            video, audio = readers[index], readers[index + 3]
            video_seek = video[video.index('-ss') + 1] if '-ss' in video else None
            audio_seek = audio[audio.index('-ss') + 1] if '-ss' in audio else None
            self.assertEqual(video_seek, audio_seek)
        graph = command[command.index('-filter_complex') + 1]
        for index in range(3):
            self.assertNotIn(f'[{index}:a]', graph)
            self.assertIn(f'[{index + 3}:a]', graph)

    def test_silent_grouped_full_length_body_with_insert(self):
        core = self.core('silent-grouped', random_cover_mode='insert', vol_orig=0,
                         vol_hook_orig=0, hook_full_duration=True, body_mode='grouped',
                         body_groups=[dict(enabled=True, clip_count=2, clip_duration=9,
                                           full_duration=True, folder='synthetic')])
        core.body_group_pools = [core.body_pool]
        output, commands = self.render(core)
        self.assertEqual(len(commands), 1)
        self.assertEqual(int(self.stream(output)['nb_frames']), 49)
        self.assertFalse(any(s['codec_type'] == 'audio' for s in probe_media(output)['streams']))

    def test_cover_selection_is_from_actual_cut_and_rng_is_independent(self):
        core = self.core('independent')
        other = self.core('independent-off', enable_random_cover=False)
        self.render(core)
        self.render(other)
        self.assertEqual(core.rng.getstate(), other.rng.getstate())
        with patch('app.core.video_cover.random.Random.random', return_value=.9):
            path, summary = prepare_cover_frame(
                [dict(file=str(self.source), start=0., duration=.5),
                 dict(file=str(self.source), start=2.4, duration=.5)], core.config, 1, core.temp_dir_path)
        self.assertAlmostEqual(summary['source_time'], 2.8)
        self.assertTrue(Path(path).is_file())

    def test_frame_extraction_is_noninteractive_and_still_one_frame(self):
        core = self.core('noninteractive')
        with patch('app.core.video_cover.run_process', wraps=run_process) as extraction:
            path, _ = prepare_cover_frame(
                [dict(file=str(self.source), start=.5, duration=2.)],
                core.config, 37, core.temp_dir_path,
            )
        command = extraction.call_args.args[0]
        self.assertIn('-nostdin', command[:command.index('-i')])
        self.assertEqual(command.count('-i'), 1)
        self.assertEqual(command[command.index('-frames:v') + 1], '1')
        self.assertEqual(extraction.call_count, 1)
        self.assertEqual((self.stream(path)['width'], self.stream(path)['height']), (160, 96))

    def test_real_task_skips_old_postprocess_and_uses_resolved_config(self):
        core = self.core('task', random_resolution_enabled=True, random_resolution_min=128,
                         random_resolution_max=144, random_bitrate_enabled=True,
                         random_bitrate_min=400, random_bitrate_max=600, _output_seed=7)
        service = TaskService(core.shared)
        status = TaskStatus(task_id='cover', task_name='cover', status='running', created_at=datetime.now())
        with patch.object(service.cover_processor, 'process', side_effect=AssertionError('second encode')), \
             patch('app.core.video_matrix.render_video', wraps=render_video) as encoding:
            self.assertTrue(service._render_job(core, 1, status))
        self.assertEqual(encoding.call_count, 1)
        config = encoding.call_args.kwargs['config']
        video = self.stream(status.output_files[0])
        self.assertEqual((video['width'], video['height']), tuple(map(int, config['resolution'].split('*'))))
        self.assertEqual(encoding.call_args.args[0][encoding.call_args.args[0].index('-b:v') + 1], config['bitrate'])

    def test_failed_extract_or_encode_uses_base_and_never_postprocesses(self):
        for failure in ('extract', 'encode'):
            with self.subTest(failure=failure):
                core = self.core('failure-' + failure)
                if failure == 'extract':
                    with patch('app.core.video_cover.prepare_cover_frame', side_effect=RuntimeError('bad frame')):
                        output, commands = self.render(core)
                else:
                    output, commands = self.render(core, failures=1)
                self.assertEqual(len(commands), 1 if failure == 'extract' else 2)
                self.assertTrue(Path(output).is_file())
                self.assertTrue(core.output_configs[1]['_cover_applied']['skipped'])
                self.assertTrue(core.output_configs[1]['_output_warnings'])
                self.assertNotIn('[v_cover]', commands[-1][commands[-1].index('-filter_complex') + 1])

    def test_dedup_blend_fallback_keeps_cover_and_same_settings(self):
        core = self.core('fallback', enable_variants=True, variant_color=True, variant_seed=3,
                         variant_blend_enabled=True, variant_blend_path=str(self.source))
        output, commands = self.render(core, failures=1)
        self.assertEqual(len(commands), 2)
        graph = commands[-1][commands[-1].index('-filter_complex') + 1]
        self.assertIn('[6:v]setpts=PTS-STARTPTS,setsar=1[cover_frame]', graph)
        self.assertNotIn('dedup_', graph)
        self.assertFalse(core.output_configs[1]['_cover_applied'].get('skipped'))
        self.assertTrue(core.output_configs[1]['_variant_applied']['skipped'])
        self.assertTrue(Path(output).is_file())

    def test_locked_or_stopped_completed_encode_needs_no_rename(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                core = self.core('retain-' + str(cancelled))
                service = TaskService(core.shared)
                status = TaskStatus(task_id='retain', task_name='retain', status='running', created_at=datetime.now())
                handles = []

                def finish_and_hold(command, *args, **kwargs):
                    result = render_video(command, *args, **kwargs)
                    self.assertTrue(result[0], result[1])
                    handles.append(open(args[1], 'rb'))
                    if cancelled:
                        core.is_running = False
                        status.status = 'stopped'
                    return result

                try:
                    with patch('app.core.video_matrix.render_video', side_effect=finish_and_hold), \
                         patch('os.rename', side_effect=AssertionError('completed file must not move')), \
                         patch.object(service.cover_processor, 'process', side_effect=AssertionError('must bypass')):
                        self.assertTrue(service._render_job(core, 1, status))
                finally:
                    for handle in handles:
                        handle.close()
                output = Path(status.output_files[0])
                core.temp_dir.cleanup()
                self.assertTrue(output.is_file())
                self.assertNotEqual(output.name, 'render.mp4')
                self.assertEqual(output.parent, Path(core.config['out_dir']))
                self.assertFalse(status.output_warnings.get(str(output)))

    def test_failed_encoding_removes_only_its_reserved_output(self):
        core = self.core('failed-cleanup')
        existing = Path(core.config['out_dir']) / 'user.mp4'
        existing.write_bytes(b'user data')
        with patch('app.core.video_matrix.render_video', return_value=(False, 'simulated failure')):
            self.assertFalse(core.render_single_video(1, True)[0])
        self.assertEqual(list(Path(core.config['out_dir']).glob('*.mp4')), [existing])
        self.assertEqual(existing.read_bytes(), b'user data')

    def test_parallel_same_name_outputs_never_overwrite(self):
        cores = [self.core('parallel') for _ in range(2)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda c: c.render_single_video(1, True), cores))
        self.assertTrue(all(r[0] for r in results))
        paths = {r[1] for r in results}
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(Path(path).is_file() for path in paths))

    def test_reservation_collision_never_overwrites_existing(self):
        folder = self.root / 'publish'
        folder.mkdir(exist_ok=True)
        existing = folder / 'name_11111111.mp4'
        existing.write_bytes(b'old')
        with patch('app.core.video_matrix.uuid.uuid4', side_effect=[
                SimpleNamespace(hex='11111111'), SimpleNamespace(hex='22222222')]):
            result = VideoMatrixCore._reserve_output(str(folder), 'name')
        self.assertNotEqual(result, str(existing))
        self.assertEqual(existing.read_bytes(), b'old')
        self.assertEqual(Path(result).read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
