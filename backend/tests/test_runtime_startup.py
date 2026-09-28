import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen


class RuntimeStartupTests(unittest.TestCase):
    def test_dynamic_port_identity_and_parent_exit(self):
        occupied = socket.socket()
        try:
            occupied.bind(('127.0.0.1', 8765))
            occupied.listen()
        except OSError:
            pass  # An existing app already occupies the old fixed port.
        with tempfile.TemporaryDirectory() as state_dir:
            binary = os.environ.get('VIDEOMATRIX_TEST_BINARY')
            command = [binary] if binary else [sys.executable, 'run_backend.py']
            process = subprocess.Popen(
                [*command, '--host', '127.0.0.1', '--port', '0'],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'APPDATA': state_dir, 'VIDEOMATRIX_INSTANCE': 'startup-test', 'PYTHONIOENCODING': 'utf-8'},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8',
            )
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
            try:
                line = lines.get(timeout=15)
                self.assertTrue(line.startswith('VIDEOMATRIX_READY '), line)
                ready = json.loads(line.split(' ', 1)[1])
                self.assertNotEqual(ready['port'], 8765)
                deadline = time.monotonic() + 10
                while True:
                    try:
                        with urlopen(f"http://127.0.0.1:{ready['port']}/api/health", timeout=1) as response:
                            health = json.load(response)
                        break
                    except OSError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.1)
                expected_version = json.loads((Path(__file__).resolve().parents[2] / 'frontend' / 'package.json').read_text(encoding='utf-8'))['version']
                self.assertEqual(health['version'], expected_version)
                self.assertEqual(health['instance'], 'startup-test')
                process.stdin.close()
                self.assertEqual(process.wait(timeout=10), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()
                occupied.close()
