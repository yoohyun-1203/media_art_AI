from __future__ import annotations

import numpy as np


class RollingSerWindow:
    def __init__(self, rate: int = 16000, seconds: float = 1.0):
        self.rate = rate
        self.seconds = seconds
        self.target_samples = int(rate * seconds)
        self._buffer = np.empty(0, dtype=np.float32)

    def push(self, pcm: np.ndarray):
        samples = np.asarray(pcm)
        if samples.ndim > 1:
            samples = samples[:, 0]

        samples = samples.astype(np.float32, copy=False).reshape(-1)
        if samples.size and np.max(np.abs(samples)) > 1.5:
            samples = samples / 32768.0

        self._buffer = np.concatenate((self._buffer, samples))[-self.target_samples :]
        if self._buffer.shape[0] < self.target_samples:
            return None

        return self._buffer.copy()

    def add(self, data: np.ndarray):
        return self.push(data)


class LocalSerFallback:
    def predict(self, samples: np.ndarray, arousal_hint: float = 0.0):
        return {
            "valence": 0.0,
            "arousal": float(arousal_hint),
            "confidence": 0.0,
            "label": "unknown",
        }
