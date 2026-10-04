"""Per-output settings for the main renderer, independent of optional layers."""
from __future__ import annotations

import hashlib
import math
import random
import re
from functools import lru_cache


OUTPUT_DEFAULTS = {
    'random_resolution_min': 1080,
    'random_resolution_max': 1440,
    'random_bitrate_min': 8000,
    'random_bitrate_max': 14000,
}
MAX_OUTPUT_EDGE = 8192
MAX_OUTPUT_PIXELS = 8192 * 4320


def _range(config: dict, prefix: str, floor: int, ceiling: int) -> tuple[int, int]:
    values = []
    for suffix in ('min', 'max'):
        key = f'{prefix}_{suffix}'
        value = config.get(key, OUTPUT_DEFAULTS[key])
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = math.nan
        label = '随机分辨率短边' if prefix == 'random_resolution' else '随机码率'
        if isinstance(value, bool) or not math.isfinite(number) or not number.is_integer() or not floor <= number <= ceiling:
            raise ValueError(f'{label}范围必须为 {floor}–{ceiling} 的整数。')
        values.append(int(number))
    if values[0] > values[1]:
        raise ValueError(f'{label}下限不能大于上限。')
    return values[0], values[1]


def parse_resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r'\s*(\d+)\s*[*xX×]\s*(\d+)\s*', str(value))
    if not match or min(map(int, match.groups())) <= 0:
        raise ValueError('随机分辨率需要有效的宽×高，例如 1080*1920。')
    return tuple(map(int, match.groups()))


@lru_cache(maxsize=128)
def resolution_candidates(resolution: str, lower: int, upper: int) -> tuple[tuple[int, int], ...]:
    width, height = parse_resolution(resolution)
    divisor = math.gcd(width, height)
    unit_w, unit_h = width // divisor, height // divisor
    # At least one reduced unit is odd. Even multipliers make BOTH sides even,
    # preserving the exact aspect ratio instead of independently rounding sides.
    short_step = min(unit_w, unit_h) * 2
    start, end = (lower + short_step - 1) // short_step, upper // short_step
    if start > end:
        raise ValueError('该短边范围内没有保持当前比例的偶数分辨率，请扩大范围或调整比例。')
    max_width, max_height = unit_w * end * 2, unit_h * end * 2
    if max(max_width, max_height) > MAX_OUTPUT_EDGE or max_width * max_height > MAX_OUTPUT_PIXELS:
        raise ValueError('随机分辨率范围上限超过尺寸预算（长边≤8192，总像素≤35389440），请降低短边上限或调整比例。')
    return tuple((unit_w * i * 2, unit_h * i * 2) for i in range(start, end + 1))


def validate_output_settings(config: dict) -> None:
    if config.get('random_resolution_enabled'):
        lower, upper = _range(config, 'random_resolution', 128, 7680)
        resolution_candidates(config.get('resolution', '1080*1920'), lower, upper)
    if config.get('random_bitrate_enabled'):
        _range(config, 'random_bitrate', 100, 200000)


def _rng(seed: int, task_name: str, output_index: int, operator: str) -> random.Random:
    # Separate namespaces prevent switching bitrate/variants/covers from changing
    # dimensions or the user's Hook / Body / BGM selection sequence.
    payload = f'output-v1\0{seed}\0{task_name}\0{output_index}\0{operator}'
    return random.Random(int.from_bytes(hashlib.sha256(payload.encode()).digest(), 'big'))


def resolve_output_config(config: dict, task_name: str, output_index: int, seed: int) -> dict:
    """Copy and resolve once; retries and post-processors receive this same copy."""
    validate_output_settings(config)
    resolved = dict(config)
    settings = {}
    if config.get('random_resolution_enabled'):
        lower, upper = _range(config, 'random_resolution', 128, 7680)
        width, height = _rng(seed, task_name, output_index, 'resolution').choice(
            resolution_candidates(config.get('resolution', '1080*1920'), lower, upper)
        )
        resolved['resolution'] = f'{width}*{height}'
        settings['reference_resolution'] = config.get('resolution', '1080*1920')
        settings['resolution'] = resolved['resolution']
    if config.get('random_bitrate_enabled'):
        lower, upper = _range(config, 'random_bitrate', 100, 200000)
        bitrate = _rng(seed, task_name, output_index, 'bitrate').randint(lower, upper)
        resolved['bitrate'] = f'{bitrate}k'
        settings['bitrate_kbps'] = bitrate
    if settings:
        resolved['_output_settings'] = settings
    return resolved


def output_probe_config(config: dict) -> dict:
    """Probe the highest enabled dimensions / target bitrate without sampling."""
    validate_output_settings(config)
    probe = dict(config)
    if config.get('random_resolution_enabled'):
        lower, upper = _range(config, 'random_resolution', 128, 7680)
        width, height = resolution_candidates(config.get('resolution', '1080*1920'), lower, upper)[-1]
        probe['resolution'] = f'{width}*{height}'
    if config.get('random_bitrate_enabled'):
        probe['bitrate'] = f"{_range(config, 'random_bitrate', 100, 200000)[1]}k"
    return probe
