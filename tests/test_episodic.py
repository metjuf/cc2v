"""Tests for memory.episodic — intent detection (no ChromaDB needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from memory.episodic import EpisodicMemory
    EPISODIC_AVAILABLE = True
except ImportError:
    EPISODIC_AVAILABLE = False


@pytest.mark.skipif(not EPISODIC_AVAILABLE, reason="chromadb not installed")
class TestAssistantIntentDetection:
    def test_recommendation_detected(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Doporučuji ti zkusit nový framework, mohl bys ušetřit čas."
        )
        assert "recommendation" in intents

    def test_promise_detected(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Zapamatuji si to a připomenu ti to zítra."
        )
        assert "promise" in intents

    def test_suggestion_detected(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Co kdybys zkusil jiný přístup?"
        )
        assert "suggestion" in intents

    def test_opinion_detected(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Osobně bych řekla, že to není nejlepší nápad."
        )
        assert "opinion" in intents

    def test_no_intent_on_plain_text(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Dnes je hezky."
        )
        assert len(intents) == 0

    def test_multiple_intents(self):
        intents = EpisodicMemory._detect_assistant_intents(
            "Myslím, že bys mohl zkusit jiný editor. Zapamatuji si, že tě to zajímá."
        )
        assert len(intents) >= 2
