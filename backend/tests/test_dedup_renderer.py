import hashlib
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from app.core.ffmpeg import FFMPEG, FFPROBE
from app.core.video_variant import VideoVariantProcessor


def ffmpeg(*args):
    subprocess.run(
        [FFMPEG, "-y", *map(str, args)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def probe(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


def audio_payload_hash(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a", "-show_packets", "-show_data",
         "-show_entries", "packet=data", "-of", "json", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    payload = "".join(packet.get("data", "") for packet in json.loads(result.stdout)["packets"])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frame_count(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return int(result.stdout.strip())


class DedupRendererTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dedup-renderer-")
        self.root = Path(self.temp.name) / "中文素材"
        self.root.mkdir()
        self.processor = VideoVariantProcessor()
        self.config = {
            "enable_gpu": False,
            "resolution": "96*64",
            "bitrate": "300k",
            "variant_strength": "mild",
            # No Hook/Body timing: the standalone API should make one whole-file recipe.
        }

    def tearDown(self):
        self.temp.cleanup()

    def _make_audio_video(self, path):
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=s=96x64:r=30000/1001:d=2.002",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:d=2.002",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-metadata", "comment=renderer-metadata",
            path,
        )

    def test_keeps_audio_payload_timing_and_video_frame_count(self):
        source = self.root / "源片段.mp4"
        output = self.root / "变换成片.mp4"
        self._make_audio_video(source)
        before = probe(source)
        before_audio = next(stream for stream in before["streams"] if stream["codec_type"] == "audio")
        before_video = next(stream for stream in before["streams"] if stream["codec_type"] == "video")
        payload = audio_payload_hash(source)
        frames = frame_count(source)

        ok, error, summary = self.processor.process_to_file(
            str(source), str(output), self.config, seed=123,
        )

        self.assertTrue(ok, error)
        self.assertEqual(summary["seed"], 123)
        self.assertEqual(audio_payload_hash(output), payload)
        after = probe(output)
        after_audio = next(stream for stream in after["streams"] if stream["codec_type"] == "audio")
        after_video = next(stream for stream in after["streams"] if stream["codec_type"] == "video")
        self.assertEqual(after_audio["codec_name"], before_audio["codec_name"])
        self.assertEqual(after_audio["sample_rate"], "48000")
        self.assertEqual(after_audio["sample_rate"], before_audio["sample_rate"])
        self.assertEqual(after_audio["channels"], before_audio["channels"])
        self.assertAlmostEqual(float(after_audio["start_time"]), float(before_audio["start_time"]), delta=0.05)
        self.assertEqual((after_video["width"], after_video["height"]), (96, 64))
        self.assertAlmostEqual(float(after_video["duration"]), float(before_video["duration"]), delta=0.15)
        self.assertEqual(frame_count(output), frames)
        self.assertEqual(after["format"].get("tags", {}).get("comment"), "renderer-metadata")

    def test_silent_input_and_multiple_segments_render(self):
        source = self.root / "静音.mp4"
        output = self.root / "分段变换.mp4"
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24:d=2.0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
        )
        config = dict(self.config, t_hook=0.75, total_clips=3, t_body=0.625)

        ok, error, summary = self.processor.process_to_file(str(source), str(output), config, seed=9)

        self.assertTrue(ok, error)
        self.assertEqual(summary["segments"], 3)
        streams = probe(output)["streams"]
        self.assertEqual(sum(stream["codec_type"] == "video" for stream in streams), 1)
        self.assertEqual(sum(stream["codec_type"] == "audio" for stream in streams), 0)
        self.assertEqual(frame_count(output), frame_count(source))

    def test_missing_timeline_and_resolution_use_full_source(self):
        source = self.root / "原尺寸.mp4"
        output = self.root / "默认整片.mp4"
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24:d=1.25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", source,
        )
        config = dict(self.config)
        config.pop("resolution")

        ok, error, summary = self.processor.process_to_file(str(source), str(output), config, seed=11)

        self.assertTrue(ok, error)
        result = probe(output)
        video = next(stream for stream in result["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (96, 64))
        self.assertEqual(summary["segments"], 1)
        self.assertEqual(frame_count(output), frame_count(source))
        source_video = next(stream for stream in probe(source)["streams"] if stream["codec_type"] == "video")
        self.assertAlmostEqual(float(video["duration"]), float(source_video["duration"]), delta=0.15)

    def test_rotated_source_defaults_to_display_dimensions_without_rotating_twice(self):
        source = self.root / "手机竖屏旋转标记.mp4"
        raw = self.root / "横屏原始.mp4"
        output = self.root / "手机竖屏正常方向.mp4"
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc2=s=96x64:r=24:d=1.25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", raw,
        )
        ffmpeg('-display_rotation:v:0', '90', '-i', raw, '-c', 'copy', source)
        source_video = next(stream for stream in probe(source)["streams"] if stream["codec_type"] == "video")
        self.assertTrue(self.processor._rotation_swaps_dimensions(source_video))
        self.assertEqual((source_video["width"], source_video["height"]), (96, 64))
        config = dict(self.config)
        config.pop("resolution")

        ok, error, _ = self.processor.process_to_file(str(source), str(output), config, seed=13)

        self.assertTrue(ok, error)
        output_video = next(stream for stream in probe(output)["streams"] if stream["codec_type"] == "video")
        self.assertEqual((output_video["width"], output_video["height"]), (64, 96))
        self.assertAlmostEqual(self.processor._rotation_degrees(output_video), 0.0, delta=0.01)
        self.assertEqual(frame_count(output), frame_count(source))

    def test_attached_picture_does_not_replace_main_video_selection(self):
        cover = {"codec_type": "video", "disposition": {"attached_pic": 1}, "width": 32, "height": 32}
        main = {"codec_type": "video", "disposition": {"attached_pic": 0}, "width": 96, "height": 64}

        selected, ordinal = self.processor._main_video([cover, main])

        self.assertIs(selected, main)
        self.assertEqual(ordinal, 1)

    def test_rejects_same_or_existing_destination_without_changing_it(self):
        source = self.root / "已有输入.mp4"
        self._make_audio_video(source)
        original = source.read_bytes()

        same_ok, same_error, _ = self.processor.process_to_file(
            str(source), str(source), self.config, seed=1,
        )
        existing = self.root / "已存在.mp4"
        existing.write_bytes(b"keep destination")
        existing_bytes = existing.read_bytes()
        exists_ok, exists_error, _ = self.processor.process_to_file(
            str(source), str(existing), self.config, seed=1,
        )

        self.assertFalse(same_ok)
        self.assertIn("不能相同", same_error)
        self.assertFalse(exists_ok)
        self.assertIn("已存在", exists_error)
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(existing.read_bytes(), existing_bytes)

    def test_cancellation_terminates_ffmpeg_and_removes_owned_temp(self):
        source = self.root / "待取消.mp4"
        output = self.root / "不应提交.mp4"
        self._make_audio_video(source)
        source_bytes = source.read_bytes()
        cancelled = threading.Event()

        def on_process(process):
            if process is not None and "ffmpeg" in str(process.args[0]).lower():
                cancelled.set()

        ok, error, _ = self.processor.process_to_file(
            str(source), str(output), self.config, seed=5,
            is_cancelled=cancelled.is_set, on_process=on_process,
        )

        self.assertFalse(ok)
        self.assertEqual(error, "已停止")
        self.assertFalse(output.exists())
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(list(self.root.glob(".*.variant-*.tmp")), [])

    def test_standalone_blend_is_reported_explicitly(self):
        source = self.root / "blend-source.mp4"
        output = self.root / "blend-output.mp4"
        self._make_audio_video(source)
        ok, error, _ = self.processor.process_to_file(
            str(source), str(output), dict(self.config, variant_blend_enabled=True), seed=5,
        )
        self.assertFalse(ok)
        self.assertEqual(error, "独立文件模式暂不支持B混合")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
