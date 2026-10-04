import os
import sys
import math
import random
import json
import tempfile
import threading
import concurrent.futures
import time
import uuid
import re
import shutil
from datetime import datetime
from typing import List, Dict, Optional, Tuple, Callable

from .ffmpeg import (
    FFMPEG, MEDIA_END_SAFETY_MARGIN, probe_media, extract_media_info, extract_audio_duration,
    render_video
)
from .timeline import apply_timeline_totals, body_segment_specs
from .bgm_tracks import configured_tracks, prepare_track, track_filter, TRACK_LABELS
from .output_settings import resolve_output_config, validate_output_settings, parse_resolution

MEDIA_CACHE_VERSION = 2
DURATION_EPSILON = 0.001
ASS_PLAY_RES_Y = 288
SOURCE_HAN_FONT_NAME = 'Source Han Sans SC'
SOURCE_HAN_FONT_FILE = 'SourceHanSansSC-Regular.otf'

APP_STATE_DIR = os.path.join(
    os.environ.get('APPDATA') or os.path.expanduser('~'),
    'VideoMatrix'
)
os.makedirs(APP_STATE_DIR, exist_ok=True)


def state_path(filename: str) -> str:
    return os.path.join(APP_STATE_DIR, filename)


def slice_count(usable_duration: float, clip_duration: float, step: float) -> int:
    """Count safe slices while allowing one exact-length clip from the start."""
    if clip_duration <= 0 or step <= 0:
        return 0
    if usable_duration >= clip_duration:
        return int(math.floor((usable_duration - clip_duration) / step)) + 1
    actual_duration = usable_duration + MEDIA_END_SAFETY_MARGIN
    return 1 if actual_duration + DURATION_EPSILON >= clip_duration else 0


def bundled_font_dir_safe() -> Optional[str]:
    if getattr(sys, 'frozen', False):
        font_dir = os.path.join(getattr(sys, '_MEIPASS', ''), 'fonts')
    else:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        font_dir = os.path.join(backend_dir, 'assets', 'fonts', 'files')
    if not os.path.isfile(os.path.join(font_dir, SOURCE_HAN_FONT_FILE)):
        return None
    return font_dir.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''")


def build_subtitle_filter(
    srt_path_safe: str,
    y_percent: float = 92.0,
    font_size_percent: float = 5.6,
    fonts_dir_safe: Optional[str] = None,
) -> str:
    """Build a libass subtitle filter with a resolution-independent vertical anchor."""
    y = max(8.0, min(92.0, float(y_percent)))
    font_size = round(ASS_PLAY_RES_Y * max(3.0, min(9.0, float(font_size_percent))) / 100.0)
    if abs(y - 50.0) < 0.5:
        alignment, margin_v = 5, 0
    elif y < 50.0:
        alignment = 8
        margin_v = round(ASS_PLAY_RES_Y * y / 100.0)
    else:
        alignment = 2
        margin_v = round(ASS_PLAY_RES_Y * (100.0 - y) / 100.0)
    fonts_dir_option = f":fontsdir='{fonts_dir_safe}'" if fonts_dir_safe else ""
    return (
        f"subtitles='{srt_path_safe}'{fonts_dir_option}:"
        f"force_style='FontName={SOURCE_HAN_FONT_NAME},FontSize={font_size},"
        f"Alignment={alignment},MarginV={margin_v}'"
    )


class SharedMediaCache:
    """全局线程安全缓存管理"""
    def __init__(self, state_dir: Optional[str] = None):
        self.media_cache: Dict[str, dict] = {}
        self.usage_history: set = set()
        root = state_dir or APP_STATE_DIR
        os.makedirs(root, exist_ok=True)
        self.media_cache_file = os.path.join(root, 'media_cache.json')
        self.usage_history_file = os.path.join(root, 'usage_history.json')
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
        self.rng = random.Random(config['_selection_seed']) if '_selection_seed' in config else random
        self._output_seed = config.get('_output_seed')
        if self._output_seed is None and (config.get('random_resolution_enabled') or config.get('random_bitrate_enabled')):
            self._output_seed = random.SystemRandom().randrange(0, 2**63)
        self.log = log_callback
        self.shared = shared_cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_dir_path = self.temp_dir.name

        self.task_name = config.get('task_name', 'SingleTask')
        self.hook_pool: List[dict] = []
        self.hook_cycle: List[dict] = []
        self.output_configs: Dict[int, dict] = {}
        self.body_pool: List[dict] = []
        self.body_group_pools: List[List[dict]] = []
        self.bgm_pool: List[dict] = []
        self.bgm_track_pools: Dict[str, List[dict]] = {}
        self.voice_pool: List[dict] = []
        self.n_total = 0
        self.last_output_path: Optional[str] = None
        self.last_elapsed: Optional[float] = None

        self.is_running = True
        self.current_process = None
        self.processes = set()
        self.core_lock = threading.Lock()
        self._variant_probe_lock = threading.Lock()
        self._variant_probe_cache = {}

    def _probe_variant_input(self, path: str) -> dict:
        """Only optional B inputs need an extra probe; share it across outputs."""
        from .video_variant import VideoVariantProcessor
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
        # Do not hold core_lock: the probe's process callback uses that lock.
        while not self._variant_probe_lock.acquire(timeout=.1):
            if not self.is_running:
                raise InterruptedError('已停止')
        try:
            if not self.is_running:
                raise InterruptedError('已停止')
            if key not in self._variant_probe_cache:
                if len(self._variant_probe_cache) >= 8:
                    self._variant_probe_cache.clear()
                try:
                    info = VideoVariantProcessor._probe_media(
                        path, lambda: not self.is_running, self._track_process)
                    self._variant_probe_cache[key] = (info, None)
                except InterruptedError:
                    raise
                except Exception as exc:
                    if not self.is_running:
                        raise InterruptedError('已停止') from exc
                    # A corrupt/slow B asset must not be re-probed for every
                    # output in a batch. A changed file gets a new cache key.
                    self._variant_probe_cache[key] = (None, str(exc))
            info, error = self._variant_probe_cache[key]
            if error:
                raise ValueError(error)
            return info
        finally:
            self._variant_probe_lock.release()

    def _scan_files(self, dir_path: str, exts: tuple) -> List[str]:
        if not dir_path or not os.path.exists(dir_path):
            return []
        if os.path.isfile(dir_path):
            return [dir_path] if dir_path.lower().endswith(exts) else []
        res = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                if f.lower().endswith(exts):
                    res.append(os.path.join(root, f))
        return res

    def probe_media_cached(self, file_path: str) -> Tuple[str, float, bool, float]:
        mtime = None
        try:
            mtime = os.path.getmtime(file_path)
            with self.shared.lock:
                cached = self.shared.media_cache.get(file_path)
                if (
                    cached
                    and cached.get('version') == MEDIA_CACHE_VERSION
                    and cached.get('mtime') == mtime
                    and 'audio_dur' in cached
                ):
                    return file_path, cached['dur'], cached['has_audio'], cached['audio_dur']
        except Exception:
            pass

        info = probe_media(file_path)
        if info:
            dur, has_audio, _, _, _ = extract_media_info(info, file_path)
            audio_dur = extract_audio_duration(info)
            with self.shared.lock:
                self.shared.media_cache[file_path] = {
                    'version': MEDIA_CACHE_VERSION,
                    'mtime': mtime, 'dur': dur, 'has_audio': has_audio,
                    'audio_dur': audio_dur,
                }
            return file_path, dur, has_audio, audio_dur

        self.log(f"[{self.task_name}] WARNING: 素材损坏或无法读取 -> {os.path.basename(file_path)}")
        return file_path, 0.0, False, 0.0

    def parse_time_to_ms(self, t_str: str) -> int:
        h, m, s, ms = map(int, re.split('[:,]', t_str.replace('.', ',').strip()))
        return (h * 3600 + m * 60 + s) * 1000 + ms

    def format_ms_to_time(self, ms: int) -> str:
        h, ms = int(ms // 3600000), ms % 3600000
        m, ms = int(ms // 60000), ms % 60000
        s, ms = int(ms // 1000), int(ms % 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def process_srt(self, srt_dir: str, duration_sec: float, offset_sec: float = 0.0) -> Optional[str]:
        frozen_files = self.config.get('_benchmark_srt_files')
        srt_files = list(frozen_files) if frozen_files is not None else self._scan_files(srt_dir, ('.srt',))
        if not srt_files:
            return None
        srt_path = self.rng.choice(srt_files)
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
        offset_ms = int(offset_sec * 1000)
        new_subs, index = [], 1
        blocks = re.compile(
            r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)',
            re.DOTALL
        ).findall(content + "\n\n")

        for _, t_start_str, t_end_str, text in blocks:
            t_start = self.parse_time_to_ms(t_start_str)
            t_end = self.parse_time_to_ms(t_end_str)
            if t_end > slice_start_ms and t_start < slice_end_ms:
                new_start = max(0, t_start - slice_start_ms) + offset_ms
                new_end = min(duration_sec * 1000, t_end - slice_start_ms) + offset_ms
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
        self.hook_cycle.clear()
        self.body_pool.clear()
        self.body_group_pools.clear()
        self.bgm_pool.clear()
        self.bgm_track_pools.clear()
        self.voice_pool.clear()

        cfg = self.config
        try:
            validate_output_settings(cfg)
        except ValueError as exc:
            return False, f'输出参数无效：{exc}'
        tracks = configured_tracks(cfg)
        if tracks:
            full = tracks.get('full', {})
            cfg['bgm_dir'] = str(full.get('path') or '').strip() if full.get('enabled', True) else ''
            cfg['apply_bgm_to_hook'] = True
            cfg['vol_bgm'] = full.get('volume', 30)
        cfg['bgm_dir'] = str(cfg.get('bgm_dir') or '').strip()
        audio_mode = cfg.get('duration_mode') == 'bgm'
        if audio_mode and not cfg['bgm_dir']:
            return False, '按全片 BGM 时长生成需要选择 BGM 并启用全片轨道，请选择音频或切回按片段数量。' if tracks else '按 BGM 时长生成需要选择 BGM，请选择音频或切回按片段数量。'
        if tracks:
            for scope, track in tracks.items():
                if track.get('enabled', True) and str(track.get('path') or '').strip() and not os.path.exists(track['path'].strip()):
                    return False, f'{TRACK_LABELS[scope]} 路径不存在，请重新选择或清空。'
        if cfg['bgm_dir'] and not os.path.exists(cfg['bgm_dir']):
            return False, 'BGM 路径不存在，请重新选择；不需要配乐时请清空路径。'
        t_hook = cfg['t_hook']
        apply_timeline_totals(cfg)
        body_specs = body_segment_specs(cfg)
        body_duration = cfg['body_duration']
        t_total = cfg['total_duration']
        bgm_duration = t_total if cfg.get('apply_bgm_to_hook', True) else body_duration

        if cfg.get('enable_srt'):
            srt_dir = str(cfg.get('srt_dir') or '').strip()
            if not srt_dir or not os.path.isdir(srt_dir):
                return False, "字幕已开启，但字幕目录不存在或未设置。"
            if not self._scan_files(srt_dir, ('.srt',)):
                return False, "字幕已开启，但字幕目录中没有找到 SRT 文件。"

        hook_files = self._scan_files(cfg['hook_dir'], ('.mp4', '.mov'))
        grouped_body = cfg.get('body_mode', 'normal') == 'grouped'
        if grouped_body and not body_specs:
            return False, f"库 [{self.task_name}] Body 分组模式至少需要启用一个分组。"
        body_files = []
        group_files: List[List[str]] = []
        if grouped_body:
            for spec in body_specs:
                files = self._scan_files(spec['folder'], ('.mp4', '.mov'))
                group_files.append(files)
                body_files.extend(files)
        else:
            body_files = [f for bd in cfg['body_dirs'] for f in self._scan_files(bd, ('.mp4', '.mov'))]
        bgm_files = self._scan_files(
            cfg['bgm_dir'],
            ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.avi', '.webm')
        )
        track_files = {
            scope: self._scan_files(str(track.get('path') or '').strip(),
                ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.mp4', '.mov', '.mkv', '.avi', '.webm'))
            for scope, track in tracks.items() if track.get('enabled', True) and str(track.get('path') or '').strip()
        }
        voice_files = self._scan_files(cfg.get('voice_dir', ''), ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'))

        probe_results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            all_media = list(set(hook_files + body_files + bgm_files + voice_files + [f for files in track_files.values() for f in files]))
            futures = {executor.submit(self.probe_media_cached, f): f for f in all_media}
            for future in concurrent.futures.as_completed(futures):
                if not self.is_running:
                    return False, "已停止"
                f_path, dur, has_audio, audio_dur = future.result()
                probe_results[f_path] = (dur, has_audio, audio_dur)

        self.shared.save_state()

        hook_candidate_count = 0
        for f in hook_files:
            dur, has_audio, _ = probe_results[f]
            if cfg.get('hook_full_duration'):
                full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                if full_duration > 0:
                    self.hook_pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                    hook_candidate_count += 1
                continue
            step = max(t_hook * (1 - cfg['hook_r']), 0.1)
            n = slice_count(dur, t_hook, step)
            if n <= 0:
                continue
            hook_candidate_count += n
            if cfg['hook_r'] >= 0.99:
                self.hook_pool.append({'file': f, 'start': 0.0, 'duration': t_hook, 'has_audio': has_audio})
                continue
            for i in range(n):
                hook_id = f"{f}_{i*step:.2f}"
                if hook_id not in self.shared.usage_history:
                    self.hook_pool.append({
                        'file': f, 'start': i * step,
                        'duration': t_hook, 'has_audio': has_audio, 'id': hook_id
                    })

        if grouped_body:
            for spec, files in zip(body_specs, group_files):
                if not spec['folder'] or not os.path.isdir(spec['folder']):
                    return False, f"库 [{self.task_name}] Body 分组 {spec['group_index']} 目录不存在或未设置。"
                pool: List[dict] = []
                duration = spec['clip_duration']
                for f in files:
                    dur, has_audio, _ = probe_results[f]
                    if spec.get('full_duration'):
                        full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                        if full_duration > 0:
                            pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                        continue
                    step = max(duration * (1 - cfg['body_r']), 0.1)
                    for i in range(slice_count(dur, duration, step)):
                        pool.append({'file': f, 'start': i * step, 'duration': duration, 'has_audio': has_audio})
                if not audio_mode and len(pool) < spec['clip_count']:
                    return False, (
                        f"库 [{self.task_name}] Body 分组 {spec['group_index']} 素材不足："
                        f"需要 {spec['clip_count']} 段，可用 {len(pool)} 段。"
                    )
                self.body_group_pools.append(pool)
        else:
            t_body = cfg['t_body']
            for f in dict.fromkeys(body_files):
                dur, has_audio, _ = probe_results[f]
                if cfg.get('body_full_duration'):
                    full_duration = dur + MEDIA_END_SAFETY_MARGIN if dur > 0 else extract_media_info(probe_media(f) or {}, f, safety_margin=0)[0]
                    if full_duration > 0:
                        self.body_pool.append({'file': f, 'start': 0.0, 'duration': full_duration, 'has_audio': has_audio})
                    continue
                step = max(t_body * (1 - cfg['body_r']), 0.1)
                n = slice_count(dur, t_body, step)
                for i in range(n):
                    self.body_pool.append({'file': f, 'start': i * step, 'duration': t_body, 'has_audio': has_audio})

        if not self.hook_pool:
            if not hook_files:
                return False, f"库 [{self.task_name}] 未找到 MP4/MOV 首段视频。"
            if hook_candidate_count == 0:
                max_duration = max(
                    (probe_results[f][0] + MEDIA_END_SAFETY_MARGIN for f in hook_files),
                    default=0.0,
                )
                return False, (
                    f"库 [{self.task_name}] 找到 {len(hook_files)} 个视频，但最长视频轨约 "
                    f"{max_duration:.2f} 秒，短于首段设置 {t_hook:g} 秒。"
                )
            return False, (
                f"库 [{self.task_name}] 找到 {len(hook_files)} 个合规视频，"
                "但对应首段切片已全部使用；请清理记录后重试。"
            )

        if cfg.get('hook_full_duration') or cfg['hook_r'] >= 0.99:
            self.n_total = "无限"
        else:
            self.n_total = len(self.hook_pool)
            if self.n_total == 0:
                return False, f"库 [{self.task_name}] 首段剩余 0 个片段，无法生产。"
            if self.n_total < cfg['target_count']:
                self.log(f"[{self.task_name}] 提示: 首段仅剩 {self.n_total} 个片段，已自动下调目标产量。")
                cfg['target_count'] = self.n_total

        if not audio_mode and not grouped_body and len(self.body_pool) < cfg['total_clips'] - 1:
            return False, f"库 [{self.task_name}] 后段素材不足拼凑 1 个视频。"

        if not audio_mode:
            pools = self.body_group_pools if grouped_body else [self.body_pool]
            body_duration = sum(sum(sorted((c['duration'] for c in pool), reverse=True)[:spec['clip_count']]) for spec, pool in zip(body_specs, pools))
            bgm_duration = body_duration + (max(clip['duration'] for clip in self.hook_pool) if cfg.get('apply_bgm_to_hook', True) else 0)
        for scope, files in track_files.items():
            pool = []
            for f in files:
                _, has_audio, audio_dur = probe_results[f]
                if has_audio:
                    duration = audio_dur + MEDIA_END_SAFETY_MARGIN if audio_dur > 0 else extract_audio_duration(probe_media(f) or {}, safety_margin=0)
                    if duration > 0:
                        pool.append(dict(file=f, start=0.0, duration=duration))
            if not pool:
                return False, f'{TRACK_LABELS[scope]} 没有可用音频，视频文件必须包含音轨。'
            self.bgm_track_pools[scope] = pool
        if tracks:
            self.bgm_pool = self.bgm_track_pools.get('full', [])
        for f in ([] if tracks else bgm_files):
            _, has_audio, audio_dur = probe_results[f]
            if has_audio:
                if audio_mode:
                    # Cached audio durations subtract the safety margin for slicing.
                    # Full-track mode must restore it, including the audio tail.
                    duration = audio_dur + MEDIA_END_SAFETY_MARGIN if audio_dur > 0 else extract_audio_duration(probe_media(f) or {}, safety_margin=0)
                    if duration > 0:
                        self.bgm_pool.append({'file': f, 'start': 0.0, 'duration': duration})
                    continue
                step = max(bgm_duration * (1 - cfg['bgm_r']), 0.1)
                n = slice_count(audio_dur, bgm_duration, step)
                for i in range(n):
                    self.bgm_pool.append({'file': f, 'start': i * step, 'duration': bgm_duration})

        if cfg['bgm_dir'] and not self.bgm_pool:
            return False, "BGM 素材不足，视频文件需包含音轨且时长足够。"

        if audio_mode:
            from .audio_timeline import fit_body_to_audio
            try:
                fit_body_to_audio(cfg, self.body_group_pools if grouped_body else [self.body_pool],
                                  min(c['duration'] for c in self.hook_pool),
                                  max(c['duration'] for c in self.bgm_pool), None)
            except ValueError as exc:
                return False, str(exc)
            self.log(f'[{self.task_name}] 完整 BGM 模式：随机抽取整条音频；Body 按组循环补齐，超长裁尾。')

        for f in voice_files:
            _, has_audio, audio_dur = probe_results[f]
            if has_audio and audio_dur > 0:
                self.voice_pool.append({'file': f, 'duration': audio_dur})

        if not self.bgm_pool and not self.bgm_track_pools:
            self.log(f'[{self.task_name}] 未选择 BGM：不添加背景音乐。')
        music_audible = any(float(tracks[scope].get('volume', 0)) > 0 for scope in self.bgm_track_pools) if tracks else bool(self.bgm_pool and cfg.get('vol_bgm', 0) > 0)
        if not music_audible and not (self.voice_pool and cfg.get('vol_voice', 0) > 0) and not (cfg.get('vol_orig', 0) > 0 or (cfg.get('vol_hook_orig') or 0) > 0):
            self.log(f'[{self.task_name}] 提示：当前声音均关闭，将生成无声视频。')

        protected = [
            name for name, enabled in (
                ('BGM', cfg.get('apply_bgm_to_hook', True)),
                ('配音', cfg.get('apply_voice_to_hook', True)),
                ('字幕', cfg.get('apply_srt_to_hook', True)),
                ('水印', cfg.get('apply_watermark_to_hook', True)),
            ) if not enabled
        ]
        if protected:
            offset_label = '实际 Hook 结束' if cfg.get('hook_full_duration') else f'{t_hook:g} 秒'
            self.log(f"[{self.task_name}] 成品 Hook 保护：{', '.join(protected)} 从 {offset_label}后开始生效。")

        self.rng.shuffle(self.hook_pool)
        return True, "预检通过"

    def render_single_video(self, task_idx: int, return_result: bool = False):
        workspace = {}
        try:
            return self._render_single_video(task_idx, return_result, workspace)
        finally:
            # Only our frame scratch directory lives in temp. The encoder writes
            # its exclusively reserved final filename directly: no rename/copy
            # after completion for Explorer/scanners to block on Windows.
            if workspace.get('path'):
                shutil.rmtree(workspace['path'], ignore_errors=True)
            if workspace.get('output') and not workspace.get('completed'):
                try:
                    os.unlink(workspace['output'])
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.log(f"未完成文件无法清理，请勿使用：{workspace['output']}（{exc}）")

    @staticmethod
    def _reserve_output(directory, stem):
        """Claim a unique name before encoding; never overwrite another output."""
        for _ in range(10):
            destination = os.path.abspath(os.path.join(directory, f'{stem}_{uuid.uuid4().hex[:8]}.mp4'))
            try:
                descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
            except FileExistsError:
                continue
            os.close(descriptor)
            return destination
        raise FileExistsError('无法分配唯一输出文件名')

    def _render_single_video(self, task_idx: int, return_result: bool, workspace):
        if not self.is_running:
            return (False, None, None) if return_result else False

        now_str_start = datetime.now().strftime("%H:%M:%S")
        self.log(f"  [{now_str_start}] [{self.task_name}] 正在拼装 视频 {task_idx:03d} ...")

        start_time = time.time()
        try:
            cfg = resolve_output_config(self.config, self.task_name, task_idx, self._output_seed or 0)
        except ValueError as exc:
            self.log(f'    [{self.task_name}] 输出参数无效：{exc}')
            return (False, None, None) if return_result else False
        if cfg.get('_output_settings'):
            active = ' / '.join(name for name, enabled in (
                ('随机分辨率', cfg.get('random_resolution_enabled')),
                ('随机码率', cfg.get('random_bitrate_enabled')),
            ) if enabled)
            self.log(f"[{self.task_name}] [输出参数] 视频 {task_idx:03d}：{cfg['resolution']} · {cfg['bitrate']}（{active}）。")
        body_specs = body_segment_specs(cfg)

        with self.core_lock:
            if not self.hook_pool:
                return (False, None, None) if return_result else False
            if cfg.get('hook_full_duration'):
                if not self.hook_cycle:
                    self.hook_cycle = list(self.hook_pool)
                    self.rng.shuffle(self.hook_cycle)
                hook_clip = self.hook_cycle.pop()
                cfg['t_hook'] = hook_clip['duration']
            elif cfg['hook_r'] >= 0.99:
                hook_clip = self.rng.choice(self.hook_pool)
            else:
                hook_clip = self.hook_pool.pop()

            bgm_clip = self.rng.choice(self.bgm_pool) if self.bgm_pool else None
            if cfg.get('duration_mode') == 'bgm':
                from .audio_timeline import fit_body_to_audio
                try:
                    body_clips = fit_body_to_audio(cfg, self.body_group_pools if cfg.get('body_mode') == 'grouped' else [self.body_pool], cfg['t_hook'], bgm_clip['duration'], self.rng)
                except ValueError as exc:
                    self.log(f'[{self.task_name}] {exc}')
                    return (False, None, None) if return_result else False
            elif cfg.get('body_mode', 'normal') == 'grouped':
                body_clips = []
                for spec, pool in zip(body_specs, self.body_group_pools):
                    body_clips.extend(self.rng.sample(pool, spec['clip_count']))
            elif len(self.body_pool) >= (cfg['total_clips'] - 1):
                body_clips = self.rng.sample(self.body_pool, cfg['total_clips'] - 1)
            else:
                body_clips = self.rng.choices(self.body_pool, k=cfg['total_clips'] - 1)

            voice_clip = self.rng.choice(self.voice_pool) if self.voice_pool else None

        apply_timeline_totals(cfg)
        planned_body_duration = cfg['body_duration']
        cfg['body_duration'] = sum(c['duration'] for c in body_clips)
        cfg['_body_clip_durations'] = [c['duration'] for c in body_clips]
        cfg['total_duration'] = cfg['t_hook'] + cfg['body_duration']
        if cfg.get('duration_mode') != 'bgm' and any(spec.get('full_duration') for spec in body_specs):
            self.log(f"[{self.task_name}] Body 使用原素材时长：{len(body_clips)} 段，共 {cfg['body_duration']:.3f}s；成片 {cfg['total_duration']:.3f}s。")
        if cfg.get('duration_mode') == 'bgm':
            old_body_duration = planned_body_duration
            cfg['body_duration'] = sum(c['duration'] for c in body_clips)
            cfg['_body_clip_durations'] = [c['duration'] for c in body_clips]
            cfg['total_duration'] = cfg['t_hook'] + cfg['body_duration']
            cfg['total_clips'] = 1 + len(body_clips)
            action = '裁掉尾部多余素材' if cfg['body_duration'] < old_body_duration else '自动补满素材' if cfg['body_duration'] > old_body_duration else '时长刚好匹配'
            self.log(f"[{self.task_name}] BGM {os.path.basename(bgm_clip['file'])} 完整 {bgm_clip['duration']:.3f}s；{action}，共 {cfg['total_clips']} 段 / {cfg['total_duration']:.3f}s。")
            if cfg.get('apply_bgm_to_hook', True) and cfg['t_hook'] >= bgm_clip['duration']:
                self.log(f"[{self.task_name}] 提示：保留完整 Hook，不追加 Body。" + ('BGM 未覆盖完整 Hook，剩余部分按原声设置输出。' if cfg['t_hook'] > bgm_clip['duration'] else '无剩余时长添加 Body。'))
        t_hook = cfg['t_hook']
        body_duration = cfg['body_duration']
        t_total = cfg['total_duration']

        music_tracks = []
        if configured_tracks(cfg):
            with self.core_lock:
                for scope, pool in self.bgm_track_pools.items():
                    settings = cfg['bgm_tracks'][scope]
                    if scope == 'body' and body_duration <= 1e-8:
                        self.log(f'[{self.task_name}] 无 Body：跳过 Body BGM。')
                        continue
                    selected = bgm_clip if scope == 'full' else self.rng.choice(pool)
                    track = prepare_track(scope, settings, selected, t_hook, t_total, cfg.get('duration_mode') == 'bgm', self.rng)
                    if not track:
                        self.log(f"[{self.task_name}] {TRACK_LABELS[scope]} {os.path.basename(selected['file'])}：静音。")
                        continue
                    music_tracks.append(track)
                    action = '循环补齐' if track['loop'] else '裁尾' if track['source_duration'] - track['start'] > track['interval'] + .001 else '播完停止' if track['duration'] < track['interval'] - .001 else '完整播放'
                    self.log(f"[{self.task_name}] {TRACK_LABELS[scope]} {os.path.basename(track['file'])}：素材 {track['start']:.3f}s 起，视频 {track['offset']:.3f}–{track['offset'] + track['duration']:.3f}s，{action}" + ('；淡化时长已按比例缩短' if track['fade_adjusted'] else '') + '。')

        temp_srt_path_safe = None
        with self.core_lock:
            self.output_configs[task_idx] = cfg
        if cfg.get('enable_srt') and (cfg.get('apply_srt_to_hook', True) or body_duration > 0):
            srt_on_hook = cfg.get('apply_srt_to_hook', True)
            temp_srt_path_safe = self.process_srt(
                cfg['srt_dir'],
                t_total if srt_on_hook else body_duration,
                0.0 if srt_on_hook else t_hook,
            )
            if not temp_srt_path_safe:
                self.log(f"    [{self.task_name}] 字幕处理失败：SRT 无有效时间轴或编码无法读取。")
                return (False, None, None) if return_result else False

        vol_orig = cfg['vol_orig'] / 100.0
        hook_volume_value = cfg.get('vol_hook_orig')
        vol_hook_orig = (cfg['vol_orig'] if hook_volume_value is None else hook_volume_value) / 100.0
        vol_bgm = cfg['vol_bgm'] / 100.0 if bgm_clip and not configured_tracks(cfg) else 0.0
        vol_voice = cfg['vol_voice'] / 100.0
        fps_val = str(cfg['fps'])

        has_voice = bool(voice_clip and vol_voice > 0 and (cfg.get('apply_voice_to_hook', True) or body_duration > 0))
        has_watermark = bool(cfg.get('watermark_path') and os.path.exists(cfg['watermark_path']) and (cfg.get('apply_watermark_to_hook', True) or body_duration > 0))
        has_original_audio = vol_hook_orig > 0 or vol_orig > 0

        clips = [hook_clip] + list(body_clips)
        inputs = [c['file'] for c in clips] + ([bgm_clip['file']] if bgm_clip and not configured_tracks(cfg) else [])
        music_inputs = {}
        for track in music_tracks:
            music_inputs[len(inputs)] = track
            inputs.append(track['file'])

        voice_idx = -1
        if has_voice:
            inputs.append(voice_clip['file'])
            voice_idx = len(inputs) - 1

        watermark_idx = -1
        if has_watermark:
            inputs.append(cfg['watermark_path'])
            watermark_idx = len(inputs) - 1

        # Never let an audio request decode a later clip's video ahead of concat.
        # With long Hooks + 4K/60fps Body, shared A/V inputs can queue gigabytes
        # of decoded Body frames. Audio-only readers keep video demand-driven,
        # without another video decode/encode. Append to preserve music/WM indices.
        original_audio_inputs = {}
        audio_clip_indices = {}
        if has_original_audio:
            for clip_index, clip in enumerate(clips):
                if clip.get('has_audio', False):
                    audio_index = len(inputs)
                    original_audio_inputs[clip_index] = audio_index
                    audio_clip_indices[audio_index] = clip_index
                    inputs.append(clip['file'])

        # Accurate input seeking skips long unused prefixes. Keep one second of
        # preroll, then trim residual timestamps for frame/audio boundary accuracy.
        seek_offsets = [max(0.0, float(c['start']) - 1.0) for c in clips]
        bgm_seek = max(0.0, float(bgm_clip['start']) - 1.0) if bgm_clip else 0.0
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
                offset = seek_offsets[idx] if idx < len(clips) else bgm_seek if idx == len(clips) else 0.0
                if idx in music_inputs:
                    offset = music_inputs[idx]['seek']
                if idx in audio_clip_indices:
                    offset = seek_offsets[audio_clip_indices[idx]]
                if offset > 0:
                    cmd.extend(['-ss', f'{offset:.9f}'])
                if idx < len(clips):
                    cmd.append('-an')
                elif idx in audio_clip_indices:
                    cmd.append('-vn')
                cmd.extend(['-threads', '2', '-i', inp])

        n_clips = len(clips)
        res_str = cfg['resolution'].lower().replace('*', 'x')
        w, h = map(int, res_str.split('x'))

        cover_path, cover_summary = None, None
        cfg.pop('_cover_applied', None)
        cfg.pop('_output_warnings', None)
        if cfg.get('enable_random_cover'):
            workspace['path'] = tempfile.mkdtemp(prefix='cover-', dir=self.temp_dir_path)
            from .video_cover import prepare_cover_frame
            from .video_variant import derive_variant_seed
            cover_seed = derive_variant_seed(int(cfg.get('_cover_seed') or 0), self.task_name + ':cover', task_idx)
            # Mark attempts, including failures, so TaskService cannot restart a
            # costly full-video postprocess after the main encode has finished.
            cfg['_cover_applied'] = {'seed': cover_seed, 'skipped': True}
            try:
                cover_path, cover_summary = prepare_cover_frame(
                    clips, cfg, cover_seed, workspace['path'],
                    lambda: not self.is_running, self._track_process)
                cfg['_cover_applied'] = cover_summary
            except Exception as exc:
                if not self.is_running:
                    return (False, None, None) if return_result else False
                message = f'随机封面取帧未完成，本条将使用基础混剪：{exc}'
                cfg.setdefault('_output_warnings', []).append(message)
                self.log(f'    [{self.task_name}] {message}')

        # The independent layer supplies normalization recipes, not a second
        # full-video encode. It never changes source selection or audio filters.
        baseline_inputs = list(cmd)
        variant_segments = {}
        blend = None
        recipe = None
        cfg.pop('_variant_applied', None)
        cfg.pop('_variant_warnings', None)
        if cfg.get('enable_variants') and cfg.get('_fuse_variants', True):
            from .dedup_plan import build_recipe, normalize_filter, summary, VERSION
            from .dedup_blend import prepare_blend
            from .video_variant import derive_variant_seed
            seed = derive_variant_seed(int(cfg.get('variant_seed') or 0), self.task_name, task_idx)
            cfg['_variant_applied'] = {'version': VERSION, 'seed': seed, 'skipped': True}
            try:
                recipe = build_recipe(cfg, seed)
                variant_segments = {part.index: normalize_filter(recipe, part) for part in recipe.segments}
                cfg['_variant_applied'] = summary(recipe)
                for message in recipe.warnings:
                    self.log(f'    [{self.task_name}] 变换保护：{message}')
                try:
                    blend = prepare_blend(cfg, self._probe_variant_input)
                except Exception as exc:
                    if not self.is_running:
                        return (False, None, None) if return_result else False
                    cfg.setdefault('_variant_warnings', []).append(f'B 画面混合未完成：{exc}')
                if blend:
                    cmd.extend(blend.input_args())
                    cfg['_variant_applied']['blend'] = {
                        'opacity': blend.opacity, 'eof': blend.eof,
                    }
            except Exception as exc:
                if not self.is_running:
                    return (False, None, None) if return_result else False
                variant_segments = {}
                cfg.setdefault('_variant_warnings', []).append(f'变换配方无效，已跳过：{exc}')
            for message in cfg.get('_variant_warnings', []):
                self.log(f'    [{self.task_name}] {message}')

        normalization_replacements = []
        filter_complex = ""
        for i, clip in enumerate(clips):
            start, dur, clip_has_audio = clip['start'] - seek_offsets[i], clip['duration'], clip.get('has_audio', False)
            base_normalization = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p"
            prefix = (
                f"[{i}:v]trim=start={start}:duration={dur},setpts=PTS-STARTPTS,"
                f"fps={fps_val},"
            )
            original = f"{prefix}{base_normalization}[v{i}]; "
            transformed = f"{prefix}{variant_segments.get(i, base_normalization)}[v{i}]; "
            filter_complex += transformed
            if transformed != original:
                normalization_replacements.append((transformed, original))
            if has_original_audio:
                clip_volume = vol_hook_orig if i == 0 else vol_orig
                if clip_has_audio:
                    filter_complex += (
                        f"[{original_audio_inputs[i]}:a]atrim=start={start}:duration={dur},asetpts=PTS-STARTPTS,"
                        f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                        f"apad=pad_dur={dur},atrim=duration={dur},volume={clip_volume}[a{i}]; "
                    )
                else:
                    filter_complex += (
                        f"anullsrc=channel_layout=stereo:sample_rate=44100:d={dur},"
                        f"volume={clip_volume}[a{i}]; "
                    )

        if has_original_audio:
            concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=1[vout_base][aout_orig]; "
        else:
            concat_inputs = "".join([f"[v{i}]" for i in range(n_clips)])
            filter_complex += f"{concat_inputs}concat=n={n_clips}:v=1:a=0[vout_base]; "

        current_v = "[vout_base]"
        blend_graph = ''
        if blend:
            blend_graph, current_v = blend.filters(len(inputs), current_v)
            blend_graph += '; '
            filter_complex += blend_graph
        if temp_srt_path_safe:
            subtitle_filter = build_subtitle_filter(
                temp_srt_path_safe,
                cfg.get('subtitle_y_percent', 92.0),
                cfg.get('subtitle_font_size_percent', 5.6),
                bundled_font_dir_safe(),
            )
            filter_complex += f"{current_v}{subtitle_filter}[v_sub]; "
            current_v = "[v_sub]"
        if has_watermark:
            watermark_offset = 0.0 if cfg.get('apply_watermark_to_hook', True) else t_hook
            watermark_input = f"[{watermark_idx}:v]"
            reference = cfg.get('_output_settings', {}).get('reference_resolution')
            if reference:
                reference_w, reference_h = parse_resolution(reference)
                ratio = w / reference_w
                # Keep the watermark's relative footprint when ONLY the output
                # dimensions change. Clip what was already outside the centered
                # reference canvas BEFORE scaling, bounding intermediate memory.
                # Fixed-output behavior remains untouched.
                filter_complex += (
                    f"{watermark_input}crop=w='min(iw,{reference_w})':h='min(ih,{reference_h})':exact=1,"
                    f"scale=w='max(2,round(iw*{ratio:.12f}/2)*2)':"
                    f"h='max(2,round(ih*{ratio:.12f}/2)*2)'[wm_scaled]; "
                )
                watermark_input = '[wm_scaled]'
            enable_filter = ""
            if watermark_offset > 0:
                filter_complex += (
                    f"{watermark_input}setpts=PTS-STARTPTS+{watermark_offset}/TB[wm_timed]; "
                )
                watermark_input = "[wm_timed]"
                enable_filter = f":enable='gte(t,{watermark_offset})'"
            filter_complex += (
                f"{current_v}{watermark_input}overlay=(W-w)/2:(H-h)/2:"
                f"shortest=1:eof_action=pass{enable_filter}[v_wm]; "
            )
            current_v = "[v_wm]"

        bgm_idx = n_clips
        for input_index, track in music_inputs.items():
            filter_complex += track_filter(track, input_index)
        if vol_bgm > 0:
            bgm_offset = 0.0 if cfg.get('apply_bgm_to_hook', True) else t_hook
            bgm_delay = f",adelay={int(round(bgm_offset * 1000))}|{int(round(bgm_offset * 1000))}" if bgm_offset > 0 else ""
            filter_complex += (
                f"[{bgm_idx}:a]atrim=start={bgm_clip['start'] - bgm_seek}:duration={bgm_clip['duration']},"
                f"asetpts=PTS-STARTPTS,aresample=44100,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,volume={vol_bgm}"
                f"{bgm_delay}[aout_bgm_v]; "
            )
        if has_voice:
            voice_on_hook = cfg.get('apply_voice_to_hook', True)
            voice_offset = 0.0 if voice_on_hook else t_hook
            voice_duration = t_total if voice_on_hook else body_duration
            voice_delay_ms = int(round(voice_offset * 1000))
            voice_delay = f",adelay={voice_delay_ms}|{voice_delay_ms}" if voice_delay_ms > 0 else ""
            filter_complex += (
                f"[{voice_idx}:a]atrim=start=0:duration={voice_duration},asetpts=PTS-STARTPTS,"
                f"aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"apad=pad_dur={voice_duration},atrim=duration={voice_duration},volume={vol_voice}"
                f"{voice_delay}[aout_voice_v]; "
            )

        mix_tracks = []
        mix_tracks.extend(f"[bgm_{track['scope']}]" for track in music_tracks)
        if has_original_audio:
            mix_tracks.append("[aout_orig]")
        if vol_bgm > 0:
            mix_tracks.append("[aout_bgm_v]")
        if has_voice:
            mix_tracks.append("[aout_voice_v]")

        audio_map = None
        if configured_tracks(cfg) and mix_tracks:
            # Preserve requested gains rather than averaging by the number of
            # configured tracks. Only peaks exceeding full scale are limited.
            inputs_str = ''.join(mix_tracks)
            if len(mix_tracks) > 1:
                filter_complex += f'{inputs_str}amix=inputs={len(mix_tracks)}:duration=longest:dropout_transition=0:normalize=0[aout_sum]; '
                audio_source = '[aout_sum]'
            else:
                audio_source = mix_tracks[0]
            filter_complex += f'{audio_source}alimiter=limit=1:level=false:latency=true[aout_limited]; '
            audio_source = '[aout_limited]'
        elif len(mix_tracks) > 1:
            inputs_str = "".join(mix_tracks)
            bgm_on_hook = cfg.get('apply_bgm_to_hook', True)
            voice_on_hook = cfg.get('apply_voice_to_hook', True)
            hook_mix_count = sum((
                int(vol_hook_orig > 0),
                int(vol_bgm > 0 and bgm_on_hook),
                int(has_voice and voice_on_hook),
            ))
            body_mix_count = sum((
                int(vol_orig > 0),
                int(vol_bgm > 0),
                int(has_voice),
            ))
            hook_mix_gain = 1.0 / max(1, hook_mix_count)
            body_mix_gain = 1.0 / max(1, body_mix_count)
            gain_expression = (
                f"if(lt(t,{t_hook}),{hook_mix_gain:.8f},{body_mix_gain:.8f})"
            )
            filter_complex += (
                f"{inputs_str}amix=inputs={len(mix_tracks)}:duration=longest:"
                f"dropout_transition=0:normalize=0[aout_sum]; "
                f"[aout_sum]volume='{gain_expression}':eval=frame[aout_mix]; "
            )
            audio_source = "[aout_mix]"
        elif len(mix_tracks) == 1:
            audio_source = mix_tracks[0]
        else:
            audio_source = None

        if audio_source:
            filter_complex += (
                f"{audio_source}atrim=start=0:duration={t_total},"
                f"asetpts=PTS-STARTPTS[aout_final]"
            )
            audio_map = "[aout_final]"

        filter_complex = filter_complex.strip('; ')
        out_stem = f"{self.task_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{task_idx:03d}"
        out_path = self._reserve_output(cfg['out_dir'], out_stem)
        workspace['output'] = out_path

        base_graph, base_v, base_audio = filter_complex, current_v, audio_map
        frame_duration = 0.
        cover_input_args = []
        if cover_path:
            from fractions import Fraction
            frame_duration = 1 / float(Fraction(fps_val)) if cover_summary['mode'] == 'insert' else 0.
            cover_index = len(inputs) + (1 if blend else 0)
            cover_input_args = ['-framerate', fps_val, '-i', cover_path]
            cmd.extend(cover_input_args)
            # The image input contains one frame, so overlay never buffers a
            # later main-video frame. tpad adds exactly one frame for insert.
            pad = ',tpad=start_mode=clone:start=1' if frame_duration else ''
            filter_complex += (
                f';{current_v}fps={fps_val}{pad}[cover_base];'
                f'[{cover_index}:v]setpts=PTS-STARTPTS,setsar=1[cover_frame];'
                '[cover_base][cover_frame]overlay=0:0:eof_action=pass:repeatlast=0:shortest=0[v_cover]'
            )
            current_v = '[v_cover]'
            if audio_map and frame_duration:
                # The main mix is 44100 Hz. Sample-based delay keeps rational
                # frame rates accurate and prepends silence without another mux.
                samples = round(frame_duration * 44100)
                filter_complex += f';{audio_map}adelay={samples}S:all=1[a_cover]'
                audio_map = '[a_cover]'

        output_args = []
        if audio_map:
            output_args.extend(['-map', audio_map, '-c:a', 'aac'])
        output_args.extend(['-r', fps_val, '-b:v', cfg['bitrate'], '-t',
                            f'{t_total + frame_duration:.12f}' if frame_duration else f'{t_total:.3f}'])
        cmd.extend(['-filter_complex', filter_complex, '-map', current_v, *output_args])

        if cfg.get('_output_settings'):
            # Also share one session when the core is used without TaskService.
            # Probes use the configured upper budget, not each sampled output.
            from .hardware import session_for
            cfg['_hardware_session'] = session_for(self.config, self.log)

        success, error = render_video(
            cmd, cfg.get('enable_gpu', True), out_path, self.temp_dir_path,
            config=cfg, log=self.log, is_cancelled=lambda: not self.is_running,
            on_process=self._track_process,
        )
        if not success and self.is_running and (normalization_replacements or blend):
            # One retry with the SAME selected clips, timeline, music, subtitles,
            # and output path. Never re-enter render_single_video/re-randomize.
            from .hardware import short_error
            message = f'去重变换失败，已回退基础混剪：{short_error(error)}'
            self.log(f'    [{self.task_name}] {message}')
            cfg.setdefault('_variant_warnings', []).append(message)
            cfg['_variant_applied'] = {'version': VERSION, 'seed': seed, 'skipped': True}
            baseline_graph = filter_complex
            for transformed, original in normalization_replacements:
                baseline_graph = baseline_graph.replace(transformed.rstrip('; '), original.rstrip('; '), 1)
            if blend_graph:
                baseline_graph = baseline_graph.replace(blend_graph.rstrip('; '), '', 1)
                baseline_graph = baseline_graph.replace('[dedup_blended]', '[vout_base]')
                if cover_path:
                    baseline_graph = baseline_graph.replace(f'[{cover_index}:v]', f'[{len(inputs)}:v]')
                baseline_graph = re.sub(r';\s*;', ';', baseline_graph).strip('; ')
            baseline_v = current_v.replace('[dedup_blended]', '[vout_base]')
            baseline_cmd = baseline_inputs + cover_input_args + ['-filter_complex', baseline_graph, '-map', baseline_v, *output_args]
            success, error = render_video(
                baseline_cmd, cfg.get('enable_gpu', True), out_path, self.temp_dir_path,
                config=cfg, log=self.log, is_cancelled=lambda: not self.is_running,
                on_process=self._track_process,
            )
        if not success and self.is_running and cover_path:
            from .hardware import short_error
            message = f'随机封面编码未完成，已回退基础混剪：{short_error(error)}'
            cfg.setdefault('_output_warnings', []).append(message)
            cfg['_cover_applied'] = {'seed': cover_summary['seed'], 'skipped': True}
            self.log(f'    [{self.task_name}] {message}')
            if normalization_replacements or blend:
                # The preceding variant fallback already removed dedup filters.
                for transformed, original in normalization_replacements:
                    base_graph = base_graph.replace(transformed.rstrip('; '), original.rstrip('; '), 1)
                if blend_graph:
                    base_graph = base_graph.replace(blend_graph.rstrip('; '), '', 1).replace('[dedup_blended]', '[vout_base]')
                    base_v = base_v.replace('[dedup_blended]', '[vout_base]')
                    base_graph = re.sub(r';\s*;', ';', base_graph).strip('; ')
            plain_args = []
            if base_audio:
                plain_args.extend(['-map', base_audio, '-c:a', 'aac'])
            plain_args.extend(['-r', fps_val, '-b:v', cfg['bitrate'], '-t', f'{t_total:.3f}'])
            success, error = render_video(
                baseline_inputs + ['-filter_complex', base_graph, '-map', base_v, *plain_args],
                cfg.get('enable_gpu', True), out_path, self.temp_dir_path,
                config=cfg, log=self.log, is_cancelled=lambda: not self.is_running,
                on_process=self._track_process,
            )
        if not success and self.is_running:
            from .hardware import short_error
            self.log(f"    [{self.task_name}] 视频 {task_idx:03d} 失败：{short_error(error)}")

        if success:
            if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
                now_str = datetime.now().strftime("%H:%M:%S")
                self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 输出异常（文件过小或不存在），可能编码失败")
                return (False, None, None) if return_result else False
            workspace['completed'] = True
            if cover_path and not cfg['_cover_applied'].get('skipped'):
                self.log(f"    [{self.task_name}] 随机封面已合并主编码（{cover_summary['mode']}）：取样约 {cover_summary['sample_time']:.3f} 秒")
            if 'id' in hook_clip:
                with self.shared.lock:
                    self.shared.usage_history.add(hook_clip['id'])
                self.shared.save_state()
            elapsed_time = time.time() - start_time
            self.last_output_path = out_path
            self.last_elapsed = round(elapsed_time, 1)
            now_str = datetime.now().strftime("%H:%M:%S")
            self.log(f"    [{now_str}] [{self.task_name}] 视频 {task_idx:03d} 混剪完成，耗时 {elapsed_time:.1f} 秒 -> {os.path.basename(out_path)}")
            return (True, out_path, self.last_elapsed) if return_result else True
        return (False, None, None) if return_result else False

    def _track_process(self, process):
        with self.core_lock:
            if process is not None:
                self.processes.add(process)
            else:
                self.processes = {p for p in self.processes if p.poll() is None}

    def stop(self):
        self.is_running = False
        with self.core_lock:
            processes = list(self.processes)
        for process in processes:
            try:
                process.terminate()
            except Exception:
                pass
