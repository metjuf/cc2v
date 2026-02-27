"""Eigy AI Assistant — Session logger.

Two logging systems:
1. System JSONL log (data/logs/eigy.jsonl) — rotating file, all Python logging
2. Per-session export (data/sessions/{session_id}.jsonl) — structured events
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON for JSONL output."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class SessionLogger:
    """Structured event logger for Eigy sessions.

    Writes events to a per-session JSONL file and optionally sets up
    a rotating file handler for the Python logging system.
    """

    def __init__(
        self,
        session_id: int | str,
        log_dir: Path | None = None,
        sessions_dir: Path | None = None,
        enabled: bool = True,
    ):
        self.session_id = str(session_id)
        self.enabled = enabled
        self._file = None
        self._session_path: Path | None = None
        self._file_handler: RotatingFileHandler | None = None
        self._started = datetime.now()

        if not enabled:
            return

        # Per-session JSONL file
        if sessions_dir:
            sessions_dir.mkdir(parents=True, exist_ok=True)
            ts = self._started.strftime("%Y%m%d_%H%M%S")
            self._session_path = sessions_dir / f"{ts}_{self.session_id}.jsonl"
            self._file = open(self._session_path, "a", encoding="utf-8")

        # System-wide JSONL log
        if log_dir:
            self._setup_file_logging(log_dir)

    def _setup_file_logging(self, log_dir: Path) -> None:
        """Add a rotating JSON file handler to the root logger."""
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "eigy.jsonl"

        handler = RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(JSONFormatter())
        handler.setLevel(logging.DEBUG)

        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        # Keep existing console handlers at WARNING to avoid DEBUG spam in terminal
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
                h.setLevel(logging.WARNING)
        root.addHandler(handler)
        self._file_handler = handler

    # ── Core logging ──────────────────────────────────────────────

    def log(self, event: str, **data) -> None:
        """Write a structured event to the session JSONL file."""
        if not self.enabled or not self._file:
            return
        entry = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            "session": self.session_id,
            "data": data,
        }
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        """Write session_end event and close file handles."""
        if self._file and not self._file.closed:
            elapsed = (datetime.now() - self._started).total_seconds()
            self.log("session_end", duration_seconds=round(elapsed, 1))
            self._file.close()
        if self._file_handler:
            root = logging.getLogger()
            root.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

    # ── Convenience methods ───────────────────────────────────────

    def log_session_start(self, user_name: str, config_snapshot: dict) -> None:
        """Log session start with user name and active configuration."""
        self.log("session_start", user_name=user_name, config=config_snapshot)

    def log_user_message(self, text: str, mood: str | None = None) -> None:
        """Log a user message with optional detected mood."""
        data = {"text": text, "length": len(text)}
        if mood:
            data["mood"] = mood
        self.log("user_message", **data)

    def log_assistant_message(
        self,
        text: str,
        emotion: str | None = None,
        tokens: int | None = None,
    ) -> None:
        """Log an assistant response with emotion and token estimate."""
        data: dict = {"text": text, "length": len(text)}
        if emotion:
            data["emotion"] = emotion
        if tokens is not None:
            data["tokens"] = tokens
        self.log("assistant_message", **data)

    def log_mood_detected(self, mood: str, method: str) -> None:
        """Log user mood detection result."""
        self.log("mood_detected", mood=mood, method=method)

    def log_emotion_detected(self, emotion: str, method: str) -> None:
        """Log assistant emotion detection result."""
        self.log("emotion_detected", emotion=emotion, method=method)

    def log_search(self, query: str, num_results: int) -> None:
        """Log a web search trigger."""
        self.log("search_triggered", query=query, num_results=num_results)

    def log_crypto(self, crypto_id: str, price_data: dict | None = None) -> None:
        """Log a crypto price lookup."""
        data: dict = {"crypto_id": crypto_id}
        if price_data:
            data["price_data"] = price_data
        self.log("crypto_triggered", **data)

    def log_context_built(
        self,
        num_messages: int,
        total_tokens: int,
        trimmed: bool = False,
        tokens_before: int | None = None,
        style_hint: str | None = None,
    ) -> None:
        """Log context building result."""
        data: dict = {
            "num_messages": num_messages,
            "total_tokens": total_tokens,
            "trimmed": trimmed,
        }
        if tokens_before is not None:
            data["tokens_before"] = tokens_before
        if style_hint:
            data["style_hint"] = style_hint
        self.log("context_built", **data)

    def log_extraction(self, keys_found: list[str]) -> None:
        """Log real-time fact extraction result."""
        self.log("extraction_result", keys=keys_found)

    def log_episode_stored(self, user_msg_preview: str) -> None:
        """Log episodic memory storage."""
        self.log("episode_stored", preview=user_msg_preview[:100])

    def log_proactive(self, tier: int, message: str) -> None:
        """Log a proactive message trigger."""
        self.log("proactive_triggered", tier=tier, message=message)

    def log_pre_reasoning(self, result_preview: str | None) -> None:
        """Log chain-of-thought pre-reasoning result."""
        self.log(
            "pre_reasoning",
            generated=result_preview is not None,
            preview=result_preview[:200] if result_preview else None,
        )

    def log_style_hint(self, hint: str) -> None:
        """Log response style hint."""
        self.log("style_hint", hint=hint)

    def log_tts(self, sentence: str, voice: str) -> None:
        """Log TTS synthesis event."""
        self.log("tts_event", sentence=sentence, voice=voice)

    def log_command(self, command: str, arg: str = "") -> None:
        """Log a slash command execution."""
        self.log("command", command=command, arg=arg)

    def log_error(self, error: str, context: str = "") -> None:
        """Log an error event."""
        self.log("error", error=error, context=context)
