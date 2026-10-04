"""Deterministic, side-effect-free plans for the creative variant layer."""
from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .timeline import body_segment_specs


VERSION = "creative-variant-v2"

_CROP_LIMITS = {"mild": 0.005, "balanced": 0.01, "strong": 0.02}
_COLOR_LIMITS = {
    # Absolute brightness, contrast delta, and saturation delta.
    "mild": (0.006, 0.010, 0.012),
    "balanced": (0.010, 0.018, 0.024),
    "strong": (0.015, 0.025, 0.035),
}


@dataclass(frozen=True)
class SegmentRecipe:
    index: int
    start: float
    duration: float
    enabled: bool
    crop_total: float
    offset_x: float
    offset_y: float
    brightness: float
    contrast: float
    saturation: float
    mirror: bool


@dataclass(frozen=True)
class Recipe:
    version: str
    seed: int
    width: int
    height: int
    segments: tuple[SegmentRecipe, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _ProtectedRegion:
    x: float
    y: float
    width: float
    height: float
    start: float
    end: float | None


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _parse_resolution(config: Mapping[str, Any]) -> tuple[int, int]:
    value = config.get("resolution", "1080*1920")
    if isinstance(value, (tuple, list)) and len(value) == 2:
        parts = value
    else:
        text = str(value).strip().lower().replace("*", "x")
        parts = text.split("x")
    if len(parts) != 2:
        raise ValueError("resolution must be WIDTH*HEIGHT")
    parsed: list[int] = []
    for part in parts:
        number = _number(part, "resolution dimension")
        if not number.is_integer():
            raise ValueError("resolution dimensions must be integers")
        parsed.append(int(number))
    width, height = parsed
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise ValueError("resolution dimensions must be positive even integers")
    return width, height


def _scaled_dimensions(width: int, height: int, crop_total: float) -> tuple[int, int]:
    """Apply a shared zoom ratio, rounding added pixels down to even values."""
    if crop_total <= 0:
        return width, height

    def even_floor(value: float) -> int:
        return max(2, int(value) // 2 * 2)

    scale = 1.0 / (1.0 - crop_total)
    return even_floor(width * scale), even_floor(height * scale)


def _read_protected_regions(config: Mapping[str, Any]) -> tuple[_ProtectedRegion, ...]:
    raw_regions = config.get("variant_protected_regions") or ()
    if not isinstance(raw_regions, (list, tuple)):
        raise ValueError("variant_protected_regions must be a list")

    regions: list[_ProtectedRegion] = []
    for index, raw in enumerate(raw_regions):
        if not isinstance(raw, Mapping):
            raise ValueError(f"protected region {index} must be an object")
        x = _number(raw.get("x"), f"protected region {index} x")
        y = _number(raw.get("y"), f"protected region {index} y")
        region_width = _number(raw.get("width"), f"protected region {index} width")
        region_height = _number(raw.get("height"), f"protected region {index} height")
        start = _number(raw.get("start", 0), f"protected region {index} start")
        end_value = raw.get("end")
        end = None if end_value is None else _number(end_value, f"protected region {index} end")
        if (
            x < 0 or y < 0 or region_width <= 0 or region_height <= 0
            or x + region_width > 1 or y + region_height > 1
            or start < 0 or (end is not None and end <= start)
        ):
            raise ValueError(f"protected region {index} is outside normalized coordinates or has an invalid time range")
        regions.append(_ProtectedRegion(x, y, region_width, region_height, start, end))
    return tuple(regions)


def _overlapping_regions(
    regions: tuple[_ProtectedRegion, ...], start: float, duration: float,
) -> tuple[_ProtectedRegion, ...]:
    segment_end = start + duration
    return tuple(
        region for region in regions
        if region.start < segment_end and (region.end is None or region.end > start)
    )


def _crop_keeps_regions(
    regions: tuple[_ProtectedRegion, ...],
    width: int,
    height: int,
    scaled_width: int,
    scaled_height: int,
    offset_x: float,
    offset_y: float,
) -> bool:
    scale_x = scaled_width / width
    scale_y = scaled_height / height
    minimum_scale = min(scale_x, scale_y)
    maximum_scale = max(scale_x, scale_y)
    pan_x = (scaled_width - width) * (offset_x - 0.5)
    pan_y = (scaled_height - height) * (offset_y - 0.5)
    safe_margin = 2.0  # Leave room for even-pixel/YUV420 crop quantization.

    def transformed_interval(start: float, end: float, dimension: int, pan: float) -> tuple[float, float]:
        center = dimension / 2
        starts = [center + scale * (start * dimension - center) - pan
                  for scale in (minimum_scale, maximum_scale)]
        ends = [center + scale * (end * dimension - center) - pan
                for scale in (minimum_scale, maximum_scale)]
        return min(starts), max(ends)

    for region in regions:
        left, right = transformed_interval(region.x, region.x + region.width, width, pan_x)
        top, bottom = transformed_interval(region.y, region.y + region.height, height, pan_y)
        if (
            left <= safe_margin or right >= width - safe_margin
            or top <= safe_margin or bottom >= height - safe_margin
        ):
            return False
    return True


def _body_durations(config: Mapping[str, Any]) -> list[float]:
    if "_body_clip_durations" in config:
        raw_durations = config["_body_clip_durations"]
        if not isinstance(raw_durations, (list, tuple)):
            raise ValueError("_body_clip_durations must be a list")
        durations = [
            _number(value, f"body duration {index}")
            for index, value in enumerate(raw_durations)
        ]
    else:
        try:
            specs = body_segment_specs(dict(config))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid Body timeline specifications") from exc
        durations = []
        for spec_index, spec in enumerate(specs):
            count_value = spec.get("clip_count", 0)
            count_number = _number(count_value, f"Body group {spec_index} clip_count")
            if not count_number.is_integer() or count_number < 0:
                raise ValueError(f"Body group {spec_index} clip_count must be a nonnegative integer")
            duration = _number(spec.get("clip_duration", 0), f"Body group {spec_index} clip_duration")
            durations.extend([duration] * int(count_number))
    if any(duration < 0 for duration in durations):
        raise ValueError("segment durations must not be negative")
    return durations


def build_recipe(config: Mapping[str, Any], seed: int) -> Recipe:
    """Build a reproducible plan without changing caller or process RNG state."""
    if not isinstance(config, Mapping):
        raise ValueError("config must be a mapping")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")

    width, height = _parse_resolution(config)
    strength = str(config.get("variant_strength", "balanced"))
    if strength not in _CROP_LIMITS:
        raise ValueError("variant_strength must be mild, balanced, or strong")
    hook_duration = _number(config.get("t_hook", 0), "t_hook")
    if hook_duration < 0:
        raise ValueError("segment durations must not be negative")
    durations = [hook_duration, *_body_durations(config)]

    total_duration = sum(durations)
    if not math.isfinite(total_duration) or total_duration <= 0:
        raise ValueError("total duration must be finite and positive")
    if "total_duration" in config:
        declared_total = _number(config["total_duration"], "total_duration")
        if declared_total <= 0 or not math.isclose(declared_total, total_duration, rel_tol=1e-6, abs_tol=1e-3):
            raise ValueError("total_duration does not match the segment timeline")

    regions = _read_protected_regions(config)
    rng = random.Random(seed)
    max_crop = _CROP_LIMITS[strength]
    brightness_limit, contrast_delta, saturation_delta = _COLOR_LIMITS[strength]
    crop_enabled = bool(config.get("variant_crop", True))
    color_enabled = bool(config.get("variant_color", False))
    mirror_enabled = bool(config.get("variant_mirror", False))
    warnings: list[str] = []
    segments: list[SegmentRecipe] = []
    start = 0.0

    for index, duration in enumerate(durations):
        if duration == 0:
            continue

        enabled = bool(config.get("variant_hook", True) if index == 0 else config.get("variant_body", True))
        crop_total = rng.uniform(max_crop * 0.5, max_crop) if enabled and crop_enabled else 0.0
        offset_x = rng.random() if crop_total > 0 else 0.5
        offset_y = rng.random() if crop_total > 0 else 0.5
        overlapping = _overlapping_regions(regions, start, duration)

        if crop_total > 0 and overlapping:
            scaled_width, scaled_height = _scaled_dimensions(width, height, crop_total)
            if not _crop_keeps_regions(
                overlapping, width, height, scaled_width, scaled_height, offset_x, offset_y,
            ):
                crop_total = 0.0
                offset_x = offset_y = 0.5
                warnings.append(f"segment {index}: crop skipped to preserve a protected region")

        brightness = 0.0
        contrast = saturation = 1.0
        if enabled and color_enabled:
            brightness = rng.uniform(-brightness_limit, brightness_limit)
            contrast = rng.uniform(1.0 - contrast_delta, 1.0 + contrast_delta)
            saturation = rng.uniform(1.0 - saturation_delta, 1.0 + saturation_delta)

        mirror = bool(enabled and mirror_enabled and rng.random() < 0.5)
        if mirror and overlapping:
            mirror = False
            warnings.append(f"segment {index}: mirror skipped because a protected region overlaps")

        segments.append(SegmentRecipe(
            index=index,
            start=start,
            duration=duration,
            enabled=enabled,
            crop_total=crop_total,
            offset_x=offset_x,
            offset_y=offset_y,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            mirror=mirror,
        ))
        start += duration

    return Recipe(
        version=VERSION,
        seed=seed,
        width=width,
        height=height,
        segments=tuple(segments),
        warnings=tuple(warnings),
    )


def _fmt(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def normalize_filter(recipe: Recipe, segment: SegmentRecipe) -> str:
    """Return one scale/crop normalization chain with any enabled operators."""
    width, height = recipe.width, recipe.height
    base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p"
    if not segment.enabled:
        return base

    scaled_width, scaled_height = _scaled_dimensions(width, height, segment.crop_total)
    has_crop = scaled_width != width or scaled_height != height
    filters = [f"scale={scaled_width}:{scaled_height}:force_original_aspect_ratio=increase"]
    if has_crop:
        growth_x = scaled_width - width
        growth_y = scaled_height - height
        x = f"(iw-{width})/2"
        y = f"(ih-{height})/2"
        if growth_x:
            x += f"+{growth_x}*({_fmt(segment.offset_x)}-0.5)"
        if growth_y:
            y += f"+{growth_y}*({_fmt(segment.offset_y)}-0.5)"
        filters.append(f"crop={width}:{height}:{x}:{y}")
    else:
        filters.append(f"crop={width}:{height}")
    filters.append("setsar=1")

    if segment.brightness != 0 or segment.contrast != 1 or segment.saturation != 1:
        filters.append(
            "eq="
            f"brightness={_fmt(segment.brightness)}:"
            f"contrast={_fmt(segment.contrast)}:"
            f"saturation={_fmt(segment.saturation)}"
        )
    if segment.mirror:
        filters.append("hflip")
    filters.append("format=yuv420p")
    return ",".join(filters)


def summary(recipe: Recipe) -> dict[str, Any]:
    """Return a JSON-serializable overview of the resolved recipe."""
    return {
        "version": recipe.version,
        "seed": recipe.seed,
        "segments": len(recipe.segments),
        "enabled_segments": sum(segment.enabled for segment in recipe.segments),
        "parameters": [asdict(segment) for segment in recipe.segments],
        "warnings": list(recipe.warnings),
        "audio_policy": "preserve",
        "timeline_policy": "preserve",
    }
