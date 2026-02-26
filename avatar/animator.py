"""Eigy AI Assistant — Animation system (simplified for spectrum visualizer).

Manages state, amplitude smoothing, breathing, and emotion tracking.
No face layers — output drives spectrum bars and glow color.
"""

from __future__ import annotations

import math


class Animator:
    """Simplified animation system for spectrum visualizer."""

    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"

    def __init__(self):
        self.state = self.IDLE
        self.time = 0.0

        self._emotion = "neutral"
        self._breath_period = 3.0

        # Lip sync / amplitude
        self._amplitude = 0.0
        self._amplitude_target = 0.0
        self._amplitude_smoothing = 12.0

    def set_state(self, state: str) -> None:
        self.state = state
        if state == self.THINKING:
            self._emotion = "thinking"
        elif state == self.IDLE:
            if self._emotion in ("thinking", "speaking"):
                self._emotion = "neutral"

    def set_emotion(self, emotion: str) -> None:
        self._emotion = emotion

    def set_amplitude(self, value: float) -> None:
        self._amplitude_target = max(0.0, min(1.0, value))

    def update(self, dt: float) -> None:
        self.time += dt
        # Smooth amplitude
        diff = self._amplitude_target - self._amplitude
        self._amplitude += diff * min(1.0, dt * self._amplitude_smoothing)
        self._amplitude_target *= 0.9

    def get_render_state(self) -> dict:
        breath_phase = (self.time % self._breath_period) / self._breath_period
        return {
            "state": self.state,
            "emotion": self._emotion,
            "amplitude": self._amplitude,
            "breath_phase": breath_phase,
        }
