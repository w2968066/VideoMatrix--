import os
import math
import random
import json
import tempfile
import threading
import concurrent.futures
import time
import uuid
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

from .ffmpeg import (
    FFMPEG, probe_media, extract_media_info,
    build_filter_complex, render_video
)

APP_STATE_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'),
    'VideoMatrix'
)
os.makedirs(APP_STATE_DIR, exist_ok=True)


def state_path(filename: str) -> str:
    return os.path.join(APP_STATE_DIR, filename)


class SharedMediaCache:
    """全局线程安全缓存管理"""
    def __init__(self):
        self.media_cache: Dict[str, dict] = {}
        self.usage_history: set = set()
        self.media_cache_file = state_path('media_cache.json')
        self.usage_history_file = state_path('usage_history.json')
        self.lock = threading.Lock()
        self.load_state()

    def load_state(self):
        if os.path.exists(self.media_cache_file):
            try:
                with open(self.media_cache_file, 'r', encoding='utf-8') as f:
                    self.media_cache = json.load(f)
            except Exception:
                pass
        if os.path.exists(self.usage_history_file):
            try:
                with open(self.usage_history_file, 'r', encoding='utf-8') as f:
                    self.usage_history = set(json.load(f))
            except Exception:
                pass

    def save_state(self):
        with self.lock:
            try:
                with open(self.media_cache_file, 'w', encoding='utf-8') as f:
                    json.dump(self.media_cache, f, ensure_ascii=False)
                with open(self.usage_history_file, 'w', encoding='utf-8') as f:
                    json.dump(list(self.usage_history), f, ensure_ascii=False)
            except Exception:
                pass

    def clear_history(self):
        with self.lock:
            self.usage_history.clear()
            if os.path.exists(self.usage_history_file):
                os.remove(self.usage_history_file)


class VideoMatrixCore:
    """单库视频处理实例"""
    def __init__(self, config: dict, log_callback: Callable, shared_cache: SharedMediaCache):
        self.config = config
        self.log = log_callback
        self.shared = shared_cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_dir_path = self.temp_dir.name

        self.task_name = config.get('task_name', 'SingleTask')
        self.hook_pool: List[dict] = []
        self.body_pool: List[dict] = []
        self.bgm_pool: List[dict] = []
        self.voice_pool: List[dict] = []
        self.n_total = 0
        self.last_output_path: Optional[str] = None
        self.last_elapsed: Optional[float] = None

        self.is_running = True
        self.current_process = None
        self.core_lock = threading.Lock()

    def _scan_files(self, dir_path: str, exts: tuple) -> List[str]:
        if not dir_path or not os.path.exists(dir_path):
            return []
        res = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.lower().endswith(exts):
                    res.append(os.path.join(root, f))
        return res

    def probe_media_cached(self, file_path: str) -> Tuple[str, float, bool]:
        try:
            mtime = os.path.getmtime(file_path)
            with self.shared.lock:
                cached = self.shared.media_cache.get(file_path)
                if cached and cached.get('mtime') == mtime:
                    return file_path, cached['dur'], cached['has_audio']
        except Exception:
            pass

        info = probe_media(file_path)
        if info:
            dur, has_audio, _, _, _ = extract_media_info(info, file_path)
            dur = max(0.0, dur - 0.2)
            with self.shared.lock:
                self.shared.media_cache[file_path] = {'mtime': mtime, 'dur': dur, 'has_audio': has_audio}
            return file_path, dur, has_audio

        self.log(f"[{self.task_name}] WARNING: 素材损坏或无法读取 -> {os.path.basename(file_path)}")
        return file_path, 0.0, False

    def parse_time_to_ms(self, t_str: str) -> int:
        h, m, s, ms = map(int, re.split('[:,]', t_str.replace('.', ',').strip()))
        return (h * 3600 + m * 60 + s) * 1000 + ms

    def format_ms_to_time(self, ms: int) -> str:
        h, ms = int(ms // 3600000), ms % 3600000
        m, ms = int(ms // 60000), ms % 60000
        s, ms = int(ms // 1000), int(ms % 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def process_srt(self, srt_dir: str, duration_sec: float) -> Optional[str]:
        srt_files = self._scan_files(srt_dir, ('.srt',))
        if not srt_files:
            return None
        srt_path = random.choice(srt_files)
        try:
            with open(srt_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception:
            try:
                with open(srt_path, 'r', encoding='gbk') as f:
                    content = f.read()
            except Exception:
                return None

        slice_start_ms, slice_end_ms = 0, int(duration_sec * 1000)
        new_subs, index = [], 1
        blocks = re.compile(
            r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)',
            re.DOTALL
        ).findall(content + "\n\n")

        for _, t_start_str, t_end_str, text in blocks:
            t_start = self.parse_time_to_ms(t_start_str)
            t_end = self.parse_time_to_ms(t_end_str)
            if t_end > slice_start_ms and t_start < slice_end_ms:
                new_start = max(0, t_start - slice_start_ms)
                new_end = min(duration_sec * 1000, t_end - slice_start_ms)
                new_subs.append(
                    f"{index}\n{self.format_ms_to_time(new_start)} --> {self.format_ms_to_time(new_end)}\n{text.strip()}"
                )
                index += 1

        if not new_subs:
            return None
        temp_srt_path = os.path.join(self.temp_dir_path, f"temp_{uuid.uuid4().hex[:8]}.srt")
        with open(temp_srt_path, 'w', encoding='utf-8') as f:
            f.write("\n\n".join(new_subs) + "\n\n")
        return temp_srt_path.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")

    def pre_flight_check(self) -> Tuple[bool, str]:
        self.hook_pool.clear()
        self.body_pool.clear()
        self.bgm_pool.clear()
        self.voice_pool.clear()

        cfg = self.config
        t_hook = cfg['t_hook']
        t_body = cfg['t_body']
        total_clips = cfg['total_clips']
        t_total = t_hook + t_body * (total_clips - 1)

        hook_files = self._scan_files(cfg['hook_dir'], ('.mp4', '.mov'))
        body_files = []
        for bd in cfg['body_dirs']:
            body_files.extend(self._scan_files(bd, ('.mp4', '.mov')))
        bgm_files = self._scan_files(cfg['bgm_dir'], ('.mp3', '.wav'))

        probe_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            all_media = list(set(hook_files + body_files + bgm_files))
            futures = {executor.submit(self.probe_media_cached, f): f for f in all_media}
            for future in concurrent.futures.as_completed(futures):
                if not self.is_running:
                    return False, "已停止"
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
                            self.hook_pool.append({
                                'file': f, 'start': i * step,
                                'duration': t_hook, 'has_audio': has_audio, 'id': hook_id
                            })

        for f in body_files:
            dur, has_audio = probe_results[f]
            if dur >= t_body:
                step = max(t_body * (1 - cfg['body_r']), 0.1)
                n = int(math.floor((dur - t_body) / step)) + 1
                for i in range(n):
                    self.body_pool.append({'file': f, 'start': i * step, 'duration': t_body, 'has_audio': has_audio})

        if not self.hook_pool:
            return False, f"库 [{self.task_name}] 首段素材耗尽或无合规视频。"

        if cfg['hook_r'] >= 0.99:
            self.n_total = "无限"
        else:
            self.n_total = len(self.hook_pool)
            if self.n_total == 0:
                return False, f"库 [{self.task_name}] 首段剩余 0 个片段，无法生产。"
            if self.n_total < cfg['target_count']:
                self.log(f"[{self.task_name}] 提示: 首段仅剩 {self.n_total} 个片段，已自动下调目标产量。")
                cfg['target_count'] = self.n_total

        if len(self.body_pool) < total_clips - 1:
            return False, f"库 [{self.task_name}] 后段素材不足拼凑 1 个视频。"

        for f in bgm_files:
            dur, _ = probe_results[f]
            if dur >= t_total:
                step = max(t_total * (1 - cfg['bgm_r']), 0.1)
                n = int(math.floor((dur - t_total) / step)) + 1
                for i in range(n):
                    self.bgm_pool.append({'file': f, 'start': i * step, 'duration': t_total})

        if not self.bgm_pool:
            return False, "BGM 库素材时长不足。"

        if cfg.get('voice_dir') and os.path.exists(cfg['voice_dir']):
            voice_files = self._scan_files(cfg['voice_dir'], ('.mp3', '.wav'))
            for f in voice_files:
                self.voice_pool.append({'file': f})

        random.shuffle(self.hook_pool)
        return True, "预检通过"

    def render_single_video(self, task_idx: int, return_result: bool = False):
        if not self.is_running:
            return (False, None, None) if return_result else False

        now_str_start = datetime.now().strftime("%H:%M:%S")
        self.log(f"  [{now_str_start}] [{self.task_name}] 正在拼装 视频 {task_idx:03d} ...")

        start_time = time.time()
        cfg = self.config
        t_total = cfg['t_hook'] + cfg['t_body'] * (cfg['total_clips'] - 1)

        with self.core_lock:
            if not self.hook_pool:
                return (False, None, None) if return_result else False
            if cfg['hook_r'] >= 0.99:
                hook_clip = random.choice(self.hook_pool)
            else:
                hook_clip = self.hook_pool.pop()

            if len(self.body_pool) >= (cfg['total_clips'] - 1):
                body_clips = random.sample(self.body_pool, cfg['total_clips'] - 1)
            else:
                body_clips = random.choices(self.body_pool, k=cfg['total_clips'] - 1)

            bgm_clip = random.choice(self.bgm_pool)
            voice_clip = random.choice(self.voice_pool) if self.voice_pool else None

        temp_srt_path_safe = None
        if cfg.get('enable_srt') and cfg.get('srt_dir') and os.path.exists(cfg['srt_dir']):
            temp_srt_path_safe = self.process_srt(cfg['srt_dir'], t_total)

        vol_orig = cfg['vol_orig'] / 100.0
        vol_bgm = cfg['vol_bgm'] / 100.0
        vol_voice = cfg['vol_voice'] / 100.0
        fps_val = str(cfg['fps'])

        has_voice = bool(voice_clip and vol_voice > 0)
        has_watermark = bool(cfg.get('watermark_path') and os.path.exists(cfg['watermark_path']))

        clips = [hook_clip] + list(body_clips)
        inputs = [c['file'] for c in clips] + [bgm_clip['file']]

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

        filter_complex, current_v, audio_map = build_filter_complex(
            clips=clips,
            bgm_clip=bgm_clip,
            voice_clip=voice_clip,
            watermark_path=cfg.get('watermark_path'),
            srt_path_safe=temp_srt_path_safe,
            resolution=cfg['resolution'],
            fps=cfg['fps'],
            vol_orig=vol_orig,
            vol_bgm=vol_bgm,
            vol_voice=vol_voice,
            total_duration=t_total,
        )

        # 修正 build_filter_complex 中的 watermark_idx 逻辑
        # 由于 build_filter_complex 内部硬编码了索引，这里我们需要重建正确的 filter_complex
        # 暂时直接使用原逻辑重写 filter_complex 以确保正确性
        n_clips = len(clips)
        res_str = cfg['resolution'].lower().replace('*', 'x')
        w, h = map(int, res_str.split('x'))
        
        filter_complex = ""
        for i, clip in enumerate(clips):
            start, dur, clip_has_audio = clip['start'], clip['duration'], clip.get('has_audio', False)
            filter_complex += (
                f"[{i}:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
                f"fps={fps_val},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
                f"setsar=1,format=yuv420p[v{i}]; "
            )
            if vol_orig > 0:
                if clip_has_audio:
                    filter_complex += (
                        f"[{i}:a]atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS,"
                        f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]; "
                    )
                else:
                    filter_complex += f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur}[a{i}]; "

        if vol_orig > 0:
            concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=1[vout_base][aout_orig]; [aout_orig]volume={vol_orig}[aout_orig_v]; "
        else:
            concat_inputs = "".join([f"[v{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=0[vout_base]; "

        current_v = "[vout_base]"
        if temp_srt_path_safe:
            filter_complex += f"{current_v}subtitles='{temp_srt_path_safe}'[v_sub]; "
            current_v = "[v_sub]"
        if has_watermark:
            wm_idx = n_clips + (1 if has_voice else 0) + 1
            filter_complex += f"{current_v}[{wm_idx}:v]overlay=(W-w)/2:(H-h)/2:shortest=1[v_wm]; "
            current_v = "[v_wm]"

        bgm_idx = n_clips
        if vol_bgm > 0:
            filter_complex += f"[{bgm_idx}:a]atrim=start={bgm_clip['start']}:duration={t_total},asetpts=PTS-STARTPTS,volume={vol_bgm}[aout_bgm_v]; "
        if has_voice:
            voice_idx_actual = n_clips + 1
            filter_complex += f"[{voice_idx_actual}:a]atrim=start=0:duration={t_total},asetpts=PTS-STARTPTS,volume={vol_voice}[aout_voice_v]; "

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
            filter_complex += f"{inputs_str}amix=inputs={len(mix_tracks)}:duration=longest:dropout_transition=2[aout]"
            audio_map = "[aout]"
        elif len(mix_tracks) == 1:
            audio_map = mix_tracks[0]

        filter_complex = filter_complex.strip('; ')
        out_name = f"{self.task_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{task_idx:03d}.mp4"
        out_path = os.path.join(cfg['out_dir'], out_name)

        cmd.extend(['-filter_complex', filter_complex, '-map', current_v])
        if audio_map:
            cmd.extend(['-map', audio_map, '-c:a', 'aac'])
        cmd.extend(['-r', fps_val, '-b:v', cfg['bitrate']])

        success, error = render_video(cmd, cfg.get('enable_gpu', True), out_path, self.temp_dir_path)

        if not success and self.is_running:
            # 如果 GPU 编码失败，尝试 CPU 编码
            success, error = render_video(cmd, False, out_path, self.temp_dir_path)

        if success and self.is_running:
            if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
                now_str = datetime.now().strftime("%H:%M:%S")
                self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 输出异常（文件过小或不存在），可能编码失败")
                return (False, None, None) if return_result else False
            if 'id' in hook_clip:
                with self.shared.lock:
                    self.shared.usage_history.add(hook_clip['id'])
                self.shared.save_state()
            elapsed_time = time.time() - start_time
            self.last_output_path = out_path
            self.last_elapsed = round(elapsed_time, 1)
            now_str = datetime.now().strftime("%H:%M:%S")
            self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 完成，耗时 {elapsed_time:.1f} 秒 -> {out_name}")
            return (True, out_path, self.last_elapsed) if return_result else True
        return (False, None, None) if return_result else False

    def stop(self):
        self.is_running = False
        if self.current_process:
            try:
                self.current_process.terminate()
            except Exception:
                pass
