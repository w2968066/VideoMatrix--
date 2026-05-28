import os
import sys
import math
import random
import subprocess
import logging
import re
import json
import tempfile
import threading
import concurrent.futures
import time
import uuid
import ctypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

# ==========================================
# ffmpeg / ffprobe 路径解析（PyInstaller 兼容）
# ==========================================
def _find_tool(name):
    """在 exe 同目录、PyInstaller 临时目录、PATH 中查找工具。"""
    import sys as _sys
    # PyInstaller onefile 临时解压目录
    if getattr(_sys, 'frozen', False):
        meipass = getattr(_sys, '_MEIPASS', '')
        if meipass:
            p = os.path.join(meipass, name)
            if os.path.exists(p):
                return p
    # exe / 脚本同目录
    local = os.path.join(os.path.dirname(os.path.abspath(_sys.argv[0])), name)
    if os.path.exists(local):
        return local
    # PATH 兜底
    return name

FFMPEG  = _find_tool('ffmpeg.exe')
FFPROBE = _find_tool('ffprobe.exe')

# ==========================================
# Windows DWM Acrylic 毛玻璃
# ==========================================
class _ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_uint),
        ("AccentFlags", ctypes.c_uint),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_uint),
    ]

class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENTPOLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]

def _enable_acrylic(hwnd, tint=0xCC0B0B12):
    if sys.platform != 'win32':
        return
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        a = _ACCENTPOLICY()
        a.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
        a.GradientColor = tint
        d = _WINCOMPATTRDATA()
        d.Attribute = 19   # WCA_ACCENT_POLICY
        d.SizeOfData = ctypes.sizeof(a)
        d.Data = ctypes.pointer(a)
        fn(hwnd, ctypes.pointer(d))
    except Exception:
        pass

def _disable_acrylic(hwnd):
    if sys.platform != 'win32':
        return
    try:
        fn = ctypes.windll.user32.SetWindowCompositionAttribute
        a = _ACCENTPOLICY()
        a.AccentState = 0  # ACCENT_DISABLED
        d = _WINCOMPATTRDATA()
        d.Attribute = 19
        d.SizeOfData = ctypes.sizeof(a)
        d.Data = ctypes.pointer(a)
        fn(hwnd, ctypes.pointer(d))
    except Exception:
        pass

def _tk_hwnd(root):
    if sys.platform != 'win32':
        return None
    try:
        root.update_idletasks()
        return ctypes.windll.user32.GetParent(root.winfo_id())
    except Exception:
        return None

# ==========================================
# "Darkroom Amber" 配色 —— 支持 Dark/Light 切换
# ==========================================
class C:
    def __init__(self):
        self.set_dark()

    def set_dark(self):
        self.BG         = "#0B0B12"
        self.SURFACE    = "#16161E"
        self.SURFACE2   = "#1C1C26"
        self.BORDER     = "#282836"
        self.TEXT       = "#E2E2EA"
        self.TEXT_DIM   = "#787886"
        self.ACCENT     = "#C8872A"
        self.ACCENT_H   = "#DCA040"
        self.ACCENT_BG  = "#1E180C"
        self.INPUT_BG   = "#0F0F16"
        self.INPUT_BD   = "#242430"
        self.DANGER     = "#C04A3A"
        self.DANGER_H   = "#D46050"
        self.LOG_BG     = "#08080E"

    def set_light(self):
        self.BG         = "#F5F3F2"
        self.SURFACE    = "#FAFAF8"
        self.SURFACE2   = "#F0EFEC"
        self.BORDER     = "#D8D4CF"
        self.TEXT       = "#1C1C1A"
        self.TEXT_DIM   = "#787670"
        self.ACCENT     = "#4A9A6E"
        self.ACCENT_H   = "#3D8A5E"
        self.ACCENT_BG  = "#EDF5F0"
        self.INPUT_BG   = "#FAFAF8"
        self.INPUT_BD   = "#C8C4BE"
        self.DANGER     = "#C04A3A"
        self.DANGER_H   = "#D46050"
        self.LOG_BG     = "#F2F0ED"

C = C()

# ==========================================
# 全局线程安全缓存管理
# ==========================================
class SharedMediaCache:
    def __init__(self):
        self.media_cache = {}
        self.usage_history = set()
        self.lock = threading.Lock()
        self.load_state()

    def load_state(self):
        if os.path.exists('media_cache.json'):
            try:
                with open('media_cache.json', 'r', encoding='utf-8') as f:
                    self.media_cache = json.load(f)
            except: pass
        if os.path.exists('usage_history.json'):
            try:
                with open('usage_history.json', 'r', encoding='utf-8') as f:
                    self.usage_history = set(json.load(f))
            except: pass

    def save_state(self):
        with self.lock:
            try:
                with open('media_cache.json', 'w', encoding='utf-8') as f:
                    json.dump(self.media_cache, f, ensure_ascii=False)
                with open('usage_history.json', 'w', encoding='utf-8') as f:
                    json.dump(list(self.usage_history), f, ensure_ascii=False)
            except Exception: pass

    def clear_history(self):
        with self.lock:
            self.usage_history.clear()
            if os.path.exists('usage_history.json'):
                os.remove('usage_history.json')

# ==========================================
# 核心逻辑模块：单库视频处理实例
# ==========================================
class VideoMatrixCore:
    def __init__(self, config, log_callback, shared_cache):
        self.config = config
        self.log = log_callback
        self.shared = shared_cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_dir_path = self.temp_dir.name
        
        self.task_name = config.get('task_name', 'SingleTask')
        self.hook_pool = []
        self.body_pool = []
        self.bgm_pool = []
        self.voice_pool = []
        self.n_total = 0
        
        self.is_running = True
        self.current_process = None
        self.core_lock = threading.Lock()

    # 核心新增：递归深度扫描，无视多少层子文件夹，统统挖出素材
    def _scan_files(self, dir_path, exts):
        if not dir_path or not os.path.exists(dir_path): return []
        res = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.lower().endswith(exts):
                    res.append(os.path.join(root, f))
        return res

    def _run_cmd(self, cmd, capture_output=True):
        try:
            return subprocess.run(
                cmd, stdout=subprocess.PIPE if capture_output else None,
                stderr=subprocess.PIPE if capture_output else None, text=True,
                encoding='utf-8', errors='ignore',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
        except Exception: return None

    def probe_media(self, file_path):
        try:
            mtime = os.path.getmtime(file_path)
            with self.shared.lock:
                if file_path in self.shared.media_cache and self.shared.media_cache[file_path].get('mtime') == mtime:
                    return file_path, self.shared.media_cache[file_path]['dur'], self.shared.media_cache[file_path]['has_audio']
        except: pass

        cmd = [FFPROBE, '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file_path]
        res = self._run_cmd(cmd)
        if res and res.returncode == 0:
            try:
                info = json.loads(res.stdout)
                dur = float(info['format'].get('duration', 0.0))
                has_audio = False
                
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
                                vd = int(h)*3600 + int(m)*60 + float(sec)
                                dur = min(dur, vd)
                
                dur = max(0.0, dur - 0.2)

                with self.shared.lock:
                    self.shared.media_cache[file_path] = {'mtime': mtime, 'dur': dur, 'has_audio': has_audio}
                return file_path, dur, has_audio
            except Exception as e:
                self.log(f"[{self.task_name}] 解析时长出错 -> {str(e)}")
                
        self.log(f"[{self.task_name}] WARNING: 素材损坏或无法读取 -> {os.path.basename(file_path)}")
        return file_path, 0.0, False

    def parse_time_to_ms(self, t_str):
        h, m, s, ms = map(int, re.split('[:,]', t_str.replace('.', ',').strip()))
        return (h * 3600 + m * 60 + s) * 1000 + ms

    def format_ms_to_time(self, ms):
        h, ms = int(ms // 3600000), ms % 3600000
        m, ms = int(ms // 60000), ms % 60000
        s, ms = int(ms // 1000), int(ms % 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def process_srt(self, srt_dir, duration_sec):
        srt_files = self._scan_files(srt_dir, ('.srt',))
        if not srt_files: return None
        srt_path = random.choice(srt_files)
        try:
            with open(srt_path, 'r', encoding='utf-8-sig') as f: content = f.read()
        except:
            try:
                with open(srt_path, 'r', encoding='gbk') as f: content = f.read()
            except: return None

        slice_start_ms, slice_end_ms = 0, int(duration_sec * 1000)
        new_subs, index = [], 1
        blocks = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)', re.DOTALL).findall(content + "\n\n")

        for _, t_start_str, t_end_str, text in blocks:
            t_start, t_end = self.parse_time_to_ms(t_start_str), self.parse_time_to_ms(t_end_str)
            if t_end > slice_start_ms and t_start < slice_end_ms:
                new_start, new_end = max(0, t_start - slice_start_ms), min(duration_sec * 1000, t_end - slice_start_ms)
                new_subs.append(f"{index}\n{self.format_ms_to_time(new_start)} --> {self.format_ms_to_time(new_end)}\n{text.strip()}")
                index += 1

        if not new_subs: return None
        temp_srt_path = os.path.join(self.temp_dir_path, f"temp_{uuid.uuid4().hex[:8]}.srt")
        with open(temp_srt_path, 'w', encoding='utf-8') as f: f.write("\n\n".join(new_subs) + "\n\n")
        return temp_srt_path.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")

    def pre_flight_check(self):
        self.hook_pool.clear()
        self.body_pool.clear()
        self.bgm_pool.clear()
        self.voice_pool.clear()
        
        cfg = self.config
        t_hook = cfg['t_hook']
        t_body = cfg['t_body']
        total_clips = cfg['total_clips']
        t_total = t_hook + t_body * (total_clips - 1)
        
        # 替换为深度扫描引擎
        hook_files = self._scan_files(cfg['hook_dir'], ('.mp4', '.mov'))
        body_files = []
        for bd in cfg['body_dirs']:
            body_files.extend(self._scan_files(bd, ('.mp4', '.mov')))
        bgm_files = self._scan_files(cfg['bgm_dir'], ('.mp3', '.wav'))
        
        probe_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            all_media = list(set(hook_files + body_files + bgm_files))
            futures = {executor.submit(self.probe_media, f): f for f in all_media}
            for future in concurrent.futures.as_completed(futures):
                if not self.is_running: return False, "已停止"
                f_path, dur, has_audio = future.result()
                probe_results[f_path] = (dur, has_audio)
        
        self.shared.save_state()

        for f in hook_files:
            dur, has_audio = probe_results[f]
            if dur >= t_hook:
                if cfg['hook_r'] >= 0.99:
                    self.hook_pool.append({'file': f, 'start': 0.0, 'duration': t_hook, 'has_audio': has_audio})
                else:
                    step = max(t_hook * (1 - cfg['hook_r']), 0.1)
                    n = int(math.floor((dur - t_hook) / step)) + 1
                    for i in range(n):
                        hook_id = f"{f}_{i*step:.2f}"
                        if hook_id not in self.shared.usage_history:
                            self.hook_pool.append({'file': f, 'start': i * step, 'duration': t_hook, 'has_audio': has_audio, 'id': hook_id})
                            
        for f in body_files:
            dur, has_audio = probe_results[f]
            if dur >= t_body:
                step = max(t_body * (1 - cfg['body_r']), 0.1)
                n = int(math.floor((dur - t_body) / step)) + 1
                for i in range(n):
                    self.body_pool.append({'file': f, 'start': i * step, 'duration': t_body, 'has_audio': has_audio})

        if not self.hook_pool: return False, f"库 [{self.task_name}] 首段素材耗尽或无合规视频。"
        
        if cfg['hook_r'] >= 0.99:
            self.n_total = "无限"
        else:
            self.n_total = len(self.hook_pool)
            if self.n_total == 0:
                return False, f"库 [{self.task_name}] 首段剩余 0 个，无法生产。"
            if self.n_total < cfg['target_count']: 
                self.log(f"[{self.task_name}] ⚠️ 提示: 首段仅剩 {self.n_total} 个片段，已自动下调目标产量。")
                cfg['target_count'] = self.n_total
                
        if len(self.body_pool) < total_clips - 1: return False, f"库 [{self.task_name}] 后段素材不足拼凑 1 个视频。"

        for f in bgm_files:
            dur, _ = probe_results[f]
            if dur >= t_total:
                step = max(t_total * (1 - cfg['bgm_r']), 0.1)
                n = int(math.floor((dur - t_total) / step)) + 1
                for i in range(n):
                    self.bgm_pool.append({'file': f, 'start': i * step, 'duration': t_total})
        if not self.bgm_pool: return False, "BGM 库素材时长不足。"

        if cfg['voice_dir'] and os.path.exists(cfg['voice_dir']):
            voice_files = self._scan_files(cfg['voice_dir'], ('.mp3', '.wav'))
            for f in voice_files: self.voice_pool.append({'file': f})

        random.shuffle(self.hook_pool)
        return True, "预检通过"

    def execute_ffmpeg(self, cmd_list):
        try:
            err_file = os.path.join(self.temp_dir_path, f"fferr_{uuid.uuid4().hex[:6]}.txt")
            with open(err_file, 'w', encoding='utf-8', errors='ignore') as err_f:
                self.current_process = subprocess.Popen(
                    cmd_list, cwd=self.temp_dir_path,
                    stdout=subprocess.DEVNULL, stderr=err_f,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                self.current_process.wait()
            if self.current_process.returncode != 0:
                try:
                    with open(err_file, 'r', encoding='utf-8', errors='ignore') as f:
                        err_text = f.read()
                    log_path = os.path.join(os.getcwd(), "ffmpeg_error_log.txt")
                    with open(log_path, 'a', encoding='utf-8', errors='ignore') as log_f:
                        log_f.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 渲染失败\n")
                        log_f.write(f"执行命令: {' '.join(cmd_list)}\n")
                        log_f.write(f"FFmpeg 底层报错:\n{err_text}\n")
                        log_f.write("-" * 60 + "\n")
                except Exception:
                    pass
                finally:
                    try: os.remove(err_file)
                    except Exception: pass
                return False
            try: os.remove(err_file)
            except Exception: pass
            return True
        except Exception:
            return False

    def render_single_video(self, task_idx):
        if not self.is_running: return False
        
        now_str_start = datetime.now().strftime("%H:%M:%S")
        self.log(f"  [{now_str_start}] [{self.task_name}] 正在拼装 视频 {task_idx:03d} ...")
        
        start_time = time.time()
        cfg = self.config
        t_total = cfg['t_hook'] + cfg['t_body'] * (cfg['total_clips'] - 1)
        
        with self.core_lock:
            if not self.hook_pool: return False
            if cfg['hook_r'] >= 0.99: hook_clip = random.choice(self.hook_pool) 
            else: hook_clip = self.hook_pool.pop() 
            
            if len(self.body_pool) >= (cfg['total_clips'] - 1): body_clips = random.sample(self.body_pool, cfg['total_clips'] - 1)
            else: body_clips = random.choices(self.body_pool, k=cfg['total_clips'] - 1)
                
            bgm_clip = random.choice(self.bgm_pool)
            voice_clip = random.choice(self.voice_pool) if self.voice_pool else None

        temp_srt_path_safe = None
        if cfg.get('enable_srt') and cfg['srt_dir'] and os.path.exists(cfg['srt_dir']):
            temp_srt_path_safe = self.process_srt(cfg['srt_dir'], t_total)

        res_str = cfg['resolution'].lower().replace('*', 'x')
        w, h = map(int, res_str.split('x'))
        v_orig, v_bgm, v_voice = cfg['vol_orig'] / 100.0, cfg['vol_bgm'] / 100.0, cfg['vol_voice'] / 100.0
        fps_val = str(cfg['fps'])
        
        has_voice = bool(voice_clip and v_voice > 0)
        has_watermark = bool(cfg.get('watermark_path') and os.path.exists(cfg['watermark_path']))
        
        inputs = [hook_clip['file']] + [b['file'] for b in body_clips] + [bgm_clip['file']]
        
        voice_idx = -1
        if has_voice:
            inputs.append(voice_clip['file'])
            voice_idx = len(inputs) - 1 

        watermark_idx = -1
        if has_watermark:
            inputs.append(cfg['watermark_path'])
            watermark_idx = len(inputs) - 1

        cmd = [FFMPEG, '-y']
        for idx, inp in enumerate(inputs):
            if idx == watermark_idx:
                ext = inp.lower().split('.')[-1]
                if ext == 'gif':
                    cmd.extend(['-ignore_loop', '0', '-i', inp])
                elif ext in ['png', 'jpg', 'jpeg']:
                    cmd.extend(['-loop', '1', '-i', inp]) 
                else:
                    cmd.extend(['-i', inp])
            else:
                cmd.extend(['-i', inp])

        filter_complex = ""
        for i in range(cfg['total_clips']):
            clip = hook_clip if i == 0 else body_clips[i-1]
            start, dur, clip_has_audio = clip['start'], clip['duration'], clip.get('has_audio', False)
            
            filter_complex += f"[{i}:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,fps={fps_val},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p[v{i}]; "
            
            if v_orig > 0:
                if clip_has_audio: filter_complex += f"[{i}:a]atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS,aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]; "
                else: filter_complex += f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur}[a{i}]; "

        if v_orig > 0:
            concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(cfg['total_clips'])])
            filter_complex += f"{concat_inputs}concat=n={cfg['total_clips']}:v=1:a=1[vout_base][aout_orig]; [aout_orig]volume={v_orig}[aout_orig_v]; "
        else:
            concat_inputs = "".join([f"[v{i}]" for i in range(cfg['total_clips'])])
            filter_complex += f"{concat_inputs}concat=n={cfg['total_clips']}:v=1:a=0[vout_base]; "

        current_v = "[vout_base]"
        if temp_srt_path_safe:
            filter_complex += f"{current_v}subtitles='{temp_srt_path_safe}'[v_sub]; "
            current_v = "[v_sub]"
        if has_watermark:
            filter_complex += f"{current_v}[{watermark_idx}:v]overlay=(W-w)/2:(H-h)/2:shortest=1[v_wm]; "
            current_v = "[v_wm]"

        bgm_idx = cfg['total_clips'] 
        if v_bgm > 0: filter_complex += f"[{bgm_idx}:a]atrim=start={bgm_clip['start']}:duration={t_total},asetpts=PTS-STARTPTS,volume={v_bgm}[aout_bgm_v]; "
        if has_voice: filter_complex += f"[{voice_idx}:a]atrim=start=0:duration={t_total},asetpts=PTS-STARTPTS,volume={v_voice}[aout_voice_v]; "

        mix_tracks = []
        if v_orig > 0: mix_tracks.append("[aout_orig_v]")
        if v_bgm > 0: mix_tracks.append("[aout_bgm_v]")
        if has_voice: mix_tracks.append("[aout_voice_v]")

        audio_map = None
        if len(mix_tracks) > 1:
            inputs_str = "".join(mix_tracks)
            filter_complex += f"{inputs_str}amix=inputs={len(mix_tracks)}:duration=longest:dropout_transition=2[aout]"
            audio_map = "[aout]"
        elif len(mix_tracks) == 1:
            audio_map = mix_tracks[0]

        filter_complex = filter_complex.strip('; ')
        out_name = f"{self.task_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{task_idx:03d}.mp4"
        out_path = os.path.join(cfg['out_dir'], out_name)

        cmd.extend(['-filter_complex', filter_complex, '-map', current_v])
        if audio_map: cmd.extend(['-map', audio_map, '-c:a', 'aac'])
        cmd.extend(['-r', fps_val, '-b:v', cfg['bitrate']])

        cmd_nvenc = cmd + ['-c:v', 'h264_nvenc', out_path]
        success = self.execute_ffmpeg(cmd_nvenc)
        
        if not success and self.is_running:
            cmd_x264 = cmd + ['-c:v', 'libx264', '-preset', 'fast', out_path]
            success = self.execute_ffmpeg(cmd_x264)

        if success and self.is_running:
            if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
                now_str = datetime.now().strftime("%H:%M:%S")
                self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 输出异常（文件过小或不存在），可能编码失败")
                return False
            if 'id' in hook_clip:
                with self.shared.lock: self.shared.usage_history.add(hook_clip['id'])
                self.shared.save_state()
            elapsed_time = time.time() - start_time
            now_str = datetime.now().strftime("%H:%M:%S")
            self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 完成，耗时 {elapsed_time:.1f} 秒 -> {out_name}")
            return True
        return False

    def stop(self):
        self.is_running = False
        if self.current_process:
            try: self.current_process.terminate()
            except: pass


# ==========================================
# GUI 模块 —— "Darkroom Amber" 暗房钨金
# ==========================================
class AppUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VideoMatrix")
        self.geometry("980x840")
        self.minsize(860, 720)
        self.configure(bg=C.BG)
        self._theme = 'dark'

        self.shared_cache = SharedMediaCache()
        self.active_cores = []
        self.cores_lock = threading.Lock()
        self.is_pipeline_running = False
        self.config_file = "config.json"

        self.vars = {
            'hook_dir': tk.StringVar(), 'body_dir': tk.StringVar(), 'bgm_dir': tk.StringVar(),
            'voice_dir': tk.StringVar(), 'srt_dir': tk.StringVar(),
            'enable_srt': tk.BooleanVar(value=False), 'watermark_path': tk.StringVar(),
            'base_out_dir': tk.StringVar(),
            'concurrent_tasks': tk.IntVar(value=3),
            't_hook': tk.DoubleVar(value=3.0), 't_body': tk.DoubleVar(value=5.0),
            'total_clips': tk.IntVar(value=3), 'target_count': tk.IntVar(value=10),
            'hook_r': tk.DoubleVar(value=0.3), 'body_r': tk.DoubleVar(value=0.2),
            'bgm_r': tk.DoubleVar(value=0.3),
            'resolution': tk.StringVar(value="1080x1920"), 'res_tag': tk.StringVar(value="1080P"),
            'fps': tk.StringVar(value="30"), 'bitrate': tk.StringVar(value="8000k"),
            'vol_orig': tk.IntVar(value=0), 'vol_bgm': tk.IntVar(value=100),
            'vol_voice': tk.IntVar(value=100)
        }

        self.vars['resolution'].trace_add('write', self.update_res_tag)

        self._init_fonts()
        self._try_acrylic()
        self.create_widgets()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.load_config()

        self.update_idletasks()
        x = (self.winfo_screenwidth() - 980) // 2
        y = (self.winfo_screenheight() - 840) // 2
        self.geometry(f"+{x}+{y}")

    def _init_fonts(self):
        self.F_TITLE   = ("Segoe UI", 15, "bold")
        self.F_CONTACT = ("Segoe UI", 11, "bold")
        self.F_SECTION = ("Segoe UI", 9, "bold")
        self.F_BODY    = ("Segoe UI", 9)
        self.F_SMALL   = ("Segoe UI", 8)
        self.F_MONO    = ("Cascadia Code", 9)
        self.F_BTN     = ("Segoe UI", 9, "bold")
        self.F_BTN_SM  = ("Segoe UI", 8, "bold")

    def _try_acrylic(self):
        if sys.platform != 'win32':
            return
        self.update_idletasks()
        hwnd = _tk_hwnd(self)
        if not hwnd:
            return
        # light 模式下关闭毛玻璃，避免过曝
        if self._theme == 'dark':
            self.after(100, lambda: _enable_acrylic(hwnd, 0xCC0B0B12))
        else:
            self.after(100, lambda: _disable_acrylic(hwnd))
        try:
            DWMWA_IMMERSIVE_DARK = 20
            v = ctypes.c_int(1 if self._theme == 'dark' else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_IMMERSIVE_DARK,
                ctypes.byref(v), ctypes.sizeof(v))
        except: pass

    def _toggle_theme(self):
        """切换 Dark / Light 配色并重建 UI。"""
        log_text = self.txt_log.get(1.0, tk.END)
        running = self.is_pipeline_running

        if self._theme == 'dark':
            C.set_light()
            self._theme = 'light'
        else:
            C.set_dark()
            self._theme = 'dark'

        # 销毁全部子控件后重建
        for child in self.winfo_children():
            child.destroy()
        self.create_widgets()

        # 还原日志
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, log_text)
        self.txt_log.config(state=tk.DISABLED)

        # 还原运行状态
        if running:
            self.toggle_ui_state(running=True)

        # 重建后重刷毛玻璃
        self._try_acrylic()

    # ===== 基础组件 =====

    def _input(self, parent, var, width=None):
        """暗色输入框,聚焦时琥珀色边框。"""
        outer = tk.Frame(parent, bg=C.INPUT_BD, padx=1, pady=1)
        e = tk.Entry(outer, textvariable=var, font=self.F_BODY,
                     fg=C.TEXT, bg=C.INPUT_BG, insertbackground=C.ACCENT,
                     relief="flat", bd=0, highlightthickness=0)
        if width: e.configure(width=width)
        e.pack(fill=tk.BOTH, expand=True, ipady=2, padx=3, pady=2)
        e.bind("<FocusIn>",  lambda ev: outer.configure(bg=C.ACCENT))
        e.bind("<FocusOut>", lambda ev: outer.configure(bg=C.INPUT_BD))
        return outer

    def _btn_mini(self, parent, text, cmd):
        b = tk.Button(parent, text=text, font=self.F_SMALL,
                      fg=C.TEXT_DIM, bg=C.SURFACE, bd=1,
                      activebackground=C.BORDER, activeforeground=C.TEXT,
                      relief="solid", padx=8, pady=2, width=5, cursor="hand2",
                      highlightbackground=C.BORDER, highlightcolor=C.ACCENT,
                      command=cmd)
        b.bind("<Enter>", lambda e: b.configure(fg=C.TEXT))
        b.bind("<Leave>", lambda e: b.configure(fg=C.TEXT_DIM))
        return b

    def _btn(self, parent, text, cmd, danger=False):
        if danger:
            fg, bg, hbg = "#FFF", C.DANGER, C.DANGER_H
        else:
            fg, bg, hbg = "#0E0E0E", C.ACCENT, C.ACCENT_H
        b = tk.Button(parent, text=text, font=self.F_BTN,
                      fg=fg, bg=bg, bd=0, activebackground=hbg,
                      activeforeground=fg, relief="flat",
                      padx=18, pady=6, cursor="hand2", command=cmd)
        b.bind("<Enter>", lambda e: b.configure(bg=hbg))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def _card(self, parent, title, pady=(0, 4)):
        outer = tk.Frame(parent, bg=C.SURFACE)
        outer.pack(fill=tk.X, pady=pady)
        # header
        hdr = tk.Frame(outer, bg=C.SURFACE, height=24)
        hdr.pack(fill=tk.X, padx=10, pady=(8, 0))
        hdr.pack_propagate(False)
        tk.Frame(hdr, bg=C.ACCENT, width=3, height=10).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(hdr, text=title, font=self.F_SECTION, fg=C.TEXT,
                 bg=C.SURFACE, anchor="w").pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # separator
        tk.Frame(outer, bg=C.BORDER, height=1).pack(fill=tk.X, padx=10, pady=(4, 0))
        # content
        inner = tk.Frame(outer, bg=C.SURFACE)
        inner.pack(fill=tk.X, padx=10, pady=(4, 8))
        return inner

    # ===== 界面构建 =====

    def create_widgets(self):
        main = tk.Frame(self, bg=C.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 8))
        main.grid_rowconfigure(0, weight=0)    # title
        main.grid_rowconfigure(1, weight=0)    # cards
        main.grid_rowconfigure(2, weight=35)   # log — 保证 >=30%
        main.grid_columnconfigure(0, weight=1)

        # -- 标题栏 + 智能压测(右上角) --
        hdr = tk.Frame(main, bg=C.BG, height=32)
        hdr.grid(row=0, column=0, sticky=tk.EW, pady=(0, 2))
        hdr.pack_propagate(False)
        tk.Frame(hdr, bg=C.ACCENT, width=3, height=16).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(hdr, text="VideoMatrix", font=self.F_TITLE,
                 fg=C.TEXT, bg=C.BG).pack(side=tk.LEFT)
        tk.Label(hdr, text="v1.5.1", font=self.F_SMALL,
                 fg=C.TEXT_DIM, bg=C.BG).pack(side=tk.LEFT, padx=(6, 0), pady=(5, 0))
        tk.Label(hdr, text="VX：18667026883", font=self.F_CONTACT,
                 fg="#0E0E0E", bg=C.ACCENT, padx=12, pady=3).pack(
                     side=tk.LEFT, padx=(14, 0), pady=(2, 0))
        # 不常用操作丢右上角
        self._theme_btn = tk.Button(hdr, text="Light", font=self.F_SMALL,
                                    fg=C.TEXT_DIM, bg=C.SURFACE2, bd=0,
                                    activebackground=C.BORDER, activeforeground=C.TEXT,
                                    relief="flat", padx=10, pady=2, cursor="hand2",
                                    command=self._toggle_theme)
        self._theme_btn.pack(side=tk.RIGHT, padx=(0, 4))

        self.btn_bench = tk.Button(hdr, text="智能压测", font=self.F_SMALL,
                                   fg=C.TEXT_DIM, bg=C.SURFACE2, bd=0,
                                   activebackground=C.BORDER, activeforeground=C.TEXT,
                                   relief="flat", padx=10, pady=2, cursor="hand2",
                                   command=self.run_benchmark)
        self.btn_bench.pack(side=tk.RIGHT, padx=(0, 0))
        tk.Frame(main, bg=C.BORDER, height=1).grid(row=0, column=0, sticky=tk.EW, pady=(32, 4))

        # -- 卡片区（不滚动，紧凑排列） --
        cards_frame = tk.Frame(main, bg=C.BG)
        cards_frame.grid(row=1, column=0, sticky=tk.EW)

        # ===== Card 1: 素材目录 =====
        c1 = self._card(cards_frame, "素材目录")
        c1.grid_columnconfigure(1, weight=1)

        rows = [
            ("首段父目录 Hook",  'hook_dir',      'dir'),
            ("后段公共池 Body",  'body_dir',       'multi'),
            ("全局音乐库 BGM",   'bgm_dir',        'dir'),
            ("全局配音库 Voice", 'voice_dir',      'dir'),
            ("字幕库 SRT",       'srt_dir',        'dir'),
            ("水印图片 / GIF",   'watermark_path', 'file'),
            ("导出父目录 Output",'base_out_dir',   'dir'),
        ]
        for i, (label, var, mode) in enumerate(rows):
            # 硬字幕开关放到标签左侧
            if var == 'srt_dir':
                cb_frame = tk.Frame(c1, bg=C.SURFACE)
                cb_frame.grid(row=i, column=0, padx=(0, 6), pady=2, sticky=tk.E)
                tk.Checkbutton(cb_frame, text="硬字幕", variable=self.vars['enable_srt'],
                               fg=C.TEXT_DIM, bg=C.SURFACE, selectcolor=C.INPUT_BG,
                               activebackground=C.SURFACE, activeforeground=C.ACCENT,
                               font=self.F_SMALL, bd=0, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 4))
                tk.Label(cb_frame, text=label, font=self.F_BODY, fg=C.TEXT_DIM,
                         bg=C.SURFACE, anchor="e").pack(side=tk.RIGHT)
            else:
                tk.Label(c1, text=label, font=self.F_BODY, fg=C.TEXT_DIM,
                         bg=C.SURFACE, anchor="e", width=16).grid(
                    row=i, column=0, padx=(0, 6), pady=2, sticky=tk.E)

            self._input(c1, self.vars[var]).grid(
                row=i, column=1, padx=(0, 4), pady=2, sticky=tk.EW)

            # 按钮区左对齐 — 第0列=动作按钮, 第1列=打开按钮
            bf = tk.Frame(c1, bg=C.SURFACE)
            bf.grid(row=i, column=2, pady=2, sticky=tk.W)

            if mode == 'multi':
                self._btn_mini(bf, "追加", lambda v=var: self.append_multi_dir(v)).grid(row=0, column=0, padx=(0, 4))
            elif mode == 'file':
                self._btn_mini(bf, "选择", lambda v=var: self.browse_file(v)).grid(row=0, column=0, padx=(0, 4))
            else:
                self._btn_mini(bf, "浏览", lambda v=var: self.browse_dir(v)).grid(row=0, column=0, padx=(0, 4))
            if mode != 'file':
                self._btn_mini(bf, "打开", lambda v=var: self.open_folder_in_os(v)).grid(row=0, column=1)

        # ===== Card 2: 参数(65%) + 操作按钮(35%) =====
        c2 = self._card(cards_frame, "视频参数与操作")

        # 用 grid 控制比例: 参数区 65%, 分隔线固定, 按钮区 35%
        c2.grid_columnconfigure(0, weight=65)
        c2.grid_columnconfigure(1, weight=0)
        c2.grid_columnconfigure(2, weight=35)
        c2.grid_rowconfigure(0, weight=1)

        # 左: 参数三列
        params_area = tk.Frame(c2, bg=C.SURFACE)
        params_area.grid(row=0, column=0, sticky=tk.NSEW)

        col_l = tk.Frame(params_area, bg=C.SURFACE)
        col_l.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        for i, (lbl, var, w) in enumerate([
            ("首段时长(秒)", 't_hook', 5),
            ("后段时长(秒)", 't_body', 5),
            ("总片段数",    'total_clips', 5),
            ("单库生成量",  'target_count', 5),
            ("并行渲染数",  'concurrent_tasks', 5),
        ]):
            tk.Label(col_l, text=lbl, font=self.F_BODY, fg=C.TEXT_DIM,
                     bg=C.SURFACE, anchor="w").grid(
                row=i, column=0, padx=(0, 4), pady=2, sticky=tk.W)
            self._input(col_l, self.vars[var], width=w).grid(
                row=i, column=1, pady=2, sticky=tk.W)

        col_m = tk.Frame(params_area, bg=C.SURFACE)
        col_m.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        for i, (lbl, var) in enumerate([
            ("Hook重合阈值", 'hook_r'),
            ("Body重合阈值", 'body_r'),
            ("BGM重合阈值",  'bgm_r'),
        ]):
            tk.Label(col_m, text=lbl, font=self.F_BODY, fg=C.TEXT_DIM,
                     bg=C.SURFACE, anchor="w").grid(
                row=i, column=0, padx=(0, 4), pady=2, sticky=tk.W)
            self._input(col_m, self.vars[var], width=5).grid(
                row=i, column=1, pady=2, sticky=tk.W)

        col_r = tk.Frame(params_area, bg=C.SURFACE)
        col_r.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)

        r = 0
        tk.Label(col_r, text="分辨率", font=self.F_BODY, fg=C.TEXT_DIM,
                 bg=C.SURFACE, anchor="w").grid(row=r, column=0, padx=(0, 4), pady=2, sticky=tk.W)
        rf = tk.Frame(col_r, bg=C.SURFACE)
        rf.grid(row=r, column=1, pady=2, sticky=tk.W)
        self._input(rf, self.vars['resolution'], width=8).pack(side=tk.LEFT)
        tk.Label(rf, textvariable=self.vars['res_tag'], font=self.F_SMALL,
                 fg=C.ACCENT, bg=C.SURFACE).pack(side=tk.LEFT, padx=4)
        r += 1

        for lbl, var in [("帧率FPS", 'fps'), ("码率", 'bitrate')]:
            tk.Label(col_r, text=lbl, font=self.F_BODY, fg=C.TEXT_DIM,
                     bg=C.SURFACE, anchor="w").grid(
                row=r, column=0, padx=(0, 4), pady=2, sticky=tk.W)
            self._input(col_r, self.vars[var], width=6).grid(
                row=r, column=1, pady=2, sticky=tk.W)
            r += 1

        tk.Frame(col_r, bg=C.BORDER, height=1).grid(
            row=r, column=0, columnspan=2, sticky=tk.EW, pady=(4, 2))
        r += 1

        for lbl, var in [("原声音量%", 'vol_orig'),
                         ("BGM音量%",  'vol_bgm'),
                         ("配音音量%", 'vol_voice')]:
            tk.Label(col_r, text=lbl, font=self.F_BODY, fg=C.TEXT_DIM,
                     bg=C.SURFACE, anchor="w").grid(
                row=r, column=0, padx=(0, 4), pady=1, sticky=tk.W)
            self._input(col_r, self.vars[var], width=4).grid(
                row=r, column=1, pady=1, sticky=tk.W)
            r += 1

        # 竖分隔线
        sep_v = tk.Frame(c2, bg=C.BORDER, width=1)
        sep_v.grid(row=0, column=1, sticky=tk.NS, padx=10)

        # 右: 操作按钮竖排(35%, 居中)
        btn_col = tk.Frame(c2, bg=C.SURFACE)
        btn_col.grid(row=0, column=2, sticky=tk.NSEW)
        btn_col.grid_columnconfigure(0, weight=1)
        btn_col.grid_rowconfigure(0, weight=1)
        btn_col.grid_rowconfigure(5, weight=1)

        # 按钮容器,水平居中
        btn_inner = tk.Frame(btn_col, bg=C.SURFACE)
        btn_inner.grid(row=1, column=0, rowspan=4)

        self.btn_pre_flight   = self._btn(btn_inner, "预检产能", self.run_pre_flight_only)
        self.btn_start        = self._btn(btn_inner, "启动渲染", self.start_pipeline)
        self.btn_clear_history = self._btn(btn_inner, "清除记录", self.clear_usage_history)
        self.btn_stop         = self._btn(btn_inner, "停止", self.stop_pipeline, danger=True)

        for b in [self.btn_pre_flight, self.btn_start, self.btn_clear_history, self.btn_stop]:
            b.pack(fill=tk.X, pady=3)
        self.btn_stop.config(state=tk.DISABLED)

        # ===== 日志区 — 独立构建以支持垂直扩展 =====
        c4_outer = tk.Frame(main, bg=C.SURFACE)
        c4_outer.grid(row=2, column=0, sticky=tk.NSEW, pady=(4, 0))

        hdr = tk.Frame(c4_outer, bg=C.SURFACE, height=24)
        hdr.pack(fill=tk.X, padx=10, pady=(8, 0))
        hdr.pack_propagate(False)
        tk.Frame(hdr, bg=C.ACCENT, width=3, height=10).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(hdr, text="运行日志", font=self.F_SECTION, fg=C.TEXT,
                 bg=C.SURFACE, anchor="w").pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Frame(c4_outer, bg=C.BORDER, height=1).pack(fill=tk.X, padx=10, pady=(4, 0))

        lf = tk.Frame(c4_outer, bg=C.BORDER, padx=1, pady=1)
        lf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 8))

        self.txt_log = tk.Text(lf, font=self.F_MONO,
                               fg=C.TEXT, bg=C.LOG_BG,
                               insertbackground=C.ACCENT,
                               relief="flat", bd=0, highlightthickness=0,
                               padx=10, pady=8, state=tk.DISABLED, wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True)

        ls = ttk.Scrollbar(self.txt_log, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=ls.set)
        ls.pack(side=tk.RIGHT, fill=tk.Y)

    # ========================================================
    # 以下方法逻辑与原始版本完全一致
    # ========================================================

    def load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved_data = json.load(f)
                    for k, v in saved_data.items():
                        if k in self.vars and k != 'res_tag': self.vars[k].set(v)
                self.log(">>> 成功读取上次的配置文件！")
            except Exception as e:
                self.log(f"WARNING: 读取配置文件失败 -> {e}")

    def save_config(self):
        try:
            saved_data = {k: v.get() for k, v in self.vars.items() if k != 'res_tag'}
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(saved_data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存配置文件失败: {e}")

    def on_closing(self):
        self.save_config()
        self.stop_pipeline()
        self.destroy()

    def update_res_tag(self, *args):
        val = self.vars['resolution'].get().lower().replace('*', 'x')
        try:
            w, h = map(int, val.split('x'))
            pixels = w * h
            if pixels >= 3840 * 2160 * 0.9: tag = "4K"
            elif pixels >= 2560 * 1440 * 0.9: tag = "2K"
            elif pixels >= 1920 * 1080 * 0.9: tag = "1080P"
            elif pixels >= 1280 * 720 * 0.9: tag = "720P"
            else: tag = "标清"
            self.vars['res_tag'].set(tag)
        except ValueError: self.vars['res_tag'].set("?")

    def browse_dir(self, var_name):
        current_path = self.vars[var_name].get()
        init_dir = current_path if os.path.exists(current_path) else None
        d = filedialog.askdirectory(initialdir=init_dir)
        if d: self.vars[var_name].set(d)

    def browse_file(self, var_name):
        current_path = self.vars[var_name].get()
        init_dir = os.path.dirname(current_path) if os.path.exists(current_path) else None
        f = filedialog.askopenfilename(initialdir=init_dir, filetypes=[("静态/动态水印", "*.png *.gif"), ("所有文件", "*.*")])
        if f: self.vars[var_name].set(f)

    def append_multi_dir(self, var_name):
        current_path = self.vars[var_name].get().strip()
        init_dir = None
        if current_path:
            paths = [p.strip() for p in current_path.split(';') if p.strip()]
            if paths and os.path.exists(paths[-1]): init_dir = paths[-1]
        d = filedialog.askdirectory(initialdir=init_dir)
        if d:
            if current_path:
                if d not in current_path: self.vars[var_name].set(current_path + ";" + d)
            else: self.vars[var_name].set(d)

    def open_folder_in_os(self, var_name):
        p = self.vars[var_name].get().strip()
        if not p:
            messagebox.showinfo("提示", "路径为空，请先选择文件夹。")
            return
        paths = [path.strip() for path in p.split(';') if path.strip()]
        for target in paths:
            if os.path.exists(target):
                try:
                    if os.name == 'nt': os.startfile(target)
                    elif sys.platform == 'darwin': subprocess.call(['open', target])
                    else: subprocess.call(['xdg-open', target])
                except Exception as e: self.log(f"无法打开目录: {target}, 错误: {e}")
            else: self.log(f"目录不存在，无法打开: {target}")

    def log(self, message):
        self.after(0, self._sync_log, message)

    def _sync_log(self, message):
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)
        self.update_idletasks()

    def get_valid_config(self):
        config = {k: v.get() for k, v in self.vars.items()}
        for k in ['hook_dir', 'body_dir', 'bgm_dir']:
            if not config[k]:
                messagebox.showerror("配置错误", f"关键素材目录不能为空: {k}")
                return None
        if not os.path.exists(config['hook_dir']):
            messagebox.showerror("配置错误", "Hook 首段目录不存在！")
            return None
        if config['voice_dir'] and not os.path.exists(config['voice_dir']):
            messagebox.showerror("配置错误", "配音目录不存在！不需要请留空。")
            return None
        if config.get('enable_srt') and (not config['srt_dir'] or not os.path.exists(config['srt_dir'])):
            messagebox.showerror("配置错误", "已勾选开启硬字幕，但未设置正确的字幕目录！")
            return None
        if config['watermark_path'] and not os.path.exists(config['watermark_path']):
            messagebox.showerror("配置错误", "水印文件路径不存在！不需要请留空。")
            return None
        out_dir = config['base_out_dir']
        if out_dir: os.makedirs(out_dir, exist_ok=True)
        else:
            messagebox.showerror("配置错误", "请指定导出父目录！")
            return None
        return config

    def toggle_ui_state(self, running=False):
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_pre_flight.config(state=state)
        self.btn_bench.config(state=state)
        self.btn_start.config(state=state)
        self.btn_clear_history.config(state=state)
        self.btn_stop.config(state=tk.NORMAL if running else tk.DISABLED)

    def clear_usage_history(self):
        if messagebox.askyesno("确认", "确定要清除所有素材的使用记录吗？\n清除后，系统将重新使用之前已被混剪过的首段素材。"):
            self.shared_cache.clear_history()
            self.log(">>> 已成功清除素材使用记录。")

    def get_tasks_from_config(self, config):
        tasks = []
        h_dir = config['hook_dir'].strip()
        b_dir = config['body_dir'].strip()
        out_base = config['base_out_dir'].strip()
        sub_folders = [f for f in os.listdir(h_dir) if os.path.isdir(os.path.join(h_dir, f))]
        if sub_folders:
            self.log(f">>> [系统] 探测到矩阵结构，共 {len(sub_folders)} 个子库。")
            for sub in sub_folders:
                task_h = os.path.join(h_dir, sub)
                if b_dir == h_dir:
                    task_b = [os.path.join(h_dir, sub)]
                else:
                    potential_b_sub = os.path.join(b_dir, sub)
                    if os.path.isdir(potential_b_sub):
                        task_b = [potential_b_sub]
                    else:
                        task_b = [p.strip() for p in b_dir.split(';') if p.strip()]
                tasks.append({
                    'name': sub,
                    'hook_dir': task_h,
                    'body_dirs': task_b,
                    'out': os.path.join(out_base, sub)
                })
        else:
            task_name = os.path.basename(h_dir.rstrip('/\\')) or "Single_Task"
            self.log(f">>> [系统] 无子文件夹，切换为经典单库模式。")
            tasks.append({
                'name': task_name,
                'hook_dir': h_dir,
                'body_dirs': [p.strip() for p in b_dir.split(';') if p.strip()],
                'out': os.path.join(out_base, task_name)
            })
        return tasks

    def run_pre_flight_only(self):
        config = self.get_valid_config()
        if not config: return
        self.is_pipeline_running = True
        self.toggle_ui_state(running=True)
        self.txt_log.config(state=tk.NORMAL); self.txt_log.delete(1.0, tk.END); self.txt_log.config(state=tk.DISABLED)
        threading.Thread(target=self._pre_flight_task, args=(config,), daemon=True).start()

    def _pre_flight_task(self, config):
        try:
            tasks = self.get_tasks_from_config(config)
            if not tasks:
                self.log(">>> [错误] 找不到任何素材目录！")
                return
            self.log(f">>> 开始执行产能预检...")
            total_capacity = 0
            report = []
            for t in tasks:
                if not self.is_pipeline_running: break
                task_cfg = config.copy()
                task_cfg['task_name'] = t['name']
                task_cfg['hook_dir'] = t['hook_dir']
                task_cfg['body_dirs'] = t['body_dirs']
                core = VideoMatrixCore(task_cfg, self.log, self.shared_cache)
                ok, msg = core.pre_flight_check()
                if ok:
                    c = core.n_total if isinstance(core.n_total, int) else "无限"
                    total_capacity += core.n_total if isinstance(core.n_total, int) else 9999
                    report.append(f"✅ [{t['name']}] 可产出: {c} 个")
                else:
                    report.append(f"❌ [{t['name']}] 拦截: {msg}")
            if self.is_pipeline_running:
                self.log("\n".join(report))
                cap_str = '充足/无限' if total_capacity > 9000 else total_capacity
                self.log(f"\n>>> 预检完成。总可用首段产能预估: {cap_str} 个。")
                messagebox.showinfo("预检报告", "\n".join(report))
        except Exception as e:
            self.log(f"\n[预检错误] {str(e)}")
        finally:
            self.is_pipeline_running = False
            self.after(0, lambda: self.toggle_ui_state(running=False))

    def run_benchmark(self):
        config = self.get_valid_config()
        if not config: return
        self.toggle_ui_state(running=True)
        self.txt_log.config(state=tk.NORMAL); self.txt_log.delete(1.0, tk.END); self.txt_log.config(state=tk.DISABLED)
        threading.Thread(target=self._bench_task, args=(config,), daemon=True).start()

    def _bench_task(self, cfg):
        try:
            tasks = self.get_tasks_from_config(cfg)
            if not tasks: return
            results = {}
            for n in range(1, 5):
                self.log(f">>> [测试] 正在评估 {n} 路并发性能...")
                test_cfg = cfg.copy()
                test_cfg.update({
                    'hook_dir': tasks[0]['hook_dir'],
                    'body_dirs': tasks[0]['body_dirs'],
                    'out_dir': tempfile.gettempdir(),
                    'target_count': 2,
                    'task_name': 'Test'
                })
                core = VideoMatrixCore(test_cfg, lambda x: None, self.shared_cache)
                if not core.pre_flight_check()[0]:
                    self.log(">>> [压测失败] 素材不足以支撑压测。")
                    return
                start_t = time.time()
                with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
                    fs = [ex.submit(core.render_single_video, i) for i in range(1, n + 1)]
                    concurrent.futures.wait(fs)
                elapsed = time.time() - start_t
                t_per_v = elapsed / n
                results[n] = t_per_v
                self.log(f"    - {n} 路并发总耗时: {elapsed:.1f} 秒，单视频平均: {t_per_v:.2f} 秒")
            best_n = min(results, key=results.get)
            self.vars['concurrent_tasks'].set(best_n)
            self.log(f"\n✅ [压测完成] 检测到最优节点为 {best_n} 路并发！参数已自动更新。")
        except Exception as e:
            self.log(f"\n[压测错误] {str(e)}")
        finally:
            self.after(0, lambda: self.toggle_ui_state(running=False))

    def start_pipeline(self):
        config = self.get_valid_config()
        if not config: return
        self.is_pipeline_running = True
        self.toggle_ui_state(running=True)
        self.txt_log.config(state=tk.NORMAL); self.txt_log.delete(1.0, tk.END); self.txt_log.config(state=tk.DISABLED)
        threading.Thread(target=self.run_matrix_task, args=(config,), daemon=True).start()

    def stop_pipeline(self):
        self.is_pipeline_running = False
        self.log("\n[!!!] 收到紧急停止指令，正在安全截断所有并发线程...")
        self.btn_stop.config(state=tk.DISABLED, text="正在停止...")
        with self.cores_lock:
            for core in self.active_cores: core.stop()

    def _render_job(self, core, idx):
        if not self.is_pipeline_running or not core.is_running: return False
        return core.render_single_video(idx)

    def run_matrix_task(self, config):
        try:
            tasks = self.get_tasks_from_config(config)
            if not tasks: return
            cores = []
            for t in tasks:
                if not self.is_pipeline_running: break
                task_cfg = config.copy()
                task_cfg['task_name'] = t['name']
                task_cfg['hook_dir'] = t['hook_dir']
                task_cfg['body_dirs'] = t['body_dirs']
                task_cfg['out_dir'] = t['out']
                os.makedirs(task_cfg['out_dir'], exist_ok=True)
                core = VideoMatrixCore(task_cfg, self.log, self.shared_cache)
                ok, msg = core.pre_flight_check()
                if ok:
                    cores.append(core)
                    with self.cores_lock: self.active_cores.append(core)
                else:
                    self.log(f"[{t['name']}] ❌ 预检拦截: {msg}")
            if not cores:
                self.log("\n>>> 所有库均被拦截，任务终止。")
                return
            jobs = []
            max_t = max([c.config['target_count'] for c in cores], default=0)
            for i in range(1, max_t + 1):
                for core in cores:
                    if i <= core.config['target_count']: jobs.append((core, i))
            self.log(f">>> [系统] 任务池组装完毕，即将并线生成 {len(jobs)} 个视频。引擎算力限制: {config['concurrent_tasks']} 路")
            success_counts = {core.task_name: 0 for core in cores}
            with concurrent.futures.ThreadPoolExecutor(max_workers=config['concurrent_tasks']) as executor:
                futures = {executor.submit(self._render_job, core, idx): core for core, idx in jobs}
                for future in concurrent.futures.as_completed(futures):
                    if not self.is_pipeline_running: break
                    core = futures[future]
                    try:
                        if future.result(): success_counts[core.task_name] += 1
                    except Exception as e:
                        self.log(f"[{core.task_name}] 渲染异常: {e}")
            if self.is_pipeline_running:
                self.log("\n>>> [完成] 渲染任务队列执行完毕！")
                for core_name, cnt in success_counts.items():
                    target = next(c.config['target_count'] for c in cores if c.task_name == core_name)
                    self.log(f"    🏁 [{core_name}] 最终产量: {cnt}/{target}")
                messagebox.showinfo("完成", "矩阵流水线全线执行完毕！")
        except Exception as e:
            self.log(f"\n[严重错误] 调度引擎崩溃: {str(e)}")
        finally:
            self.is_pipeline_running = False
            self.after(0, lambda: self.toggle_ui_state(running=False))
            self.after(0, lambda: self.btn_stop.config(text="停止"))
            with self.cores_lock: self.active_cores.clear()

if __name__ == "__main__":
    app = AppUI()
    app.mainloop()
