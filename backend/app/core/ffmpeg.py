import os
import sys
import json
import subprocess
import tempfile
import re
import time
import shutil
import threading
import uuid
from pathlib import Path
from typing import Optional, Tuple, List


MEDIA_END_SAFETY_MARGIN = 0.2
_failure_log_lock = threading.Lock()


def _failure_detail(errors, command, reason=None):
    """Keep full stderr on disk and a bounded, root-cause-first in-memory excerpt."""
    errors.flush()
    errors.seek(0, os.SEEK_END)
    size = errors.tell()
    errors.seek(0)
    detail = errors.read(65536).decode('utf-8', errors='replace')
    if size > 65536:
        errors.seek(max(65536, size - 8192))
        detail += '\n…（完整输出见日志）…\n' + errors.read().decode('utf-8', errors='replace')
    if reason:
        detail = reason + '\n' + detail
    try:
        root = Path(os.environ.get('APPDATA') or os.path.expanduser('~')) / 'VideoMatrix' / 'logs'
        with _failure_log_lock:
            root.mkdir(parents=True, exist_ok=True)
            target = root / f'ffmpeg-error-{time.time_ns()}-{uuid.uuid4().hex[:8]}.log'
            with target.open('xb') as saved:
                saved.write((json.dumps(command, ensure_ascii=False) + '\n\n').encode('utf-8'))
                if reason:
                    saved.write((reason + '\n').encode('utf-8'))
                errors.seek(0)
                shutil.copyfileobj(errors, saved)
            # Rotate only logs created by this feature, not other application files.
            for old in sorted(root.glob('ffmpeg-error-*.log'), key=lambda file: file.name)[:-20]:
                try:
                    old.unlink()
                except OSError:
                    pass
        detail += f'\n完整日志：{target}'
    except OSError:
        pass
    return detail or 'FFmpeg 执行失败'


def _find_tool(name: str) -> str:
    """在 exe 同目录、PyInstaller 临时目录、PATH 中查找工具。"""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', '')
        if meipass:
            p = os.path.join(meipass, name)
            if os.path.exists(p):
                return p
    local = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), name)
    if os.path.exists(local):
        return local
    bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'ffmpeg', name)
    if os.path.isfile(bundled):
        return bundled
    return name


FFMPEG = _find_tool('ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
FFPROBE = _find_tool('ffprobe.exe' if sys.platform == 'win32' else 'ffprobe')


def run_cmd(cmd: List[str], capture_output: bool = True, cwd: Optional[str] = None) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            cwd=cwd,
        )
    except Exception:
        return None


def probe_media(file_path: str) -> Optional[dict]:
    """探测媒体文件信息，返回 dict 或 None。"""
    if not os.path.exists(file_path):
        return None
    cmd = [
        FFPROBE, '-v', 'error',
        '-print_format', 'json',
        '-show_format', '-show_streams',
        file_path
    ]
    res = run_cmd(cmd)
    if not res or res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except Exception:
        return None


def extract_media_info(info: dict, file_path: str, safety_margin: float = MEDIA_END_SAFETY_MARGIN) -> Tuple[float, bool, Optional[int], Optional[int], Optional[float]]:
    """从 ffprobe JSON 中提取时长、是否有音频、宽高、帧率。"""
    dur = float(info.get('format', {}).get('duration', 0.0))
    has_audio = False
    width = height = fps = None
    
    for s in info.get('streams', []):
        if s.get('codec_type') == 'audio':
            has_audio = True
        elif s.get('codec_type') == 'video':
            v_dur = s.get('duration')
            if v_dur:
                dur = min(dur, float(v_dur))
            else:
                tags = s.get('tags', {})
                if 'DURATION' in tags:
                    t = tags['DURATION']
                    h, m, sec = t.split(':')
                    vd = int(h) * 3600 + int(m) * 60 + float(sec)
                    dur = min(dur, vd)
            
            width = s.get('width')
            height = s.get('height')
            
            # 计算帧率
            r_frame_rate = s.get('r_frame_rate', '')
            if '/' in r_frame_rate:
                num, den = r_frame_rate.split('/')
                fps = float(num) / float(den) if float(den) != 0 else None
            elif r_frame_rate:
                try:
                    fps = float(r_frame_rate)
                except ValueError:
                    pass
    
    dur = max(0.0, dur - safety_margin)
    return dur, has_audio, width, height, fps


def extract_audio_duration(info: dict, safety_margin: float = MEDIA_END_SAFETY_MARGIN) -> float:
    """Return the safe duration of the first audio stream."""
    try:
        format_duration = float(info.get('format', {}).get('duration', 0.0) or 0.0)
    except (TypeError, ValueError):
        format_duration = 0.0
    for stream in info.get('streams', []):
        if stream.get('codec_type') != 'audio':
            continue
        duration = stream.get('duration')
        if duration:
            try:
                stream_duration = float(duration)
                return max(
                    0.0,
                    min(format_duration or stream_duration, stream_duration) - safety_margin,
                )
            except (TypeError, ValueError):
                pass
        tagged = stream.get('tags', {}).get('DURATION')
        if tagged:
            try:
                h, m, sec = tagged.split(':')
                tagged_duration = int(h) * 3600 + int(m) * 60 + float(sec)
                return max(
                    0.0,
                    min(format_duration or tagged_duration, tagged_duration) - safety_margin,
                )
            except (TypeError, ValueError):
                pass
        return max(0.0, format_duration - safety_margin)
    return 0.0


def build_filter_complex(
    clips: List[dict],
    bgm_clip: dict,
    voice_clip: Optional[dict],
    watermark_path: Optional[str],
    srt_path_safe: Optional[str],
    resolution: str,
    fps: int,
    vol_orig: float,
    vol_bgm: float,
    vol_voice: float,
    total_duration: float,
) -> Tuple[str, str, Optional[str]]:
    """
    构建 FFmpeg filter_complex 字符串。
    返回: (filter_complex, final_video_label, final_audio_label)
    """
    res_str = resolution.lower().replace('*', 'x')
    w, h = map(int, res_str.split('x'))
    fps_val = str(fps)
    
    n_clips = len(clips)
    has_voice = bool(voice_clip and vol_voice > 0)
    has_watermark = bool(watermark_path and os.path.exists(watermark_path))
    
    filter_complex = ""
    
    for i, clip in enumerate(clips):
        start = clip['start']
        dur = clip['duration']
        clip_has_audio = clip.get('has_audio', False)
        
        filter_complex += (
            f"[{i}:v]trim=start={start}:duration={dur},"
            f"setpts=PTS-STARTPTS,fps={fps_val},"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"setsar=1,format=yuv420p[v{i}]; "
        )
        
        if vol_orig > 0:
            if clip_has_audio:
                filter_complex += (
                    f"[{i}:a]atrim=start={start}:duration={dur},"
                    f"asetpts=PTS-STARTPTS,aresample=44100,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]; "
                )
            else:
                filter_complex += (
                    f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur}[a{i}]; "
                )
    
    if vol_orig > 0:
        concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n_clips)])
        filter_complex += (
            f"{concat_inputs}concat=n={n_clips}:v=1:a=1[vout_base][aout_orig]; "
            f"[aout_orig]volume={vol_orig}[aout_orig_v]; "
        )
    else:
        concat_inputs = "".join([f"[v{i}]" for i in range(n_clips)])
        filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=0[vout_base]; "
    
    current_v = "[vout_base]"
    
    if srt_path_safe:
        filter_complex += f"{current_v}subtitles='{srt_path_safe}'[v_sub]; "
        current_v = "[v_sub]"
    
    if has_watermark:
        wm_idx = n_clips + (1 if has_voice else 0) + 1  # 需要与实际输入索引匹配
        filter_complex += (
            f"{current_v}[{wm_idx}:v]overlay=(W-w)/2:(H-h)/2:shortest=1[v_wm]; "
        )
        current_v = "[v_wm]"
    
    bgm_idx = n_clips
    if vol_bgm > 0:
        filter_complex += (
            f"[{bgm_idx}:a]atrim=start={bgm_clip['start']}:duration={total_duration},"
            f"asetpts=PTS-STARTPTS,volume={vol_bgm}[aout_bgm_v]; "
        )
    
    voice_idx = n_clips + 1 if has_voice else -1
    if has_voice:
        filter_complex += (
            f"[{voice_idx}:a]atrim=start=0:duration={total_duration},"
            f"asetpts=PTS-STARTPTS,volume={vol_voice}[aout_voice_v]; "
        )
    
    mix_tracks = []
    if vol_orig > 0:
        mix_tracks.append("[aout_orig_v]")
    if vol_bgm > 0:
        mix_tracks.append("[aout_bgm_v]")
    if has_voice:
        mix_tracks.append("[aout_voice_v]")
    
    audio_map = None
    if len(mix_tracks) > 1:
        inputs_str = "".join(mix_tracks)
        filter_complex += (
            f"{inputs_str}amix=inputs={len(mix_tracks)}:"
            f"duration=longest:dropout_transition=2[aout]"
        )
        audio_map = "[aout]"
    elif len(mix_tracks) == 1:
        audio_map = mix_tracks[0]
    
    return filter_complex.strip('; '), current_v, audio_map


def run_process(command, is_cancelled=None, on_process=None, timeout=None, cwd=None):
    """Drain stderr without a pipe deadlock; cancellation also applies to base renders."""
    started = time.monotonic()
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                # Electron's parent watcher blocks on its stdin pipe. Sharing
                # that synchronous Windows handle can strand FFmpeg's keyboard
                # polling after it has already written the requested frame.
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors, cwd=cwd,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            if on_process:
                on_process(process)
            try:
                while process.poll() is None:
                    cancelled = is_cancelled and is_cancelled()
                    expired = timeout is not None and time.monotonic() - started > timeout
                    if cancelled or expired:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                        return False, '已停止' if cancelled else _failure_detail(errors, command, 'FFmpeg timeout')
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode == 0:
                    return True, None
                return False, _failure_detail(errors, command)
            finally:
                if on_process:
                    on_process(None)
    except OSError as exc:
        return False, str(exc)


def render_video(
    cmd_base: List[str],
    enable_gpu: bool,
    output_path: str,
    temp_dir: str,
    config=None,
    log=None,
    is_cancelled=None,
    on_process=None,
) -> Tuple[bool, Optional[str]]:
    """
    执行 FFmpeg 渲染。
    返回: (success, error_message)
    """
    from .hardware import session_for
    runtime = config if config is not None else {'enable_gpu': enable_gpu}
    return session_for(runtime, log).run(
        cmd_base, [output_path], '混剪', is_cancelled, on_process,
        runner=lambda command: run_process(command, is_cancelled, on_process, cwd=temp_dir),
    )
