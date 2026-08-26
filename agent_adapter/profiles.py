from __future__ import annotations

from copy import deepcopy
from typing import Any


BASE_CONFIG: dict[str, Any] = {
    "task_name": "AgentTask",
    "hook_dir": "",
    "body_dirs": [],
    "bgm_dir": "",
    "voice_dir": "",
    "srt_dir": "",
    "watermark_path": "",
    "base_out_dir": "",
    "t_hook": 3.0,
    "t_body": 3.0,
    "total_clips": 5,
    "target_count": 10,
    "hook_r": 0.0,
    "body_r": 0.2,
    "bgm_r": 0.1,
    "resolution": "1440*2560",
    "fps": "24",
    "bitrate": "8000k",
    "vol_orig": 80,
    "vol_hook_orig": 80,
    "vol_bgm": 30,
    "vol_voice": 100,
    "apply_bgm_to_hook": True,
    "apply_voice_to_hook": True,
    "apply_srt_to_hook": True,
    "apply_watermark_to_hook": True,
    "enable_srt": False,
    "enable_gpu": True,
    "concurrent_tasks": 3,
    "enable_variants": False,
    "variant_strength": "balanced",
    "variant_hook": True,
    "variant_body": True,
    "variant_mirror": False,
    "variant_frame_mix": True,
    "variant_seed": None,
}

PRESETS: dict[str, dict[str, Any]] = {
    "standard": {
        "description": "普通混剪，效果作用于整条视频。",
    },
    "finished-hook": {
        "description": "Hook 已是成品，BGM、配音、字幕、水印仅作用于 Body。",
        "hook_r": 0.0,
        "body_r": 0.2,
        "bgm_r": 0.1,
        "vol_orig": 0,
        "vol_hook_orig": 100,
        "vol_bgm": 100,
        "apply_bgm_to_hook": False,
        "apply_voice_to_hook": False,
        "apply_srt_to_hook": False,
        "apply_watermark_to_hook": False,
    },
}

PROFILE_ALIASES = {
    "标准": "standard",
    "普通": "standard",
    "普通混剪": "standard",
    "成品hook": "finished-hook",
    "成品 hook": "finished-hook",
    "finished_hook": "finished-hook",
}


def normalize_profile(profile: str) -> str:
    key = profile.strip().lower()
    key = PROFILE_ALIASES.get(key, key)
    if key not in PRESETS:
        choices = ", ".join(PRESETS)
        raise ValueError(f"未知预设 {profile!r}，可选：{choices}")
    return key


def available_profiles() -> list[dict[str, str]]:
    return [
        {"id": name, "description": str(values["description"])}
        for name, values in PRESETS.items()
    ]


def build_config(
    *,
    profile: str,
    hook_dir: str,
    body_dir: str = "",
    bgm_path: str = "",
    output_dir: str = "",
    count: int = 10,
    hook_seconds: float = 3.0,
    body_seconds: float = 3.0,
    clips: int = 5,
    concurrency: int = 3,
    resolution: str = "1440*2560",
    fps: str = "24",
    bitrate: str = "8000k",
    voice_dir: str = "",
    subtitle_dir: str = "",
    watermark_path: str = "",
    gpu: bool = True,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_id = normalize_profile(profile)
    config = deepcopy(BASE_CONFIG)
    config.update({k: v for k, v in PRESETS[profile_id].items() if k != "description"})

    resolved_body = body_dir.strip() or hook_dir.strip()
    resolved_bgm = bgm_path.strip() or resolved_body
    config.update(
        {
            "task_name": f"Agent-{profile_id}",
            "hook_dir": hook_dir.strip(),
            "body_dirs": [resolved_body],
            "bgm_dir": resolved_bgm,
            "voice_dir": voice_dir.strip(),
            "srt_dir": subtitle_dir.strip(),
            "watermark_path": watermark_path.strip(),
            "base_out_dir": output_dir.strip(),
            "t_hook": hook_seconds,
            "t_body": body_seconds,
            "total_clips": clips,
            "target_count": count,
            "resolution": resolution,
            "fps": str(fps),
            "bitrate": bitrate,
            "enable_srt": bool(subtitle_dir.strip()),
            "enable_gpu": gpu,
            "concurrent_tasks": concurrency,
        }
    )
    if overrides:
        config.update({key: value for key, value in overrides.items() if value is not None})
    return config
