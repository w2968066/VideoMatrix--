"""Compatibility facade and standalone renderer for the deduplication V2 plan."""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from .dedup_plan import Recipe, SegmentRecipe, build_recipe, normalize_filter, summary as recipe_summary
from .ffmpeg import FFMPEG, FFPROBE, run_process
from .hardware import session_for


# The recipe format is V2. Random-cover selection deliberately keeps using the
# original seed namespace below so enabling V2 does not reshuffle first frames.
VARIANT_VERSION = "creative-variant-v2"
_SEED_NAMESPACE = "creative-variant-v1"
_MAX_SEGMENTS = 32
_PROBE_TIMEOUT_SECONDS = 30.0


def derive_variant_seed(base_seed: int, task_name: str, output_index: int) -> int:
    value = f"{_SEED_NAMESPACE}:{base_seed}:{task_name}:{output_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")


def parse_resolution(config: dict) -> tuple[int, int]:
    value = str(config.get("resolution", "1080*1920")).lower().replace("*", "x")
    parts = value.split("x")
    return int(parts[0]), int(parts[1])


class VariantPlan(tuple):
    """Tuple-compatible legacy plan that retains its source Recipe for summary."""

    def __new__(cls, recipe: Recipe):
        result = super().__new__(cls, recipe.segments)
        result.recipe = recipe
        return result


def build_variant_plan(config: dict, seed: int) -> tuple[SegmentRecipe, ...]:
    """Keep the old API while returning the real V2 segment recipes."""
    return VariantPlan(build_recipe(config, seed))


def summarize_variant_plan(plan, seed: int) -> dict:
    """Summarize either a V2 Recipe or the segment tuple returned above."""
    if isinstance(plan, Recipe):
        recipe = replace(plan, seed=seed)
    elif isinstance(getattr(plan, "recipe", None), Recipe):
        recipe = replace(plan.recipe, seed=seed)
    else:
        width, height = parse_resolution({})
        recipe = Recipe(
            version=VARIANT_VERSION,
            seed=seed,
            width=width,
            height=height,
            segments=tuple(plan),
            warnings=(),
        )
    result = recipe_summary(recipe)
    result.setdefault("version", recipe.version)
    result.setdefault("seed", seed)
    return result


class VideoVariantProcessor:
    """Render the V2 plan to a separate file, or adapt it to the legacy API."""

    @staticmethod
    def inline_filters(config: dict, seed: int, video_label: str, audio_label: str | None):
        raise RuntimeError(
            "旧 inline_filters 已停用；宿主应在每个原始片段的 scale/crop 处融合 V2 滤镜"
        )

    def process_to_file(
        self,
        input_path: str,
        output_path: str,
        config: dict,
        seed: int,
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
    ) -> tuple[bool, str | None, dict]:
        """Render without replacing the source or overwriting an existing output."""
        source = Path(input_path)
        destination = Path(output_path)
        temporary: Path | None = None
        summary: dict = {}

        try:
            if self._same_path(source, destination):
                return False, "输入和输出路径不能相同", {}
            if destination.exists():
                return False, "输出文件已存在", {}
            if is_cancelled and is_cancelled():
                return False, "已停止", {}
            if bool(config.get("variant_blend_enabled", False)):
                return False, "独立文件模式暂不支持B混合", {}

            source_info = self._probe_media(str(source), is_cancelled, on_process)
            streams = source_info.get("streams", [])
            video_stream, video_ordinal = self._main_video(streams)
            if video_stream is None:
                return False, "输入文件没有可用的视频轨", {}
            if self._is_hdr(video_stream):
                return False, "独立文件模式暂不支持HDR视频", {}

            duration = self._video_duration(source_info, video_stream)
            if duration <= 0:
                return False, "输入视频时长无效", {}

            recipe = self._build_recipe_for_input(config, seed, duration, video_stream)
            if len(recipe.segments) > _MAX_SEGMENTS:
                return False, f"独立文件模式最多支持{_MAX_SEGMENTS}个片段（更多片段会同时打开较多解码器）", recipe_summary(recipe)
            summary = recipe_summary(recipe)
            summary.setdefault("version", recipe.version)
            summary.setdefault("seed", seed)

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(
                f".{destination.name}.variant-{uuid.uuid4().hex}.tmp"
            )
            if not recipe.segments or not any(segment.enabled for segment in recipe.segments):
                # A disabled recipe is an exact file copy; preserve all streams and metadata.
                import shutil

                shutil.copy2(source, temporary)
            else:
                base, tail, audio_reference = self._build_render_command(
                    source, temporary, source_info, video_ordinal, recipe, config,
                )
                ok, error = session_for(config).run(
                    base, tail, "成品变换", is_cancelled, on_process,
                    runner=lambda full_command: self._run(full_command, is_cancelled, on_process),
                )
                if not ok:
                    self._unlink_with_retry(temporary)
                    return False, error or "变体处理失败", summary
                if is_cancelled and is_cancelled():
                    self._unlink_with_retry(temporary)
                    return False, "已停止", summary
                self._validate_output(
                    str(temporary), source_info, recipe, audio_reference,
                    is_cancelled, on_process,
                )

            if is_cancelled and is_cancelled():
                self._unlink_with_retry(temporary)
                return False, "已停止", summary
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                self._unlink_with_retry(temporary)
                return False, "变体输出校验失败", summary
            self._commit_new_file(temporary, destination)
            temporary = None
            return True, None, summary
        except InterruptedError as exc:
            if temporary is not None:
                self._unlink_with_retry(temporary)
            return False, str(exc) or "已停止", summary
        except (OSError, RuntimeError, ValueError, TypeError, TimeoutError, json.JSONDecodeError) as exc:
            if temporary is not None:
                self._unlink_with_retry(temporary)
            return False, str(exc), summary
        except Exception as exc:
            if temporary is not None:
                self._unlink_with_retry(temporary)
            return False, str(exc), summary

    def process(
        self,
        input_path: str,
        config: dict,
        seed: int,
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
    ) -> tuple[bool, str | None, dict]:
        """Legacy adapter: build beside the host output, then replace it atomically."""
        source = Path(input_path)
        if not source.is_file():
            return False, "基础成片不存在", {}
        if bool(config.get("variant_blend_enabled", False)):
            return False, "独立文件模式暂不支持B混合", {}
        temporary = source.with_name(f".{source.name}.variant-{uuid.uuid4().hex}.tmp")
        ok, error, summary = self.process_to_file(
            str(source), str(temporary), config, seed, is_cancelled, on_process,
        )
        if not ok:
            self._unlink_with_retry(temporary)
            return False, error, summary
        if is_cancelled and is_cancelled():
            self._unlink_with_retry(temporary)
            return False, "已停止", summary
        try:
            self._replace_with_retry(temporary, source)
            return True, None, summary
        except OSError as exc:
            self._unlink_with_retry(temporary)
            return False, str(exc), summary

    @staticmethod
    def _build_recipe_for_input(
        config: dict, seed: int, duration: float, video_stream: dict | None = None,
    ) -> Recipe:
        normalized = dict(config)
        if not normalized.get("resolution") and video_stream:
            width = int(video_stream.get("width") or 0)
            height = int(video_stream.get("height") or 0)
            if width > 0 and height > 0:
                if VideoVariantProcessor._rotation_swaps_dimensions(video_stream):
                    width, height = height, width
                normalized["resolution"] = f"{max(2, width // 2 * 2)}*{max(2, height // 2 * 2)}"
        if "t_hook" not in normalized and "total_clips" not in normalized:
            normalized["t_hook"] = duration
            normalized["total_clips"] = 1
            normalized["t_body"] = 0.0
        recipe = build_recipe(normalized, seed)
        if not recipe.segments:
            normalized.update({"t_hook": duration, "total_clips": 1, "t_body": 0.0})
            recipe = build_recipe(normalized, seed)

        cursor = 0.0
        fitted: list[SegmentRecipe] = []
        for segment in recipe.segments:
            remaining = duration - cursor
            if remaining <= 0.000001:
                break
            segment_duration = min(max(0.0, float(segment.duration)), remaining)
            if segment_duration <= 0.000001:
                continue
            fitted.append(replace(segment, start=round(cursor, 9), duration=round(segment_duration, 9)))
            cursor += segment_duration
        if fitted and cursor < duration - 0.000001:
            fitted[-1] = replace(fitted[-1], duration=round(fitted[-1].duration + duration - cursor, 9))
        fitted_recipe = replace(recipe, segments=tuple(fitted))
        return fitted_recipe

    @staticmethod
    def _build_render_command(
        source: Path,
        temporary: Path,
        source_info: dict,
        video_ordinal: int,
        recipe: Recipe,
        config: dict,
    ) -> tuple[list[str], list[str], list[dict]]:
        inputs: list[str] = []
        filters: list[str] = []
        for index, segment in enumerate(recipe.segments):
            inputs.extend([
                "-ss", f"{segment.start:.9f}", "-t", f"{segment.duration:.9f}", "-threads", "2",
                "-i", str(source),
            ])
            normalized = normalize_filter(recipe, segment)
            if not normalized or not math.isfinite(float(segment.duration)):
                raise ValueError("V2滤镜配方无效")
            filters.append(
                f"[{index}:v:{video_ordinal}]setpts=PTS-STARTPTS,{normalized}[variant_v{index}]"
            )
        concat_inputs = "".join(f"[variant_v{index}]" for index in range(len(recipe.segments)))
        filters.append(f"{concat_inputs}concat=n={len(recipe.segments)}:v=1:a=0[variant_vout]")

        audio_input = len(recipe.segments)
        base = [FFMPEG, "-y", *inputs, "-threads", "2", "-i", str(source), "-filter_complex", ";".join(filters),
                "-map", "[variant_vout]", "-map", f"{audio_input}:a?", "-map_metadata", str(audio_input),
                "-map_chapters", str(audio_input), "-c:a", "copy", "-copyts", "-copytb", "1",
                "-avoid_negative_ts", "disabled", "-movflags", "+faststart"]
        tail = ["-b:v", str(config.get("bitrate", "8000k")), "-f", "mp4", str(temporary)]
        video_stream, _ = VideoVariantProcessor._main_video(source_info.get("streams", []))
        if video_stream and abs(VideoVariantProcessor._rotation_degrees(video_stream)) > 0.01:
            tail[0:0] = ["-metadata:s:v:0", "rotate=0"]
        audio_reference = [stream for stream in source_info.get("streams", []) if stream.get("codec_type") == "audio"]
        return base, tail, audio_reference

    @staticmethod
    def _validate_output(
        output_path: str,
        source_info: dict,
        recipe: Recipe,
        audio_reference: list[dict],
        is_cancelled: Callable[[], bool] | None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None,
    ) -> None:
        checked = VideoVariantProcessor._probe_media(output_path, is_cancelled, on_process)
        streams = checked.get("streams", [])
        output_video, _ = VideoVariantProcessor._main_video(streams)
        if output_video is None:
            raise RuntimeError("变体输出缺少视频轨")
        if int(output_video.get("width") or 0) != recipe.width or int(output_video.get("height") or 0) != recipe.height:
            raise RuntimeError("变体输出尺寸校验失败")

        input_video, _ = VideoVariantProcessor._main_video(source_info.get("streams", []))
        input_duration = VideoVariantProcessor._video_duration(source_info, input_video)
        output_duration = VideoVariantProcessor._video_duration(checked, output_video)
        fps = VideoVariantProcessor._fps_value(input_video or {})
        duration_tolerance = max(0.15, 2.0 / fps if fps > 0 else 0.15)
        if output_duration <= 0 or abs(output_duration - input_duration) > duration_tolerance:
            raise RuntimeError("变体输出时长校验失败")

        output_audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
        if len(output_audio) != len(audio_reference):
            raise RuntimeError("变体输出音轨数量校验失败")
        for index, (before, after) in enumerate(zip(audio_reference, output_audio), start=1):
            for key in ("codec_name", "sample_rate", "channels"):
                if str(before.get(key, "")) != str(after.get(key, "")):
                    raise RuntimeError(f"变体输出第{index}条音轨{key}校验失败")
            before_start = VideoVariantProcessor._number(before.get("start_time"), 0.0)
            after_start = VideoVariantProcessor._number(after.get("start_time"), 0.0)
            if abs(before_start - after_start) > 0.05:
                raise RuntimeError(f"变体输出第{index}条音轨起始时间校验失败")
            before_duration = VideoVariantProcessor._number(before.get("duration"), 0.0)
            after_duration = VideoVariantProcessor._number(after.get("duration"), 0.0)
            if before_duration > 0 and after_duration > 0 and abs(before_duration - after_duration) > 0.12:
                raise RuntimeError(f"变体输出第{index}条音轨时长校验失败")

        before_frames = VideoVariantProcessor._optional_int(input_video.get("nb_frames"))
        after_frames = VideoVariantProcessor._optional_int(output_video.get("nb_frames"))
        if before_frames and after_frames and abs(before_frames - after_frames) > max(2, round(fps * 0.08)):
            raise RuntimeError("变体输出帧数校验失败")

    @staticmethod
    def _probe_output(
        command: list[str],
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
        timeout: float = _PROBE_TIMEOUT_SECONDS,
    ) -> str:
        if is_cancelled and is_cancelled():
            raise InterruptedError("已停止")
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + timeout
        try:
            if on_process:
                on_process(process)
            while True:
                if is_cancelled and is_cancelled():
                    raise InterruptedError("已停止")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("媒体探测超时")
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if is_cancelled and is_cancelled():
                raise InterruptedError("已停止")
            if process.returncode != 0:
                raise RuntimeError("媒体探测失败：" + (stderr.strip()[-400:] or str(process.returncode)))
            return stdout
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if process.stdout or process.stderr:
                process.communicate()
            if on_process:
                on_process(None)

    @staticmethod
    def _probe_media(
        path: str,
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
    ) -> dict:
        output = VideoVariantProcessor._probe_output([
            FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path,
        ], is_cancelled, on_process)
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("媒体探测结果无效") from exc
        if not isinstance(result, dict):
            raise RuntimeError("媒体探测结果无效")
        return result

    @staticmethod
    def _main_video(streams: list[dict]) -> tuple[dict | None, int]:
        ordinal = 0
        for stream in streams:
            if stream.get("codec_type") != "video":
                continue
            current = ordinal
            ordinal += 1
            if int((stream.get("disposition") or {}).get("attached_pic", 0) or 0) != 1:
                return stream, current
        return None, -1

    @staticmethod
    def _rotation_degrees(stream: dict) -> float:
        for item in stream.get("side_data_list") or []:
            if "display matrix" in str(item.get("side_data_type", "")).lower() and item.get("rotation") is not None:
                try:
                    value = float(item["rotation"])
                    if math.isfinite(value):
                        return value
                except (TypeError, ValueError):
                    pass
        try:
            value = float((stream.get("tags") or {}).get("rotate", 0))
            return value if math.isfinite(value) else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _rotation_swaps_dimensions(stream: dict) -> bool:
        rotation = VideoVariantProcessor._rotation_degrees(stream) % 360.0
        return min(abs(rotation - 90.0), abs(rotation - 270.0)) <= 1.0

    @staticmethod
    def _is_hdr(stream: dict) -> bool:
        transfer = str(stream.get("color_transfer", "")).lower()
        if transfer in {"smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12"}:
            return True
        return any(
            any(marker in str(item.get("side_data_type", "")).lower() for marker in (
                "mastering display", "content light", "dynamic hdr", "hdr10",
            ))
            for item in stream.get("side_data_list", [])
        )

    @staticmethod
    def _video_duration(info: dict, video: dict | None) -> float:
        if video:
            value = VideoVariantProcessor._number(video.get("duration"), 0.0)
            if value > 0:
                return value
        return VideoVariantProcessor._number((info.get("format") or {}).get("duration"), 0.0)

    @staticmethod
    def _fps_value(stream: dict) -> float:
        for key in ("avg_frame_rate", "r_frame_rate"):
            value = str(stream.get(key) or "")
            try:
                if "/" in value:
                    numerator, denominator = value.split("/", 1)
                    result = float(numerator) / float(denominator)
                else:
                    result = float(value)
                if math.isfinite(result) and result > 0:
                    return result
            except (ValueError, ZeroDivisionError):
                pass
        return 0.0

    @staticmethod
    def _number(value, fallback: float) -> float:
        try:
            result = float(value)
            return result if math.isfinite(result) else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _optional_int(value) -> int | None:
        try:
            result = int(value)
            return result if result > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _same_path(source: Path, destination: Path) -> bool:
        try:
            return os.path.normcase(str(source.resolve())) == os.path.normcase(str(destination.resolve()))
        except OSError:
            return os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(destination))

    @staticmethod
    def _commit_new_file(temporary: Path, destination: Path) -> None:
        # A same-directory hard link is atomic and fails if another process
        # created the destination after the initial existence check.
        os.link(temporary, destination)
        VideoVariantProcessor._unlink_with_retry(temporary)

    @staticmethod
    def _replace_with_retry(temp_path: Path, destination: Path, attempts: int = 24, delay: float = 0.25):
        """Replace a completed host output after transient Windows file locks clear."""
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
    def _run(
        command: list[str],
        is_cancelled: Callable[[], bool] | None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None,
    ) -> tuple[bool, str | None]:
        return run_process(command, is_cancelled, on_process)
