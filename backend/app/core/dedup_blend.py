"""Optional B-picture compositing. No scheduler, audio, or output ownership here."""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable


def _video_duration(stream: dict) -> float | None:
    """Never mistake a longer audio track's container duration for B video."""
    candidates = [stream.get('duration')]
    try:
        candidates.append(float(stream['duration_ts']) * float(Fraction(stream['time_base'])))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    tag = (stream.get('tags') or {}).get('DURATION')
    if tag:
        try:
            hours, minutes, seconds = map(float, tag.split(':'))
            candidates.append(hours * 3600 + minutes * 60 + seconds)
        except (ValueError, AttributeError):
            pass
    for value in candidates:
        try:
            number = float(value)
            if math.isfinite(number) and number > 0:
                return number
        except (TypeError, ValueError):
            pass
    return None


@dataclass(frozen=True)
class BlendRecipe:
    path: str
    stream_index: int
    opacity: float
    eof: str
    duration: float
    width: int
    height: int
    fps: str
    hook_duration: float
    on_hook: bool
    on_body: bool

    def input_args(self) -> list[str]:
        return (["-stream_loop", "-1"] if self.eof == "loop" else []) + [
            "-threads", "2", "-i", self.path,
        ]

    def filters(self, input_index: int, video_label: str) -> tuple[str, str]:
        chain = (f"[{input_index}:{self.stream_index}]setpts=PTS-STARTPTS,"
                 f"fps={self.fps},scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                 f"crop={self.width}:{self.height},setsar=1,format=yuv420p")
        if self.eof == "freeze":
            chain += f",tpad=stop_mode=clone:stop_duration={self.duration:.9f}"
        chain += f",trim=duration={self.duration:.9f}[dedup_b]"
        enable = ""
        if not self.on_hook:
            enable = f":enable='gte(t,{self.hook_duration:.9f})'"
        elif not self.on_body:
            enable = f":enable='lt(t,{self.hook_duration:.9f})'"
        # The main stream owns EOF and timestamps. B audio is never mapped.
        # Normal blend uses FFmpeg's native weighted path, avoiding per-pixel
        # expression evaluation. Top A keeps 1-p; bottom B contributes p.
        chain += (f";{video_label}[dedup_b]blend=all_mode=normal:all_opacity={1-self.opacity:.9f}:"
                  f"shortest=0:repeatlast=1:eof_action=repeat{enable}[dedup_blended]")
        return chain, "[dedup_blended]"


def prepare_blend(config: dict, probe: Callable[[str], dict]) -> BlendRecipe | None:
    if not config.get("variant_blend_enabled"):
        return None
    on_hook, on_body = bool(config.get("variant_hook", True)), bool(config.get("variant_body", True))
    duration, hook_duration = float(config["total_duration"]), float(config["t_hook"])
    if not on_hook and (not on_body or duration <= hook_duration):
        return None
    path = Path(str(config.get("variant_blend_path") or "")).expanduser()
    if not path.is_file():
        raise ValueError("B 画面素材不存在，请重新选择文件")
    opacity = float(config.get("variant_blend_opacity", .03))
    if not math.isfinite(opacity) or not .01 <= opacity <= .15:
        raise ValueError("B 画面占比必须在 1%–15% 之间")
    eof = config.get("variant_blend_eof", "loop")
    if eof not in ("loop", "freeze", "error"):
        raise ValueError("B 画面时长不足策略无效")
    info = probe(str(path))
    video = next((s for s in (info or {}).get("streams", []) if s.get("codec_type") == "video"
                  and int((s.get("disposition") or {}).get("attached_pic") or 0) != 1), None)
    if not video:
        raise ValueError("B 素材没有可用视频画面")
    from .video_variant import VideoVariantProcessor
    if VideoVariantProcessor._is_hdr(video):
        raise ValueError("B 画面混合暂不支持 HDR，请使用 SDR 素材")
    source_duration = _video_duration(video)
    fps = str(config.get("fps", 30))
    fps_number = float(Fraction(fps))
    if not all(math.isfinite(n) and n > 0 for n in (duration, fps_number)):
        raise ValueError("B 画面的时长或输出帧率无效")
    if eof == "error":
        if source_duration is None:
            raise ValueError('无法快速确认 B 视频轨时长，已按不足策略跳过混合；可改选循环或定格')
        if source_duration + 1/fps_number < duration:
            raise ValueError(f"B 画面只有 {source_duration:.3f} 秒，短于主片 {duration:.3f} 秒，已跳过混合")
    width, height = map(int, str(config.get("resolution", "1080*1920")).lower().replace("*", "x").split("x"))
    return BlendRecipe(str(path.resolve()), int(video["index"]), opacity, eof, duration,
                       width, height, fps, hook_duration, on_hook, on_body)
