import os
import sys
import json
import subprocess
import tempfile
import re
from typing import Optional, Tuple, List


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


def extract_media_info(info: dict, file_path: str) -> Tuple[float, bool, Optional[int], Optional[int], Optional[float]]:
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
    
    dur = max(0.0, dur - 0.2)
    return dur, has_audio, width, height, fps


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


def render_video(
    cmd_base: List[str],
    enable_gpu: bool,
    output_path: str,
    temp_dir: str,
) -> Tuple[bool, Optional[str]]:
    """
    执行 FFmpeg 渲染。
    返回: (success, error_message)
    """
    import uuid
    from datetime import datetime
    
    err_file = os.path.join(temp_dir, f"fferr_{uuid.uuid4().hex[:6]}.txt")
    
    if enable_gpu:
        cmd = cmd_base + ['-c:v', 'h264_nvenc', output_path]
    else:
        cmd = cmd_base + ['-c:v', 'libx264', '-preset', 'fast', output_path]
    
    try:
        with open(err_file, 'w', encoding='utf-8', errors='ignore') as err_f:
            proc = subprocess.Popen(
                cmd,
                cwd=temp_dir,
                stdout=subprocess.DEVNULL,
                stderr=err_f,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            proc.wait()
        
        if proc.returncode != 0:
            try:
                with open(err_file, 'r', encoding='utf-8', errors='ignore') as f:
                    err_text = f.read()
                log_path = os.path.join(os.getcwd(), "ffmpeg_error_log.txt")
                with open(log_path, 'a', encoding='utf-8', errors='ignore') as log_f:
                    log_f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 渲染失败\n")
                    log_f.write(f"执行命令: {' '.join(cmd)}\n")
                    log_f.write(f"FFmpeg 底层报错:\n{err_text}\n")
                    log_f.write("-" * 60 + "\n")
            except Exception:
                pass
            return False, err_text if 'err_text' in dir() else "FFmpeg 渲染失败"
        
        return True, None
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(err_file)
        except Exception:
            pass
