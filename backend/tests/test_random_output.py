"""Random main-output settings stay independent from media selection and layers."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.core.ffmpeg import FFMPEG, FFPROBE, probe_media, render_video
from app.core.output_settings import (
    parse_resolution, resolution_candidates, resolve_output_config, validate_output_settings,
)
from app.core.video_cover import RandomCoverProcessor
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.core.video_variant import VideoVariantProcessor
from app.models.schemas import VideoConfig
from app.services.task_service import TaskService


def ffmpeg(*args):
    subprocess.run([FFMPEG, '-v', 'error', '-y', *map(str, args)],
                   check=True, capture_output=True, timeout=30)


def audio_hash(path):
    packets = json.loads(subprocess.check_output([
        FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_packets',
        '-show_data', '-of', 'json', str(path),
    ], timeout=30))['packets']
    return hashlib.sha256(''.join(packet['data'] for packet in packets).encode()).hexdigest()


def video_stream(path):
    return next(stream for stream in probe_media(path)['streams'] if stream['codec_type'] == 'video')


class RandomOutputSettingsTests(unittest.TestCase):
    def config(self, **overrides):
        config = dict(resolution='1080*1920', bitrate='8000k',
                      random_resolution_enabled=True, random_resolution_min=1080,
                      random_resolution_max=1440, random_bitrate_enabled=True,
                      random_bitrate_min=8000, random_bitrate_max=14000)
        config.update(overrides)
        return config

    def test_defaults_and_inactive_invalid_hidden_values(self):
        config = VideoConfig(hook_dir='hooks', random_resolution_min='invalid',
                             random_resolution_max=None, random_bitrate_min=-1,
                             random_bitrate_max='nan')
        self.assertFalse(config.random_resolution_enabled)
        self.assertFalse(config.random_bitrate_enabled)
        self.assertEqual((config.random_resolution_min, config.random_resolution_max), (1080, 1440))
        self.assertEqual((config.random_bitrate_min, config.random_bitrate_max), (8000, 14000))
        disabled = self.config(random_resolution_enabled=False, random_bitrate_enabled=False,
                               random_resolution_min='invalid', random_bitrate_max=None)
        resolved = resolve_output_config(disabled, 'task', 1, 7)
        self.assertEqual(resolved, disabled)
        self.assertIsNot(resolved, disabled)
        self.assertNotIn('_output_settings', resolved)

    def test_exact_even_ratio_candidates_include_both_endpoints(self):
        candidates = resolution_candidates('1080*1920', 1080, 1440)
        self.assertEqual(len(candidates), 21)
        self.assertEqual((candidates[0], candidates[-1]), ((1080, 1920), (1440, 2560)))
        for width, height in candidates:
            self.assertEqual(width * 16, height * 9)
            self.assertEqual((width % 2, height % 2), (0, 0))
        self.assertEqual(resolution_candidates('1920x1080', 1080, 1440),
                         tuple((height, width) for width, height in candidates))
        self.assertEqual(resolution_candidates('1000×1000', 129, 132), ((130, 130), (132, 132)))

    def test_bad_enabled_ranges_and_no_legal_even_size_are_rejected(self):
        cases = [
            {'random_resolution_min': 127}, {'random_resolution_max': 7681},
            {'random_resolution_min': 1441}, {'random_resolution_min': 128.5},
            {'random_resolution_min': 'nan'}, {'random_resolution_min': True},
            {'random_resolution_min': None}, {'resolution': 'invalid'},
            {'resolution': '0*1920'},
            {'resolution': '2*2000000', 'random_resolution_min': 128, 'random_resolution_max': 128},
            {'resolution': '1*1', 'random_resolution_min': 7680, 'random_resolution_max': 7680},
            {'random_resolution_min': 1081, 'random_resolution_max': 1082},
            {'random_bitrate_min': 99}, {'random_bitrate_max': 200001},
            {'random_bitrate_min': 14001}, {'random_bitrate_min': float('inf')},
            {'random_bitrate_min': False},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    validate_output_settings(self.config(**overrides))
                with self.assertRaises(ValidationError):
                    VideoConfig(hook_dir='hooks', **self.config(**overrides))

    def test_dimension_budget_is_checked_before_material_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            config = self.config(resolution='2*2000000', random_resolution_min=128,
                                 random_resolution_max=128, _output_seed=1)
            core = VideoMatrixCore(config, lambda _: None, SharedMediaCache(folder))
            self.addCleanup(core.temp_dir.cleanup)
            core.hook_pool = [dict(file='must-not-read.mp4')]
            with patch('app.core.video_matrix.render_video', side_effect=AssertionError('no encode')):
                self.assertFalse(core.render_single_video(1))
            self.assertEqual(len(core.hook_pool), 1)
            self.assertFalse(core.output_configs)

    def test_resolution_parser_accepts_separators_and_rejects_extra_text(self):
        for value in ('1080*1920', '1080x1920', '1080X1920', ' 1080 × 1920 '):
            self.assertEqual(parse_resolution(value), (1080, 1920))
        for value in ('1080*1920px', '0*1920', '-1080*1920', '1080:1920'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_resolution(value)

    def test_stable_seed_and_independent_resolution_bitrate_namespaces(self):
        config = self.config()
        before = config.copy()
        sampled = []
        for index in range(1, 33):
            both = resolve_output_config(config, 'task', index, 71)
            self.assertEqual(both, resolve_output_config(config, 'task', index, 71))
            resolution_only = resolve_output_config(
                {**config, 'random_bitrate_enabled': False}, 'task', index, 71)
            bitrate_only = resolve_output_config(
                {**config, 'random_resolution_enabled': False}, 'task', index, 71)
            optional = resolve_output_config(
                {**config, 'enable_variants': True, 'variant_seed': 123,
                 'enable_random_cover': True, '_cover_seed': 456}, 'task', index, 71)
            self.assertEqual(both['resolution'], resolution_only['resolution'])
            self.assertEqual(both['bitrate'], bitrate_only['bitrate'])
            self.assertEqual(both['_output_settings'], optional['_output_settings'])
            self.assertIn(parse_resolution(both['resolution']), resolution_candidates('1080*1920', 1080, 1440))
            self.assertTrue(8000 <= int(both['bitrate'][:-1]) <= 14000)
            sampled.append((both['resolution'], both['bitrate']))
        self.assertGreater(len(set(sampled)), 1)
        self.assertNotEqual(sampled, [(resolve_output_config(config, 'other', index, 71)['resolution'],
                                      resolve_output_config(config, 'other', index, 71)['bitrate'])
                                     for index in range(1, 33)])
        self.assertEqual(config, before)

    def test_task_normalization_creates_output_seed_once_and_preserves_fixed_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            service = TaskService(SharedMediaCache(folder))
            config = VideoConfig(hook_dir='hooks', random_resolution_enabled=True).model_dump()
            with patch('app.services.task_service.random.SystemRandom') as system_random:
                system_random.return_value.randrange.return_value = 12345
                normalized = service._normalize_config(config)
                repeated = service._normalize_config(normalized)
                fixed = service._normalize_config({**config, '_output_seed': 0})
            self.assertEqual(normalized['_output_seed'], 12345)
            self.assertEqual(repeated['_output_seed'], 12345)
            self.assertEqual(fixed['_output_seed'], 0)
            self.assertNotIn('_output_seed', config)
            system_random.return_value.randrange.assert_called_once()


class RandomOutputProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.source = cls.root / '原素材.mp4'
        cls.blend = cls.root / 'B 画面.mp4'
        cls.watermark = cls.root / 'watermark.png'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=256x192:r=24:d=3',
               '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=48000:d=3',
               '-c:v', 'libx264', '-c:a', 'aac', '-shortest', cls.source)
        ffmpeg('-f', 'lavfi', '-i', 'color=red:s=96x64:r=24:d=0.5',
               '-f', 'lavfi', '-i', 'sine=frequency=1800:d=0.5',
               '-c:v', 'libx264', '-c:a', 'aac', '-shortest', cls.blend)
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=16x16', '-frames:v', '1', cls.watermark)
        cls.source_digests = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in (cls.source, cls.blend, cls.watermark)}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def make_core(self, output_name, **overrides):
        output = self.root / output_name
        output.mkdir(exist_ok=True)
        config = dict(task_name='stable-task', out_dir=str(output), t_hook=1., t_body=.5,
                      total_clips=3, hook_r=1, body_r=1, bgm_r=1,
                      resolution='256*192', fps='24', bitrate='400k', enable_gpu=False,
                      vol_hook_orig=100, vol_orig=70, vol_bgm=100, vol_voice=100,
                      enable_variants=False, variant_seed=3, _selection_seed=7, _output_seed=17,
                      random_resolution_enabled=True, random_resolution_min=128,
                      random_resolution_max=192, random_bitrate_enabled=True,
                      random_bitrate_min=400, random_bitrate_max=600)
        config.update(overrides)
        core = VideoMatrixCore(config, lambda message: None, SharedMediaCache(str(output / 'state')))
        self.addCleanup(core.temp_dir.cleanup)
        core.hook_pool = [dict(file=str(self.source), start=0., duration=1., has_audio=True)]
        core.body_pool = [dict(file=str(self.source), start=1., duration=.5, has_audio=True),
                          dict(file=str(self.source), start=2., duration=.5, has_audio=True)]
        return core

    def assert_sources_unchanged(self):
        for path, digest in self.source_digests.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, path)

    def test_oversized_watermark_is_clipped_before_random_upscale(self):
        watermark = self.root / 'oversized-watermark.png'
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=1024x1024', '-frames:v', '1', watermark)
        core = self.make_core('oversized-watermark', resolution='2*2', watermark_path=str(watermark),
                              random_resolution_min=128, random_resolution_max=128)
        path, commands, _ = self.render(core)
        graph = commands[0][commands[0].index('-filter_complex') + 1]
        self.assertIn("crop=w='min(iw,2)':h='min(ih,2)':exact=1,scale=", graph)
        info = video_stream(path)
        self.assertEqual((info['width'], info['height']), (128, 128))

    def render(self, core, index=1, fail_first=False):
        commands, configs = [], []

        def capture(command, *args, **kwargs):
            commands.append(command[:])
            configs.append(kwargs['config'].copy())
            if fail_first and len(commands) == 1:
                return False, 'Error initializing filter: simulated failure'
            return render_video(command, *args, **kwargs)

        with patch('app.core.video_matrix.render_video', side_effect=capture):
            ok, path, _ = core.render_single_video(index, return_result=True)
        self.assertTrue(ok, core.output_configs.get(index))
        self.assert_sources_unchanged()
        return path, commands, configs

    def assert_resolved_output(self, path, core, commands, index=1):
        config = core.output_configs[index]
        width, height = parse_resolution(config['resolution'])
        self.assertIn((width, height), resolution_candidates('256*192', 128, 192))
        self.assertTrue(400 <= int(config['bitrate'][:-1]) <= 600)
        video = video_stream(path)
        self.assertEqual((video['width'], video['height']), (width, height))
        self.assertEqual(int(video['nb_frames']), 48)
        self.assertAlmostEqual(float(video['duration']), 2., places=3)
        for command in commands:
            self.assertEqual(command[command.index('-b:v') + 1], config['bitrate'])
            graph = command[command.index('-filter_complex') + 1]
            self.assertEqual(graph.count(f'fps=24,scale={width}:{height}:'),
                             4 if 'blend=' in graph else 3)
        self.assertEqual((core.config['resolution'], core.config['bitrate']), ('256*192', '400k'))
        return config

    def test_random_settings_apply_without_dedup_and_preserve_audio_timeline(self):
        manual = self.make_core('off-manual', random_resolution_enabled=False, random_bitrate_enabled=False)
        before = manual.config.copy()
        baseline, _, _ = self.render(manual)
        self.assertEqual(manual.config, before)
        self.assertNotIn('_output_settings', manual.output_configs[1])
        random_core = self.make_core('off-random', variant_blend_enabled=True,
                                     variant_blend_path='missing')
        with patch('app.core.dedup_plan.build_recipe', side_effect=AssertionError('must bypass')):
            output, commands, _ = self.render(random_core)
        self.assertEqual(len(commands), 1)
        self.assertNotIn('_variant_applied', random_core.output_configs[1])
        self.assert_resolved_output(output, random_core, commands)
        self.assertEqual(audio_hash(output), audio_hash(baseline))
        for key in ('duration', 'nb_frames', 'avg_frame_rate'):
            self.assertEqual(video_stream(output)[key], video_stream(baseline)[key])

    def test_fused_dedup_and_b_blend_share_size_bitrate_and_single_encode(self):
        baseline, _, _ = self.render(self.make_core('blend-baseline'))
        core = self.make_core('blend-random', enable_variants=True, variant_color=True,
                              variant_blend_enabled=True, variant_blend_path=str(self.blend))
        output, commands, configs = self.render(core)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].count('-i'), 7)
        self.assertIn('blend=', commands[0][commands[0].index('-filter_complex') + 1])
        self.assert_resolved_output(output, core, commands)
        self.assertEqual(configs[0]['_output_settings'], core.output_configs[1]['_output_settings'])
        self.assertEqual(audio_hash(output), audio_hash(baseline))

    def test_dedup_fallback_reuses_sampled_settings_and_clip_inputs(self):
        baseline, _, _ = self.render(self.make_core('fallback-baseline'))
        core = self.make_core('fallback-random', enable_variants=True, variant_color=True,
                              variant_blend_enabled=True, variant_blend_path=str(self.blend))
        output, commands, configs = self.render(core, fail_first=True)
        self.assertEqual(len(commands), 2)
        self.assert_resolved_output(output, core, commands)
        self.assertEqual(configs[0]['_output_settings'], configs[1]['_output_settings'])
        # The final input in the first command is B; all original clip inputs remain identical.
        initial_inputs = commands[0][:commands[0].index('-filter_complex')]
        fallback_inputs = commands[1][:commands[1].index('-filter_complex')]
        self.assertEqual(initial_inputs[:len(fallback_inputs)], fallback_inputs)
        self.assertTrue(core.output_configs[1]['_variant_applied']['skipped'])
        self.assertEqual(audio_hash(output), audio_hash(baseline))

    def test_output_sampling_does_not_consume_material_selection_rng(self):
        manual = self.make_core('rng-manual', random_resolution_enabled=False, random_bitrate_enabled=False)
        random_core = self.make_core('rng-random')
        for index in (1, 2, 3):
            _, manual_commands, _ = self.render(manual, index)
            _, random_commands, _ = self.render(random_core, index)
            self.assertEqual(manual.rng.getstate(), random_core.rng.getstate())
            for command in (manual_commands[0], random_commands[0]):
                command[:] = command[:command.index('-filter_complex')]
            self.assertEqual(manual_commands, random_commands)

    def test_watermark_scales_relative_to_reference_resolution(self):
        core = self.make_core('watermark-random', watermark_path=str(self.watermark))
        output, commands, _ = self.render(core)
        config = self.assert_resolved_output(output, core, commands)
        ratio = parse_resolution(config['resolution'])[0] / 256
        graph = commands[0][commands[0].index('-filter_complex') + 1]
        self.assertIn(f"iw*{ratio:.12f}/2", graph)
        self.assertIn('[wm_scaled]', graph)

    def test_cover_replace_insert_preserve_sampled_size_target_bitrate_and_audio(self):
        for mode in ('replace', 'insert'):
            with self.subTest(mode=mode):
                core = self.make_core('cover-' + mode)
                path, commands, _ = self.render(core)
                config = self.assert_resolved_output(path, core, commands)
                before_audio = audio_hash(path)
                encoding_commands = []

                def capture(command, *args, **kwargs):
                    encoding_commands.append(command[:])
                    return original_run(command, *args, **kwargs)

                original_run = VideoVariantProcessor._run
                with patch.object(VideoVariantProcessor, '_run', side_effect=capture):
                    ok, error, summary = RandomCoverProcessor().process(
                        path, {**config, 'random_cover_mode': mode}, 23)
                self.assertTrue(ok, error)
                self.assertEqual(summary['mode'], mode)
                encoding = next(command for command in encoding_commands if '-b:v' in command)
                self.assertEqual(encoding[encoding.index('-b:v') + 1], config['bitrate'])
                video = video_stream(path)
                self.assertEqual((video['width'], video['height']), parse_resolution(config['resolution']))
                self.assertEqual(int(video['nb_frames']), 48 + (mode == 'insert'))
                self.assertAlmostEqual(float(video['duration']), 2 + (mode == 'insert') / 24, places=3)
                self.assertEqual(audio_hash(path), before_audio)
                self.assert_sources_unchanged()


if __name__ == '__main__':
    unittest.main()
