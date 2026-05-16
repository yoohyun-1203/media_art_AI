import unittest

from live_signal import (
    EnvelopeSmoother,
    NoiseFloorTracker,
    SegmentEndpoint,
    SpeakerBleedGate,
    compose_led_mood_signal,
)


class LiveSignalCoreTests(unittest.TestCase):
    def test_noise_floor_tracks_quiet_baseline_and_scores_louder_signal(self):
        tracker = NoiseFloorTracker(initial_floor=0.02, learning_rate=0.5)

        tracker.update(0.01, is_speech=False)
        tracker.update(0.01, is_speech=False)

        self.assertLess(tracker.floor, 0.02)
        self.assertLess(tracker.relative_level(0.01), tracker.relative_level(0.2))
        self.assertGreater(tracker.relative_level(0.2), 1.0)

    def test_envelope_smoother_attack_is_faster_than_release(self):
        smoother = EnvelopeSmoother(value=0.0, attack=0.65, release=0.12)

        attacked = smoother.update(1.0)
        released = smoother.update(0.0)

        self.assertAlmostEqual(attacked, 0.65)
        self.assertAlmostEqual(released, 0.572)
        self.assertGreater(attacked, attacked - released)

    def test_segment_endpoint_ends_after_one_second_without_meaningful_signal(self):
        endpoint = SegmentEndpoint(silence_seconds=1.0)

        self.assertEqual(endpoint.update(True, now=10.0), "start")
        self.assertEqual(endpoint.update(False, now=10.5), "continue")
        self.assertEqual(endpoint.update(False, now=11.0), "end")
        self.assertEqual(endpoint.update(False, now=11.1), "idle")

    def test_compose_led_mood_signal_updates_arousal_before_valence(self):
        result = compose_led_mood_signal(
            arousal_live=0.7,
            arousal_confidence=0.9,
            latest_valence=-0.8,
            latest_valence_confidence=0.0,
            ambient_valence=0.4,
            ambient_arousal=-0.2,
            has_mic_activity=True,
        )

        self.assertEqual(result["serial_prefix"], "v")
        self.assertEqual(result["valence"], 0.0)
        self.assertEqual(result["arousal"], 0.7)

    def test_compose_led_mood_signal_clamps_valence_and_arousal(self):
        result = compose_led_mood_signal(
            arousal_live=2.0,
            arousal_confidence=1.0,
            latest_valence=-2.0,
            latest_valence_confidence=1.0,
            ambient_valence=0.0,
            ambient_arousal=0.0,
            has_mic_activity=True,
        )

        self.assertEqual(result["valence"], -1.0)
        self.assertEqual(result["arousal"], 1.0)


class SpeakerBleedGateTests(unittest.TestCase):
    def test_reference_mic_can_reduce_confidence_for_speaker_bleed(self):
        gate = SpeakerBleedGate(reference_weight=0.8)

        bleed = gate.score_voice_likelihood(voice_rms=0.08, reference_rms=0.09)
        speech = gate.score_voice_likelihood(voice_rms=0.08, reference_rms=0.01)

        self.assertLess(bleed, 0.25)
        self.assertGreater(speech, 0.7)

    def test_zero_or_negative_voice_returns_zero_confidence(self):
        gate = SpeakerBleedGate()

        self.assertEqual(
            gate.score_residual_voice_confidence(voice_rms=0.0, reference_rms=0.01),
            0.0,
        )
        self.assertEqual(
            gate.score_residual_voice_confidence(voice_rms=-0.01, reference_rms=0.01),
            0.0,
        )

    def test_negative_reference_does_not_reduce_confidence(self):
        gate = SpeakerBleedGate(reference_weight=0.8)

        confidence = gate.score_residual_voice_confidence(
            voice_rms=0.08,
            reference_rms=-0.09,
        )

        self.assertEqual(confidence, 1.0)

    def test_reference_matching_voice_clamps_confidence_to_zero(self):
        gate = SpeakerBleedGate(reference_weight=1.0)

        confidence = gate.score_residual_voice_confidence(
            voice_rms=0.08,
            reference_rms=0.08,
        )

        self.assertEqual(confidence, 0.0)


if __name__ == "__main__":
    unittest.main()
