from __future__ import annotations

import hashlib
import math
import os
import random
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from fractions import Fraction
from typing import Callable, Optional

from .ffmpeg import FFMPEG, probe_media
from .timeline import apply_timeline_totals, body_segment_specs


VARIANT_VERSION = "creative-variant-v1"

STRENGTHS = {
    "mild": {
        "zoom": (1.004, 1.012), "rotate": (0.04, 0.14), "brightness": (0.002, 0.008),
        "contrast": (0.992, 1.012), "saturation": (0.992, 1.015), "gamma": (0.995, 1.008),
        "noise": (0.25, 0.7), "mix": (0.012, 0.025), "mirror_chance": 0.18,
        "audio_gain": 0.12, "audio_eq": 0.18,
    },
    "balanced": {
        "zoom": (1.008, 1.024), "rotate": (0.08, 0.28), "brightness": (0.004, 0.014),
        "contrast": (0.982, 1.025), "saturation": (0.982, 1.032), "gamma": (0.99, 1.015),
        "noise": (0.5, 1.25), "mix": (0.02, 0.045), "mirror_chance": 0.35,
        "audio_gain": 0.25, "audio_eq": 0.35,
    },
    "strong": {
        "zoom": (1.014, 1.038), "rotate": (0.14, 0.48), "brightness": (0.007, 0.022),
        "contrast": (0.965, 1.04), "saturation": (0.965, 1.05), "gamma": (0.98, 1.025),
        "noise": (0.8, 2.0), "mix": (0.035, 0.07), "mirror_chance": 0.5,
        "audio_gain": 0.4, "audio_eq": 0.55,
    },
}


@dataclass(frozen=True)
class SegmentVariant:
    index: int
    start: float
    duration: float
    enabled: bool
    mirror: bool
    zoom: float
    offset_x: float
    offset_y: float
    rotation_degrees: float
    brightness: float
    contrast: float
    saturation: float
    gamma: float
    noise: float
    sharpen: float
    frame_mix: float
    audio_gain_db: float
    audio_eq_db: float
    audio_eq_hz: int


def derive_variant_seed(base_seed: int, task_name: str, output_index: int) -> int:
    value = f"{VARIANT_VERSION}:{base_seed}:{task_name}:{output_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def parse_resolution(config: dict) -> tuple[int, int]:
    value = str(config.get("resolution", "1440*2560")).lower().replace("*", "x")
    parts = value.split("x")
    return int(parts[0]), int(parts[1])


def build_variant_plan(config: dict, seed: int) -> list[SegmentVariant]:
    strength = str(config.get("variant_strength", "balanced"))
    limits = STRENGTHS.get(strength, STRENGTHS["balanced"])
    rng = random.Random(seed)
    t_hook = float(config["t_hook"])
    normalized = dict(config)
    apply_timeline_totals(normalized)
    body_durations = [
        spec["clip_duration"]
        for spec in body_segment_specs(normalized)
        for _ in range(spec["clip_count"])
    ]
    width, height = parse_resolution(config)
    aspect = height / width
    plan: list[SegmentVariant] = []
    start = 0.0

    for index, duration in enumerate([t_hook] + body_durations):
        enabled = bool(config.get("variant_hook", True) if index == 0 else config.get("variant_body", True))
        mirror = enabled and bool(config.get("variant_mirror", True)) and rng.random() < limits["mirror_chance"]
        signed = lambda span: rng.uniform(-span, span)
        rotation = signed(rng.uniform(*limits["rotate"])) if enabled else 0.0
        zoom = rng.uniform(*limits["zoom"]) if enabled else 1.0
        if enabled:
            radians = abs(rotation) * math.pi / 180.0
            safe_zoom = max(
                math.cos(radians) + aspect * math.sin(radians),
                math.cos(radians) + math.sin(radians) / aspect,
            ) + 0.002
            zoom = max(zoom, safe_zoom)
        plan.append(SegmentVariant(
            index=index,
            start=round(start, 6),
            duration=round(duration, 6),
            enabled=enabled,
            mirror=mirror,
            zoom=round(zoom, 6),
            offset_x=round(rng.random(), 6),
            offset_y=round(rng.random(), 6),
            rotation_degrees=round(rotation, 6),
            brightness=round(signed(rng.uniform(*limits["brightness"])), 6) if enabled else 0.0,
            contrast=round(rng.uniform(*limits["contrast"]), 6) if enabled else 1.0,
            saturation=round(rng.uniform(*limits["saturation"]), 6) if enabled else 1.0,
            gamma=round(rng.uniform(*limits["gamma"]), 6) if enabled else 1.0,
            noise=round(rng.uniform(*limits["noise"]), 6) if enabled else 0.0,
            sharpen=round(signed(0.18 if strength != "strong" else 0.28), 6) if enabled else 0.0,
            frame_mix=(
                round(rng.uniform(*limits["mix"]), 6)
                if enabled and config.get("variant_frame_mix", True) else 0.0
            ),
            audio_gain_db=round(signed(limits["audio_gain"]), 6) if enabled else 0.0,
            audio_eq_db=round(signed(limits["audio_eq"]), 6) if enabled else 0.0,
            audio_eq_hz=rng.choice((180, 320, 640, 1200, 2400, 4800)),
        ))
        start += duration
    return plan


def summarize_variant_plan(plan: list[SegmentVariant], seed: int) -> dict:
    enabled = [item for item in plan if item.enabled]
    return {
        "version": VARIANT_VERSION,
        "seed": seed,
        "segments": len(enabled),
        "mirrored": sum(item.mirror for item in enabled),
        "frame_mixed": sum(item.frame_mix > 0 for item in enabled),
        "parameters": [asdict(item) for item in plan],
    }


class VideoVariantProcessor:
    def process(
        self,
        input_path: str,
        config: dict,
        seed: int,
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
    ) -> tuple[bool, str | None, dict]:
        plan = build_variant_plan(config, seed)
        summary = summarize_variant_plan(plan, seed)
        if not any(item.enabled for item in plan):
            return True, None, summary

        info = probe_media(input_path)
        if not info:
            return False, "无法探测基础成片", summary
        has_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))
        width, height = self._resolution(config)
        fps = str(config.get("fps", "24"))
        total_duration = sum(item.duration for item in plan)
        filter_complex, video_label, audio_label = self._build_filters(
            plan, width, height, fps, has_audio,
        )

        source = Path(input_path)
        # Keep work files out of media-library scans and Explorer video counts.
        # The explicit muxer below lets FFmpeg write MP4 data to a .tmp file.
        temp_path = source.with_name(f".{source.stem}.variant-{uuid.uuid4().hex[:8]}.tmp")
        base_cmd = [
            FFMPEG, "-y", "-i", str(source), "-filter_complex", filter_complex,
            "-map", video_label,
        ]
        if audio_label:
            base_cmd.extend(["-map", audio_label, "-c:a", "aac", "-b:a", "192k"])
        base_cmd.extend([
            "-r", fps, "-b:v", str(config.get("bitrate", "8000k")),
            "-t", f"{total_duration:.3f}", "-map_metadata", "-1", "-movflags", "+faststart",
        ])

        error = None
        encoder_attempts = (True, False) if config.get("enable_gpu", True) else (False,)
        for use_gpu in encoder_attempts:
            if is_cancelled and is_cancelled():
                temp_path.unlink(missing_ok=True)
                return False, "已停止", summary
            command = list(base_cmd)
            if use_gpu:
                command.extend(["-c:v", "h264_nvenc", "-preset", "p4"])
            else:
                command.extend(["-c:v", "libx264", "-preset", "fast"])
            fps_number = float(Fraction(fps))
            gop = max(12, int(round(fps_number * random.Random(seed).uniform(1.5, 2.8))))
            command.extend(["-g", str(gop), "-bf", "2", "-f", "mp4", str(temp_path)])
            ok, error = self._run(command, is_cancelled, on_process)
            if ok:
                break
            if not use_gpu:
                temp_path.unlink(missing_ok=True)
                return False, error, summary

        checked = probe_media(str(temp_path))
        if not checked or not temp_path.exists() or temp_path.stat().st_size < 1024:
            self._unlink_with_retry(temp_path)
            return False, "变体输出校验失败", summary
        try:
            self._replace_with_retry(temp_path, source)
            return True, None, summary
        except OSError as exc:
            self._unlink_with_retry(temp_path)
            return False, str(exc), summary

    @staticmethod
    def _replace_with_retry(temp_path: Path, destination: Path, attempts: int = 24, delay: float = 0.25):
        """Replace a completed output after transient Windows file locks clear."""
        last_error: OSError | None = None
        for attempt in range(attempts):
            try:
                os.replace(temp_path, destination)
                return
            except OSError as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
        if last_error:
            raise last_error

    @staticmethod
    def _unlink_with_retry(path: Path, attempts: int = 12, delay: float = 0.25):
        for attempt in range(attempts):
            try:
                path.unlink(missing_ok=True)
                return
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(delay)

    @staticmethod
    def _resolution(config: dict) -> tuple[int, int]:
        return parse_resolution(config)

    @staticmethod
    def _build_filters(
        plan: list[SegmentVariant],
        width: int,
        height: int,
        fps: str,
        has_audio: bool,
    ) -> tuple[str, str, str | None]:
        filters: list[str] = []
        for item in plan:
            end = item.start + item.duration
            video = (
                f"[0:v]trim=start={item.start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,"
                f"tpad=stop_mode=clone:stop_duration=0.12,trim=duration={item.duration:.6f}"
            )
            if item.enabled:
                if item.mirror:
                    video += ",hflip"
                scaled_w = math.ceil(width * item.zoom / 2) * 2
                scaled_h = math.ceil(height * item.zoom / 2) * 2
                max_x = max(0, scaled_w - width)
                max_y = max(0, scaled_h - height)
                crop_x = round(max_x * item.offset_x, 3)
                crop_y = round(max_y * item.offset_y, 3)
                radians = item.rotation_degrees * math.pi / 180.0
                video += (
                    f",scale={scaled_w}:{scaled_h},"
                    f"rotate={radians:.9f}:ow=iw:oh=ih:fillcolor=black,"
                    f"crop={width}:{height}:{crop_x}:{crop_y},"
                    f"eq=brightness={item.brightness}:contrast={item.contrast}:"
                    f"saturation={item.saturation}:gamma={item.gamma},"
                    f"noise=alls={item.noise}:allf=t+u,"
                    f"unsharp=5:5:{item.sharpen}:5:5:0"
                )
                if item.frame_mix > 0:
                    scale = 1.0 / (1.0 + item.frame_mix)
                    video += f",tmix=frames=2:weights='1 {item.frame_mix}':scale={scale:.8f}"
            video += f",fps={fps},setsar=1,format=yuv420p[v{item.index}]"
            filters.append(video)

            if has_audio:
                audio = (
                    f"[0:a]atrim=start={item.start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,"
                    "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    f"apad=pad_dur={item.duration:.6f},atrim=duration={item.duration:.6f}"
                )
                if item.enabled:
                    audio += (
                        f",highpass=f=28,lowpass=f=19000,"
                        f"equalizer=f={item.audio_eq_hz}:t=q:w=1:g={item.audio_eq_db},"
                        f"volume={item.audio_gain_db}dB"
                    )
                audio += f"[a{item.index}]"
                filters.append(audio)

        if has_audio:
            concat = "".join(f"[v{i}][a{i}]" for i in range(len(plan)))
            filters.append(f"{concat}concat=n={len(plan)}:v=1:a=1[vout][aout]")
            return ";".join(filters), "[vout]", "[aout]"
        concat = "".join(f"[v{i}]" for i in range(len(plan)))
        filters.append(f"{concat}concat=n={len(plan)}:v=1:a=0[vout]")
        return ";".join(filters), "[vout]", None

    @staticmethod
    def _run(
        command: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None,
    ) -> tuple[bool, str | None]:
        with tempfile.TemporaryFile() as error_stream:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=error_stream,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if on_process:
                on_process(process)
            try:
                while process.poll() is None:
                    if is_cancelled and is_cancelled():
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        return False, "已停止"
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                error_stream.seek(0)
                error = error_stream.read().decode("utf-8", errors="replace")
                return process.returncode == 0, error[-2000:] or None
            finally:
                if on_process:
                    on_process(None)
