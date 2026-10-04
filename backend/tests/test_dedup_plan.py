import json
import random
import re
import unittest

from app.core.dedup_plan import VERSION, build_recipe, normalize_filter, summary


def config(**overrides):
    value = {
        "t_hook": 3.0,
        "t_body": 2.0,
        "total_clips": 3,
        "resolution": "1080*1920",
        "variant_strength": "balanced",
    }
    value.update(overrides)
    return value


class DedupPlanTests(unittest.TestCase):
    def test_seed_is_reproducible_and_does_not_change_global_random_state(self):
        state = random.getstate()
        first = build_recipe(config(variant_color=True, variant_mirror=True), 1234)
        second = build_recipe(config(variant_color=True, variant_mirror=True), 1234)

        self.assertEqual(first, second)
        self.assertEqual(first.version, VERSION)
        self.assertEqual(random.getstate(), state)

    def test_actual_body_durations_and_zero_duration_segments_are_preserved(self):
        host_clip_durations = [1.25, 2.75, 0, 1.5]
        recipe = build_recipe(config(
            t_hook=host_clip_durations[0],
            t_body=99,
            total_clips=8,
            _body_clip_durations=host_clip_durations[1:],
            total_duration=sum(host_clip_durations),
        ), 5)

        self.assertEqual([segment.index for segment in recipe.segments], [0, 1, 3])
        self.assertEqual([segment.start for segment in recipe.segments], [0.0, 1.25, 4.0])
        self.assertEqual([segment.duration for segment in recipe.segments], [1.25, 2.75, 1.5])
        self.assertEqual(sum(segment.duration for segment in recipe.segments), sum(host_clip_durations))

        # The host clip index is the original position (Hook included), even
        # though the zero-length clip at position 2 has no recipe entry.
        clips_by_index = {index: duration for index, duration in enumerate(host_clip_durations)}
        recipe_by_index = {segment.index: segment for segment in recipe.segments}
        self.assertEqual([clips_by_index[index] for index in recipe_by_index], [1.25, 2.75, 1.5])
        self.assertEqual([recipe_by_index[index].duration for index in recipe_by_index], [1.25, 2.75, 1.5])
        self.assertNotIn(2, recipe_by_index)

    def test_timeline_specs_are_used_when_actual_clip_durations_are_absent(self):
        recipe = build_recipe(config(
            t_hook=1,
            body_mode="grouped",
            body_groups=[
                {"enabled": True, "clip_count": 2, "clip_duration": 1.5},
                {"enabled": False, "clip_count": 1, "clip_duration": 7},
            ],
        ), 6)

        self.assertEqual([segment.duration for segment in recipe.segments], [1, 1.5, 1.5])
        self.assertEqual([segment.start for segment in recipe.segments], [0, 1, 2.5])

    def test_host_enable_gate_and_legacy_frame_mix_are_ignored(self):
        base = build_recipe(config(enable_variants=False, variant_frame_mix=False), 7)
        enabled = build_recipe(config(enable_variants=True, variant_frame_mix=True), 7)
        self.assertEqual(base, enabled)

    def test_hook_and_body_toggles_are_independent(self):
        hook_off = build_recipe(config(variant_hook=False), 8)
        self.assertFalse(hook_off.segments[0].enabled)
        self.assertTrue(all(segment.enabled for segment in hook_off.segments[1:]))

        body_off = build_recipe(config(variant_body=False), 8)
        self.assertTrue(body_off.segments[0].enabled)
        self.assertTrue(all(not segment.enabled for segment in body_off.segments[1:]))

    def test_protected_region_skips_crop_and_mirror_for_overlapping_segment(self):
        recipe = build_recipe(config(
            variant_mirror=True,
            variant_protected_regions=[{"x": 0, "y": 0.4, "width": 0.1, "height": 0.1, "end": 3}],
        ), 987)

        hook = recipe.segments[0]
        body = recipe.segments[1]
        self.assertEqual(hook.crop_total, 0)
        self.assertFalse(hook.mirror)
        self.assertTrue(body.crop_total > 0)
        self.assertTrue(any("segment 0: crop skipped" in warning for warning in recipe.warnings))

        protected_mirror_warnings = [
            warning
            for seed in range(20)
            for warning in build_recipe(config(
                variant_mirror=True,
                variant_protected_regions=[{"x": 0, "y": 0.4, "width": 0.1, "height": 0.1, "end": 3}],
            ), seed).warnings
        ]
        self.assertTrue(any("segment 0: mirror skipped" in warning for warning in protected_mirror_warnings))

    def test_filter_cost_is_one_scale_and_no_split_concat_or_audio(self):
        recipe = build_recipe(config(variant_color=True, variant_mirror=True), 321)
        filter_chain = normalize_filter(recipe, recipe.segments[0])

        self.assertEqual(filter_chain.count("scale="), 1)
        self.assertEqual(filter_chain.count("crop="), 1)
        self.assertNotRegex(filter_chain, r"(?:a?split|concat|atrim|volume|amix)")
        self.assertIn("format=yuv420p", filter_chain)

    def test_small_resolution_rounding_and_crop_never_exceed_strength_cap(self):
        tiny = build_recipe(config(resolution="8*8", variant_strength="strong"), 10)
        self.assertEqual(normalize_filter(tiny, tiny.segments[0]),
                         "scale=8:8:force_original_aspect_ratio=increase,crop=8:8,setsar=1,format=yuv420p")

        recipe = build_recipe(config(variant_strength="strong"), 11)
        segment = recipe.segments[0]
        filter_chain = normalize_filter(recipe, segment)
        scale_dims = re.search(r"scale=(\d+):(\d+):", filter_chain)
        scaled_width, scaled_height = map(int, scale_dims.groups())
        self.assertEqual(scaled_width % 2, 0)
        self.assertEqual(scaled_height % 2, 0)
        self.assertLessEqual(1 - recipe.width / scaled_width, 0.02)
        self.assertLessEqual(1 - recipe.height / scaled_height, 0.02)

    def test_protection_geometry_and_extra_crop_cap_across_input_aspect_ratios(self):
        protected = {"x": 0.2, "y": 0.25, "width": 0.2, "height": 0.2, "end": 1}
        source_sizes = [
            (1080, 1920), (1920, 1080), (1280, 720),
            (720, 1280), (3840, 1080), (1080, 3840), (1000, 1000),
        ]
        outputs = [(1080, 1920), (1920, 1080)]
        checked = 0
        for width, height in outputs:
            for seed in range(12):
                recipe = build_recipe(config(
                    t_hook=1,
                    total_clips=1,
                    resolution=f"{width}*{height}",
                    variant_strength="strong",
                    variant_protected_regions=[protected],
                ), seed)
                segment = recipe.segments[0]
                if segment.crop_total == 0:
                    continue
                filter_chain = normalize_filter(recipe, segment)
                scaled = re.search(r"scale=(\d+):(\d+):", filter_chain)
                scaled_width, scaled_height = map(int, scaled.groups())

                for source_width, source_height in source_sizes:
                    # Model FFmpeg's increase scale followed by center crop.
                    # Coordinates are mapped from the normal output crop to
                    # the larger intermediate raster, then tested against the
                    # filter's actual centered crop plus its bounded offset.
                    base_scale = max(width / source_width, height / source_height)
                    variant_scale = max(scaled_width / source_width, scaled_height / source_height)
                    base_width, base_height = source_width * base_scale, source_height * base_scale
                    variant_width = source_width * variant_scale
                    variant_height = source_height * variant_scale
                    base_left, base_top = (base_width - width) / 2, (base_height - height) / 2
                    crop_left = (variant_width - width) / 2 + (scaled_width - width) * (segment.offset_x - 0.5)
                    crop_top = (variant_height - height) / 2 + (scaled_height - height) * (segment.offset_y - 0.5)
                    left = (base_left + protected["x"] * width) / base_scale * variant_scale
                    right = (base_left + (protected["x"] + protected["width"]) * width) / base_scale * variant_scale
                    top = (base_top + protected["y"] * height) / base_scale * variant_scale
                    bottom = (base_top + (protected["y"] + protected["height"]) * height) / base_scale * variant_scale

                    output_left = left - crop_left
                    output_right = right - crop_left
                    output_top = top - crop_top
                    output_bottom = bottom - crop_top
                    self.assertGreater(output_left, 2.0)
                    self.assertLess(output_right, width - 2.0)
                    self.assertGreater(output_top, 2.0)
                    self.assertLess(output_bottom, height - 2.0)
                    actual_extra_crop = 1 - base_scale / variant_scale
                    self.assertLessEqual(actual_extra_crop, segment.crop_total + 1e-9)
                    self.assertLessEqual(actual_extra_crop, 0.02 + 1e-9)
                    checked += 1
        self.assertGreater(checked, 0)

    def test_subframe_and_fractional_frame_durations_are_not_quantized_by_fps(self):
        durations = [1 / 24, 1 / 30, 0, 1 / 60, 0.5 / 60]
        hook_duration = 0.5 / 60
        total = hook_duration + sum(durations)
        expected_durations = [hook_duration, 1 / 24, 1 / 30, 1 / 60, 0.5 / 60]
        expected_starts = [0.0]
        for duration in expected_durations[:-1]:
            expected_starts.append(expected_starts[-1] + duration)

        timelines = [
            build_recipe(config(
                t_hook=hook_duration,
                total_clips=6,
                _body_clip_durations=durations,
                total_duration=total,
                fps=fps,
            ), 15)
            for fps in (24, "24000/1001", 30, "30000/1001", 60)
        ]
        for recipe in timelines:
            self.assertEqual([segment.index for segment in recipe.segments], [0, 1, 2, 4, 5])
            self.assertEqual([segment.duration for segment in recipe.segments], expected_durations)
            self.assertEqual([segment.start for segment in recipe.segments], expected_starts)
            self.assertAlmostEqual(sum(segment.duration for segment in recipe.segments), total)
        self.assertTrue(all(recipe == timelines[0] for recipe in timelines[1:]))

    def test_finite_sizes_durations_and_total_timeline_are_validated(self):
        with self.assertRaises(ValueError):
            build_recipe(config(t_hook=float("nan")), 1)
        with self.assertRaises(ValueError):
            build_recipe(config(_body_clip_durations=[float("inf")]), 1)
        with self.assertRaises(ValueError):
            build_recipe(config(total_duration=999), 1)
        with self.assertRaises(ValueError):
            build_recipe(config(resolution="9*1920"), 1)

    def test_disabled_and_empty_recipe_use_legacy_normalization_verbatim(self):
        expected = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p"
        disabled = build_recipe(config(variant_hook=False), 1)
        self.assertEqual(normalize_filter(disabled, disabled.segments[0]), expected)

        empty = build_recipe(config(variant_crop=False), 1)
        self.assertEqual(normalize_filter(empty, empty.segments[0]), expected)

    def test_color_and_mirror_are_off_by_default(self):
        recipe = build_recipe(config(), 12)
        self.assertTrue(all(segment.brightness == 0 for segment in recipe.segments))
        self.assertTrue(all(segment.contrast == 1 for segment in recipe.segments))
        self.assertTrue(all(segment.saturation == 1 for segment in recipe.segments))
        self.assertTrue(all(not segment.mirror for segment in recipe.segments))

    def test_summary_is_json_serializable_and_preserves_audio_and_timeline(self):
        recipe = build_recipe(config(), 13)
        result = summary(recipe)
        json.dumps(result)
        self.assertEqual(result["audio_policy"], "preserve")
        self.assertEqual(result["timeline_policy"], "preserve")
        self.assertEqual(result["segments"], 3)
        self.assertEqual(len(result["parameters"]), 3)


if __name__ == "__main__":
    unittest.main()
