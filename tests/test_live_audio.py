import unittest
import os
import subprocess
import sys
from unittest import mock

os.environ["MEDIA_ART_LOAD_DOTENV"] = "0"
os.environ["GEMINI_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""

import numpy as np

import main


class LiveAudioFeatureTests(unittest.TestCase):
    def test_silence_has_low_arousal_and_confidence(self):
        samples = np.zeros((1024, 1), dtype=np.int16)

        result = main.compute_live_audio_features(samples, rate=16000)

        self.assertLess(result["arousal_live"], -0.8)
        self.assertLess(result["arousal_confidence"], 0.05)
        self.assertEqual(result["rms"], 0.0)

    def test_loud_signal_has_higher_arousal_and_confidence_than_silence(self):
        silence = np.zeros((1024, 1), dtype=np.int16)
        tone = (np.sin(np.linspace(0, np.pi * 16, 1024)) * 12000).astype(np.int16)

        quiet_result = main.compute_live_audio_features(silence, rate=16000)
        loud_result = main.compute_live_audio_features(tone.reshape(-1, 1), rate=16000)

        self.assertGreater(loud_result["arousal_live"], quiet_result["arousal_live"])
        self.assertGreater(loud_result["arousal_confidence"], 0.5)
        self.assertGreaterEqual(loud_result["arousal_live"], -1.0)
        self.assertLessEqual(loud_result["arousal_live"], 1.0)

    def test_tiny_int16_noise_is_still_treated_as_quiet(self):
        samples = np.ones((1024, 1), dtype=np.int16)

        result = main.compute_live_audio_features(samples, rate=16000)

        self.assertLess(result["arousal_confidence"], 0.05)
        self.assertLess(result["rms"], 0.001)

    def test_live_segment_requires_volume_and_confidence(self):
        quiet_features = {"arousal_confidence": 0.0}
        weak_features = {"arousal_confidence": 0.2}
        speech_features = {"arousal_confidence": 0.6}

        self.assertFalse(main.should_collect_live_segment(1000, quiet_features))
        self.assertFalse(main.should_collect_live_segment(1000, weak_features))
        self.assertFalse(main.should_collect_live_segment(1000, {"arousal_confidence": 0.4}))
        self.assertFalse(main.should_collect_live_segment(100, speech_features))
        self.assertTrue(main.should_collect_live_segment(1000, speech_features))

    def test_valence_confidence_needs_transcript_signal(self):
        empty = main.estimate_valence_confidence("", 0.9)
        clear = main.estimate_valence_confidence("오늘은 정말 기분이 좋아", 0.8)

        self.assertEqual(empty, 0.0)
        self.assertGreater(clear, 0.7)
        self.assertLessEqual(clear, 1.0)

    def test_send_live_osc_mirrors_live_controls_to_legacy_channels(self):
        with mock.patch.object(main.osc_client, "send_message") as send_message:
            main.send_live_osc(
                arousal_live=0.35,
                arousal_confidence=0.75,
                valence_target=-0.45,
                valence_confidence=0.6,
            )

        send_message.assert_any_call("/emotion/arousal_live", 0.35)
        send_message.assert_any_call("/emotion/arousal_confidence", 0.75)
        send_message.assert_any_call("/emotion/valence_target", -0.45)
        send_message.assert_any_call("/emotion/valence_confidence", 0.6)
        send_message.assert_any_call("/emotion/arousal", 0.35)
        send_message.assert_any_call("/emotion/valence", -0.45)

    def test_import_does_not_initialize_ai_clients(self):
        env = os.environ.copy()
        env["MEDIA_ART_LOAD_DOTENV"] = "0"
        env["GEMINI_API_KEY"] = ""
        env["OPENAI_API_KEY"] = ""

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import importlib, main; "
                    "importlib.reload(main); "
                    "print(f'clients:{main.gemini_client is None}:"
                    "{main.openai_client is None}')"
                ),
            ],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("clients:True:True", result.stdout)
        self.assertNotIn("초고속 클라우드 AI", result.stdout)
        self.assertNotIn("AI 클라이언트 세팅 완료", result.stdout)

    def test_get_ai_emotion_raises_when_gemini_key_is_missing(self):
        main.gemini_client = None

        with mock.patch.dict(
            os.environ,
            {"MEDIA_ART_LOAD_DOTENV": "0", "GEMINI_API_KEY": ""},
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is required"):
                main.get_ai_emotion("테스트 발화", arousal=0.0)

    def test_lazy_clients_fail_only_when_key_is_missing(self):
        main.gemini_client = None
        main.openai_client = None

        with mock.patch.dict(
            os.environ,
            {
                "MEDIA_ART_LOAD_DOTENV": "0",
                "GEMINI_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY is required"):
                main.get_gemini_client()
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is required"):
                main.get_openai_client()

    def test_gemini_client_is_cached_after_successful_construction(self):
        main.gemini_client = None
        fake_client = object()

        with mock.patch.dict(
            os.environ,
            {"MEDIA_ART_LOAD_DOTENV": "0", "GEMINI_API_KEY": "test-key"},
            clear=False,
        ), mock.patch.object(main.genai, "Client", return_value=fake_client) as client_factory:
            self.assertIs(main.get_gemini_client(), fake_client)
            self.assertIs(main.get_gemini_client(), fake_client)

        client_factory.assert_called_once_with(api_key="test-key")


if __name__ == "__main__":
    unittest.main()
