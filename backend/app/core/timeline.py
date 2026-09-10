"""Shared timeline calculations for normal and grouped Body modes."""
from __future__ import annotations

from typing import Any


def body_segment_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ordered Body segments which make up one output."""
    if config.get("body_mode", "normal") == "grouped":
        return [
            {
                "folder": str(group.get("folder") or ""),
                "clip_count": int(group.get("clip_count", 0)),
                "clip_duration": float(group.get("clip_duration", 0)),
                "group_index": index + 1,
            }
            for index, group in enumerate(config.get("body_groups") or [])
            if group.get("enabled", False)
        ]
    return [{
        "folder": None,
        "clip_count": max(0, int(config.get("total_clips", 1)) - 1),
        "clip_duration": float(config.get("t_body", 0)),
        "group_index": 0,
    }]


def apply_timeline_totals(config: dict[str, Any]) -> dict[str, Any]:
    """Normalize derived fields in-place and return config for convenient chaining."""
    specs = body_segment_specs(config)
    if config.get("body_mode", "normal") == "grouped":
        config["total_clips"] = 1 + sum(spec["clip_count"] for spec in specs)
    config["body_duration"] = sum(spec["clip_count"] * spec["clip_duration"] for spec in specs)
    config["total_duration"] = float(config.get("t_hook", 0)) + config["body_duration"]
    return config
