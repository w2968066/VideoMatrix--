import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import hardware
from app.core.output_settings import resolve_output_config


class RandomOutputHardwareTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.commands = []
        for target, options in [
            ((hardware.sys, "platform"), {"new": "linux"}),
            ((hardware, "_gpu_admission"), {"new": hardware._GpuAdmission()}),
            ((hardware, "_memory_cache"), {"new": {}}),
            ((hardware, "_capture"), {"return_value": "h264_nvenc ffmpeg test"}),
            ((hardware, "run_process"), {"side_effect": self.record_probe}),
        ]:
            patcher = patch.object(*target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)

    def record_probe(self, command, *args, **kwargs):
        self.commands.append(command)
        return True, None

    def config(self, **overrides):
        config = {
            "resolution": "1080*1920", "fps": "30", "bitrate": "8000k",
            "random_resolution_enabled": True,
            "random_resolution_min": 1080, "random_resolution_max": 1440,
            "random_bitrate_enabled": True,
            "random_bitrate_min": 8000, "random_bitrate_max": 14000,
        }
        config.update(overrides)
        return config

    def probe(self, config):
        return hardware.probe_encoders(config, self.folder.name)

    def assert_settings(self, command, resolution, bitrate):
        source = command[command.index("-i") + 1]
        self.assertEqual(source, f"testsrc2=size={resolution}:rate=30")
        self.assertEqual(command[command.index("-b:v") + 1], bitrate)

    def test_probe_and_signature_use_upper_bounds_without_mutating_config(self):
        config = self.config()
        original = config.copy()
        with patch.object(hardware, "output_probe_config", wraps=hardware.output_probe_config) as normalize, \
                patch.object(hardware, "_signature", wraps=hardware._signature) as signature:
            result = self.probe(config)

        self.assertEqual(result["available"], ["h264_nvenc"])
        normalize.assert_called_once_with(config)
        probed = signature.call_args.args[0]
        self.assertIsNot(probed, config)
        self.assertEqual(probed["resolution"], "1440*2560")
        self.assertEqual(probed["bitrate"], "14000k")
        self.assertEqual(config, original)
        self.assertEqual(len(self.commands), 1)
        self.assert_settings(self.commands[0], "1440x2560", "14000k")

    def test_resolution_and_bitrate_upper_bounds_each_invalidate_lower_cache(self):
        cases = [
            (False, False, "1080x1920", "8000k"),
            (True, False, "1440x2560", "8000k"),
            (False, True, "1080x1920", "14000k"),
            (True, True, "1440x2560", "14000k"),
        ]
        for count, (resolution_enabled, bitrate_enabled, size, bitrate) in enumerate(cases, 1):
            with self.subTest(resolution=resolution_enabled, bitrate=bitrate_enabled):
                result = self.probe(self.config(
                    random_resolution_enabled=resolution_enabled,
                    random_bitrate_enabled=bitrate_enabled,
                ))
                self.assertEqual(result["available"], ["h264_nvenc"])
                self.assertEqual(len(self.commands), count)
                self.assert_settings(self.commands[-1], size, bitrate)
        self.assertEqual(len(hardware._memory_cache), 4)

    def test_sampled_settings_reuse_only_the_matching_upper_bound_cache(self):
        config = self.config()
        first = self.probe(config)
        sampled = self.config(resolution="1134*2016", bitrate="10300k")
        with patch.object(hardware, "output_probe_config", wraps=hardware.output_probe_config) as normalize:
            self.assertEqual(self.probe(sampled), first)
            normalize.assert_called_once_with(sampled)
        self.assertEqual(len(self.commands), 1)

        # The persistent cache must obey the same effective settings as memory.
        self.assertTrue((Path(self.folder.name) / "hardware-capabilities.json").exists())
        hardware._memory_cache.clear()
        self.assertEqual(self.probe(sampled), first)
        self.assertEqual(len(self.commands), 1)

        self.probe(self.config(random_resolution_max=1602))
        self.assertEqual(len(self.commands), 2)
        self.assert_settings(self.commands[-1], "1602x2848", "14000k")
        self.probe(self.config(random_bitrate_max=15000))
        self.assertEqual(len(self.commands), 3)
        self.assert_settings(self.commands[-1], "1440x2560", "15000k")

    def test_outputs_keep_the_shared_session_without_probing_again(self):
        config = self.config()
        with patch.dict(hardware.os.environ, {"APPDATA": self.folder.name}), \
                patch.object(hardware, "probe_encoders", wraps=hardware.probe_encoders) as probe:
            session = hardware.session_for(config)
            self.addCleanup(session.close)
            for index in range(1, 5):
                resolved = resolve_output_config(config, "same-task", index, 123)
                self.assertTrue(resolved["random_resolution_enabled"])
                self.assertTrue(resolved["random_bitrate_enabled"])
                self.assertIs(hardware.session_for(resolved), session)
                self.assertTrue(session.run(
                    ["ffmpeg", "-y"], ["-b:v", resolved["bitrate"], "out.mp4"],
                    "output", runner=lambda command: (True, None),
                )[0])
            probe.assert_called_once_with(config, None, None)
        self.assertEqual(len(self.commands), 1)


if __name__ == "__main__":
    unittest.main()
