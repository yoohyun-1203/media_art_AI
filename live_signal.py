def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, float(value)))


class NoiseFloorTracker:
    def __init__(self, initial_floor=0.01, learning_rate=0.05):
        self.floor = max(float(initial_floor), 1e-9)
        self.learning_rate = float(learning_rate)

    def update(self, rms, is_speech):
        if not is_speech:
            rms = max(float(rms), 0.0)
            self.floor = (
                (1.0 - self.learning_rate) * self.floor
                + self.learning_rate * rms
            )
            self.floor = max(self.floor, 1e-9)
        return self.floor

    def relative_level(self, rms):
        return max(float(rms), 0.0) / self.floor


class EnvelopeSmoother:
    def __init__(self, value=0.0, attack=0.65, release=0.12):
        self.value = float(value)
        self.attack = float(attack)
        self.release = float(release)

    def update(self, target):
        target = float(target)
        coefficient = self.attack if target > self.value else self.release
        self.value = self.value + (target - self.value) * coefficient
        return self.value


class SegmentEndpoint:
    def __init__(self, silence_seconds=1.0):
        self.silence_seconds = float(silence_seconds)
        self.active = False
        self.last_signal_at = None

    def update(self, has_signal, now):
        now = float(now)

        if has_signal:
            self.last_signal_at = now
            if not self.active:
                self.active = True
                return "start"
            return "continue"

        if not self.active:
            return "idle"

        if self.last_signal_at is not None and now - self.last_signal_at >= self.silence_seconds:
            self.active = False
            self.last_signal_at = None
            return "end"

        return "continue"


class SpeakerBleedGate:
    """Two-mic speaker-bleed attenuation heuristic, not AEC or standalone VAD."""

    def __init__(self, reference_weight=0.8):
        self.reference_weight = float(reference_weight)

    def score_residual_voice_confidence(self, voice_rms, reference_rms):
        voice = max(float(voice_rms), 0.0)
        reference = max(float(reference_rms), 0.0)
        adjusted = voice - (reference * self.reference_weight)
        if voice <= 1e-6:
            return 0.0
        return max(0.0, min(1.0, adjusted / voice))

    def score_voice_likelihood(self, voice_rms, reference_rms):
        return self.score_residual_voice_confidence(voice_rms, reference_rms)


def compose_led_mood_signal(
    arousal_live,
    arousal_confidence,
    latest_valence,
    latest_valence_confidence,
    ambient_valence,
    ambient_arousal,
    has_mic_activity,
):
    if has_mic_activity and float(arousal_confidence) > 0.0:
        arousal = arousal_live
    else:
        arousal = ambient_arousal

    if float(latest_valence_confidence) > 0.0:
        valence = latest_valence
    elif has_mic_activity:
        valence = 0.0
    else:
        valence = ambient_valence

    return {
        "serial_prefix": "v",
        "valence": _clamp(valence),
        "arousal": _clamp(arousal),
    }
