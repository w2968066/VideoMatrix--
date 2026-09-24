"""Replace (not prepend) a finished video's first frame with a random cover frame."""
from __future__ import annotations

import random
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg import FFMPEG, FFPROBE, probe_media, run_cmd
from .video_variant import VideoVariantProcessor
from .hardware import session_for


class RandomCoverProcessor:
    @staticmethod
    def _frame_count(path: str, video: dict, duration: float, fps_value: float) -> int:
        value = video.get("nb_frames")
        try:
            if value and int(value) > 0:
                return int(value)
        except (TypeError, ValueError):
            pass
        result = run_cmd([
            FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", path,
        ])
        try:
            count = int((result.stdout or "").strip()) if result else 0
            if count > 0:
                return count
        except (TypeError, ValueError):
            pass
        return max(0, round(duration * fps_value))

    def process(
        self,
        input_path: str,
        config: dict,
        seed: int,
        is_cancelled: Callable[[], bool] | None = None,
        on_process: Callable[[Optional[subprocess.Popen]], None] | None = None,
    ) -> tuple[bool, str | None, dict]:
        info = probe_media(input_path)
        if not info:
            return False, "无法探测基础成片", {}
        video = next((item for item in info.get("streams", []) if item.get("codec_type") == "video"), None)
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        if not video or duration <= 0:
            return False, "基础成片没有可用视频轨", {}
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        fps = str(video.get("avg_frame_rate") or config.get("fps", "30"))
        try:
            fps_value = float(fps.split("/")[0]) / float(fps.split("/")[1]) if "/" in fps else float(fps)
        except (ValueError, ZeroDivisionError):
            fps, fps_value = str(config.get("fps", "30")), float(config.get("fps", 30))
        frame_count = self._frame_count(input_path, video, duration, fps_value)
        if width <= 0 or height <= 0 or fps_value <= 0 or frame_count < 2:
            return False, "基础成片时长或画面参数异常", {}

        rng = random.Random(seed)
        mode = str(config.get("random_cover_mode", "replace"))
        if mode not in ("replace", "insert"):
            return False, "随机封面模式无效", {}
        frame_duration = 1 / fps_value
        sample_frame = rng.randrange(frame_count)
        sample_time = sample_frame / fps_value
        zoom = rng.uniform(1.02, 1.18)
        scaled_w = max(width, int(width * zoom) // 2 * 2)
        scaled_h = max(height, int(height * zoom) // 2 * 2)
        crop_x = rng.uniform(0, scaled_w - width)
        crop_y = rng.uniform(0, scaled_h - height)
        summary = {"sample_time": sample_time, "zoom": zoom, "seed": seed, "mode": mode}

        source = Path(input_path)
        temp_path = source.with_name(f".{source.stem}.cover-{uuid.uuid4().hex[:8]}.tmp")
        video_temp_path = source.with_name(f".{source.stem}.cover-video-{uuid.uuid4().hex[:8]}.tmp")
        output_frame_count = frame_count + (1 if mode == "insert" else 0)
        base_video = "[0:v]setpts=PTS-STARTPTS,setsar=1[base]"
        if mode == "insert":
            base_video = (
                f"[0:v]setpts=PTS-STARTPTS,tpad=start_mode=clone:start_duration={frame_duration:.9f},"
                f"trim=end_frame={output_frame_count},setsar=1[base]"
            )
        filter_complex = (
            f"[0:v]trim=start_frame={sample_frame}:end_frame={sample_frame + 1},setpts=PTS-STARTPTS,"
            f"scale={scaled_w}:{scaled_h},crop={width}:{height}:{crop_x:.3f}:{crop_y:.3f},setsar=1,"
            "trim=end_frame=1[cover];"
            f"{base_video};[base][cover]overlay=0:0:eof_action=pass:repeatlast=0:shortest=0[vout]"
        )
        base = [
            FFMPEG, "-y", "-copyts", "-i", str(source), "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-r", fps, "-frames:v", str(output_frame_count), "-pix_fmt", "yuv420p",
        ]
        ok, error = session_for(config).run(
            base, ["-b:v", str(config.get("bitrate", "8000k")), "-f", "mp4", str(video_temp_path)],
            '随机封面', is_cancelled, on_process,
            runner=lambda command: VideoVariantProcessor._run(command, is_cancelled, on_process),
        )
        if not ok:
            VideoVariantProcessor._unlink_with_retry(video_temp_path)
            return False, error or "随机封面处理失败", summary

        if is_cancelled and is_cancelled():
            VideoVariantProcessor._unlink_with_retry(video_temp_path)
            return False, "已停止", summary
        mux_command = [
            FFMPEG, "-y", "-copyts", "-i", str(video_temp_path),
        ]
        if mode == "insert":
            mux_command.extend(["-itsoffset", f"{frame_duration:.12f}"])
        mux_command.extend([
            "-i", str(source),
            "-map", "0:v:0", "-map", "1:a?", "-map_metadata", "1",
            "-c", "copy", "-copytb", "1", "-avoid_negative_ts", "disabled", "-f", "mp4", str(temp_path),
        ])
        ok, error = VideoVariantProcessor._run(mux_command, is_cancelled, on_process)
        VideoVariantProcessor._unlink_with_retry(video_temp_path)
        if not ok:
            VideoVariantProcessor._unlink_with_retry(temp_path)
            return False, error or "随机封面封装失败", summary

        checked = probe_media(str(temp_path))
        checked_video = next((item for item in (checked or {}).get("streams", []) if item.get("codec_type") == "video"), None)
        checked_frames = self._frame_count(str(temp_path), checked_video or {}, duration, fps_value) if checked_video else 0
        if (
            not checked or not temp_path.exists() or temp_path.stat().st_size < 1024
            or not checked_video or int(checked_video.get("width") or 0) != width
            or int(checked_video.get("height") or 0) != height or checked_frames != output_frame_count
        ):
            VideoVariantProcessor._unlink_with_retry(temp_path)
            return False, "随机封面输出校验失败", summary
        if is_cancelled and is_cancelled():
            VideoVariantProcessor._unlink_with_retry(temp_path)
            return False, "已停止", summary
        try:
            VideoVariantProcessor._replace_with_retry(temp_path, source)
            return True, None, summary
        except OSError as exc:
            VideoVariantProcessor._unlink_with_retry(temp_path)
            return False, str(exc), summary
