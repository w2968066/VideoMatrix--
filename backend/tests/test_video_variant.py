import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.video_variant import VideoVariantProcessor, build_variant_plan, derive_variant_seed


def config(**overrides):
    value = {
        "t_hook": 3.0,
        "t_body": 2.0,
        "total_clips": 4,
        "variant_strength": "balanced",
        "variant_hook": True,
        "variant_body": True,
        "variant_mirror": True,
        "variant_frame_mix": True,
    }
    value.update(overrides)
    return value


class VariantPlanTests(unittest.TestCase):
    def test_seed_is_stable_per_task_and_output(self):
        self.assertEqual(
            derive_variant_seed(42, "shirt", 1),
            derive_variant_seed(42, "shirt", 1),
        )
        self.assertNotEqual(
            derive_variant_seed(42, "shirt", 1),
            derive_variant_seed(42, "shirt", 2),
        )

    def test_each_segment_has_independent_parameters_and_exact_timing(self):
        plan = build_variant_plan(config(), 1234)
        self.assertEqual(len(plan), 4)
        self.assertEqual([item.start for item in plan], [0.0, 3.0, 5.0, 7.0])
        self.assertEqual(sum(item.duration for item in plan), 9.0)
        self.assertGreater(len({item.zoom for item in plan}), 1)
        self.assertGreater(len({item.brightness for item in plan}), 1)

    def test_hook_and_body_can_be_disabled_independently(self):
        hook_off = build_variant_plan(config(variant_hook=False), 99)
        self.assertFalse(hook_off[0].enabled)
        self.assertTrue(all(item.enabled for item in hook_off[1:]))

        body_off = build_variant_plan(config(variant_body=False), 99)
        self.assertTrue(body_off[0].enabled)
        self.assertTrue(all(not item.enabled for item in body_off[1:]))

    def test_mirror_and_frame_mix_toggles_are_respected(self):
        plan = build_variant_plan(
            config(variant_mirror=False, variant_frame_mix=False),
            5678,
        )
        self.assertTrue(all(not item.mirror for item in plan))
        self.assertTrue(all(item.frame_mix == 0 for item in plan))

    def test_completed_output_retries_transient_windows_lock(self):
        processor = VideoVariantProcessor()
        with patch("app.core.video_variant.os.replace", side_effect=[PermissionError(32, "locked"), None]) as replace:
            with patch("app.core.video_variant.time.sleep") as sleep:
                processor._replace_with_retry(Path("work.tmp"), Path("final.mp4"), attempts=2)

        self.assertEqual(replace.call_count, 2)
        sleep.assert_called_once()


if __name__ == "__main__":
    unittest.main()
