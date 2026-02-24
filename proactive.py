"""Eigy AI Assistant — Proactive behavior module.

Monitors idle time and triggers proactive messages when the user
has been silent for too long.
"""

from __future__ import annotations

import asyncio
import logging
import time

import config

logger = logging.getLogger(__name__)


class IdleMonitor:
    """Monitors silence duration and fires idle_trigger events."""

    def __init__(
        self,
        event_queue: asyncio.Queue,
        timeout: float | None = None,
    ):
        self._event_queue = event_queue
        self._timeout = timeout or config.PROACTIVE_IDLE_TIMEOUT
        self._last_interaction = time.time()
        self._last_proactive = 0.0
        self._running = True
        # After a proactive message, wait at least 2x timeout before another
        self._cooldown_multiplier = 2.0

    def reset(self) -> None:
        """Call after every user interaction to reset the idle timer."""
        self._last_interaction = time.time()

    def mark_spoke(self) -> None:
        """Call after a proactive message to apply cooldown."""
        self._last_proactive = time.time()
        self._last_interaction = time.time()

    def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False

    async def run(self) -> None:
        """Background loop — checks idle time every 30 seconds."""
        while self._running:
            await asyncio.sleep(30)

            if not self._running or not config.PROACTIVE_ENABLED:
                continue

            now = time.time()
            idle_duration = now - self._last_interaction
            since_proactive = now - self._last_proactive

            # Check cooldown: don't fire again too soon after a proactive message
            cooldown = self._timeout * self._cooldown_multiplier
            if self._last_proactive > 0 and since_proactive < cooldown:
                continue

            if idle_duration >= self._timeout:
                logger.info(
                    "Idle for %.0f seconds, triggering proactive message",
                    idle_duration,
                )
                await self._event_queue.put({"type": "idle_trigger"})
                # Mark that we spoke proactively (resets idle + applies cooldown)
                self.mark_spoke()
