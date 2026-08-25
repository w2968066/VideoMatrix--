from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .profiles import PRESETS, normalize_profile


AGENT_DEFAULTS: dict[str, Any] = {
    "hook_r": 0.0,
    "body_r": 0.2,
    "bgm_r": 0.1,
    "resolution": "1440*2560",
    "fps": "24",
    "bitrate": "8000k",
}


def _positive(value: float | None, name: str) -> float | None:
    if value is not None and value <= 0:
        raise ValueError(f"{name}必须大于 0。")
    return value


def derive_timing(
    *,
    total_seconds: float | None = None,
    hook_seconds: float | None = None,
    body_seconds: float | None = None,
    clips: int | None = None,
    preferred_body_seconds: float = 2.0,
) -> dict[str, float | int]:
    """Return only timing values that can be determined without reading GUI state."""
    total_seconds = _positive(total_seconds, "总时长")
    hook_seconds = _positive(hook_seconds, "首段时长")
    body_seconds = _positive(body_seconds, "后段时长")
    preferred_body_seconds = _positive(preferred_body_seconds, "参考后段时长") or 2.0
    if clips is not None and clips < 2:
        raise ValueError("总片段数至少为 2，并且包含首段。")

    if total_seconds is None:
        result: dict[str, float | int] = {}
        if hook_seconds is not None:
            result["t_hook"] = hook_seconds
        if body_seconds is not None:
            result["t_body"] = body_seconds
        if clips is not None:
            result["total_clips"] = clips
        return result

    if hook_seconds is None:
        raise ValueError("指定总时长时还需要明确首段时长。")
    if total_seconds <= hook_seconds:
        raise ValueError("总时长必须大于首段时长。")

    body_total = total_seconds - hook_seconds
    if clips is not None:
        body_count = clips - 1
    else:
        target = body_seconds or preferred_body_seconds
        body_count = max(1, math.ceil(body_total / target))

    exact_body_seconds = round(body_total / body_count, 3)
    return {
        "t_hook": hook_seconds,
        "t_body": exact_body_seconds,
        "total_clips": body_count + 1,
    }


def build_prepare_update(
    *,
    hook_dir: str = "",
    body_dir: str = "",
    bgm_path: str = "",
    voice_dir: str = "",
    subtitle_dir: str = "",
    watermark_path: str = "",
    output_dir: str = "",
    profile: str = "",
    use_defaults: bool = False,
    total_seconds: float | None = None,
    hook_seconds: float | None = None,
    body_seconds: float | None = None,
    clips: int | None = None,
    count: int | None = None,
    concurrency: int | None = None,
    resolution: str = "",
    fps: str = "",
    bitrate: str = "",
    hook_overlap: float | None = None,
    body_overlap: float | None = None,
    bgm_overlap: float | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = dict(AGENT_DEFAULTS) if use_defaults else {}

    path_values: dict[str, Any] = {
        "hook_dir": hook_dir.strip(),
        "body_dirs": [body_dir.strip()] if body_dir.strip() else None,
        "bgm_dir": bgm_path.strip(),
        "voice_dir": voice_dir.strip(),
        "srt_dir": subtitle_dir.strip(),
        "watermark_path": watermark_path.strip(),
        "base_out_dir": output_dir.strip(),
    }
    update.update({key: value for key, value in path_values.items() if value})

    if profile.strip():
        profile_id = normalize_profile(profile)
        update.update({key: value for key, value in PRESETS[profile_id].items() if key != "description"})

    update.update(
        derive_timing(
            total_seconds=total_seconds,
            hook_seconds=hook_seconds,
            body_seconds=body_seconds,
            clips=clips,
        )
    )

    explicit_values = {
        "target_count": count,
        "concurrent_tasks": concurrency,
        "resolution": resolution.strip() or None,
        "fps": str(fps).strip() or None,
        "bitrate": bitrate.strip() or None,
        "hook_r": hook_overlap,
        "body_r": body_overlap,
        "bgm_r": bgm_overlap,
    }
    update.update({key: value for key, value in explicit_values.items() if value is not None})

    if count is not None and count < 1:
        raise ValueError("生成数量至少为 1。")
    if concurrency is not None and not 1 <= concurrency <= 16:
        raise ValueError("并发数必须在 1 到 16 之间。")
    for key in ("hook_r", "body_r", "bgm_r"):
        if key in update and not 0 <= float(update[key]) <= 1:
            raise ValueError("重叠率必须在 0 到 1 之间。")
    if not update:
        raise ValueError("没有可写入 VideoMatrix 的路径或参数。")
    return update


def summarize_prepare_update(update: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"fields": sorted(update)}
    if {"t_hook", "t_body", "total_clips"}.issubset(update):
        total = float(update["t_hook"]) + float(update["t_body"]) * (int(update["total_clips"]) - 1)
        summary["timing"] = {
            "total_seconds": round(total, 3),
            "hook_seconds": update["t_hook"],
            "body_seconds": update["t_body"],
            "clips": update["total_clips"],
        }
    return summary


class VideoMatrixLauncher:
    def __init__(self, app_exe: str | None = None):
        self.app_exe = app_exe or os.environ.get("VIDEOMATRIX_APP_EXE")

    def prepare(self, update: dict[str, Any]) -> dict[str, Any]:
        executable = self._resolve_app_executable()
        ack_handle, ack_path = tempfile.mkstemp(prefix="videomatrix-prepare-ack-", suffix=".json")
        os.close(ack_handle)
        Path(ack_path).unlink(missing_ok=True)
        payload = {"version": 1, "config": update, "ack_path": ack_path}
        handle, payload_path = tempfile.mkstemp(prefix="videomatrix-prepare-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            subprocess.Popen(
                [str(executable), f"--videomatrix-prepare={payload_path}"],
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            Path(payload_path).unlink(missing_ok=True)
            Path(ack_path).unlink(missing_ok=True)
            raise

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not Path(ack_path).is_file():
            time.sleep(0.1)
        if not Path(ack_path).is_file():
            raise RuntimeError("VideoMatrix 已启动，但界面未确认接收设置。请确认运行的是新版程序。")
        Path(ack_path).unlink(missing_ok=True)

        result = {
            "ok": True,
            "action": "prepared",
            "app": str(executable),
            "render_started": False,
            "message": "已打开 VideoMatrix 并填写设置；请检查后手动预检或开始渲染。",
        }
        result.update(summarize_prepare_update(update))
        return result

    def _resolve_app_executable(self) -> Path:
        project_root = Path(__file__).resolve().parents[1]
        local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates = [
            Path(self.app_exe) if self.app_exe else None,
            local_app_data / "Programs" / "VideoMatrix" / "VideoMatrix.exe",
            project_root / "frontend" / "release" / "win-unpacked" / "VideoMatrix.exe",
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        raise RuntimeError("找不到 VideoMatrix 桌面程序。请安装软件或设置 VIDEOMATRIX_APP_EXE。")
