import concurrent.futures
import gc
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import hardware


CUDA_FAILURE = (
    "[h264_nvenc] cuCtxCreate_v2 failed: CUDA_ERROR_UNKNOWN: unknown error\n"
    "[h264_nvenc] No capable devices found\n"
    "[vost#0:0/h264_nvenc] Task finished with error code: -22 (Invalid argument)\n"
    "Conversion failed!"
)


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition did not become true")


def encoder_of(command):
    return command[command.index("-c:v") + 1]


class HardwareRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.governor = hardware._GpuAdmission()
        self.patch_governor = patch.object(hardware, "_gpu_admission", self.governor)
        self.patch_governor.start()
        self.addCleanup(self.patch_governor.stop)
        self.patch_probe = patch.object(hardware, "probe_encoders", return_value={
            "available": ["h264_nvenc"], "failures": {},
        })
        self.patch_probe.start()
        self.addCleanup(self.patch_probe.stop)

    def session(self, limit=3, **kwargs):
        session = hardware.HardwareSession({"concurrent_tasks": limit}, **kwargs)
        self.addCleanup(session.close)
        return session

    def run_job(self, session, runner, cancelled=None):
        return session.run(["ffmpeg", "-y"], ["out.mp4"], "test", cancelled=cancelled, runner=runner)

    def test_global_budget_and_each_tasks_limit_are_both_respected(self):
        sessions = [self.session(2), self.session(1)]
        lock = threading.Lock()
        current = [0, 0]
        peak = [0, 0]
        global_peak = 0

        def runner(index, _command):
            nonlocal global_peak
            with lock:
                current[index] += 1
                peak[index] = max(peak[index], current[index])
                global_peak = max(global_peak, sum(current))
            time.sleep(0.025)
            with lock:
                current[index] -= 1
            return True, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(self.run_job, sessions[i % 2], lambda cmd, n=i % 2: runner(n, cmd))
                       for i in range(12)]
            self.assertTrue(all(f.result(timeout=4)[0] for f in futures))
        self.assertEqual(global_peak, 2)
        self.assertLessEqual(peak[0], 2)
        self.assertEqual(peak[1], 1)
        self.assertEqual(self.governor.active, 0)

    def test_failed_cohort_uses_one_drained_recovery_and_blocks_new_arrivals(self):
        logs = []
        session = self.session(3, log=logs.append)
        first_batch = threading.Barrier(3)
        release_last = threading.Event()
        attempts = {name: 0 for name in ("a", "b", "c", "new")}
        first_finished = set()
        retry_active_counts = []
        lock = threading.Lock()

        def runner(name, _command):
            with lock:
                attempts[name] += 1
                attempt = attempts[name]
            if name != "new" and attempt == 1:
                first_batch.wait(timeout=3)
                if name == "c":
                    self.assertTrue(release_last.wait(3))
                with lock:
                    first_finished.add(name)
                return (True, None) if name == "c" else (False, CUDA_FAILURE)
            if attempt == 2:
                self.assertEqual(first_finished, {"a", "b", "c"})
                retry_active_counts.append(self.governor.active)
            return True, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            first = [pool.submit(self.run_job, session, lambda cmd, n=name: runner(n, cmd))
                     for name in ("a", "b", "c")]
            try:
                wait_until(lambda: self.governor.recovery is not None and self.governor.active == 1)
                arriving = pool.submit(self.run_job, session, lambda cmd: runner("new", cmd))
                time.sleep(0.05)
                self.assertEqual(attempts["new"], 0)
                self.assertEqual(attempts["a"], 1)
                self.assertEqual(attempts["b"], 1)
            finally:
                release_last.set()
            self.assertTrue(all(f.result(timeout=3)[0] for f in [*first, arriving]))
        self.assertEqual(attempts, {"a": 2, "b": 2, "c": 1, "new": 1})
        self.assertEqual(retry_active_counts, [1, 1])
        self.assertEqual(sum("等待在途" in line for line in logs), 1)
        self.assertNotIn("h264_nvenc", session.blocked)

    def test_persistent_unknown_error_falls_back_but_can_recover_after_cooldown(self):
        session = self.session()
        calls = []

        def runner(command):
            encoder = encoder_of(command)
            calls.append(encoder)
            return (False, CUDA_FAILURE) if encoder == "h264_nvenc" else (True, None)

        self.assertTrue(self.run_job(session, runner)[0])
        self.assertEqual(calls, ["h264_nvenc", "h264_nvenc", "libx264"])
        self.assertNotIn("h264_nvenc", session.blocked)
        calls.clear()
        self.assertTrue(self.run_job(session, runner)[0])
        self.assertEqual(calls, ["libx264"])
        self.governor.cooldown["h264_nvenc"] = time.monotonic() - 1
        calls.clear()
        self.assertTrue(self.run_job(session, lambda cmd: (calls.append(encoder_of(cmd)) or True, None))[0])
        self.assertEqual(calls, ["h264_nvenc"])

    def test_definite_missing_driver_is_blocked_without_repeated_retry(self):
        session = self.session()
        calls = []

        def runner(command):
            encoder = encoder_of(command)
            calls.append(encoder)
            return (False, "Cannot load nvcuda.dll") if encoder == "h264_nvenc" else (True, None)

        self.assertTrue(self.run_job(session, runner)[0])
        self.assertTrue(self.run_job(session, runner)[0])
        self.assertEqual(calls, ["h264_nvenc", "libx264", "libx264"])
        self.assertIn("h264_nvenc", session.blocked)

    def test_nvenc_prefix_on_input_error_does_not_trigger_gpu_fallback(self):
        session = self.session()
        error = "[h264_nvenc @ 0000] Task finished with error code: -22 (Invalid argument)\nInvalid filter argument"
        calls = []
        self.assertEqual(self.run_job(session, lambda cmd: (calls.append(encoder_of(cmd)) or False, error)), (False, error))
        self.assertEqual(calls, ["h264_nvenc"])
        self.assertEqual(session.blocked, set())

    def test_cancelled_recovery_owner_releases_barrier_without_disabling_gpu(self):
        session = self.session(2)
        slow_started, release_slow, cancel = threading.Event(), threading.Event(), threading.Event()

        def slow(_command):
            slow_started.set()
            self.assertTrue(release_slow.wait(3))
            return True, None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            slow_future = pool.submit(self.run_job, session, slow)
            self.assertTrue(slow_started.wait(2))
            failed = pool.submit(self.run_job, session, lambda _cmd: (False, CUDA_FAILURE), cancel.is_set)
            try:
                wait_until(lambda: self.governor.recovery is not None)
                cancel.set()
                self.assertEqual(failed.result(timeout=2), (False, "已停止"))
                self.assertIsNone(self.governor.recovery)
                self.assertEqual(self.governor.cooldown, {})
            finally:
                release_slow.set()
            self.assertTrue(slow_future.result(timeout=2)[0])
        self.assertTrue(self.run_job(session, lambda _cmd: (True, None))[0])

    def test_exception_and_waiting_cancellation_release_permits(self):
        session = self.session(1)
        with self.assertRaisesRegex(RuntimeError, "runner"):
            self.run_job(session, lambda _cmd: (_ for _ in ()).throw(RuntimeError("runner")))
        self.assertEqual((self.governor.active, session.active), (0, 0))
        permit = self.governor.acquire(session, "h264_nvenc", None)
        cancel = threading.Event()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(self.run_job, session, lambda _cmd: self.fail("cancelled runner started"), cancel.is_set)
            cancel.set()
            self.assertEqual(waiting.result(timeout=2), (False, "已停止"))
        self.governor.finish(permit)

    def test_closed_and_collected_sessions_do_not_keep_budget_registrations(self):
        session = self.session(4)
        session.close()
        self.assertEqual(len(self.governor.sessions), 0)
        standalone = hardware.HardwareSession({"concurrent_tasks": 8})
        del standalone
        gc.collect()
        self.assertEqual(len(self.governor.sessions), 0)

    def test_probe_waits_for_production_and_prevents_new_admission(self):
        session = self.session(1)
        permit = self.governor.acquire(session, "h264_nvenc", None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            probe = pool.submit(self.governor.begin_probe, None)
            wait_until(lambda: self.governor.probe_waiters == 1)
            arriving = pool.submit(self.governor.acquire, session, "h264_nvenc", None)
            self.governor.finish(permit)
            self.assertTrue(probe.result(timeout=2))
            self.assertFalse(arriving.done())
            self.governor.end_probe()
            admitted = arriving.result(timeout=2)
            self.governor.finish(admitted)


class ProbeAndErrorTests(unittest.TestCase):
    def test_transient_probe_failure_is_not_cached_as_a_day_long_negative(self):
        hardware._memory_cache.clear()
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(hardware.sys, "platform", "win32"), \
                patch.object(hardware, "_gpu_admission", hardware._GpuAdmission()), \
                patch.object(hardware, "_signature", return_value="probe-test"), \
                patch.object(hardware, "_capture", return_value="h264_nvenc ffmpeg"), \
                patch.object(hardware, "run_process", side_effect=[(False, CUDA_FAILURE), (True, None)]) as run:
            first = hardware.probe_encoders({}, directory)
            second = hardware.probe_encoders({}, directory)
            self.assertEqual(first["available"], [])
            self.assertEqual(second["available"], ["h264_nvenc"])
            self.assertEqual(run.call_count, 2)
            third = hardware.probe_encoders({}, directory)
            self.assertEqual(third, second)
            self.assertEqual(run.call_count, 2)
            cache = json.loads((Path(directory) / "hardware-capabilities.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["probe-test"]["available"], ["h264_nvenc"])
        hardware._memory_cache.clear()

    def test_short_error_keeps_root_cause_and_full_log_location(self):
        message = CUDA_FAILURE + "\n[h264_nvenc] Task finished with error code: -22 (Invalid argument)\n完整日志：C:/logs/failure.log"
        short = hardware.short_error(message)
        self.assertIn("cuCtxCreate_v2", short)
        self.assertIn("CUDA_ERROR_UNKNOWN", short)
        self.assertIn("完整日志：C:/logs/failure.log", short)
        self.assertNotIn("Task finished", short)
        self.assertEqual(hardware.error_kind(message), "transient")
        self.assertEqual(hardware.error_kind("CUDA_ERROR_INSUFFICIENT_DRIVER\nNo capable devices found"), "hardware")
        self.assertEqual(hardware.error_kind("CUDA_ERROR_NO_DEVICE\nNo capable devices found"), "hardware")


if __name__ == "__main__":
    unittest.main()
