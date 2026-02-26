"""Eigy AI Assistant — Proactive behavior module.

3-tier idle state machine:
  ACTIVE → (tier1 timeout) → TIER1_FIRED → (tier2 timeout) → TIER2_FIRED → (shutdown timeout) → SHUTDOWN

Any user input resets state to ACTIVE.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time

import config

logger = logging.getLogger(__name__)


class IdleState(enum.Enum):
    ACTIVE = "active"
    TIER1_FIRED = "tier1_fired"
    TIER2_FIRED = "tier2_fired"


class IdleMonitor:
    """Monitors silence duration and fires tiered idle events."""

    def __init__(
        self,
        event_queue: asyncio.Queue,
        tier1_timeout: float | None = None,
        tier2_timeout: float | None = None,
        shutdown_timeout: float | None = None,
    ):
        self._event_queue = event_queue
        self._tier1 = tier1_timeout or config.PROACTIVE_IDLE_TIMEOUT
        self._tier2 = tier2_timeout or config.PROACTIVE_TIER2_TIMEOUT
        self._shutdown = shutdown_timeout or config.PROACTIVE_SHUTDOWN_TIMEOUT
        self._last_interaction = time.time()
        self._state = IdleState.ACTIVE
        self._running = True

    def reset(self) -> None:
        """Call after every user interaction to reset to ACTIVE."""
        self._last_interaction = time.time()
        self._state = IdleState.ACTIVE

    def stop(self) -> None:
        """Stop the monitor loop."""
        self._running = False

    async def run(self) -> None:
        """Background loop — checks idle time every 15 seconds."""
        while self._running:
            await asyncio.sleep(15)

            if not self._running or not config.PROACTIVE_ENABLED:
                continue

            idle = time.time() - self._last_interaction

            if self._state == IdleState.ACTIVE and idle >= self._tier1:
                logger.info("Idle %.0fs — tier 1 proactive message", idle)
                await self._event_queue.put({"type": "idle_trigger", "tier": 1})
                self._state = IdleState.TIER1_FIRED

            elif self._state == IdleState.TIER1_FIRED and idle >= self._tier2:
                logger.info("Idle %.0fs — tier 2 notification", idle)
                await self._event_queue.put({"type": "idle_trigger", "tier": 2})
                self._state = IdleState.TIER2_FIRED

            elif self._state == IdleState.TIER2_FIRED and idle >= self._shutdown:
                logger.info("Idle %.0fs — triggering auto-shutdown", idle)
                await self._event_queue.put({"type": "idle_shutdown"})
                self._running = False
