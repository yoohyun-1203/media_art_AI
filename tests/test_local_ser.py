import unittest

import numpy as np

from local_ser import LocalSerFallback, RollingSerWindow


class RollingSerWindowTests(unittest.TestCase):
    def test_window_returns_none_until_enough_audio_is_available(self):
        window = RollingSerWindow(rate=16000, seconds=1.0)
        samples = np.zeros(1024, dtype=np.float32)

        result = window.push(samples)

        self.assertIsNone(result)

    def test_window_returns_audio_after_one_second(self):
        window = RollingSerWindow(rate=16000, seconds=1.0)
        samples = np.zeros(16000, dtype=np.float32)

        result = window.push(samples)

        self.assertIsNotNone(result)
        self.assertEqual(result.shape[0], 16000)

    def test_window_uses_first_channel_for_multichannel_input(self):
        window = RollingSerWindow(rate=4, seconds=1.0)
        samples = np.array(
            [
                [0.1, 0.9],
                [0.2, 0.8],
                [0.3, 0.7],
                [0.4, 0.6],
            ],
            dtype=np.float32,
        )

        result = window.push(samples)

        np.testing.assert_allclose(result, np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32))

    def test_window_normalizes_int_like_pcm(self):
        window = RollingSerWindow(rate=4, seconds=1.0)
        samples = np.array([0, 16384, -32768, 32767], dtype=np.int16)

        result = window.push(samples)

        np.testing.assert_allclose(
            result,
            np.array([0.0, 0.5, -1.0, 32767 / 32768.0], dtype=np.float32),
        )

    def test_window_overflow_keeps_latest_audio(self):
        window = RollingSerWindow(rate=4, seconds=1.0)

        self.assertIsNone(window.push(np.array([0.1, 0.2], dtype=np.float32)))
        result = window.push(np.array([0.3, 0.4, 0.5], dtype=np.float32))

        np.testing.assert_allclose(result, np.array([0.2, 0.3, 0.4, 0.5], dtype=np.float32))

    def test_window_returns_copy_not_internal_buffer(self):
        window = RollingSerWindow(rate=4, seconds=1.0)

        first_result = window.push(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32))
        first_result[0] = 99.0
        second_result = window.push(np.array([], dtype=np.float32))

        np.testing.assert_allclose(
            second_result,
            np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32),
        )


class LocalSerFallbackTests(unittest.TestCase):
    def test_predict_returns_unknown_with_arousal_hint(self):
        fallback = LocalSerFallback()

        result = fallback.predict(np.zeros(16000, dtype=np.float32), arousal_hint=0.25)

        self.assertEqual(
            result,
            {
                "valence": 0.0,
                "arousal": 0.25,
                "confidence": 0.0,
                "label": "unknown",
            },
        )


if __name__ == "__main__":
    unittest.main()
