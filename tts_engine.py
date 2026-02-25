"""Eigy AI Assistant — Text-to-speech engine.

edge-tts (Microsoft Azure neural voices).
Includes sentence buffer for streaming TTS pipeline.
"""

from __future__ import annotations

import logging
import re
import tempfile
import uuid
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Temp directory for TTS audio files
TTS_TEMP_DIR = Path(tempfile.gettempdir()) / "eigy_tts"
TTS_TEMP_DIR.mkdir(exist_ok=True)


_TEXT_EMOTICONS_RE = re.compile(
    r"(?<!\w)"        # not preceded by word char
    r"(?:"
    r"[:;8xXB=]"      # eyes
    r"[-']?"           # optional nose
    r"[)(\[\]DPpOo3>/\\|*]"  # mouth
    r"|"
    r"[)(\[\]DPp]"    # reversed: mouth first
    r"[-']?"
    r"[:;]"
    r"|"
    r"<3"             # heart
    r"|"
    r"\^\^"           # ^^
    r"|"
    r"[xX][dD]"       # xD / XD
    r"|"
    r"[oO]_[oO]"      # O_O
    r"|"
    r"-_-"            # -_-
    r"|"
    r"¯\\?_\(ツ\)_/¯"  # shrug
    r")"
    r"(?!\w)"          # not followed by word char
)

_URL_RE = re.compile(
    r"https?://[^\s,;)}\]<>\"']+|www\.[^\s,;)}\]<>\"']+"
)


def clean_for_tts(text: str) -> str:
    """Clean text for natural TTS output.

    - Removes *action text* (e.g. *přikývne s kamenným výrazem*)
    - Removes code blocks, URLs, text emoticons
    - Replaces special characters with spoken equivalents or removes them
    - Strips markdown formatting
    - Normalizes punctuation for natural speech
    """
    # 0. Remove fenced code blocks (```...```) entirely
    text = re.sub(r"```[^`]*```", "", text, flags=re.DOTALL)

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

    # 2. Remove URLs
    text = _URL_RE.sub("", text)

    # 3. Remove text emoticons/smileys (:), ;D, xD, <3, ^^ etc.)
    text = _TEXT_EMOTICONS_RE.sub("", text)

    # 4. Replace symbols with spoken Czech equivalents
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
    text = text.replace("@", " zavináč ")
    text = text.replace("\u201e", "")  # „ (Czech opening quote)
    text = text.replace("\u201c", "")  # " (Czech closing quote)
    text = text.replace("\u201d", "")  # " (right double quote)
    text = text.replace("\u2018", "")  # ' (left single quote)
    text = text.replace("\u2019", "")  # ' (right single quote)
    text = text.replace('"', "")

    # Unicode dashes → comma (natural pause in speech)
    text = text.replace("\u2014", ",")  # — em dash
    text = text.replace("\u2013", ",")  # – en dash

    # Bullet/list markers at start of lines
    text = re.sub(r"^\s*[-•·]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)

    # 5. Remove remaining special characters that TTS reads literally
    text = re.sub(r"[~#^{}|\\]", "", text)
    text = text.replace("[", "").replace("]", "")
    # Decorative lines (===, ---, etc.)
    text = re.sub(r"[=\-]{3,}", "", text)

    # 6. Normalize repeated punctuation (... → ., !!! → !, ??? → ?)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\?{2,}", "?", text)

    # Ellipsis unicode char → single dot
    text = text.replace("\u2026", ".")

    # 7. Collapse multiple spaces and blank lines
    text = re.sub(r"  +", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


class TTSEngine:
    """Text-to-speech via edge-tts (Microsoft Azure neural voices)."""

    def __init__(self):
        self.enabled = config.TTS_ENABLED
        self.voice = config.TTS_VOICE

    async def synthesize(self, text: str) -> str | None:
        """Synthesize text to audio file. Returns file path or None."""
        text = clean_for_tts(text)
        if not self.enabled or not text.strip():
            return None

        try:
            import edge_tts

            filepath = TTS_TEMP_DIR / f"eigy_{uuid.uuid4().hex[:12]}.mp3"
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(str(filepath))
            return str(filepath)
        except Exception as e:
            logger.warning("TTS failed: %s", e)
            return None

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def set_voice(self, voice: str) -> None:
        self.voice = voice


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
