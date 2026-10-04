"""Real FFmpeg checks for the optional, single-encode production adapter."""
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.dedup_blend import prepare_blend
from app.core.ffmpeg import FFMPEG, FFPROBE, probe_media, render_video
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.core.video_variant import VideoVariantProcessor


def ffmpeg(*args):
    subprocess.run([FFMPEG, '-v', 'error', '-y', *map(str, args)], check=True, capture_output=True)


def audio_hash(path):
    data = subprocess.check_output([
        FFPROBE, '-v', 'error', '-select_streams', 'a', '-show_packets',
        '-show_data', '-of', 'json', str(path),
    ])
    packets = json.loads(data)['packets']
    return hashlib.sha256(''.join(p['data'] for p in packets).encode()).hexdigest()


class DedupProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        cls.source = cls.root / '原素材.mp4'
        cls.b = cls.root / 'B 画面.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=s=256x192:r=24:d=3',
               '-f', 'lavfi', '-i', 'sine=frequency=400:sample_rate=48000:d=3',
               '-c:v', 'libx264', '-c:a', 'aac', '-shortest', cls.source)
        ffmpeg('-f', 'lavfi', '-i', 'color=red:s=96x64:r=24:d=0.5',
               '-f', 'lavfi', '-i', 'sine=frequency=1800:d=0.5',
               '-c:v', 'libx264', '-c:a', 'aac', '-shortest', cls.b)
        cls.source_digest = hashlib.sha256(cls.source.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def make_core(self, name, **overrides):
        output = self.root / name
        output.mkdir(exist_ok=True)
        config = dict(
            task_name=name, out_dir=str(output), t_hook=1., t_body=.5,
            total_clips=3, hook_r=1, body_r=1, bgm_r=1,
            resolution='256*192', fps='24', bitrate='400k', enable_gpu=False,
            vol_hook_orig=100, vol_orig=70, vol_bgm=100, vol_voice=100,
            enable_variants=False, variant_seed=3, _selection_seed=7,
        )
        config.update(overrides)
        core = VideoMatrixCore(config, lambda message: None, SharedMediaCache(str(output / 'state')))
        self.addCleanup(core.temp_dir.cleanup)
        core.hook_pool = [dict(file=str(self.source), start=0., duration=1., has_audio=True)]
        core.body_pool = [dict(file=str(self.source), start=1., duration=.5, has_audio=True),
                          dict(file=str(self.source), start=2., duration=.5, has_audio=True)]
        return core

    def render(self, core):
        commands = []

        def capture(command, *args, **kwargs):
            commands.append(command[:])
            return render_video(command, *args, **kwargs)

        with patch('app.core.video_matrix.render_video', side_effect=capture):
            ok, path, _ = core.render_single_video(1, return_result=True)
        self.assertTrue(ok, core.output_configs.get(1))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_digest)
        return path, commands

    def test_fused_render_keeps_timeline_audio_sources_and_one_encode(self):
        base, _ = self.render(self.make_core('base'))
        core = self.make_core('variant', enable_variants=True, variant_color=True, variant_mirror=True)
        variant, commands = self.render(core)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].count('-i'), 6)
        graph = commands[0][commands[0].index('-filter_complex') + 1]
        self.assertEqual(graph.count('scale='), 3)
        self.assertEqual(graph.count('concat='), 1)
        self.assertNotIn('split=', graph)
        self.assertNotIn('tmix', graph)
        self.assertEqual(audio_hash(base), audio_hash(variant))
        before = next(s for s in probe_media(base)['streams'] if s['codec_type'] == 'video')
        after = next(s for s in probe_media(variant)['streams'] if s['codec_type'] == 'video')
        for key in ('width', 'height', 'duration', 'nb_frames', 'avg_frame_rate'):
            self.assertEqual(before[key], after[key], key)
        self.assertNotEqual(Path(base).read_bytes(), Path(variant).read_bytes())
        self.assertIn('_variant_applied', core.output_configs[1])
        self.assertEqual(len(list(Path(variant).parent.glob('*.mp4'))), 1)

    def test_off_never_prepares_recipe_or_probes_stale_blend_path(self):
        core = self.make_core('disabled', variant_blend_enabled=True, variant_blend_path='missing')
        with patch('app.core.dedup_plan.build_recipe', side_effect=AssertionError('must bypass')), \
             patch.object(core, '_probe_variant_input', side_effect=AssertionError('must bypass')):
            _, commands = self.render(core)
        graph = commands[0][commands[0].index('-filter_complex') + 1]
        self.assertIn('fps=24,scale=256:192:force_original_aspect_ratio=increase,crop=256:192,setsar=1,format=yuv420p[v0]', graph)
        self.assertNotIn('_variant_applied', core.output_configs[1])

    def test_graph_failure_retries_identical_base_selection_once(self):
        core = self.make_core('fallback', enable_variants=True, variant_color=True,
                              variant_blend_enabled=True, variant_blend_path=str(self.b))
        calls = []

        def fail_then_render(command, *args, **kwargs):
            calls.append(command[:])
            if len(calls) == 1:
                return False, 'Error initializing filter: simulated failure'
            return render_video(command, *args, **kwargs)

        with patch('app.core.video_matrix.render_video', side_effect=fail_then_render):
            ok, path, _ = core.render_single_video(1, return_result=True)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        fallback_graph = calls[1][calls[1].index('-filter_complex') + 1]
        self.assertNotIn('dedup_', fallback_graph)
        self.assertNotIn('eq=', fallback_graph)
        self.assertEqual(calls[1].count('-i'), 6)
        self.assertEqual(calls[0][:calls[1].index('-filter_complex')], calls[1][:calls[1].index('-filter_complex')])
        self.assertTrue(core.output_configs[1]['_variant_applied']['skipped'])
        self.assertIn('回退基础混剪', core.output_configs[1]['_variant_warnings'][0])
        self.assertTrue(Path(path).is_file())

    def test_stop_does_not_start_fallback_encode(self):
        core = self.make_core('stop', enable_variants=True, variant_color=True)

        def cancel(*args, **kwargs):
            core.is_running = False
            return False, '已停止'

        with patch('app.core.video_matrix.render_video', side_effect=cancel) as render:
            self.assertFalse(core.render_single_video(1))
        self.assertEqual(render.call_count, 1)

    def test_blend_loop_freeze_and_short_skip_preserve_main_audio_and_duration(self):
        base, _ = self.render(self.make_core('blend-base'))
        for eof in ('loop', 'freeze', 'error'):
            with self.subTest(eof=eof):
                core = self.make_core('blend-' + eof, enable_variants=True, variant_crop=False,
                                      variant_blend_enabled=True, variant_blend_path=str(self.b),
                                      variant_blend_eof=eof, variant_blend_opacity=.1)
                out, commands = self.render(core)
                self.assertEqual(len(commands), 1)
                self.assertEqual(audio_hash(base), audio_hash(out))
                video = next(s for s in probe_media(out)['streams'] if s['codec_type'] == 'video')
                self.assertEqual(int(video['nb_frames']), 48)
                if eof == 'error':
                    self.assertIn('短于主片', core.output_configs[1]['_variant_warnings'][0])
                    self.assertEqual(commands[0].count('-i'), 6)
                else:
                    self.assertNotIn('_variant_warnings', core.output_configs[1])
                    self.assertEqual(commands[0].count('-i'), 7)

    def test_grouped_and_original_durations_drive_actual_recipe(self):
        core = self.make_core('grouped', enable_variants=True, hook_full_duration=True,
                              body_mode='grouped', body_groups=[
                                  dict(enabled=True, folder='one', clip_count=1, clip_duration=2, full_duration=True),
                                  dict(enabled=True, folder='two', clip_count=1, clip_duration=2, full_duration=True),
                              ])
        core.hook_pool[0]['duration'] = .75
        core.body_group_pools = [[{**core.body_pool[0], 'duration': .75}], [core.body_pool[1]]]
        self.render(core)
        parts = core.output_configs[1]['_variant_applied']['parameters']
        self.assertEqual([p['duration'] for p in parts], [.75, .75, .5])
        self.assertEqual([p['start'] for p in parts], [0, .75, 1.5])

    def test_three_bgm_voice_subtitle_watermark_keep_audio_and_order(self):
        watermark = self.root / 'watermark.png'
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=16x16', '-frames:v', '1', watermark)
        subtitles = self.root / 'subs'
        subtitles.mkdir(exist_ok=True)
        (subtitles / 'demo.srt').write_text('1\n00:00:00,000 --> 00:00:02,000\nSUBTITLE\n', encoding='utf-8')
        output_paths = []
        for enabled in (False, True):
            core = self.make_core('tracks-' + str(enabled), enable_variants=enabled,
                                  enable_random_cover=enabled, random_cover_mode='replace', _cover_seed=17,
                                  variant_color=True, variant_blend_enabled=enabled,
                                  variant_blend_path=str(self.b),
                                  enable_srt=True, srt_dir=str(subtitles),
                                  watermark_path=str(watermark), apply_srt_to_hook=False,
                                  apply_voice_to_hook=False, apply_watermark_to_hook=False,
                                  bgm_tracks={scope: dict(enabled=True, volume=20, fade_in=.1, fade_out=.1)
                                              for scope in ('full', 'hook', 'body')})
            music = dict(file=str(self.source), start=0, duration=3, has_audio=True)
            core.bgm_pool = [music]
            core.bgm_track_pools = {scope: [music] for scope in ('full', 'hook', 'body')}
            core.voice_pool = [music]
            path, commands = self.render(core)
            output_paths.append(path)
            if enabled:
                self.assertEqual(len(commands), 1)
                graph = commands[0][commands[0].index('-filter_complex') + 1]
                self.assertLess(graph.index('blend='), graph.index('subtitles='))
                self.assertLess(graph.index('subtitles='), graph.index('overlay='))
                self.assertIn('[11:0]', graph, 'B must be appended after all original audio/overlay inputs')
                self.assertIn('[12:v]setpts=PTS-STARTPTS,setsar=1[cover_frame]', graph)
        self.assertEqual(audio_hash(output_paths[0]), audio_hash(output_paths[1]))

    def test_full_bgm_duration_uses_fitted_body_recipe(self):
        core = self.make_core('bgm-duration', enable_variants=True, duration_mode='bgm')
        core.body_pool.append(dict(file=str(self.source), start=1.5, duration=.5, has_audio=True))
        core.bgm_pool = [dict(file=str(self.source), start=0, duration=2.5, has_audio=True)]
        path, _ = self.render(core)
        cfg = core.output_configs[1]
        self.assertEqual(cfg['total_duration'], 2.5)
        self.assertEqual(sum(p['duration'] for p in cfg['_variant_applied']['parameters']), 2.5)
        video = next(s for s in probe_media(path)['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video['nb_frames']), 60)

    def test_blend_probe_excludes_attached_picture_and_is_cached(self):
        core = self.make_core('probe')
        with patch('app.core.video_variant.VideoVariantProcessor._probe_media', wraps=VideoVariantProcessor._probe_media) as probe:
            first = core._probe_variant_input(str(self.b))
            second = core._probe_variant_input(str(self.b))
        self.assertEqual(first, second)
        self.assertEqual(probe.call_count, 1)
        info = {'streams': [dict(index=0, codec_type='video', disposition={'attached_pic': 1}),
                            dict(index=2, codec_type='video', duration='2')], 'format': {}}
        blend = prepare_blend(dict(variant_blend_enabled=True, variant_blend_path=str(self.b),
                                   total_duration=2, t_hook=1, fps=24), lambda path: info)
        self.assertEqual(blend.stream_index, 2)

    def test_unknown_b_video_length_is_not_taken_from_longer_audio(self):
        info = {'streams': [dict(index=0, codec_type='video'),
                            dict(index=1, codec_type='audio', duration='100')],
                'format': {'duration': '100'}}
        config = dict(variant_blend_enabled=True, variant_blend_path=str(self.b),
                      variant_blend_eof='error', total_duration=2, t_hook=1, fps=24)
        with self.assertRaisesRegex(ValueError, '无法快速确认'):
            prepare_blend(config, lambda path: info)
        self.assertIsNotNone(prepare_blend({**config, 'variant_blend_eof': 'loop'}, lambda path: info))

    def test_bad_b_probe_is_cached_for_batch_without_repeated_timeout(self):
        core = self.make_core('bad-b-probe')
        with patch('app.core.video_variant.VideoVariantProcessor._probe_media', side_effect=TimeoutError('探测超时')) as probe:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, '探测超时'):
                    core._probe_variant_input(str(self.b))
        self.assertEqual(probe.call_count, 1)

    def test_native_blend_weights_match_expression_without_pixel_interpreter(self):
        for opacity in (.01, .03, .15):
            results = []
            for expression in (f"all_expr='A*{1-opacity:.9f}+B*{opacity:.9f}'",
                               f'all_mode=normal:all_opacity={1-opacity:.9f}'):
                results.append(subprocess.check_output([
                    FFMPEG, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=24:d=0.1',
                    '-f', 'lavfi', '-i', 'color=red:s=64x64:r=24:d=0.1',
                    '-filter_complex', '[0:v][1:v]blend=' + expression,
                    '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-',
                ]))
            self.assertEqual(len(results[0]), len(results[1]))
            # Native SIMD and expression paths may round by one luma/chroma level.
            self.assertLessEqual(max(abs(a - b) for a, b in zip(*results)), 1)


if __name__ == '__main__':
    unittest.main()
