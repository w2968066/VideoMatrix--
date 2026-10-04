"""Manual synthetic benchmark: legacy full-video cover pass vs fused cover.

Run from backend: python -m tests.benchmark_fused_cover
Only disposable generated media is rendered; no user configuration is loaded.
"""
import concurrent.futures
import ctypes
import json
import os
import statistics
import tempfile
import threading
import time
from pathlib import Path

from app.core.ffmpeg import probe_media
from app.core.hardware import HardwareSession
from app.core.video_cover import RandomCoverProcessor
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from tests.benchmark_dedup_v2 import _create_media, _config, _sha256, SELECTION_SEED


class MemorySampler:
    """Sample aggregate live FFmpeg working sets (not GPU VRAM), Windows only."""
    def __init__(self):
        self.processes = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.peak = 0
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()

    def track(self, process):
        if process is not None:
            with self.lock:
                self.processes.append(process)

    def _sample(self):
        if os.name != 'nt':
            return
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'peak_ws', 'ws', 'peak_pool_paged', 'pool_paged',
                    'peak_pool_nonpaged', 'pool_nonpaged', 'pagefile', 'peak_pagefile')]

        query = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        query.restype = wintypes.BOOL
        while not self.stop.is_set():
            with self.lock:
                active = self.processes[:]
            total = 0
            for process in active:
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                if process.poll() is None and query(int(process._handle), ctypes.byref(counters), counters.cb):
                    total += counters.ws
            self.peak = max(self.peak, total)
            self.stop.wait(.05)

    def close(self):
        self.stop.set()
        self.thread.join()


def run():
    with tempfile.TemporaryDirectory(prefix='vm-cover-benchmark-') as directory:
        root = Path(directory)
        source, blend = _create_media(root)
        source_hash = _sha256(source)
        config = dict(resolution='1920*1080', fps='30', bitrate='8000k',
                      enable_gpu=True, concurrent_tasks=2)
        session = HardwareSession(config, state_dir=str(root / 'hardware'))
        session.benchmark_blend_path = str(blend)
        if not session.result.get('available'):
            session.close()
            raise RuntimeError('No hardware encoder available for comparable GPU benchmark')
        encoders = []
        execute = session._execute

        def record(base, tail, encoder, stage, runner):
            encoders.append(encoder)
            return execute(base, tail, encoder, stage, runner)

        session._execute = record

        def batch(kind, mode, repeat):
            output = root / f'{kind}-{mode}-{repeat}'
            output.mkdir()
            sampler = MemorySampler()
            cores = []
            for index in range(2):
                cfg = _config(output, session, 'off')
                cfg.update(config, enable_random_cover=kind == 'fused', random_cover_mode=mode,
                           _cover_seed=37, _selection_seed=SELECTION_SEED + index)
                core = VideoMatrixCore(cfg, lambda _: None, SharedMediaCache(str(output / f'state-{index}')))
                core.hook_pool = [dict(file=str(source), start=0., duration=1., has_audio=True)]
                core.body_pool = [dict(file=str(source), start=float(n), duration=1., has_audio=True)
                                  for n in range(1, 6)]
                original_track = core._track_process

                def track(process, original=original_track):
                    sampler.track(process)
                    original(process)

                core._track_process = track
                cores.append(core)

            def job(index):
                core = cores[index]
                ok, path, _ = core.render_single_video(index + 1, True)
                if not ok:
                    raise RuntimeError('Main encode failed')
                resolved = core.output_configs[index + 1]
                if kind == 'legacy':
                    ok, error, _ = RandomCoverProcessor().process(
                        path, resolved, seed=37 + index, on_process=core._track_process)
                    if not ok:
                        raise RuntimeError(error)
                if resolved.get('_output_warnings'):
                    raise RuntimeError(resolved['_output_warnings'])
                return path

            encoders.clear()
            started = time.perf_counter()
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    paths = list(executor.map(job, range(2)))
                seconds = time.perf_counter() - started
            finally:
                sampler.close()
                for core in cores:
                    core.temp_dir.cleanup()
            attempts = encoders[:]
            for path in paths:
                info = probe_media(path)
                video = next(s for s in info['streams'] if s['codec_type'] == 'video')
                assert int(video['nb_frames']) == 180 + (mode == 'insert'), video
                assert (video['width'], video['height']) == (1920, 1080)
                assert any(s['codec_type'] == 'audio' for s in info['streams'])
            expected = 4 if kind == 'legacy' else 2
            if len(attempts) != expected or set(attempts) != {session.candidates[0]}:
                raise RuntimeError(f'Unexpected encoder fallback/attempts: {attempts}')
            result = dict(kind=kind, mode=mode, repeat=repeat, batch_seconds=round(seconds, 3),
                          ffmpeg_peak_working_set_mib=round(sampler.peak / 1024**2, 1),
                          encoders=attempts)
            print(json.dumps(result), flush=True)
            return result

        records = []
        cases = [('legacy', 'replace'), ('fused', 'replace'), ('legacy', 'insert'), ('fused', 'insert')]
        try:
            for kind, mode in cases:
                batch(kind, mode, 0)
            for repeat in range(1, 4):
                for kind, mode in (cases if repeat % 2 else cases[::-1]):
                    records.append(batch(kind, mode, repeat))
        finally:
            session.close()
        assert _sha256(source) == source_hash
        summary = {}
        for kind, mode in cases:
            rows = [r for r in records if (r['kind'], r['mode']) == (kind, mode)]
            summary[f'{kind}-{mode}'] = dict(
                median_batch_seconds=round(statistics.median(r['batch_seconds'] for r in rows), 3),
                max_ffmpeg_working_set_mib=max(r['ffmpeg_peak_working_set_mib'] for r in rows))
        print(json.dumps(dict(summary=summary, concurrent_tasks=2, clips_per_output=6,
                              seconds_per_output=6, resolution='1920x1080', fps=30,
                              measured_batches_per_case=3, source_unchanged=True)), flush=True)


if __name__ == '__main__':
    run()
