import math
import unittest

from ambient_emotion import average_mood


class AverageMoodTests(unittest.TestCase):
    def test_confidence_weighting_ignores_zero_confidence_item(self):
        result = average_mood([
            {"valence": 0.8, "arousal": 0.2, "confidence": 1.0},
            {"valence": -1.0, "arousal": -1.0, "confidence": 0.0},
        ])

        self.assertEqual(
            result,
            {"valence": 0.8, "arousal": 0.2, "confidence": 1.0},
        )

    def test_empty_or_all_zero_confidence_returns_neutral(self):
        neutral = {"valence": 0.0, "arousal": 0.0, "confidence": 0.0}

        self.assertEqual(average_mood([]), neutral)
        self.assertEqual(
            average_mood([
                {"valence": 0.8, "arousal": 0.2, "confidence": 0.0},
                {"valence": -0.8, "arousal": -0.2, "confidence": -1.0},
            ]),
            neutral,
        )

    def test_confidence_and_mood_values_are_clamped(self):
        result = average_mood([
            {"valence": 2.0, "arousal": -2.0, "confidence": 2.0},
            {"valence": -1.0, "arousal": 1.0, "confidence": -0.5},
        ])

        self.assertEqual(
            result,
            {"valence": 1.0, "arousal": -1.0, "confidence": 1.0},
        )

    def test_weighted_average_uses_clamped_confidence(self):
        result = average_mood([
            {"valence": 1.0, "arousal": 0.0, "confidence": 0.75},
            {"valence": -1.0, "arousal": 1.0, "confidence": 0.25},
        ])

        self.assertAlmostEqual(result["valence"], 0.5)
        self.assertAlmostEqual(result["arousal"], 0.25)
        self.assertAlmostEqual(result["confidence"], 0.5)

    def test_nan_confidence_is_ignored(self):
        result = average_mood([
            {"valence": 1.0, "arousal": 1.0, "confidence": math.nan},
            {"valence": -0.5, "arousal": 0.25, "confidence": 1.0},
        ])

        self.assertEqual(
            result,
            {"valence": -0.5, "arousal": 0.25, "confidence": 1.0},
        )

    def test_non_finite_valence_and_arousal_are_neutral_before_weighting(self):
        result = average_mood([
            {"valence": math.nan, "arousal": math.inf, "confidence": 1.0},
            {"valence": -math.inf, "arousal": math.nan, "confidence": 1.0},
        ])

        self.assertEqual(
            result,
            {"valence": 0.0, "arousal": 0.0, "confidence": 1.0},
        )

    def test_missing_confidence_defaults_to_zero(self):
        result = average_mood([
            {"valence": 1.0, "arousal": 1.0},
            {"valence": "0.25", "arousal": "-0.5", "confidence": "1.0"},
        ])

        self.assertEqual(
            result,
            {"valence": 0.25, "arousal": -0.5, "confidence": 1.0},
        )


if __name__ == "__main__":
    unittest.main()
