import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.ffmpeg import FFMPEG, FFPROBE, probe_media
from app.core.timeline import apply_timeline_totals
from app.core.video_cover import RandomCoverProcessor
from app.core.video_matrix import SharedMediaCache, VideoMatrixCore
from app.core.video_variant import VideoVariantProcessor, build_variant_plan
from app.models.schemas import TaskStatus
from app.services.task_service import TaskService
from datetime import datetime


def run(*args):
    subprocess.run([FFMPEG, "-y", *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def audio_hash(path: str) -> str:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_packets", "-show_data", "-of", "json", path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return hashlib.sha256(
        "".join(packet.get("data", "") for packet in json.loads(result.stdout)["packets"]).encode()
    ).hexdigest()


def first_audio_pts(path: str) -> float:
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_packets", "-of", "json", path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return float(json.loads(result.stdout)["packets"][0]["pts_time"])


def frame_bytes(path: str, index: int) -> bytes:
    result = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout


def assert_frame_close(test: unittest.TestCase, left: bytes, right: bytes):
    test.assertEqual(len(left), len(right))
    mean_error = sum(abs(a - b) for a, b in zip(left, right)) / len(left)
    test.assertLess(mean_error, 10, f"frame drift too large: {mean_error:.2f}")


def dominant_channel(frame: bytes) -> int:
    means = [sum(frame[index::3]) for index in range(3)]
    return means.index(max(means))


class GroupedBodyTests(unittest.TestCase):
    def test_grouped_timeline_drives_variant_segment_durations(self):
        config = {
            "t_hook": 1.0, "t_body": 99, "total_clips": 9, "body_mode": "grouped",
            "body_groups": [
                {"enabled": True, "folder": "one", "clip_count": 2, "clip_duration": 1.5},
                {"enabled": False, "folder": "off", "clip_count": 9, "clip_duration": 8},
                {"enabled": True, "folder": "two", "clip_count": 1, "clip_duration": 2.0},
            ],
        }
        apply_timeline_totals(config)
        self.assertEqual(config["total_clips"], 4)
        self.assertEqual(config["total_duration"], 6.0)
        plan = build_variant_plan(config, 12)
        self.assertEqual([item.duration for item in plan], [1.0, 1.5, 1.5, 2.0])
        self.assertEqual([item.start for item in plan], [0.0, 1.0, 2.5, 4.0])

    def test_group_specific_shortage_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("hook", "g1", "g2", "bgm"):
                (root / name).mkdir()
            # Only group 1 receives one qualifying source; group 2 remains empty.
            run("-f", "lavfi", "-i", "color=red:s=64x64:d=2", str(root / "hook" / "h.mp4"))
            run("-f", "lavfi", "-i", "color=blue:s=64x64:d=2", str(root / "g1" / "a.mp4"))
            run("-f", "lavfi", "-i", "sine=frequency=500:d=4", str(root / "bgm" / "b.wav"))
            config = {
                "task_name": "group-test", "hook_dir": str(root / "hook"), "body_dirs": [],
                "body_mode": "grouped", "body_groups": [
                    {"enabled": True, "folder": str(root / "g1"), "clip_count": 1, "clip_duration": 1.0},
                    {"enabled": True, "folder": str(root / "g2"), "clip_count": 1, "clip_duration": 1.0},
                ], "bgm_dir": str(root / "bgm"), "t_hook": 1.0, "t_body": 1.0,
                "total_clips": 3, "target_count": 1, "hook_r": 1.0, "body_r": 1.0,
                "bgm_r": 1.0, "resolution": "64*64", "fps": "24", "bitrate": "300k",
            }
            core = VideoMatrixCore(config, lambda _message: None, SharedMediaCache(str(root / "state")))
            ok, message = core.pre_flight_check()
            self.assertFalse(ok)
            self.assertIn("Body 分组 2", message)


class RandomCoverTests(unittest.TestCase):
    def _exercise_cover(self, fps: str, mode: str = "replace"):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "input.mp4")
            run("-f", "lavfi", "-i", "testsrc2=s=96x64:r=" + fps + ":d=2", "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-c:v", "libx264", "-c:a", "aac", path)
            before = probe_media(path)
            before_video = next(item for item in before["streams"] if item["codec_type"] == "video")
            before_frames = RandomCoverProcessor._frame_count(path, before_video, 2, 24)
            before_audio = audio_hash(path)
            before_audio_pts = first_audio_pts(path)
            source_frame = frame_bytes(path, 0 if mode == "insert" else 1)
            ok, error, _ = RandomCoverProcessor().process(
                path, {"enable_gpu": False, "bitrate": "300k", "random_cover_mode": mode}, 54,
            )
            self.assertTrue(ok, error)
            after = probe_media(path)
            after_video = next(item for item in after["streams"] if item["codec_type"] == "video")
            self.assertEqual((after_video["width"], after_video["height"]), (96, 64))
            self.assertEqual(
                RandomCoverProcessor._frame_count(path, after_video, 2, 24),
                before_frames + (1 if mode == "insert" else 0),
            )
            self.assertEqual(audio_hash(path), before_audio)
            assert_frame_close(self, frame_bytes(path, 1), source_frame)
            if mode == "insert":
                fps_value = 30000 / 1001 if "/" in fps else float(fps)
                # Packet PTS are quantized to the source audio timebase; mux offset stays within one AAC frame.
                self.assertLess(abs(first_audio_pts(path) - before_audio_pts - 1 / fps_value), 1024 / 44100)
            else:
                self.assertEqual(first_audio_pts(path), before_audio_pts)

    def test_cover_preserves_audio_and_frame_count_at_24fps(self):
        self._exercise_cover("24")

    def test_cover_preserves_audio_and_frame_count_at_2997fps(self):
        self._exercise_cover("30000/1001")

    def test_insert_cover_offsets_audio_and_adds_one_frame_at_24fps(self):
        self._exercise_cover("24", "insert")

    def test_insert_cover_offsets_audio_and_adds_one_frame_at_2997fps(self):
        self._exercise_cover("30000/1001", "insert")

    def test_independent_seek_selects_first_middle_and_last_frames_without_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for fps in ("24", "30000/1001"):
                source = root / "source.mp4"
                # Alternating luminance makes an off-by-one seek conspicuous.
                run("-f", "lavfi", "-i", f"nullsrc=s=96x64:r={fps},geq=lum='40+mod(N,2)*160':cb=128:cr=128",
                    "-frames:v", "60", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source))
                original = source.read_bytes()
                first = frame_bytes(str(source), 0)
                second = frame_bytes(str(source), 1)
                for mode in ("replace", "insert"):
                    for sample in (0, 31, 59):
                        with self.subTest(fps=fps, mode=mode, sample=sample):
                            output = root / "output.mp4"
                            output.write_bytes(original)
                            expected = frame_bytes(str(source), sample)
                            with patch("app.core.video_cover.random.Random.randrange", return_value=sample), \
                                 patch("app.core.video_cover.random.Random.uniform", side_effect=[1.0, 0.0, 0.0]):
                                ok, error, _ = RandomCoverProcessor().process(
                                    str(output), {"enable_gpu": False, "bitrate": "300k", "random_cover_mode": mode}, 54,
                                )
                            self.assertTrue(ok, error)
                            streams = probe_media(str(output))["streams"]
                            video = next(item for item in streams if item["codec_type"] == "video")
                            self.assertEqual(int(video["nb_frames"]), 60 + (mode == "insert"))
                            self.assertFalse(any(item["codec_type"] == "audio" for item in streams))
                            assert_frame_close(self, frame_bytes(str(output), 0), expected)
                            assert_frame_close(self, frame_bytes(str(output), 1), first if mode == "insert" else second)

    def test_frame_metadata_avoids_full_decode(self):
        with patch.object(RandomCoverProcessor, "_probe_output") as probe:
            self.assertEqual(RandomCoverProcessor._frame_count("unused.mp4", {"nb_frames": "72000"}, 3000, 24), 72000)
            probe.assert_not_called()

    def test_probe_timeout_and_cancellation_reap_process(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                tracked = []
                started = time.monotonic()
                with self.assertRaises(InterruptedError if cancelled else TimeoutError):
                    RandomCoverProcessor._probe_output(
                        [sys.executable, "-c", "import time; time.sleep(10)"],
                        is_cancelled=(lambda: time.monotonic() - started > 0.15) if cancelled else None,
                        on_process=tracked.append, timeout=0.3,
                    )
                self.assertLess(time.monotonic() - started, 3)
                self.assertIsNotNone(tracked[0].poll())
                self.assertIsNone(tracked[-1])

    def test_failed_cover_keeps_original_and_cleans_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.mp4"
            run("-f", "lavfi", "-i", "testsrc2=s=64x64:r=24:d=1", "-c:v", "libx264", str(path))
            before = path.read_bytes()

            def failed_run(_base, tail, *_args, **_kwargs):
                Path(tail[-1]).write_bytes(b"partial render")
                return False, "模拟编码失败"

            with patch("app.core.video_cover.session_for") as session:
                session.return_value.run.side_effect = failed_run
                ok, error, _ = RandomCoverProcessor().process(str(path), {"enable_gpu": False}, 9)
            self.assertFalse(ok)
            self.assertEqual(error, "模拟编码失败")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_cancelled_cover_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "input.mp4")
            run("-f", "lavfi", "-i", "testsrc=s=64x64:r=24:d=1", "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libx264", "-c:a", "aac", path)
            before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            ok, error, _ = RandomCoverProcessor().process(path, {"enable_gpu": False}, 9, is_cancelled=lambda: True)
            self.assertFalse(ok)
            self.assertEqual(error, "已停止")
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), before)

    def test_grouped_render_then_variants_and_cover(self):
        """Synthetic end-to-end regression for the two post-mix transforms."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("hook", "g1", "g2", "bgm", "out"):
                (root / name).mkdir()
            for folder, color in (("hook", "red"), ("g1", "green"), ("g2", "blue")):
                run("-f", "lavfi", "-i", f"color={color}:s=64x64:r=24:d=2", str(root / folder / "clip.mp4"))
            run("-f", "lavfi", "-i", "sine=frequency=700:d=4", str(root / "bgm" / "track.wav"))
            config = {
                "task_name": "e2e", "hook_dir": str(root / "hook"), "body_dirs": [], "body_mode": "grouped",
                "body_groups": [
                    {"enabled": True, "folder": str(root / "g1"), "clip_count": 2, "clip_duration": 0.5},
                    {"enabled": True, "folder": str(root / "g2"), "clip_count": 1, "clip_duration": 1.0},
                ], "bgm_dir": str(root / "bgm"), "out_dir": str(root / "out"), "t_hook": 1.0,
                "t_body": 1.0, "total_clips": 4, "target_count": 1, "hook_r": 1.0, "body_r": 0.5,
                "bgm_r": 1.0, "resolution": "64*64", "fps": "24", "bitrate": "300k", "enable_gpu": False,
                "vol_orig": 0, "vol_bgm": 100, "vol_voice": 0,
                "enable_variants": True, "variant_seed": 91, "enable_random_cover": True, "_cover_seed": 37,
            }
            core = VideoMatrixCore(config, lambda _message: None, SharedMediaCache(str(root / "state")))
            self.assertTrue(core.pre_flight_check()[0])
            service = TaskService(SharedMediaCache(str(root / "service-state")))
            status = TaskStatus(task_id="e2e", task_name="e2e", status="running", created_at=datetime.now())
            service.tasks["e2e"] = status
            self.assertTrue(service._render_job(core, 1, status))
            output = status.output_files[0]
            info = probe_media(output)
            video = next(item for item in info["streams"] if item["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (64, 64))
            self.assertEqual(RandomCoverProcessor._frame_count(output, video, 3, 24), 72)
            self.assertTrue(any(item["codec_type"] == "audio" for item in info["streams"]))
            self.assertEqual(dominant_channel(frame_bytes(output, 30)), 1)  # group 1 green at 1.25s
            self.assertEqual(dominant_channel(frame_bytes(output, 54)), 2)  # group 2 blue at 2.25s


if __name__ == "__main__":
    unittest.main()
