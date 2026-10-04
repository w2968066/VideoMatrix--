import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.core.ffmpeg import run_process


class FfmpegDiagnosticsTests(unittest.TestCase):
    def test_runner_does_not_share_desktop_parent_pipe(self):
        # Match Electron's open pipe plus app.main.watch_parent, rather than a
        # terminal with EOF on stdin (which conceals this Windows deadlock).
        script = """
import json, os, sys, threading
from app.core.ffmpeg import run_process
entered = threading.Event()
def watch_parent():
    entered.set()
    os.read(sys.stdin.fileno(), 1)
threading.Thread(target=watch_parent, daemon=True).start()
entered.wait()
ok, error = run_process(
    [sys.executable, '-c', 'import sys; assert sys.stdin.buffer.read(1) == b""'],
    timeout=2,
)
print(json.dumps([ok, error]), flush=True)
"""
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen(
                [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'APPDATA': directory},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                # Do not communicate yet: that would close the desktop pipe.
                process.wait(timeout=8)
                stdout, stderr = process.stdout.read(), process.stderr.read()
                self.assertEqual(process.returncode, 0, stderr.decode('utf-8', 'replace'))
                self.assertEqual(json.loads(stdout), [True, None])
                self.assertFalse((Path(directory) / 'VideoMatrix' / 'logs').exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()

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
