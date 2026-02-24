"""Eigy AI Assistant — Timer manager.

Manages countdown timers with asyncio tasks.
Fires events into an asyncio.Queue when timers expire.
Also provides regex-based detection of timer requests from user text.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

logger = logging.getLogger(__name__)


# ── Timer request parsing ─────────────────────────────────────────

# Czech patterns
_CZ_PATTERNS = [
    # "stopni mi 10 minut", "nastav timer na 5 sekund"
    r"(?:stopni|nastav\s+timer|odpočítej|odměř)\s+(?:mi\s+)?(?:na\s+)?(\d+)\s*(sekund|sek|s|minut|min|m|hodin|hod|h)",
    # "připomeň mi za 10 minut", "za 5 minut mi řekni"
    r"(?:připomeň|upozorni)\s+(?:mi\s+)?za\s+(\d+)\s*(sekund|sek|s|minut|min|m|hodin|hod|h)",
    r"za\s+(\d+)\s*(sekund|sek|s|minut|min|m|hodin|hod|h)\s+(?:mi\s+)?(?:řekni|připomeň|upozorni)",
    # "timer 10 minut"
    r"timer\s+(\d+)\s*(sekund|sek|s|minut|min|m|hodin|hod|h)",
]

# English patterns
_EN_PATTERNS = [
    r"(?:set\s+(?:a\s+)?timer|remind\s+me)\s+(?:for\s+|in\s+)?(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)",
    r"timer\s+(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)",
]

_ALL_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _CZ_PATTERNS + _EN_PATTERNS]

_UNIT_MAP = {
    "s": 1, "sek": 1, "sekund": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "minut": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hod": 3600, "hodin": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}


def parse_timer_request(text: str) -> tuple[float, str] | None:
    """Parse a timer request from user text.

    Returns (seconds, label) or None if no timer request detected.
    """
    for pattern in _ALL_PATTERNS:
        match = pattern.search(text)
        if match:
            amount = int(match.group(1))
            unit_raw = match.group(2).lower()
            multiplier = _UNIT_MAP.get(unit_raw, 60)  # default to minutes
            seconds = amount * multiplier

            # Build a human-readable label
            if multiplier == 1:
                label = f"{amount} sekund"
            elif multiplier == 60:
                label = f"{amount} minut"
            else:
                label = f"{amount} hodin"

            return (seconds, label)

    return None


# ── Timer Manager ─────────────────────────────────────────────────


class TimerManager:
    """Manages countdown timers backed by asyncio tasks."""

    def __init__(self, event_queue: asyncio.Queue):
        self._event_queue = event_queue
        self._timers: dict[str, dict] = {}  # id -> {label, task, start_time, duration}

    def add_timer(self, seconds: float, label: str) -> str:
        """Add a new timer. Returns timer ID."""
        timer_id = uuid.uuid4().hex[:8]
        task = asyncio.create_task(self._run_timer(timer_id, seconds, label))
        self._timers[timer_id] = {
            "label": label,
            "task": task,
            "start_time": time.time(),
            "duration": seconds,
        }
        logger.info("Timer %s started: %s (%s seconds)", timer_id, label, seconds)
        return timer_id

    def cancel_timer(self, timer_id: str) -> bool:
        """Cancel a timer by ID. Returns True if found and cancelled."""
        timer = self._timers.pop(timer_id, None)
        if timer:
            timer["task"].cancel()
            return True
        return False

    def cancel_all(self) -> None:
        """Cancel all active timers."""
        for timer_id in list(self._timers):
            self.cancel_timer(timer_id)

    def list_timers(self) -> list[dict]:
        """Return list of active timers with remaining time."""
        now = time.time()
        result = []
        for tid, info in self._timers.items():
            elapsed = now - info["start_time"]
            remaining = max(0, info["duration"] - elapsed)
            result.append({
                "id": tid,
                "label": info["label"],
                "remaining": remaining,
            })
        return result

    async def _run_timer(self, timer_id: str, seconds: float, label: str) -> None:
        """Background task that waits and then fires a timer_expired event."""
        try:
            await asyncio.sleep(seconds)
            # Timer expired — send event
            await self._event_queue.put({
                "type": "timer_expired",
                "timer_id": timer_id,
                "label": label,
            })
        except asyncio.CancelledError:
            pass
        finally:
            self._timers.pop(timer_id, None)
