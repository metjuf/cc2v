"""Tests for web_search — query detection, vague query refinement, result summarization."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from web_search import (
    detect_search_request,
    is_vague_query,
    refine_search_query,
    summarize_search_results,
    format_results,
)


# ── detect_search_request tests ──────────────────────────────────


def test_detect_explicit_search_cs():
    """Detects Czech explicit search commands."""
    assert detect_search_request("najdi mi informace o Praze") is not None
    assert detect_search_request("vyhledej počasí v Brně") is not None
    assert detect_search_request("hledej restaurace v centru") is not None


def test_detect_factual_question_cs():
    """Detects Czech factual questions."""
    assert detect_search_request("co je to blockchain") is not None
    assert detect_search_request("kdo je prezident ČR") is not None


def test_detect_no_search():
    """Normal conversational text does not trigger search."""
    assert detect_search_request("ahoj") is None
    assert detect_search_request("díky za pomoc") is None


def test_detect_excludes_time_questions():
    """Time questions are excluded from search."""
    assert detect_search_request("kolik je hodin") is None


# ── is_vague_query tests ─────────────────────────────────────────


def test_vague_query_with_pronouns():
    """Short queries with pronouns are detected as vague."""
    assert is_vague_query("ty lety") is True
    assert is_vague_query("to auto") is True
    assert is_vague_query("ten film") is True
    assert is_vague_query("toho člověka") is True
    assert is_vague_query("jeho profil") is True


def test_vague_query_short_with_se():
    """Reflexive pronouns in short queries are vague."""
    assert is_vague_query("to se mi líbí") is True


def test_not_vague_specific_query():
    """Specific queries are NOT vague."""
    assert is_vague_query("Air China CA 720") is False
    assert is_vague_query("počasí v Brně") is False
    assert is_vague_query("cena bitcoinu dnes") is False
    assert is_vague_query("restaurace Praha centrum") is False


def test_not_vague_long_query():
    """Long queries (>5 words) are never vague even with pronouns."""
    assert is_vague_query("ten nový film co jsme viděli v kině") is False


def test_not_vague_no_pronouns():
    """Short queries without pronouns are not vague."""
    assert is_vague_query("počasí Brno") is False
    assert is_vague_query("bitcoin cena") is False


# ── refine_search_query tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_refine_passes_through_specific_query():
    """Specific queries pass through without LLM call."""
    result = await refine_search_query("Air China CA 720", [])
    assert result == "Air China CA 720"


@pytest.mark.asyncio
async def test_refine_vague_query_with_context():
    """Vague query gets refined using LLM and conversation context."""
    context = [
        {"role": "user", "content": "čím letím do Hanoje?"},
        {"role": "assistant", "content": "Letíš Air China CA 720 z Budapešti do Pekingu a pak CA 741 do Hanoje."},
    ]

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.return_value = "Air China CA 720 CA 741 Budapešť Peking Hanoj"

        result = await refine_search_query("ty lety", context)

        assert result == "Air China CA 720 CA 741 Budapešť Peking Hanoj"
        mock_aux.assert_called_once()
        # Verify context was included in the prompt
        call_args = mock_aux.call_args[0][0]  # first positional arg (messages list)
        prompt_text = call_args[0]["content"]
        assert "ty lety" in prompt_text
        assert "Air China" in prompt_text


@pytest.mark.asyncio
async def test_refine_fallback_on_llm_failure():
    """Falls back to original query if LLM call fails."""
    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.side_effect = Exception("API error")

        result = await refine_search_query("ty lety", [{"role": "user", "content": "test"}])

        assert result == "ty lety"


@pytest.mark.asyncio
async def test_refine_fallback_on_empty_response():
    """Falls back to original query if LLM returns empty."""
    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.return_value = ""

        result = await refine_search_query("to auto", [{"role": "user", "content": "test"}])

        assert result == "to auto"


# ── summarize_search_results tests ───────────────────────────────


@pytest.mark.asyncio
async def test_summarize_replaces_raw_format():
    """Aux model produces a clean summary instead of [1], [2] format."""
    raw = (
        '[1] Air China Flight CA720 (zdroj: flightaware.com)\n'
        '    Popis: Track Air China #720\n\n'
        '[2] CA741 Flight Tracking (zdroj: flightaware.com)\n'
        '    Popis: Track Air China #741'
    )

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.return_value = (
            "Let CA 720 létá z Budapešti do Pekingu, "
            "CA 741 pak z Pekingu do Hanoje. "
            "Zdroje: flightaware.com"
        )

        result = await summarize_search_results("Air China CA 720 CA 741", raw)

        assert "[1]" not in result
        assert "[2]" not in result
        assert "CA 720" in result
        assert "flightaware.com" in result
        mock_aux.assert_called_once()


@pytest.mark.asyncio
async def test_summarize_fallback_on_failure():
    """Falls back to raw results if summarization fails."""
    raw = "[1] Test result (zdroj: test.com)\n    Popis: Test"

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.side_effect = Exception("API error")

        result = await summarize_search_results("test", raw)

        assert result == raw


@pytest.mark.asyncio
async def test_summarize_fallback_on_short_response():
    """Falls back to raw results if summary is too short."""
    raw = "[1] Test result (zdroj: test.com)\n    Popis: Test description"

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock) as mock_aux:
        mock_aux.return_value = "OK"  # too short (<20 chars)

        result = await summarize_search_results("test", raw)

        assert result == raw


# ── format_results tests ─────────────────────────────────────────


def test_format_results_empty():
    """Empty results return appropriate message."""
    assert "nevrátilo" in format_results([])


def test_format_results_with_snippet():
    """Results with snippets are formatted correctly."""
    results = [
        {"title": "Test Page", "url": "https://test.com/page", "snippet": "A test snippet"},
    ]
    formatted = format_results(results)
    assert "[1]" in formatted
    assert "test.com" in formatted
    assert "A test snippet" in formatted


def test_format_results_with_content():
    """Results with full page content use content instead of snippet."""
    results = [
        {
            "title": "Test Page",
            "url": "https://test.com/page",
            "snippet": "short snippet",
            "content": "Full page content with lots of detail",
        },
    ]
    formatted = format_results(results)
    assert "Full page content" in formatted
    assert "short snippet" not in formatted
