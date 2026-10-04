"""B video EOF is independent of a longer, unused B audio track."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.dedup_blend import prepare_blend
from app.core.ffmpeg import FFMPEG, probe_media


def run_ffmpeg(*args):
    subprocess.run([FFMPEG, '-v', 'error', '-y', *map(str, args)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class BlendLoopEOFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='blend-loop-eof-')
        cls.root = Path(cls.temp.name).resolve()
        cls.main = cls.root / 'main.mp4'
        cls.b = cls.root / 'short-video-long-audio.mp4'
        run_ffmpeg('-f', 'lavfi', '-i', 'color=black:s=64x48:r=24:d=2',
                   '-c:v', 'libx264', '-pix_fmt', 'yuv420p', cls.main)
        # Twelve distinct gray frames; audio is four times longer than picture.
        run_ffmpeg('-f', 'lavfi', '-i', "nullsrc=s=64x48:r=24:d=0.5,geq=lum='40+N*10':cb=128:cr=128",
                   '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:d=2',
                   '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-crf', '0',
                   '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.b)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_loop_repeats_picture_and_freeze_holds_last_frame(self):
        for mode in ('loop', 'freeze'):
            with self.subTest(mode=mode):
                blend = prepare_blend(dict(variant_blend_enabled=True, variant_blend_path=str(self.b),
                                           variant_blend_eof=mode, variant_blend_opacity=.15,
                                           total_duration=2, t_hook=1, resolution='64*48', fps=24), probe_media)
                graph, label = blend.filters(1, '[0:v]')
                frames = subprocess.check_output([
                    FFMPEG, '-v', 'error', '-i', str(self.main), *blend.input_args(),
                    '-filter_complex', graph + f';{label}select=eq(n\\,6)+eq(n\\,11)+eq(n\\,18)[sample]',
                    '-map', '[sample]', '-an', '-vsync', '0', '-frames:v', '3',
                    '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-',
                ])
                size = 64 * 48 * 3 // 2
                self.assertEqual(len(frames), size * 3)
                early, last, repeated = (frames[i * size:(i+1) * size] for i in range(3))
                self.assertTrue(early != last, 'fixture must have distinct moving frames')
                expected = early if mode == 'loop' else last
                self.assertTrue(repeated == expected, f'{mode} must use B video EOF, not its audio tail')


if __name__ == '__main__':
    unittest.main()
