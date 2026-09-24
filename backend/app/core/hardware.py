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
from pathlib import Path

from .ffmpeg import FFMPEG, run_process


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
        "nvenc", "nvencode", "libcuda", "cuda_error", "nvcuda", "no capable devices",
        "cannot load", "driver does not support", "minimum required nvidia driver",
        "mfx", "qsv", "amf", "videotoolbox", "hardware accelerator",
        "no device available", "device creation failed", "unsupported device",
    )):
        return "hardware"
    return "other"


def short_error(error: str | None) -> str:
    lines = [line.strip() for line in (error or "未知错误").splitlines() if line.strip()]
    useful = [line for line in lines if any(word in line.lower() for word in (
        "error", "fail", "support", "driver", "cannot", "memory", "device", "timeout",
    ))]
    return " / ".join((useful or lines)[-2:])[-400:]


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
    payload = (3, stamp, platform.platform(), devices, config.get("resolution"),
               str(config.get("fps")), config.get("bitrate"))
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def probe_encoders(config: dict, state_dir: str | None = None, cancelled=None) -> dict:
    """Persist short probes for 24h; invalidate on driver/OS/binary/settings change."""
    with _probe_lock:
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
        for name in candidates:
            if cancelled and cancelled():
                return {"available": [], "failures": {}, "cancelled": True}
            if not re.search(r"\b" + name + r"\b", listing):
                failures[name] = "打包的 FFmpeg 未包含此编码器"
                continue
            command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                       f"testsrc2=size={int(width)}x{int(height)}:rate={fps}", "-frames:v", "12",
                       "-b:v", str(config.get("bitrate", "5000k")), *encoder_options(name), "-f", "null", "-"]
            started = time.monotonic()
            ok, error = run_process(command, cancelled, timeout=15)
            if ok:
                available.append({"name": name, "seconds": time.monotonic() - started})
            else:
                failures[name] = short_error(error)
        if cancelled and cancelled():
            return {"available": [], "failures": {}, "cancelled": True}
        # A short encode ranks usable devices; production throughput tunes concurrency.
        available.sort(key=lambda item: item["seconds"])
        result = {"available": [item["name"] for item in available], "failures": failures,
                  "checked_at": time.time(), "ffmpeg": _capture([FFMPEG, "-version"]).splitlines()[0]}
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


class HardwareSession:
    """One session shared by every SKU and postprocessor in a task."""
    def __init__(self, config: dict, log=None, update=None, state_dir=None, cancelled=None):
        self.log = log or (lambda message: None)
        self.update = update or (lambda values: None)
        self.enabled = config.get("enable_gpu", True)
        self.limit = max(1, int(config.get("concurrent_tasks", 3)))
        self.threads = max(1, min(4, (os.cpu_count() or 2) // self.limit))
        self.condition = threading.Condition()
        self.active = 0
        self.resource_limited = False
        self.blocked = set()
        self.announced = set()
        self.warning = ""
        self.result = probe_encoders(config, state_dir, cancelled) if self.enabled else {"available": [], "failures": {}}
        self.candidates = list(self.result["available"]) + ["libx264"]
        if self.enabled and not self.result["available"] and not self.result.get("cancelled"):
            details = "；".join(f"{ENCODERS[k][0]}: {v}" for k, v in self.result["failures"].items())
            self.warning = "硬件编码不可用，已改用 CPU（速度可能降低）"
            self.log(f">>> [加速降级] {self.warning}。{details}")
        self.update({"acceleration": "检测完成，等待编码" if self.enabled else "CPU 编码（手动关闭 GPU）",
                     "acceleration_warning": self.warning, "effective_concurrency": self.limit})

    def set_limit(self, value: int):
        with self.condition:
            self.limit = max(1, int(value))
            self.condition.notify_all()
        self.update({"effective_concurrency": self.limit})

    def run(self, base: list[str], tail: list[str], stage: str, cancelled=None, on_process=None, runner=None):
        runner = runner or (lambda command: run_process(command, cancelled, on_process))
        for encoder in self.candidates:
            if encoder in self.blocked:
                continue
            for attempt in range(2):
                with self.condition:
                    while self.active >= self.limit:
                        if cancelled and cancelled():
                            return False, "已停止"
                        self.condition.wait(0.2)
                    if cancelled and cancelled():
                        return False, "已停止"
                    if encoder in self.blocked:
                        break
                    self.active += 1
                try:
                    label = f"{ENCODERS[encoder][0]} {'硬件' if encoder != 'libx264' else ''}编码 · CPU 滤镜"
                    with self.condition:
                        announce = (stage, encoder) not in self.announced
                        self.announced.add((stage, encoder))
                    self.update({"acceleration": f"{stage}：{label}", "acceleration_warning": self.warning})
                    if announce:
                        self.log(f">>> [加速] {stage}：{label} ({encoder})")
                    # Bound software filter threads so parallel jobs do not starve the encoder.
                    command = [base[0], "-nostdin", "-filter_complex_threads", str(self.threads),
                               *base[1:], *encoder_options(encoder, self.threads), *tail]
                    ok, error = runner(command)
                finally:
                    with self.condition:
                        self.active -= 1
                        self.condition.notify_all()
                if ok:
                    return True, None
                if cancelled and cancelled():
                    return False, "已停止"
                kind = error_kind(error)
                if kind == "resource" and attempt == 0:
                    self.resource_limited = True
                    self.set_limit(1)
                    self.log(f">>> [加速] {stage}资源不足，降为 1 路重试：{short_error(error)}")
                    continue
                if encoder == "libx264" or kind == "other":
                    return False, error  # Invalid input/filter/path is not a GPU failure.
                with self.condition:
                    self.blocked.add(encoder)
                self.warning = f"{ENCODERS[encoder][0]} 编码失败，切换备用编码：{short_error(error)}"
                self.log(f">>> [加速降级] {self.warning}")
                self.update({"acceleration_warning": self.warning})
                break
        return False, "没有可用编码器"


def session_for(config: dict, log=None) -> HardwareSession:
    session = config.get("_hardware_session")
    if session is None:
        session = HardwareSession(config, log=log)
        config["_hardware_session"] = session
    return session
