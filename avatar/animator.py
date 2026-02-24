"""Eigy AI Assistant — 2D Animation system.

Manages all avatar animations with discrete layer outputs:
blinking, breathing, eye drift, micro-expressions, lip sync,
and smooth emotion transitions.

Output: which PNG layer variant to display for each face part.
"""

from __future__ import annotations

import math
import random


# ── Emotion presets (which layers to show) ────────────────────────

EMOTION_PRESETS: dict[str, dict[str, str]] = {
    "neutral":   {"eyes": "open",  "mouth": "closed",    "eyebrows": "neutral"},
    "amused":    {"eyes": "open",  "mouth": "smirk",     "eyebrows": "neutral"},
    "happy":     {"eyes": "open",  "mouth": "smile",     "eyebrows": "neutral"},
    "concerned": {"eyes": "half",  "mouth": "sad",       "eyebrows": "frown"},
    "surprised": {"eyes": "open",  "mouth": "surprised", "eyebrows": "raised"},
    "thinking":  {"eyes": "half",  "mouth": "closed",    "eyebrows": "frown"},
    "speaking":  {"eyes": "open",  "mouth": "closed",    "eyebrows": "neutral"},
}

# Mouth shapes ordered by openness (for lip sync amplitude mapping)
MOUTH_OPENNESS = ["closed", "open_1", "open_2", "open_3"]


class Animator:
    """2D animation system outputting discrete layer names."""

    IDLE = "idle"
    THINKING = "thinking"
    SPEAKING = "speaking"

    def __init__(self):
        self.state = self.IDLE
        self.time = 0.0

        # ── Current emotion ────────────────────────────────────
        self._emotion = "amused"  # Start with subtle smirk

        # ── Blink ──────────────────────────────────────────────
        self._next_blink = random.uniform(2.0, 5.0)
        self._blink_progress = -1.0
        self._blink_duration = 0.2

        # ── Breathing ──────────────────────────────────────────
        self._breath_period = 3.0

        # ── Eye drift ──────────────────────────────────────────
        self._drift_x = 0.0
        self._drift_target = 0.0
        self._drift_speed = 0.0
        self._next_drift = random.uniform(5.0, 12.0)

        # ── Micro-expression ───────────────────────────────────
        self._next_micro = random.uniform(10.0, 20.0)
        self._micro_active = False
        self._micro_end = 0.0

        # ── Lip sync ──────────────────────────────────────────
        self._mouth_amplitude = 0.0
        self._mouth_target = 0.0
        self._mouth_smoothing = 12.0

    def set_state(self, state: str) -> None:
        """Set animator state: idle, thinking, speaking."""
        self.state = state
        if state == self.THINKING:
            self._emotion = "thinking"
        elif state == self.IDLE:
            if self._emotion == "thinking" or self._emotion == "speaking":
                self._emotion = "neutral"

    def set_emotion(self, emotion: str) -> None:
        """Set current emotion."""
        if emotion in EMOTION_PRESETS:
            self._emotion = emotion

    def set_amplitude(self, value: float) -> None:
        """Set lip sync target amplitude (0.0-1.0)."""
        self._mouth_target = max(0.0, min(1.0, value))

    def update(self, dt: float) -> None:
        """Advance all animations by dt seconds."""
        self.time += dt
        self._update_blink(dt)
        self._update_eye_drift(dt)
        self._update_micro_expression()
        self._update_lip_sync(dt)

    def get_render_state(self) -> dict:
        """Return which layers to render and transform offsets."""
        preset = EMOTION_PRESETS.get(self._emotion, EMOTION_PRESETS["neutral"])

        eyes = preset["eyes"]
        mouth = preset["mouth"]
        eyebrows = preset["eyebrows"]

        # Blink overrides eyes
        if self._blink_progress >= 0:
            t = self._blink_progress / self._blink_duration
            if t < 0.3:
                eyes = "half"
            elif t < 0.7:
                eyes = "closed"
            else:
                eyes = "half"

        # Lip sync overrides mouth during amplitude
        if self._mouth_amplitude > 0.05:
            idx = min(
                len(MOUTH_OPENNESS) - 1,
                int(self._mouth_amplitude * len(MOUTH_OPENNESS)),
            )
            mouth = MOUTH_OPENNESS[idx]

        # Micro-expression override (subtle smirk)
        if self._micro_active and self.state == self.IDLE:
            mouth = "smirk"

        # Breathing Y offset
        y_offset = 2.0 * math.sin(2 * math.pi * self.time / self._breath_period)

        # Eye drift X offset
        x_offset = self._drift_x * 3.0

        return {
            "eyes": eyes,
            "mouth": mouth,
            "eyebrows": eyebrows,
            "x_offset": x_offset,
            "y_offset": y_offset,
            # Visual effects data
            "emotion": self._emotion,
            "state": self.state,
            "breath_phase": (self.time % self._breath_period) / self._breath_period,
            "amplitude": self._mouth_amplitude,
        }

    # ── Private: animation updates ──────────────────────────────

    def _update_blink(self, dt: float) -> None:
        if self._blink_progress >= 0:
            self._blink_progress += dt
            if self._blink_progress >= self._blink_duration:
                self._blink_progress = -1.0
                self._next_blink = self.time + random.uniform(2.0, 5.0)
        elif self.time >= self._next_blink:
            self._blink_progress = 0.0

    def _update_eye_drift(self, dt: float) -> None:
        if self.time >= self._next_drift:
            self._drift_target = random.uniform(-1.0, 1.0)
            self._drift_speed = random.uniform(2.0, 4.0)
            self._next_drift = self.time + random.uniform(5.0, 12.0)

        diff = self._drift_target - self._drift_x
        self._drift_x += diff * min(1.0, dt * self._drift_speed)

        # Thinking state: more active eye movement
        if self.state == self.THINKING:
            self._drift_x += 0.5 * math.sin(self.time * 1.5) * dt

    def _update_micro_expression(self) -> None:
        if self.state != self.IDLE:
            self._micro_active = False
            return
        if self._micro_active:
            if self.time >= self._micro_end:
                self._micro_active = False
                self._next_micro = self.time + random.uniform(10.0, 20.0)
        elif self.time >= self._next_micro:
            self._micro_active = True
            self._micro_end = self.time + random.uniform(0.5, 1.5)

    def _update_lip_sync(self, dt: float) -> None:
        diff = self._mouth_target - self._mouth_amplitude
        self._mouth_amplitude += diff * min(1.0, dt * self._mouth_smoothing)
        # Decay target so mouth closes when no new amplitude arrives
        self._mouth_target *= 0.9
