import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.video_variant import VideoVariantProcessor, build_variant_plan, derive_variant_seed


def config(**overrides):
    value = {
        "resolution": "96*64",
        "t_hook": 3.0,
        "t_body": 2.0,
        "total_clips": 4,
        "variant_strength": "balanced",
        "variant_hook": True,
        "variant_body": True,
        "variant_crop": True,
        "variant_color": True,
        "variant_mirror": True,
    }
    value.update(overrides)
    return value


class VariantPlanTests(unittest.TestCase):
    def test_seed_keeps_original_v1_namespace(self):
        self.assertEqual(derive_variant_seed(42, "shirt", 1), 17300054937814657454)
        self.assertNotEqual(
            derive_variant_seed(42, "shirt", 1),
            derive_variant_seed(42, "shirt", 2),
        )

    def test_v2_plan_has_exact_timing_and_new_parameters(self):
        plan = build_variant_plan(config(), 1234)
        self.assertEqual(len(plan), 4)
        self.assertEqual([item.start for item in plan], [0.0, 3.0, 5.0, 7.0])
        self.assertEqual(sum(item.duration for item in plan), 9.0)
        self.assertTrue(all(item.enabled for item in plan))
        self.assertTrue(all(0.005 <= item.crop_total <= 0.01 for item in plan))
        self.assertTrue(all(0.982 <= item.contrast <= 1.018 for item in plan))

    def test_hook_and_body_can_be_disabled_independently(self):
        hook_off = build_variant_plan(config(variant_hook=False), 99)
        self.assertFalse(hook_off[0].enabled)
        self.assertTrue(all(item.enabled for item in hook_off[1:]))

        body_off = build_variant_plan(config(variant_body=False), 99)
        self.assertTrue(body_off[0].enabled)
        self.assertTrue(all(not item.enabled for item in body_off[1:]))

    def test_crop_color_mirror_defaults_and_toggles_are_respected(self):
        default_config = config()
        default_config.pop("variant_color")
        default_config.pop("variant_mirror")
        defaults = build_variant_plan(default_config, 17)
        self.assertTrue(all(item.crop_total > 0 for item in defaults))
        self.assertTrue(all(item.brightness == 0 for item in defaults))
        self.assertTrue(all(item.contrast == 1 for item in defaults))
        self.assertTrue(all(item.saturation == 1 for item in defaults))
        self.assertTrue(all(not item.mirror for item in defaults))

        disabled = build_variant_plan(config(variant_crop=False), 17)
        self.assertTrue(all(item.crop_total == 0 for item in disabled))

    def test_legacy_frame_mix_setting_is_ignored(self):
        with_old_setting = build_variant_plan(config(variant_frame_mix=True), 42)
        without_old_setting = build_variant_plan(config(variant_frame_mix=False), 42)
        self.assertEqual(with_old_setting, without_old_setting)

    def test_completed_output_retries_transient_windows_lock(self):
        processor = VideoVariantProcessor()
        with patch("app.core.video_variant.os.replace", side_effect=[PermissionError(32, "locked"), None]) as replace:
            with patch("app.core.video_variant.time.sleep") as sleep:
                processor._replace_with_retry(Path("work.tmp"), Path("final.mp4"), attempts=2)

        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
