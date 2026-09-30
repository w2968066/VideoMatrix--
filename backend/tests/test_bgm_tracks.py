import array
import math
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.bgm_tracks import prepare_track
from app.core.ffmpeg import FFMPEG, probe_media
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.models.schemas import VideoConfig
from tests.test_grouped_body_and_cover import run


def tone_level(file, start, frequency):
    result = subprocess.run([FFMPEG, '-v', 'error', '-ss', str(start), '-i', str(file),
        '-t', '0.1', '-vn', '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'],
        check=True, capture_output=True)
    samples = array.array('f', result.stdout)
    return abs(sum(sample * complex(math.cos(2 * math.pi * frequency * i / 8000),
                                  math.sin(2 * math.pi * frequency * i / 8000))
                   for i, sample in enumerate(samples))) / max(1, len(samples))


class ThreeTrackMusicTests(unittest.TestCase):
    def test_actual_boundaries_fades_and_master_policy(self):
        settings = dict(volume=100, fade_in=2, fade_out=2, short_behavior='loop', source_mode='random', overlap=1)
        clip = dict(file='x.wav', duration=.2)
        hook = prepare_track('hook', settings, clip, .75, 3, False, random.Random(0))
        self.assertTrue(hook['loop'])
        self.assertEqual(hook['offset'], 0)
        self.assertEqual(hook['duration'], .75)
        self.assertAlmostEqual(hook['fade_in'], .375)
        body = prepare_track('body', settings, clip, .75, 3, False, random.Random(0))
        self.assertEqual(body['offset'], .75)
        self.assertEqual(body['duration'], 2.25)
        master = prepare_track('full', settings, clip, 1, 1, True, random.Random(0))
        self.assertFalse(master['loop'])
        self.assertEqual(master['start'], 0)
        self.assertEqual(master['duration'], .2)
        self.assertIsNone(prepare_track('body', settings, clip, 3, 3, False, random.Random(0)))

    def test_real_output_track_scopes_loop_stop_and_input_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('hook', 'body', 'state', 'out'):
                (root / name).mkdir()
            run('-f', 'lavfi', '-i', 'color=red:s=64x64:r=30:d=1', str(root / 'hook' / 'h.mp4'))
            run('-f', 'lavfi', '-i', 'color=blue:s=64x64:r=30:d=8', str(root / 'body' / 'b.mp4'))
            for name, frequency, duration in [('full', 220, 2.5), ('shortfull', 220, .2), ('hookmusic', 440, .2), ('bodymusic', 880, .31), ('voice', 1760, 3)]:
                run('-f', 'lavfi', '-i', f'sine=frequency={frequency}:d={duration}', str(root / f'{name}.wav'))
            run('-f', 'lavfi', '-i', 'color=black:s=64x64:r=30:d=3',
                '-f', 'lavfi', '-i', 'sine=frequency=880:d=0.31', str(root / 'short_audio.mp4'))
            def track(name, **changes):
                return dict(enabled=True, path=str(root / f'{name}.wav'), volume=100,
                            fade_in=0, fade_out=0, short_behavior='loop', source_mode='start', overlap=.3, **changes)
            # Validate the real public schema, not just a hand-made config.
            config = VideoConfig(hook_dir=str(root / 'hook'), body_dirs=[str(root / 'body')],
                t_hook=1, hook_r=1, t_body=.5, body_r=0, total_clips=4, duration_mode='bgm',
                vol_orig=0, vol_hook_orig=0, enable_gpu=False, resolution='64*64', fps=30, bitrate='300k',
                bgm_tracks=dict(full=track('full'), hook=track('hookmusic'), body=track('bodymusic'))).model_dump()
            config['out_dir'] = str(root / 'out')
            def render(config, index):
                logs = []
                core = VideoMatrixCore(dict(config, task_name=f'case{index}'), logs.append, SharedMediaCache(str(root / 'state')))
                try:
                    ok, reason = core.pre_flight_check()
                    self.assertTrue(ok, reason)
                    ok, output, _ = core.render_single_video(index, return_result=True)
                    self.assertTrue(ok, '\n'.join(logs))
                    return output, logs, core.output_configs[index]
                finally:
                    core.temp_dir.cleanup()
            output, logs, actual = render(config, 1)
            self.assertAlmostEqual(actual['total_duration'], 2.5)
            for point in (.4, 1.6):
                self.assertGreater(tone_level(output, point, 220), .045)
            self.assertGreater(tone_level(output, .4, 440), .02)  # survives short-source loop
            self.assertLess(tone_level(output, 1.6, 440), .004)
            self.assertLess(tone_level(output, .4, 880), .004)
            self.assertGreater(tone_level(output, 1.6, 880), .02)
            self.assertTrue(any('循环补齐' in line for line in logs))
            # An additional input after all three BGM tracks must still map to voice.
            with_voice = dict(config, voice_dir=str(root / 'voice.wav'), vol_voice=100)
            output, _, _ = render(with_voice, 2)
            self.assertGreater(tone_level(output, .4, 1760), .02)
            self.assertGreater(tone_level(output, 1.6, 1760), .02)
            # Stop leaves the remainder silent; per-track fades use audible length.
            stopped = dict(config, bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            stopped['bgm_tracks']['hook'].update(short_behavior='stop', fade_in=1, fade_out=1)
            stopped['bgm_tracks']['body'].update(short_behavior='stop')
            output, logs, _ = render(stopped, 3)
            self.assertLess(tone_level(output, .4, 440), .004)
            self.assertLess(tone_level(output, 1.6, 880), .004)
            self.assertTrue(any('淡化时长已按比例缩短' in line for line in logs))
            # Full track can be silent while determining length, Hook-only audio stays.
            muted = dict(config, bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            muted['bgm_tracks']['full']['volume'] = 0
            muted['bgm_tracks']['body']['enabled'] = False
            output, _, actual = render(muted, 4)
            self.assertEqual(actual['total_duration'], 2.5)
            self.assertGreater(tone_level(output, .4, 440), .02)
            self.assertLess(tone_level(output, .4, 220), .004)
            # Long full Hook is retained; the master must not loop past its ending.
            long_hook = dict(config, hook_full_duration=True, body_dirs=[], bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            long_hook['bgm_tracks']['full']['path'] = str(root / 'shortfull.wav')
            output, logs, actual = render(long_hook, 5)
            self.assertEqual(actual['total_clips'], 1)
            self.assertTrue(any('跳过 Body BGM' in line for line in logs))
            self.assertLess(tone_level(output, .4, 220), .004)
            # No full music is required in manual-length mode.
            manual = dict(config, duration_mode='clips', bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            manual['bgm_tracks']['full']['enabled'] = False
            output, _, actual = render(manual, 6)
            self.assertEqual(actual['total_duration'], 2.5)
            # Loop boundaries must not restart the fade on every short source.
            faded = dict(config, bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            faded['bgm_tracks']['hook'].update(fade_in=.1, fade_out=.1)
            output, _, _ = render(faded, 7)
            middle = tone_level(output, .4, 440)
            self.assertGreater(middle, .045)
            self.assertLess(tone_level(output, 0, 440), middle * .7)
            self.assertLess(tone_level(output, .9, 440), middle * .7)
            # Grouped full-source Body still trims to the full-track target.
            grouped = dict(config, hook_full_duration=True, body_mode='grouped', body_groups=[
                dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=99, full_duration=True),
                dict(enabled=True, folder=str(root / 'body'), clip_count=1, clip_duration=.5)])
            output, _, actual = render(grouped, 8)
            self.assertAlmostEqual(actual['total_duration'], 2.5)
            self.assertAlmostEqual(actual['_body_clip_durations'][-1], 1.5)
            self.assertGreater(tone_level(output, 1.6, 880), .045)
            # Video container duration must not introduce gaps when looping audio.
            video_music = dict(config, bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            video_music['bgm_tracks']['body']['path'] = str(root / 'short_audio.mp4')
            output, _, _ = render(video_music, 9)
            self.assertGreater(tone_level(output, 1.6, 880), .045)
            missing = dict(manual, duration_mode='bgm')
            core = VideoMatrixCore(missing, lambda _: None, SharedMediaCache(str(root / 'state')))
            self.assertFalse(core.pre_flight_check()[0])
            core.temp_dir.cleanup()
            missing = dict(config, bgm_tracks={scope: dict(settings) for scope, settings in config['bgm_tracks'].items()})
            missing['bgm_tracks']['hook']['path'] = str(root / 'missing.wav')
            core = VideoMatrixCore(missing, lambda _: None, SharedMediaCache(str(root / 'state')))
            ok, message = core.pre_flight_check()
            self.assertFalse(ok)
            self.assertIn('Hook BGM 路径不存在', message)
            core.temp_dir.cleanup()


if __name__ == '__main__':
    unittest.main()
