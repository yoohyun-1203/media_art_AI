import os
import unittest

os.environ["MEDIA_ART_LOAD_DOTENV"] = "0"

from tools import virtual_mic_scenarios as scenarios


class VirtualMicScenarioTests(unittest.TestCase):
    def test_all_expected_scenarios_are_defined_in_order(self):
        self.assertEqual(
            scenarios.scenario_names(),
            [
                "silence_baseline",
                "left_soft_voice",
                "right_soft_voice",
                "left_loud_burst",
                "right_loud_burst",
                "balanced_center_voice",
                "left_to_right_sweep",
                "right_to_left_sweep",
                "call_and_response",
                "noisy_room_with_left_speech",
            ],
        )

    def test_frame_messages_include_lr_channels_and_max_legacy_mirror(self):
        frame = scenarios.VirtualMicFrame(
            time=0.5,
            left_arousal=0.25,
            right_arousal=0.75,
            valence=-0.4,
            left_confidence=0.6,
            right_confidence=0.9,
            valence_confidence=0.7,
        )

        messages = dict(scenarios.frame_to_messages(frame))

        self.assertEqual(messages["/emotion/left_arousal_live"], 0.25)
        self.assertEqual(messages["/emotion/right_arousal_live"], 0.75)
        self.assertEqual(messages["/emotion/left_arousal_confidence"], 0.6)
        self.assertEqual(messages["/emotion/right_arousal_confidence"], 0.9)
        self.assertEqual(messages["/emotion/valence_target"], -0.4)
        self.assertEqual(messages["/emotion/valence_confidence"], 0.7)
        self.assertEqual(messages["/emotion/arousal_live"], 0.75)
        self.assertEqual(messages["/emotion/arousal"], 0.75)
        self.assertEqual(messages["/emotion/arousal_confidence"], 0.9)
        self.assertEqual(messages["/emotion/valence"], -0.4)

    def test_left_loud_burst_reaches_left_edge_peak_before_decay(self):
        scenario = scenarios.get_scenario("left_loud_burst")
        frames = list(scenarios.iter_frames(scenario, duration_scale=0.2))

        self.assertGreater(max(frame.left_arousal for frame in frames), 0.9)
        self.assertLess(max(frame.right_arousal for frame in frames), -0.4)
        self.assertLess(frames[-1].left_arousal, 0.0)

    def test_run_named_scenario_records_osc_send_separately_from_readback(self):
        sent = []

        class FakeSender:
            def send(self, address, value):
                sent.append((address, value))

        result = scenarios.run_named_scenario(
            "left_soft_voice",
            sender=FakeSender(),
            duration_scale=0.01,
            readback=True,
            readback_client=lambda path: (_ for _ in ()).throw(TimeoutError("bridge timeout")),
            sleep=lambda seconds: None,
        )

        self.assertTrue(result["osc"]["ok"])
        self.assertGreater(result["osc"]["framesSent"], 0)
        self.assertGreater(len(sent), 0)
        self.assertFalse(result["readback"]["ok"])
        self.assertEqual(result["readback"]["error"], "bridge timeout")


if __name__ == "__main__":
    unittest.main()
