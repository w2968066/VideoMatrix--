"""Isolated local benchmark for VideoMatrix's fused deduplication V2 path.

Run manually from the backend directory with the project's Python runtime.
The script writes only beneath TemporaryDirectory and emits one JSON document.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.ffmpeg import FFMPEG, FFPROBE, probe_media  # noqa: E402
from app.core.hardware import HardwareSession  # noqa: E402
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore  # noqa: E402


WIDTH = 1920
HEIGHT = 1080
FPS = "30"
DURATION = 6.0
HOOK_SECONDS = 1.0
BODY_SECONDS = 1.0
BODY_COUNT = 5
TASK_NAME = "DedupV2Benchmark"
SELECTION_SEED = 20261002
VARIANT_SEED = 88172645463325252

# Latin-square order puts every condition in every position over three rounds.
SCHEDULE = (
    ("off", 1), ("crop", 1), ("blend", 1),
    ("crop", 2), ("blend", 2), ("off", 2),
    ("blend", 3), ("off", 3), ("crop", 3),
)


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "FFmpeg failed").strip()
        raise RuntimeError(detail[-2000:])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _audio_packet_sha256(path: Path) -> str:
    result = subprocess.run(
        [
            FFPROBE, "-v", "error", "-select_streams", "a", "-show_packets",
            "-show_data", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    packets = json.loads(result.stdout).get("packets", [])
    if not packets:
        raise RuntimeError(f"output has no audio packets: {path.name}")
    payload = "".join(packet.get("data", "") for packet in packets)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _create_media(root: Path) -> tuple[Path, Path]:
    source = root / "synthetic-source-1080p.mp4"
    blend = root / "synthetic-blend-1080p.mp4"
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={DURATION}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", str(source),
    ])
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:size={WIDTH}x{HEIGHT}:rate={FPS}:duration={DURATION}",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(blend),
    ])
    return source, blend


class _RecordingRandom(random.Random):
    def __init__(self, seed: int):
        super().__init__(seed)
        self.body_selections: list[list[float]] = []

    def sample(self, population, k, *, counts=None):
        selected = super().sample(population, k, counts=counts)
        if selected and isinstance(selected[0], dict) and "start" in selected[0]:
            self.body_selections.append([float(item["start"]) for item in selected])
        return selected


def _config(output_dir: Path, session: HardwareSession, mode: str) -> dict[str, Any]:
    return {
        "task_name": TASK_NAME,
        "out_dir": str(output_dir),
        "hook_dir": "synthetic-hook",
        "body_dirs": ["synthetic-body"],
        "t_hook": HOOK_SECONDS,
        "t_body": BODY_SECONDS,
        "total_clips": BODY_COUNT + 1,
        "hook_r": 1.0,
        "body_r": 1.0,
        "resolution": f"{WIDTH}*{HEIGHT}",
        "fps": FPS,
        "bitrate": "8000k",
        "vol_hook_orig": 100,
        "vol_orig": 100,
        "vol_bgm": 0,
        "vol_voice": 0,
        "enable_gpu": True,
        "concurrent_tasks": 1,
        "enable_variants": mode != "off",
        "variant_crop": True,
        "variant_color": False,
        "variant_mirror": False,
        "variant_seed": VARIANT_SEED,
        "_selection_seed": SELECTION_SEED,
        "_hardware_session": session,
        "variant_blend_enabled": mode == "blend",
        "variant_blend_path": str(session.benchmark_blend_path),
        "variant_blend_opacity": 0.03,
        "variant_blend_eof": "loop",
    }


def _render_trial(
    root: Path,
    source: Path,
    session: HardwareSession,
    mode: str,
    repeat: int,
    position: int,
    actual_encoders: list[str],
) -> dict[str, Any]:
    output_dir = root / "outputs" / f"{mode}-{repeat}"
    output_dir.mkdir(parents=True, exist_ok=False)
    state_dir = root / "state" / f"{mode}-{repeat}"
    shared = SharedMediaCache(str(state_dir))
    config = _config(output_dir, session, mode)
    core = VideoMatrixCore(config, lambda _message: None, shared)
    recorder = _RecordingRandom(SELECTION_SEED)
    core.rng = recorder
    core.hook_pool = [{"file": str(source), "start": 0.0, "duration": HOOK_SECONDS, "has_audio": True}]
    core.body_pool = [
        {"file": str(source), "start": float(index), "duration": BODY_SECONDS, "has_audio": True}
        for index in range(1, BODY_COUNT + 1)
    ]
    core.body_group_pools = []
    core.bgm_pool = []
    core.voice_pool = []
    actual_encoders.clear()
    try:
        started = time.perf_counter()
        ok, output_path_text, app_elapsed = core.render_single_video(1, return_result=True)
        wall_seconds = time.perf_counter() - started
        if not ok or not output_path_text:
            detail = core.output_configs.get(1, {}).get("_variant_warnings") or []
            raise RuntimeError(f"{mode} render {repeat} failed: {detail or 'render returned false'}")
        output_path = Path(output_path_text)
        info = probe_media(str(output_path))
        video = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "video"), None)
        if video is None:
            raise RuntimeError(f"output video stream missing: {output_path.name}")
        duration_value = video.get("duration") or info.get("format", {}).get("duration")
        output_duration = float(duration_value or 0)
        applied = core.output_configs[1].get("_variant_applied", {})
        if applied.get("skipped"):
            raise RuntimeError(f"{mode} variant path unexpectedly skipped: {applied}")
        if core.output_configs[1].get("_variant_warnings"):
            raise RuntimeError(f"{mode} emitted variant warnings: {core.output_configs[1]['_variant_warnings']}")
        if not recorder.body_selections:
            raise RuntimeError("body selection was not captured")
        return {
            "position": position,
            "mode": mode,
            "repeat": repeat,
            "wall_seconds": round(wall_seconds, 6),
            "app_elapsed_seconds": app_elapsed,
            "encoder_attempts": list(actual_encoders),
            "body_start_selection": recorder.body_selections[-1],
            "source_sha256": _sha256(source),
            "audio_packets_sha256": _audio_packet_sha256(output_path),
            "output_duration_seconds": round(output_duration, 6),
            "output_frames": int(video.get("nb_frames") or 0),
            "output_path": str(output_path.relative_to(root)),
            "variant_summary": applied,
        }
    finally:
        core.temp_dir.cleanup()


def run_benchmark(include_blend: bool = True) -> dict[str, Any]:
    overall_started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="dedup-v2-benchmark-") as temporary:
        root = Path(temporary).resolve()
        source, blend = _create_media(root)
        source_hash = _sha256(source)
        session_config: dict[str, Any] = {
            "task_name": TASK_NAME,
            "resolution": f"{WIDTH}*{HEIGHT}",
            "fps": FPS,
            "bitrate": "8000k",
            "enable_gpu": True,
            "concurrent_tasks": 1,
        }
        # Keep the hardware capability cache inside this run's disposable root.
        session = HardwareSession(session_config, log=lambda _message: None,
                                  state_dir=str(root / "hardware-state"))
        session.benchmark_blend_path = str(blend)
        if not session.result.get("available"):
            session.close()
            raise RuntimeError(f"no hardware encoder available: {session.result.get('failures', {})}")
        expected_encoder = session.candidates[0]
        actual_encoders: list[str] = []
        original_execute = session._execute

        def record_encoder(base, tail, encoder, stage, runner):
            actual_encoders.append(encoder)
            return original_execute(base, tail, encoder, stage, runner)

        session._execute = record_encoder
        records: list[dict[str, Any]] = []
        schedule = SCHEDULE if include_blend else (
            ("off", 1), ("crop", 1), ("crop", 2),
            ("off", 2), ("off", 3), ("crop", 3),
        )
        modes = ("off", "crop", "blend") if include_blend else ("off", "crop")
        source_hashes = {source_hash}
        body_selections: set[tuple[float, ...]] = set()
        audio_hashes: set[str] = set()
        encoder_mismatch: list[dict[str, Any]] = []
        try:
            # Warm each graph and codec path before collecting timed samples.
            for mode in modes:
                _render_trial(root, source, session, mode, 0, 0, actual_encoders)
            for position, (mode, repeat) in enumerate(schedule, start=1):
                record = _render_trial(root, source, session, mode, repeat, position, actual_encoders)
                record["expected_encoder"] = expected_encoder
                if not record["encoder_attempts"] or set(record["encoder_attempts"]) != {expected_encoder}:
                    encoder_mismatch.append({
                        "position": position,
                        "expected": expected_encoder,
                        "attempts": record["encoder_attempts"],
                    })
                if abs(record["output_duration_seconds"] - DURATION) > 0.05:
                    raise RuntimeError(
                        f"{mode} output duration {record['output_duration_seconds']}s is not 6s"
                    )
                source_hashes.add(record["source_sha256"])
                body_selections.add(tuple(record["body_start_selection"]))
                audio_hashes.add(record["audio_packets_sha256"])
                records.append(record)
        finally:
            session.close()

        if len(source_hashes) != 1:
            raise RuntimeError("synthetic source changed during benchmark")
        if len(body_selections) != 1:
            raise RuntimeError("selection order changed between benchmark trials")
        if len(audio_hashes) != 1:
            raise RuntimeError("audio packets changed between benchmark trials")

        aggregates: dict[str, Any] = {}
        for mode in modes:
            trials = [record["wall_seconds"] for record in records if record["mode"] == mode]
            aggregates[mode] = {
                "samples": len(trials),
                "mean_wall_seconds": round(statistics.mean(trials), 6),
                "median_wall_seconds": round(statistics.median(trials), 6),
                "trials_wall_seconds": trials,
            }

        return {
            "status": "complete" if not encoder_mismatch else "invalid_encoder_mismatch",
            "benchmark": "videomatrix-dedup-v2-fused-render",
            "platform": platform.platform(),
            "warmup_per_case": 1,
            "source_sha256": source_hash,
            "source_duration_seconds": DURATION,
            "resolution": f"{WIDTH}x{HEIGHT}",
            "fps": FPS,
            "output_duration_target_seconds": DURATION,
            "task_name": TASK_NAME,
            "selection_seed": SELECTION_SEED,
            "variant_seed": VARIANT_SEED,
            "concurrent_tasks": 1,
            "encoder": expected_encoder,
            "encoder_mismatches": encoder_mismatch,
            "selection_stable": len(body_selections) == 1,
            "audio_packet_hash_stable": len(audio_hashes) == 1,
            "cases": {
                "off": "variants disabled",
                "crop": "default crop only",
                "blend": "default crop plus 3% B-picture blend",
            } if include_blend else {
                "off": "variants disabled",
                "crop": "default crop only",
            },
            "schedule": [{"position": index, "mode": mode, "repeat": repeat}
                         for index, (mode, repeat) in enumerate(schedule, start=1)],
            "aggregates": aggregates,
            "runs": records,
            "benchmark_wall_seconds": round(time.perf_counter() - overall_started, 6),
        }


def main() -> int:
    try:
        parser = argparse.ArgumentParser(add_help=True)
        parser.add_argument("--skip-blend", action="store_true",
                            help="temporarily run only the OFF and default-crop cases")
        args = parser.parse_args()
        result = run_benchmark(include_blend=not args.skip_blend)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result["status"] == "complete" else 1
    except Exception as exc:
        print(json.dumps({
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
