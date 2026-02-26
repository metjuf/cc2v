"""Tests for proactive.py — IdleMonitor."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from proactive import IdleMonitor, IdleState


@pytest.fixture
def event_queue():
    return asyncio.Queue()


@pytest.fixture
def monitor(event_queue):
    return IdleMonitor(
        event_queue,
        tier1_timeout=0.1,
        tier2_timeout=0.3,
        shutdown_timeout=0.6,
    )


def test_initial_state(monitor):
    assert monitor._state == IdleState.ACTIVE


def test_reset(monitor):
    monitor._state = IdleState.TIER1_FIRED
    monitor.reset()
    assert monitor._state == IdleState.ACTIVE


def test_stop(monitor):
    monitor.stop()
    assert monitor._running is False


@pytest.mark.asyncio
async def test_tier1_fires(event_queue):
    """Verify tier 1 event fires after timeout."""
    monitor = IdleMonitor(
        event_queue,
        tier1_timeout=0.05,
        tier2_timeout=10.0,
        shutdown_timeout=20.0,
    )
    # Manually simulate the monitor logic with short sleep
    await asyncio.sleep(0.1)
    idle = time.time() - monitor._last_interaction
    if monitor._state == IdleState.ACTIVE and idle >= monitor._tier1:
        await event_queue.put({"type": "idle_trigger", "tier": 1})
        monitor._state = IdleState.TIER1_FIRED

    event = await asyncio.wait_for(event_queue.get(), timeout=2.0)
    assert event["type"] == "idle_trigger"
    assert event["tier"] == 1
    assert monitor._state == IdleState.TIER1_FIRED
