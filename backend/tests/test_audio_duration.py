import random
import tempfile
import unittest
from pathlib import Path

from app.core.audio_timeline import fit_body_to_audio
from app.core.ffmpeg import probe_media, extract_audio_duration
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.core.video_variant import build_variant_plan
from app.core.video_cover import RandomCoverProcessor
from app.models.schemas import VideoConfig
from tests.test_grouped_body_and_cover import run


class AudioTimelineTests(unittest.TestCase):
    def test_group_cycle_trim_and_exhaustion(self):
        config = dict(body_mode='grouped', body_groups=[
            dict(enabled=True, folder='a', clip_count=2, clip_duration=1),
            dict(enabled=True, folder='b', clip_count=1, clip_duration=1),
        ], apply_bgm_to_hook=False)
        pools = [[dict(file=f'{group}{i}', duration=1) for i in range(6)] for group in 'ab']
        clips = fit_body_to_audio(config, pools, 2, 4.3, random.Random(0))
        self.assertEqual([c['file'][0] for c in clips], list('aabaa'))
        self.assertAlmostEqual(clips[-1]['duration'], .3)
        self.assertEqual(len({c['file'] for c in clips}), len(clips))
        self.assertEqual([len(p) for p in pools], [6, 6])
        with self.assertRaisesRegex(ValueError, '素材不足'):
            fit_body_to_audio(config, pools, 2, 30, random.Random(0))
        config['apply_bgm_to_hook'] = True
        self.assertEqual(fit_body_to_audio(config, [[], []], 10, 2, random.Random(0)), [])

    def test_optional_schema_and_full_audio_duration(self):
        self.assertEqual(VideoConfig(hook_dir='x').bgm_dir, '')
        info = {'format': {'duration': '2.123'}, 'streams': [{'codec_type': 'audio', 'duration': '2.123'}]}
        self.assertEqual(extract_audio_duration(info, safety_margin=0), 2.123)

    def test_render_combinations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['hook', 'body', 'bgm', 'voice', 'out', 'srt', 'full_body']:
                (root / name).mkdir()
            for i, seconds in enumerate((.75, 1.25)):
                run('-f', 'lavfi', '-i', f'color=green:s=64x64:r=24:d={seconds}', str(root / 'full_body' / f'{i}.mp4'))
            run('-f', 'lavfi', '-i', 'color=red:s=64x64:r=24:d=1', '-f', 'lavfi', '-i', 'sine=d=1', '-shortest', str(root / 'hook' / 'h.mp4'))
            run('-f', 'lavfi', '-i', 'color=blue:s=64x64:r=24:d=8', '-f', 'lavfi', '-i', 'sine=d=8', '-shortest', str(root / 'body' / 'b.mp4'))
            run('-f', 'lavfi', '-i', 'sine=frequency=880:d=2.375', str(root / 'bgm' / 'music.wav'))
            run('-f', 'lavfi', '-i', 'sine=d=4', str(root / 'voice' / 'v.wav'))
            (root / 'srt' / 's.srt').write_text('1\n00:00:00,000 --> 00:00:04,000\n字幕功能回归测试\n', encoding='utf-8')
            base = VideoConfig(hook_dir=str(root / 'hook'), body_dirs=[str(root / 'body')], t_hook=1,
                               t_body=.5, total_clips=3, target_count=1, hook_r=1, body_r=0,
                               vol_orig=0, vol_hook_orig=0, vol_bgm=100, enable_gpu=False,
                               resolution='64*64', fps=24, bitrate='300k').model_dump()
            base['out_dir'] = str(root / 'out')
            cases = [
                ('variable_full_body', {'body_full_duration': True, 'body_dirs': [str(root / 'full_body')], 't_body': 99}, 3, False),
                ('body_full', {'body_full_duration': True, 'total_clips': 2, 't_body': 99, 'enable_variants': True}, 9, False),
                ('body_full_bgm', {'body_full_duration': True, 'duration_mode': 'bgm'}, 2.375, True),
                ('mixed_groups', {'body_mode': 'grouped', 'body_groups': [
                    dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=99, full_duration=True),
                    dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=.5)]}, 9.5, False),
                ('silent', {}, 2, False),
                ('original', {'vol_hook_orig': 100, 'vol_orig': 100}, 2, True),
                ('voice', {'voice_dir': str(root / 'voice')}, 2, True),
                ('full', {'duration_mode': 'bgm'}, 2.375, True),
                ('body_only', {'duration_mode': 'bgm', 'apply_bgm_to_hook': False}, 3.375, True),
                ('trim', {'duration_mode': 'bgm', 'total_clips': 10}, 2.375, True),
                ('muted', {'duration_mode': 'bgm', 'vol_bgm': 0}, 2.375, False),
                ('grouped', {'duration_mode': 'bgm', 'body_mode': 'grouped', 'body_groups': [
                    dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=.5),
                    dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=.5)]}, 2.375, True),
                ('silent_grouped', {'body_mode': 'grouped', 'body_groups': [
                    dict(enabled=True, folder=str(root / 'body'), clip_count=2, clip_duration=.5)]}, 2, False),
                ('effects', {'duration_mode': 'bgm', 'enable_variants': True, 'enable_srt': True, 'srt_dir': str(root / 'srt')}, 2.375, True),
            ]
            for index, (name, changes, duration, audio) in enumerate(cases):
                with self.subTest(name=name):
                    config = dict(base, task_name=name, **changes)
                    if config.get('duration_mode') == 'bgm':
                        config['bgm_dir'] = str(root / 'bgm')
                    logs = []
                    core = VideoMatrixCore(config, logs.append, SharedMediaCache(str(root / 'state')))
                    ok, message = core.pre_flight_check()
                    self.assertTrue(ok, message)
                    ok, output, _ = core.render_single_video(index, return_result=True)
                    self.assertTrue(ok, '\n'.join(logs))
                    streams = probe_media(output)['streams']
                    video = next(s for s in streams if s['codec_type'] == 'video')
                    self.assertAlmostEqual(float(video['duration']), duration, delta=1/24 + .001)
                    self.assertEqual(any(s['codec_type'] == 'audio' for s in streams), audio)
                    if name in ('body_full', 'mixed_groups'):
                        plan = build_variant_plan(core.output_configs[index], 1)
                        self.assertAlmostEqual(sum(p.duration for p in plan), duration, places=3)
                    if config.get('duration_mode') == 'bgm':
                        self.assertEqual(core.bgm_pool[0]['start'], 0)
                        self.assertAlmostEqual(core.bgm_pool[0]['duration'], 2.375, places=3)
                        plan = build_variant_plan(core.output_configs[index], 1)
                        self.assertAlmostEqual(sum(p.duration for p in plan), duration, places=3)
                    if name == 'effects':
                        for mode in ('replace', 'insert'):
                            cover_config = dict(core.output_configs[index], random_cover_mode=mode)
                            ok, error, _ = RandomCoverProcessor().process(output, cover_config, 42)
                            self.assertTrue(ok, str(error))
                    core.temp_dir.cleanup()
            for config, message in [(dict(base, bgm_dir=str(root / 'missing')), '路径不存在'),
                                    (dict(base, body_full_duration=True), '后段素材不足'),
                                    (dict(base, duration_mode='bgm'), '需要选择 BGM'),
                                    (dict(base, bgm_dir=str(root / 'srt')), 'BGM 素材不足')]:
                core = VideoMatrixCore(config, lambda _: None, SharedMediaCache(str(root / 'state')))
                ok, reason = core.pre_flight_check()
                self.assertFalse(ok)
                self.assertIn(message, reason)
                core.temp_dir.cleanup()
            # Long Hook must survive even when there is no usable Body.
            short_bgm = root / 'short.wav'
            run('-f', 'lavfi', '-i', 'sine=d=0.5', str(short_bgm))
            config = dict(base, duration_mode='bgm', bgm_dir=str(short_bgm), body_dirs=[], hook_full_duration=True,
                          enable_srt=True, srt_dir=str(root / 'srt'), apply_srt_to_hook=False,
                          voice_dir=str(root / 'voice'), apply_voice_to_hook=False)
            logs = []
            core = VideoMatrixCore(config, logs.append, SharedMediaCache(str(root / 'state')))
            self.assertTrue(core.pre_flight_check()[0])
            self.assertTrue(core.render_single_video(30))
            self.assertEqual(core.output_configs[30]['total_clips'], 1)
            self.assertTrue(any('未覆盖完整 Hook' in line for line in logs))
            core.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
