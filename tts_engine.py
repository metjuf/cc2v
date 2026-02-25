"""Eigy AI Assistant — Text-to-speech engine.

OpenAI TTS HD primary, edge-tts fallback.
Includes sentence buffer for streaming TTS pipeline.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import uuid
from pathlib import Path

import httpx

import config

logger = logging.getLogger(__name__)

# Temp directory for TTS audio files
TTS_TEMP_DIR = Path(tempfile.gettempdir()) / "eigy_tts"
TTS_TEMP_DIR.mkdir(exist_ok=True)


def clean_for_tts(text: str) -> str:
    """Clean text for natural TTS output.

    - Removes *action text* (e.g. *přikývne s kamenným výrazem*)
    - Replaces special characters with spoken equivalents or removes them
    - Strips markdown formatting
    """
    # 1. Strip markdown — preserve content
    # Bold **text** → text (must be BEFORE action removal)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    # Remove *action/emote text* (single asterisks — roleplay actions)
    text = re.sub(r"\*[^*]+\*", "", text)
    # Clean up any remaining stray asterisks
    text = text.replace("*", "")

    # Italic _text_ / __text__ → text
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)

    # Markdown headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # Markdown links [text](url) → keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Inline code backticks
    text = re.sub(r"`([^`]*)`", r"\1", text)

    # 2. Replace symbols with spoken Czech equivalents
    text = text.replace(" != ", " se nerovná ")
    text = text.replace(" >= ", " větší nebo rovno ")
    text = text.replace(" <= ", " menší nebo rovno ")
    text = text.replace(" = ", " se rovná ")
    text = text.replace(" -> ", " vede k ")
    text = text.replace(" => ", " vede k ")
    text = text.replace(" > ", " větší než ")
    text = text.replace(" < ", " menší než ")
    text = text.replace(" & ", " a ")
    text = text.replace(" | ", " nebo ")
    text = text.replace(" + ", " plus ")
    text = text.replace(" % ", " procent ")
    text = text.replace("\u201e", "")  # „ (Czech opening quote)
    text = text.replace("\u201c", "")  # " (Czech closing quote)
    text = text.replace('"', "")

    # 3. Remove remaining special characters that TTS reads literally
    text = re.sub(r"[~#^{}|\\]", "", text)
    text = text.replace("[", "").replace("]", "")
    # Decorative lines (===, ---, etc.)
    text = re.sub(r"[=\-]{3,}", "", text)

    # Collapse multiple spaces and blank lines
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


class TTSEngine:
    """Text-to-speech with OpenAI TTS HD primary and edge-tts fallback."""

    def __init__(self, assistant_id: str = "eigy"):
        self.assistant_id = assistant_id
        self.enabled = config.TTS_ENABLED
        # Per-assistant voice from ASSISTANTS config
        assistant_cfg = config.ASSISTANTS.get(assistant_id, {})
        self.voice = assistant_cfg.get("tts_voice", config.TTS_VOICE)
        self.provider = self._select_provider()

    def _select_provider(self) -> str:
        if config.TTS_PROVIDER == "openai" and config.OPENAI_API_KEY:
            return "openai"
        return "edge"

    async def synthesize(self, text: str) -> str | None:
        """Synthesize text to audio file. Returns file path or None."""
        text = clean_for_tts(text)
        if not self.enabled or not text.strip():
            return None

        try:
            if self.provider == "openai":
                return await self._openai_tts(text)
            else:
                return await self._edge_tts(text)
        except Exception as e:
            logger.warning("TTS %s failed: %s", self.provider, e)
            # Fallback from OpenAI to edge-tts
            if self.provider == "openai":
                try:
                    return await self._edge_tts(text)
                except Exception as e2:
                    logger.warning("edge-tts fallback also failed: %s", e2)
            return None

    async def _openai_tts(self, text: str) -> str:
        """Generate speech via OpenAI TTS HD API."""
        filepath = TTS_TEMP_DIR / f"eigy_{uuid.uuid4().hex[:12]}.mp3"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "tts-1-hd",
                    "voice": self.voice,
                    "input": text,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            filepath.write_bytes(response.content)

        return str(filepath)

    async def _edge_tts(self, text: str) -> str:
        """Generate speech via edge-tts (free Microsoft Azure neural voices)."""
        import edge_tts

        filepath = TTS_TEMP_DIR / f"eigy_{uuid.uuid4().hex[:12]}.mp3"

        # Map OpenAI voice names to edge-tts voices
        edge_voice = self.voice
        if self.voice in ("nova", "shimmer", "alloy", "echo", "fable", "onyx"):
            edge_voice = "cs-CZ-VlastaNeural"  # Czech female voice

        communicate = edge_tts.Communicate(text, edge_voice)
        await communicate.save(str(filepath))

        return str(filepath)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_voice(self, voice: str) -> None:
        self.voice = voice
        self.provider = self._select_provider()


class SentenceBuffer:
    """Buffer streaming tokens and emit complete sentences for TTS.

    Sentences are split on `.!?` followed by whitespace.
    """

    def __init__(self):
        self.buffer = ""

    def add_token(self, token: str) -> list[str]:
        """Add a token, return list of complete sentences (may be empty)."""
        self.buffer += token
        sentences = []
        # Split on sentence boundaries: .!? followed by space or end
        pattern = r"(?<=[.!?])\s+"
        parts = re.split(pattern, self.buffer)
        if len(parts) > 1:
            # All parts except the last are complete sentences
            sentences = parts[:-1]
            self.buffer = parts[-1]
        return sentences

    def flush(self) -> str | None:
        """Return any remaining buffered text."""
        if self.buffer.strip():
            text = self.buffer.strip()
            self.buffer = ""
            return text
        return None


def cleanup_temp_files() -> None:
    """Remove old TTS temp files."""
    if TTS_TEMP_DIR.exists():
        for f in TTS_TEMP_DIR.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
