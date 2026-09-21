import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from app.core.ffmpeg import probe_media
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from tests.test_grouped_body_and_cover import run, frame_bytes, dominant_channel


class FullHookTests(unittest.TestCase):
    def test_complete_hooks_cycle_and_concurrent_timelines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('hook', 'body', 'bgm', 'out'):
                (root / name).mkdir()
            for index, seconds in enumerate((0.5, 1, 1.5)):
                run('-f', 'lavfi', '-i', f'color=red:s=64x64:r=24:d={seconds}', str(root / 'hook' / f'{index}.mp4'))
            run('-f', 'lavfi', '-i', 'color=blue:s=64x64:r=24:d=2', str(root / 'body' / 'b.mp4'))
            run('-f', 'lavfi', '-i', 'sine=d=4', str(root / 'bgm' / 'a.wav'))
            config = dict(task_name='full', hook_dir=str(root / 'hook'), body_dirs=[str(root / 'body')],
                          bgm_dir=str(root / 'bgm'), out_dir=str(root / 'out'), hook_full_duration=True,
                          t_hook=99, t_body=0.5, total_clips=2, target_count=6, hook_r=0.2, body_r=0,
                          bgm_r=0, resolution='64*64', fps='24', bitrate='300k', enable_gpu=False,
                          vol_orig=0, vol_hook_orig=0, vol_bgm=100, vol_voice=0, apply_bgm_to_hook=False)
            core = VideoMatrixCore(config, lambda message: None, SharedMediaCache(str(root / 'state')))
            ok, message = core.pre_flight_check()
            self.assertTrue(ok, message)
            self.assertEqual(sorted(c['duration'] for c in core.hook_pool), [0.5, 1, 1.5])
            for start in (1, 4):
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    results = list(pool.map(lambda index: core.render_single_video(index, return_result=True), range(start, start + 3)))
                durations = []
                for index, (success, output, _) in zip(range(start, start + 3), results):
                    self.assertTrue(success)
                    duration = core.output_configs[index]['t_hook']
                    durations.append(duration)
                    video = next(s for s in probe_media(output)['streams'] if s['codec_type'] == 'video')
                    self.assertAlmostEqual(float(video['duration']), duration + 0.5, delta=1 / 24)
                    self.assertEqual(dominant_channel(frame_bytes(output, int(duration * 24) - 1)), 0)
                    self.assertEqual(dominant_channel(frame_bytes(output, int(duration * 24) + 1)), 2)
                self.assertEqual(sorted(durations), [0.5, 1, 1.5])
            self.assertEqual(core.config['t_hook'], 99)


if __name__ == '__main__':
    unittest.main()
