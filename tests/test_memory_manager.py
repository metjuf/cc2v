"""Tests for memory.memory_manager — MemoryManager (no LLM calls)."""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.database import Database
from memory.memory_manager import MemoryManager


def test_build_context_has_system_prompt(manager):
    context = manager.build_context([])
    assert any(m["role"] == "system" for m in context)
    # System prompt should mention assistant name
    system_msgs = [m for m in context if m["role"] == "system"]
    assert any("Eigy" in m["content"] or "asistent" in m["content"].lower()
               for m in system_msgs)


def test_build_context_includes_current_messages(manager):
    msgs = [
        {"role": "user", "content": "Ahoj"},
        {"role": "assistant", "content": "Čau"},
    ]
    context = manager.build_context(msgs)
    contents = [m["content"] for m in context]
    assert "Ahoj" in contents
    assert "Čau" in contents


def test_build_context_xml_profile(manager):
    manager.profile.set_name("TestUser")
    context = manager.build_context([{"role": "user", "content": "test"}])

    profile_msgs = [m for m in context
                    if m["role"] == "system" and "<user_profile" in m["content"]]
    assert len(profile_msgs) == 1
    assert 'verified="true"' in profile_msgs[0]["content"]
    assert "</user_profile>" in profile_msgs[0]["content"]


def test_build_context_xml_summaries(manager):
    # Create a finished conversation with a summary
    conv_id = manager.db.create_conversation()
    manager.db.insert_message(conv_id, "user", "test")
    manager.db.update_conversation_summary(conv_id, "Test summary")
    manager.db.end_conversation(conv_id)

    context = manager.build_context([{"role": "user", "content": "test"}])

    summary_msgs = [m for m in context
                    if m["role"] == "system" and "<conversation_summaries>" in m["content"]]
    assert len(summary_msgs) == 1
    assert "</conversation_summaries>" in summary_msgs[0]["content"]


def test_build_context_xml_mid_session(manager):
    manager.mid_session_summaries = ["Shrnutí části 1"]
    context = manager.build_context([{"role": "user", "content": "test"}])

    mid_msgs = [m for m in context
                if m["role"] == "system" and "<mid_session_summaries>" in m["content"]]
    assert len(mid_msgs) == 1
    assert "</mid_session_summaries>" in mid_msgs[0]["content"]


def test_build_context_debug_output(manager):
    manager.build_context([{"role": "user", "content": "test"}])
    assert any("Kontext:" in msg for msg in manager._debug_msgs)


def test_estimate_tokens():
    assert MemoryManager._estimate_tokens("abc") == 1
    assert MemoryManager._estimate_tokens("abcdef") == 2
    assert MemoryManager._estimate_tokens("") == 0


def test_enforce_token_budget_no_trim(manager):
    context = [
        {"role": "system", "content": "short"},
        {"role": "user", "content": "hello"},
    ]
    result = manager._enforce_token_budget(context)
    assert len(result) == len(context)


def test_enforce_token_budget_trims_mid_session_first(manager):
    import config
    old_max = config.MAX_CONTEXT_TOKENS
    config.MAX_CONTEXT_TOKENS = 50  # very low budget

    context = [
        {"role": "system", "content": "System prompt " * 10},
        {"role": "system", "content": "<user_profile verified=\"true\">\nJméno: Jan\n</user_profile>"},
        {"role": "system", "content": "<mid_session_summaries>\n" + "x" * 300 + "\n</mid_session_summaries>"},
        {"role": "user", "content": "hello"},
    ]
    result = manager._enforce_token_budget(context)

    # Mid-session should be trimmed or removed first
    mid_msgs = [m for m in result
                if m["role"] == "system" and "<mid_session_summaries>" in m["content"]]
    # Either truncated or fully removed
    if mid_msgs:
        assert len(mid_msgs[0]["content"]) < 300
    else:
        assert True  # fully removed is also fine

    config.MAX_CONTEXT_TOKENS = old_max


@pytest.mark.asyncio
async def test_correct_profile(manager):
    manager.profile.set_name("Jan")
    manager.user_name = "Jan"

    corrected_profile = {
        "version": 2,
        "basic": {"name": "Jan", "age": 25},
        "life": {"occupation": "developer"},
    }

    with patch("chat_engine.get_auxiliary_json_response",
               new_callable=AsyncMock,
               return_value=json.dumps(corrected_profile)):
        result = await manager.correct_profile("mám 25 let a jsem developer")

    assert result is True
    full = manager.profile.get_full_profile()
    assert full["basic"]["name"] == "Jan"  # preserved
    assert full["basic"]["age"] == 25
    assert full["life"]["occupation"] == "developer"


@pytest.mark.asyncio
async def test_correct_profile_preserves_changelog(manager):
    manager.profile.set_name("Jan")
    profile = manager.profile.get_full_profile()
    profile["_changelog"] = [{"field": "test", "old": "a", "new": "b", "date": "2025-01-01"}]
    manager.profile._profile = profile
    manager.profile._save()

    corrected = {"version": 2, "basic": {"name": "Jan"}}

    with patch("chat_engine.get_auxiliary_json_response",
               new_callable=AsyncMock,
               return_value=json.dumps(corrected)):
        await manager.correct_profile("test")

    full = manager.profile.get_full_profile()
    assert "_changelog" in full
    assert len(full["_changelog"]) == 1


# ── Temporal awareness tests ─────────────────────────────────────


def test_build_temporal_block_contains_date():
    block = MemoryManager._build_temporal_block()
    assert "<current_time>" in block
    assert "</current_time>" in block
    assert "Datum:" in block
    assert "Čas:" in block


def test_build_temporal_block_time_of_day():
    block = MemoryManager._build_temporal_block()
    time_periods = ["ráno", "dopoledne", "poledne", "odpoledne", "večer", "noc"]
    assert any(t in block for t in time_periods)


def test_build_context_with_temporal_awareness(manager):
    import config
    old = config.TEMPORAL_AWARENESS_ENABLED
    config.TEMPORAL_AWARENESS_ENABLED = True
    context = manager.build_context([{"role": "user", "content": "test"}])
    time_msgs = [m for m in context
                 if m["role"] == "system" and "<current_time>" in m["content"]]
    assert len(time_msgs) == 1
    config.TEMPORAL_AWARENESS_ENABLED = old


def test_build_context_temporal_disabled(manager):
    import config
    old = config.TEMPORAL_AWARENESS_ENABLED
    config.TEMPORAL_AWARENESS_ENABLED = False
    context = manager.build_context([{"role": "user", "content": "test"}])
    time_msgs = [m for m in context
                 if m["role"] == "system" and "<current_time>" in m["content"]]
    assert len(time_msgs) == 0
    config.TEMPORAL_AWARENESS_ENABLED = old


# ── Mood injection tests ─────────────────────────────────────────


def test_build_context_with_mood(manager):
    import config
    old = config.EMOTIONAL_ADAPTATION_ENABLED
    config.EMOTIONAL_ADAPTATION_ENABLED = True
    context = manager.build_context(
        [{"role": "user", "content": "test"}],
        user_mood="frustrated",
    )
    mood_msgs = [m for m in context
                 if m["role"] == "system" and "<user_mood" in m["content"]]
    assert len(mood_msgs) == 1
    assert "frustrated" in mood_msgs[0]["content"]
    config.EMOTIONAL_ADAPTATION_ENABLED = old


def test_build_context_neutral_mood_not_injected(manager):
    import config
    old = config.EMOTIONAL_ADAPTATION_ENABLED
    config.EMOTIONAL_ADAPTATION_ENABLED = True
    context = manager.build_context(
        [{"role": "user", "content": "test"}],
        user_mood="neutral",
    )
    mood_msgs = [m for m in context
                 if m["role"] == "system" and "<user_mood" in m["content"]]
    assert len(mood_msgs) == 0
    config.EMOTIONAL_ADAPTATION_ENABLED = old


def test_mood_to_guidance():
    g = MemoryManager._mood_to_guidance("frustrated")
    assert "frustrovaný" in g.lower() or "empatická" in g.lower()
    assert MemoryManager._mood_to_guidance("neutral") == ""
    assert MemoryManager._mood_to_guidance("unknown_mood") == ""


# ── Style variation tests ────────────────────────────────────────


def test_compute_style_hint_user_short_assistant_verbose(manager):
    """When user writes short but assistant is verbose, return KRATCE."""
    msgs = [
        {"role": "user", "content": "ahoj"},
        {"role": "assistant", "content": "x" * 250},
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "y" * 220},
        {"role": "user", "content": "díky"},
        {"role": "assistant", "content": "z" * 210},
    ]
    assert manager._compute_style_hint(msgs) == "KRATCE"


def test_compute_style_hint_monotony_long(manager):
    """Detects monotonous long responses — returns KRATCE."""
    msgs = [
        {"role": "user", "content": "ahoj"},
        {"role": "assistant", "content": "x" * 250},
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "y" * 250},
        {"role": "user", "content": "díky"},
        {"role": "assistant", "content": "z" * 250},
    ]
    assert manager._compute_style_hint(msgs) == "KRATCE"


def test_compute_style_hint_too_many_questions(manager):
    """Detects when all recent responses end with questions."""
    # Need short-enough responses to avoid triggering verbose/monotony first
    msgs = [
        {"role": "user", "content": "ahoj, jak se máš dneska celý den"},
        {"role": "assistant", "content": "Čau, jak se máš?"},
        {"role": "user", "content": "dobře, co ty dneska plánuješ dělat"},
        {"role": "assistant", "content": "Super, co plánuješ?"},
        {"role": "user", "content": "nic moc, uvidíme, možná půjdu ven"},
        {"role": "assistant", "content": "Opravdu nic? Ani procházku?"},
    ]
    assert manager._compute_style_hint(msgs) == "BEZ_OTAZKY"


def test_compute_style_hint_few_messages(manager):
    msgs = [{"role": "user", "content": "ahoj"}]
    assert manager._compute_style_hint(msgs) is None


def test_build_context_style_hint_in_user_msg(manager):
    """Style hint is appended to last user message, not as system message."""
    import config
    old = config.STYLE_VARIATION_ENABLED
    config.STYLE_VARIATION_ENABLED = True
    msgs = [
        {"role": "user", "content": "ahoj"},
        {"role": "assistant", "content": "x" * 250},
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "y" * 250},
        {"role": "user", "content": "díky"},
        {"role": "assistant", "content": "z" * 250},
        {"role": "user", "content": "test"},
    ]
    context = manager.build_context(msgs)
    # Should NOT be a system message
    style_sys = [m for m in context
                 if m["role"] == "system" and "<response_style_hint>" in m["content"]]
    assert len(style_sys) == 0
    # Should be appended to last user message
    last_user = [m for m in context if m["role"] == "user"][-1]
    assert "[Styl odpovědi:" in last_user["content"]
    config.STYLE_VARIATION_ENABLED = old


# ── Observations tests ───────────────────────────────────────────


def test_get_observations_block_empty(manager):
    assert manager._get_observations_block() is None


def test_get_observations_block_with_data(manager):
    profile = manager.profile.get_full_profile()
    profile["eigy_observations"] = {
        "behavioral_patterns": ["uživatel má tendenci psát krátce"],
        "communication_notes": [],
        "personal_insights": ["zdá se, že je introvert"],
        "relationship_notes": [],
    }
    manager.profile._profile = profile
    manager.profile._save()

    block = manager._get_observations_block()
    assert block is not None
    assert "<eigy_observations>" in block
    assert "tendenci psát krátce" in block
    assert "introvert" in block


# ── Pre-reasoning tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_pre_reasoning_disabled(manager):
    import config
    old = config.CHAIN_OF_THOUGHT_ENABLED
    config.CHAIN_OF_THOUGHT_ENABLED = False
    result = await manager.generate_pre_reasoning([])
    assert result is None
    config.CHAIN_OF_THOUGHT_ENABLED = old


@pytest.mark.asyncio
async def test_generate_pre_reasoning_enabled(manager):
    import config
    old = config.CHAIN_OF_THOUGHT_ENABLED
    config.CHAIN_OF_THOUGHT_ENABLED = True

    with patch("chat_engine.get_auxiliary_response",
               new_callable=AsyncMock,
               return_value="1. Uživatel je v dobré náladě\n2. Zmínil práci"):
        result = await manager.generate_pre_reasoning(
            [{"role": "user", "content": "Měl jsem skvělý den v práci"}]
        )

    assert result is not None
    assert "Uživatel" in result
    config.CHAIN_OF_THOUGHT_ENABLED = old


def test_build_context_with_internal_reasoning(manager):
    context = manager.build_context(
        [{"role": "user", "content": "test"}],
        internal_reasoning="1. Uživatel se ptá na test",
    )
    reasoning_msgs = [m for m in context
                      if m["role"] == "system" and "<internal_reasoning>" in m["content"]]
    assert len(reasoning_msgs) == 1
    assert "Uživatel se ptá na test" in reasoning_msgs[0]["content"]
