import os
import unittest
from unittest import mock

os.environ["MEDIA_ART_LOAD_DOTENV"] = "0"

import web_app


class RunTestOscTests(unittest.TestCase):
    def test_run_test_osc_sends_live_verification_channels(self):
        fake_result = {
            "ok": True,
            "transcript": "웹 테스트 감정 메시지",
            "valence": -0.2,
            "audio_arousal": 0.7,
            "td_valence": -0.7,
            "td_arousal": 0.7,
        }

        with mock.patch.object(web_app.backend, "analyze_text_result", return_value=dict(fake_result)), \
             mock.patch.object(web_app.backend, "estimate_valence_confidence", return_value=0.56), \
             mock.patch.object(web_app.backend, "send_live_osc") as send_live_osc, \
             mock.patch.object(web_app, "read_touchdesigner_state", return_value={}):
            result = web_app.run_test_osc()

        send_live_osc.assert_called_once_with(
            arousal_live=0.7,
            arousal_confidence=1.0,
            valence_target=-0.7,
            valence_confidence=0.56,
            text_final="웹 테스트 감정 메시지",
        )
        self.assertTrue(result["live_osc_sent"])
        self.assertEqual(
            result["live_osc"],
            {
                "arousal_live": 0.7,
                "arousal_confidence": 1.0,
                "valence_target": -0.7,
                "valence_confidence": 0.56,
            },
        )
        self.assertEqual(result["valence_confidence"], 0.56)


class LiveSignalCompositionTests(unittest.TestCase):
    def test_live_loop_can_send_arousal_with_neutral_valence_before_ser_result(self):
        signal = {"serial_prefix": "v", "valence": -0.25, "arousal": 0.65}

        with mock.patch.object(web_app, "compose_led_mood_signal", return_value=signal) as compose_led_mood_signal, \
             mock.patch.object(web_app.backend, "send_live_osc") as send_live_osc:
            result = web_app.send_composed_live_signal(
                arousal_live=0.8,
                arousal_confidence=0.9,
                latest_valence=0.0,
                latest_valence_confidence=0.0,
                ambient_valence=-0.2,
                ambient_arousal=-0.4,
                has_mic_activity=True,
            )

        compose_led_mood_signal.assert_called_once_with(
            arousal_live=0.8,
            arousal_confidence=0.9,
            latest_valence=0.0,
            latest_valence_confidence=0.0,
            ambient_valence=-0.2,
            ambient_arousal=-0.4,
            has_mic_activity=True,
        )
        send_live_osc.assert_called_once_with(
            arousal_live=0.65,
            arousal_confidence=0.9,
            valence_target=-0.25,
            valence_confidence=0.0,
        )
        self.assertIs(result, signal)


class SideOwnershipTests(unittest.TestCase):
    def test_quieter_side_does_not_claim_voice_when_other_side_is_clearly_stronger(self):
        self.assertFalse(
            web_app.side_owns_current_voice(
                "left",
                {"arousal_confidence": 0.25},
                {"arousal_confidence": 0.80},
            )
        )

    def test_side_can_claim_voice_when_it_is_stronger(self):
        self.assertTrue(
            web_app.side_owns_current_voice(
                "right",
                {"arousal_confidence": 0.80},
                {"arousal_confidence": 0.25},
            )
        )


class VirtualMicWebTests(unittest.TestCase):
    def test_run_virtual_mic_scenario_updates_latest_frame_and_result(self):
        fake_frame = web_app.virtual_mic_scenarios.VirtualMicFrame(
            time=0.0,
            left_arousal=0.4,
            right_arousal=-0.4,
            valence=0.2,
            left_confidence=0.7,
            right_confidence=0.3,
            valence_confidence=0.8,
        )
        fake_result = {"name": "left_soft_voice", "osc": {"ok": True}, "readback": {"ok": False}}

        def fake_runner(**kwargs):
            kwargs["on_frame"](fake_frame)
            return fake_result

        with mock.patch.object(web_app.virtual_mic_scenarios, "run_named_scenario", side_effect=fake_runner) as runner:
            result = web_app.run_virtual_mic_scenario("left_soft_voice", duration_scale=0.5, readback=True)

        runner.assert_called_once()
        self.assertIs(result, fake_result)
        state = web_app.get_virtual_mic()
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"], fake_result)
        self.assertEqual(state["latest"]["left_arousal_live"], 0.4)
        self.assertEqual(state["latest"]["right_arousal_live"], -0.4)
        self.assertEqual(state["latest"]["valence_target"], 0.2)


if __name__ == "__main__":
    unittest.main()
