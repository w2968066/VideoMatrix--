import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.core.ffmpeg import run_process


class FfmpegDiagnosticsTests(unittest.TestCase):
    def test_failure_preserves_first_cause_and_full_stderr(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APPDATA': directory}):
            script = "import sys; sys.stderr.write('CUDA_ERROR_UNKNOWN: context creation failed\\n' + 'x'*100000 + '\\nConversion failed!'); sys.exit(1)"
            ok, detail = run_process([sys.executable, '-c', script], timeout=10)
            self.assertFalse(ok)
            self.assertIn('CUDA_ERROR_UNKNOWN', detail)
            self.assertIn('Conversion failed!', detail)
            self.assertLess(len(detail), 80000)
            logs = list((Path(directory) / 'VideoMatrix' / 'logs').glob('ffmpeg-error-*.log'))
            self.assertEqual(len(logs), 1)
            saved = logs[0].read_text(encoding='utf-8')
            self.assertIn('x'*100000, saved)
            self.assertIn(str(logs[0]), detail)

    def test_cancel_does_not_create_a_failure_log(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'APPDATA': directory}):
            ok, detail = run_process([sys.executable, '-c', 'import time; time.sleep(10)'], is_cancelled=lambda: True)
            self.assertFalse(ok)
            self.assertEqual(detail, '已停止')
            self.assertFalse((Path(directory) / 'VideoMatrix' / 'logs').exists())


if __name__ == '__main__':
    unittest.main()
