import unittest

from agent_adapter.client import VideoMatrixClient
from agent_adapter.preparation import AGENT_DEFAULTS, build_prepare_update, derive_timing
from agent_adapter.profiles import build_config, normalize_profile


class ProfileTests(unittest.TestCase):
    def test_finished_hook_alias_and_defaults(self):
        self.assertEqual(normalize_profile("成品Hook"), "finished-hook")
        config = build_config(profile="成品Hook", hook_dir="H", body_dir="B", bgm_path="M")
        self.assertEqual(config["vol_hook_orig"], 100)
        self.assertFalse(config["apply_bgm_to_hook"])
        self.assertFalse(config["apply_watermark_to_hook"])

    def test_render_config_keeps_core_required_fallbacks(self):
        config = build_config(profile="standard", hook_dir="H")
        self.assertEqual(config["body_dirs"], ["H"])
        self.assertEqual(config["bgm_dir"], "H")

    def test_new_render_defaults_are_vertical_2k(self):
        config = build_config(profile="standard", hook_dir="H")
        self.assertEqual(config["resolution"], "1440*2560")
        self.assertEqual(config["fps"], "24")
        self.assertEqual(config["bitrate"], "8000k")


class PreparationTests(unittest.TestCase):
    def test_path_only_prepare_preserves_all_other_settings(self):
        update = build_prepare_update(hook_dir=r"D:\Hook", body_dir=r"D:\Body")
        self.assertEqual(update, {"hook_dir": r"D:\Hook", "body_dirs": [r"D:\Body"]})

    def test_defaults_are_opt_in(self):
        update = build_prepare_update(hook_dir="H", use_defaults=True)
        for key, value in AGENT_DEFAULTS.items():
            self.assertEqual(update[key], value)

    def test_total_duration_derives_exact_body_duration_and_clip_count(self):
        timing = derive_timing(total_seconds=12, hook_seconds=3)
        self.assertEqual(timing, {"t_hook": 3, "t_body": 1.8, "total_clips": 6})

    def test_explicit_body_duration_derives_clip_count_and_stays_exact(self):
        timing = derive_timing(total_seconds=15, hook_seconds=3, body_seconds=2)
        self.assertEqual(timing, {"t_hook": 3, "t_body": 2.0, "total_clips": 7})

    def test_total_duration_requires_hook_duration(self):
        with self.assertRaisesRegex(ValueError, "首段时长"):
            derive_timing(total_seconds=12)


class SummaryTests(unittest.TestCase):
    def test_task_summary_does_not_return_file_list_or_logs(self):
        summary = VideoMatrixClient._summarize_task(
            {
                "task_id": "t1",
                "status": "completed",
                "progress": 100,
                "current": 2,
                "total": 2,
                "output_files": [r"F:\out\a.mp4", r"F:\out\b.mp4"],
                "output_elapsed": {"a": 4.0, "b": 6.0},
                "log_lines": ["large log"],
            }
        )
        self.assertEqual(summary["outputs"], 2)
        self.assertEqual(summary["average_seconds"], 5.0)
        self.assertNotIn("output_files", summary)
        self.assertNotIn("log_lines", summary)


if __name__ == "__main__":
    unittest.main()
