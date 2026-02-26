"""Tests for avatar.emotion_detector — emotion and mood detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from avatar.emotion_detector import detect_emotion, detect_user_mood


# ── Assistant emotion detection ──────────────────────────────────


class TestDetectEmotion:
    def test_neutral_on_plain_text(self):
        assert detect_emotion("Dnes je hezky.") == "neutral"

    def test_happy_detection(self):
        assert detect_emotion("To je skvělé, moc děkuji za výbornou práci!") == "happy"

    def test_amused_detection(self):
        assert detect_emotion("Haha, to je vtipné! Dobrý žert.") == "amused"

    def test_concerned_detection(self):
        assert detect_emotion("Bohužel to je nebezpečné a riskantní, buď opatrná.") == "concerned"

    def test_thinking_detection(self):
        assert detect_emotion("Hmm, to je zajímavé, musím přemýšlet a zvážit to.") == "thinking"


# ── User mood detection ──────────────────────────────────────────


class TestDetectUserMood:
    def test_neutral_on_plain_text(self):
        assert detect_user_mood("Ok.") == "neutral"

    def test_frustrated_detection(self):
        assert detect_user_mood("Sakra, zase to nefunguje!") == "frustrated"

    def test_happy_detection(self):
        assert detect_user_mood("Super, díky!!") == "happy"

    def test_curious_detection(self):
        assert detect_user_mood("Jak to funguje?") == "curious"

    def test_stressed_detection(self):
        assert detect_user_mood("Nestíhám, mám deadline.") == "stressed"

    def test_excited_detection(self):
        assert detect_user_mood("Ty jo, konečně se to povedlo!") == "excited"

    def test_sad_detection(self):
        assert detect_user_mood("Je mi smutno a blbě.") == "sad"
