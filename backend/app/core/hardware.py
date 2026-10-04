"""Verified, task-wide hardware encoding with bounded retries and visible fallback.

Capability listings are not proof of usable hardware. Probe actual frames using
the bundled executable and requested output settings before choosing a backend.
Software filters remain software; this module never labels them GPU-accelerated.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFMPEG, run_process
from .output_settings import output_probe_config


ENCODERS = {
    "h264_nvenc": ("NVIDIA", ["-preset", "p4", "-gpu", "0"]),
    "h264_qsv": ("Intel", ["-preset", "medium"]),
    "h264_amf": ("AMD", ["-quality", "balanced"]),
    # Keep the probe strict by requiring the platform encoder to open; do not
    # pass version-specific VideoToolbox flags that older FFmpeg builds reject.
    "h264_videotoolbox": ("Mac", []),
    "libx264": ("CPU", ["-preset", "fast"]),
}
_probe_lock = threading.Lock()
_session_lock = threading.Lock()
_memory_cache: dict = {}


def encoder_options(name: str, threads: int = 2) -> list[str]:
    return ["-c:v", name, *ENCODERS[name][1], "-pix_fmt", "yuv420p", "-threads", str(threads)]


def error_kind(error: str | None) -> str:
    value = (error or "").lower()
    if any(word in value for word in (
        "out of memory", "cannot allocate memory", "resource temporarily unavailable",
        "too many concurrent", "all encode sessions", "encoder busy", "device busy",
        "mfx_err_memory_alloc", "amf_out_of_memory", "no free surfaces",
    )):
        return "resource"
    if any(word in value for word in (
        "cannot load libcuda", "cannot load nvcuda", "cannot load libnvidia-encode",
        "cannot load nvencodeapi", "driver does not support", "minimum required nvidia driver",
        "cuda_error_no_device", "cuda_error_insufficient_driver", "unknown encoder",
        "not compiled with",
    )):
        return "hardware"
    # An encoder name in an FFmpeg log prefix is not itself a hardware fault.
    if any(word in value for word in (
        "cuda_error", "cuctxcreate", "cuctxcreate_v2", "device creation failed",
        "initializeencoder", "openencodesession", "failed to initialise nvenc",
        "failed to initialize nvenc", "mfx_err_device", "mfx_err_unknown",
        "amf_fail", "videotoolbox session", "ffmpeg timeout",
    )):
        return "transient"
    # NVENC also prints "No capable devices found" after a temporary CUDA
    # context failure. That secondary message must not override its root cause.
    if any(word in value for word in (
        "no capable devices", "no nvenc capable devices", "unsupported device", "no device available",
    )):
        return "hardware"
    return "other"


def short_error(error: str | None) -> str:
    lines = [line.strip() for line in (error or "未知错误").splitlines() if line.strip()]
    useful = [line for line in lines if any(word in line.lower() for word in (
        "error", "fail", "support", "driver", "cannot", "memory", "device", "timeout",
    ))]
    generic = ("task finished with error code", "conversion failed", "error opening output",
               "error while opening encoder", "nothing was written into output")
    specific = [line for line in useful if not any(word in line.lower() for word in generic)]
    roots = [line for line in specific if any(word in line.lower() for word in (
        "cuda_error", "cuctxcreate", "out of memory", "cannot load", "minimum required",
        "resource temporarily unavailable", "failed to configure", "error initializing",
    ))]
    chosen = roots + [line for line in specific if line not in roots]
    result = " / ".join((chosen or useful or lines)[:2])[:500]
    detail = next((line for line in lines if line.startswith("完整日志：")), None)
    return result + ("\n" + detail if detail and detail not in result else "")


@dataclass
class _Permit:
    session: object
    encoder: str


@dataclass
class _Recovery:
    owner: _Permit
    done: bool = False
    retry_allowed: bool = False


class _GpuAdmission:
    """Process-wide budget and one recovery barrier, including capability probes.

    Registrations are weak: standalone processors need not manage task lifetime.
    A task may lower its own limit, but parallel tasks never add their budgets.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.sessions = weakref.WeakKeyDictionary()
        self.active = 0
        self.learned_limit = None
        self.recovery = None
        self.cooldown = {}
        self.probing = False
        self.probe_waiters = 0

    def register(self, session):
        with self.condition:
            if not self.sessions and not self.active:
                self.learned_limit = None
            if session.enabled:
                self.sessions[session] = session.limit

    def unregister(self, session):
        with self.condition:
            self.sessions.pop(session, None)
            self.condition.notify_all()

    def budget(self):
        with self.condition:
            requested = max(self.sessions.values(), default=1)
            return min(requested, self.learned_limit or requested)

    def effective_limit(self, session):
        with self.condition:
            return min(session.limit, self.budget()) if session.enabled else session.limit

    def acquire(self, session, encoder, cancelled):
        gpu = encoder != "libx264"
        with self.condition:
            while True:
                if session.closed or (cancelled and cancelled()):
                    return None
                if encoder in session.blocked or (gpu and time.monotonic() < self.cooldown.get(encoder, 0)):
                    return None
                if session.active < session.limit and (not gpu or (
                    not self.probing and not self.probe_waiters and self.recovery is None
                    and self.active < self.budget()
                )):
                    session.active += 1
                    if gpu:
                        self.active += 1
                    return _Permit(session, encoder)
                self.condition.wait(0.1)

    def finish(self, permit, kind="other", recover=False, cancelled=False):
        with self.condition:
            permit.session.active -= 1
            recovery = None
            if permit.encoder != "libx264":
                self.active -= 1
                if not cancelled and kind == "hardware":
                    permit.session.blocked.add(permit.encoder)
                elif not cancelled and kind in ("resource", "transient"):
                    if recover:
                        if self.recovery is None:
                            self.recovery = _Recovery(permit)
                        recovery = self.recovery
                    else:
                        # A failed isolated/second attempt is not proof of a
                        # permanently unavailable device. Avoid a retry storm.
                        self.cooldown[permit.encoder] = time.monotonic() + 30
            # Install the barrier before freeing slots/awakening other workers.
            self.condition.notify_all()
            return recovery

    def start_recovery(self, recovery, cancelled):
        with self.condition:
            session = recovery.owner.session
            while self.active or self.probing or session.active >= session.limit:
                if session.closed or (cancelled and cancelled()):
                    return False
                self.condition.wait(0.1)
            if session.closed or (cancelled and cancelled()):
                return False
            session.active += 1
            self.active += 1
            return True

    def end_recovery(self, recovery, started, ok, kind, cancelled=False):
        with self.condition:
            permit = recovery.owner
            if started:
                permit.session.active -= 1
                self.active -= 1
            if not cancelled:
                self.learned_limit = max(1, self.budget() // 2)
                if kind == "hardware" and not ok:
                    permit.session.blocked.add(permit.encoder)
                elif kind in ("resource", "transient") and not ok:
                    self.cooldown[permit.encoder] = time.monotonic() + 30
            recovery.retry_allowed = ok or cancelled or kind == "other"
            recovery.done = True
            self.recovery = None
            self.condition.notify_all()

    def wait_recovery(self, recovery, cancelled):
        with self.condition:
            while not recovery.done:
                if cancelled and cancelled():
                    return False
                self.condition.wait(0.1)
            return recovery.retry_allowed

    def begin_probe(self, cancelled):
        with self.condition:
            self.probe_waiters += 1
            try:
                while self.active or self.probing or self.recovery is not None:
                    if cancelled and cancelled():
                        return False
                    self.condition.wait(0.1)
                if cancelled and cancelled():
                    return False
                self.probing = True
                return True
            finally:
                self.probe_waiters -= 1
                self.condition.notify_all()

    def end_probe(self):
        with self.condition:
            self.probing = False
            self.condition.notify_all()


_gpu_admission = _GpuAdmission()


def _capture(command: list[str], timeout=8) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def _signature(config: dict, executable: str) -> str:
    binary = Path(shutil.which(executable) or executable)
    try:
        stamp = (str(binary.resolve()), binary.stat().st_size, binary.stat().st_mtime_ns)
    except OSError:
        stamp = (str(binary),)
    if sys.platform == "win32":
        devices = _capture(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress"], 5)
    elif sys.platform == "darwin":
        devices = _capture(["system_profiler", "SPDisplaysDataType", "-json"], 5)
    else:
        devices = platform.platform()
    payload = (5, stamp, platform.platform(), devices, config.get("resolution"),
               str(config.get("fps")), config.get("bitrate"))
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def probe_encoders(config: dict, state_dir: str | None = None, cancelled=None) -> dict:
    """Persist short probes for 24h; invalidate on driver/OS/binary/settings change."""
    # Check the full random-output budget, including when a caller already has
    # sampled this output. Use the same copied settings for cache and encode.
    config = output_probe_config(config)
    while not _probe_lock.acquire(timeout=0.1):
        if cancelled and cancelled():
            return {"available": [], "failures": {}, "cancelled": True}
    try:
        key = _signature(config, FFMPEG)
        root = Path(state_dir or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "VideoMatrix"))
        cache_path = root / "hardware-capabilities.json"
        try:
            disk = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            disk = {}
        cached = _memory_cache.get(key) or disk.get(key)
        if cached and time.time() - cached.get("checked_at", 0) < 86400:
            return cached
        listing = _capture([FFMPEG, "-hide_banner", "-encoders"])
        candidates = ["h264_videotoolbox"] if sys.platform == "darwin" else ["h264_nvenc", "h264_qsv", "h264_amf"]
        width, height = str(config.get("resolution", "1080*1920")).lower().replace("*", "x").split("x")
        fps = str(config.get("fps", "30"))
        available, failures = [], {}
        cacheable = listing != "unavailable"
        for name in candidates:
            if cancelled and cancelled():
                return {"available": [], "failures": {}, "cancelled": True}
            if not re.search(r"\b" + name + r"\b", listing):
                failures[name] = "打包的 FFmpeg 未包含此编码器"
                continue
            command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                       f"testsrc2=size={int(width)}x{int(height)}:rate={fps}", "-frames:v", "12",
                       "-b:v", str(config.get("bitrate", "5000k")), *encoder_options(name), "-f", "null", "-"]
            if not _gpu_admission.begin_probe(cancelled):
                return {"available": [], "failures": {}, "cancelled": True}
            try:
                started = time.monotonic()
                ok, error = run_process(command, cancelled, timeout=15)
            finally:
                _gpu_admission.end_probe()
            if ok:
                available.append({"name": name, "seconds": time.monotonic() - started})
            else:
                failures[name] = short_error(error)
                if error_kind(error) != "hardware":
                    cacheable = False
        if cancelled and cancelled():
            return {"available": [], "failures": {}, "cancelled": True}
        # A short encode ranks usable devices; production throughput tunes concurrency.
        available.sort(key=lambda item: item["seconds"])
        result = {"available": [item["name"] for item in available], "failures": failures,
                  "checked_at": time.time(), "ffmpeg": _capture([FFMPEG, "-version"]).splitlines()[0]}
        if not cacheable:
            # Busy/unknown/input failures describe this moment, not capability.
            # Never turn them into a 24-hour negative capability result.
            return result
        _memory_cache[key] = result
        try:
            root.mkdir(parents=True, exist_ok=True)
            disk = {k: v for k, v in disk.items() if time.time() - v.get("checked_at", 0) < 86400}
            disk[key] = result
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(disk, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, cache_path)
        except OSError:
            pass  # Read-only profile directories must not prevent production.
        return result
    finally:
        _probe_lock.release()


class HardwareSession:
    """One session shared by every SKU and postprocessor in a task."""
    def __init__(self, config: dict, log=None, update=None, state_dir=None, cancelled=None):
        self.log = log or (lambda message: None)
        self.update = update or (lambda values: None)
        self.enabled = config.get("enable_gpu", True)
        self.limit = max(1, int(config.get("concurrent_tasks", 3)))
        self.threads = max(1, min(4, (os.cpu_count() or 2) // self.limit))
        self.governor = _gpu_admission
        self.condition = self.governor.condition
        self.active = 0
        self.closed = False
        self.resource_limited = False
        self.blocked = set()
        self.announced = set()
        self.warning = ""
        self.result = probe_encoders(config, state_dir, cancelled) if self.enabled else {"available": [], "failures": {}}
        self.candidates = list(self.result["available"]) + ["libx264"]
        if self.result["available"]:
            self.governor.register(self)
        if self.enabled and not self.result["available"] and not self.result.get("cancelled"):
            details = "；".join(f"{ENCODERS[k][0]}: {v}" for k, v in self.result["failures"].items())
            self.warning = "硬件编码不可用，已改用 CPU（速度可能降低）"
            self.log(f">>> [加速降级] {self.warning}。{details}")
        self.update({"acceleration": "检测完成，等待编码" if self.enabled else "CPU 编码（手动关闭 GPU）",
                     "acceleration_warning": self.warning, "effective_concurrency": self.limit})

    def set_limit(self, value: int):
        with self.condition:
            self.limit = max(1, int(value))
            if not self.closed and self.result["available"]:
                self.governor.sessions[self] = self.limit
            self.condition.notify_all()
        self.update({"effective_concurrency": self.governor.effective_limit(self)})

    def close(self):
        with self.condition:
            self.closed = True
            self.governor.unregister(self)

    def _execute(self, base, tail, encoder, stage, runner):
        label = f"{ENCODERS[encoder][0]} {'硬件' if encoder != 'libx264' else ''}编码 · CPU 滤镜"
        with self.condition:
            announce = (stage, encoder) not in self.announced
            self.announced.add((stage, encoder))
        self.update({"acceleration": f"{stage}：{label}", "acceleration_warning": self.warning,
                     "effective_concurrency": self.governor.effective_limit(self)})
        if announce:
            self.log(f">>> [加速] {stage}：{label} ({encoder})")
        command = [base[0], "-nostdin", "-nostats", "-filter_complex_threads", str(self.threads),
                   *base[1:], *encoder_options(encoder, self.threads), *tail]
        return runner(command)

    def run(self, base: list[str], tail: list[str], stage: str, cancelled=None, on_process=None, runner=None):
        runner = runner or (lambda command: run_process(command, cancelled, on_process))
        for encoder in self.candidates:
            if encoder in self.blocked:
                continue
            for attempt in range(2):
                permit = self.governor.acquire(self, encoder, cancelled)
                if permit is None:
                    if self.closed or (cancelled and cancelled()):
                        return False, "已停止"
                    if encoder not in self.blocked:
                        warning = f"{ENCODERS[encoder][0]} 编码暂时冷却，本条视频使用备用编码，稍后允许重试"
                        if warning != self.warning:
                            self.warning = warning
                            self.log(f">>> [加速降级] {warning}")
                            self.update({"acceleration_warning": warning})
                    break
                try:
                    ok, error = self._execute(base, tail, encoder, stage, runner)
                except BaseException:
                    self.governor.finish(permit, cancelled=True)
                    raise
                stopped = self.closed or bool(cancelled and cancelled())
                kind = "other" if ok else error_kind(error)
                recovery = self.governor.finish(permit, kind, recover=attempt == 0, cancelled=stopped)
                if ok:
                    return True, None
                if stopped:
                    return False, "已停止"
                if recovery is not None:
                    self.resource_limited = True
                    if recovery.owner is not permit:
                        if self.governor.wait_recovery(recovery, cancelled):
                            continue
                        if cancelled and cancelled():
                            return False, "已停止"
                        self.warning = f"{ENCODERS[encoder][0]} 同批硬件恢复失败，本条视频使用备用编码：{short_error(error)}"
                        self.log(f">>> [加速降级] {self.warning}")
                        self.update({"acceleration_warning": self.warning})
                        break
                    self.log(f">>> [加速恢复] {stage}暂时无法使用硬件编码，等待在途编码结束后单路重试：{short_error(error)}")
                    started = self.governor.start_recovery(recovery, cancelled)
                    retry_ok, retry_error = False, "已停止"
                    try:
                        if started:
                            retry_ok, retry_error = self._execute(base, tail, encoder, stage, runner)
                    finally:
                        stopped = self.closed or bool(cancelled and cancelled())
                        self.governor.end_recovery(
                            recovery, started, retry_ok,
                            "other" if retry_ok else error_kind(retry_error), cancelled=stopped,
                        )
                    self.update({"effective_concurrency": self.governor.effective_limit(self)})
                    if stopped or not started:
                        return False, "已停止"
                    if retry_ok:
                        self.warning = ""
                        self.log(f">>> [加速恢复] {stage}硬件编码重试成功，当前 GPU 总并发上限 {self.governor.budget()}")
                        self.update({"acceleration_warning": ""})
                        return True, None
                    error, kind = retry_error, error_kind(retry_error)
                if encoder == "libx264" or kind == "other":
                    return False, error  # Invalid input/filter/path is not a GPU failure.
                reason = "不可用" if kind == "hardware" else "暂时繁忙，本条视频切换备用编码"
                self.warning = f"{ENCODERS[encoder][0]} {reason}：{short_error(error)}"
                self.log(f">>> [加速降级] {self.warning}")
                self.update({"acceleration_warning": self.warning})
                break
        return False, "没有可用编码器"


def session_for(config: dict, log=None) -> HardwareSession:
    # Standalone processors/benchmarks can first touch one configuration from
    # multiple workers. Keep its task limit shared, just as TaskService does.
    session = config.get("_hardware_session")
    if session is not None and not session.closed:
        return session
    with _session_lock:
        session = config.get("_hardware_session")
        if session is None or session.closed:
            session = HardwareSession(config, log=log)
            config["_hardware_session"] = session
        return session
