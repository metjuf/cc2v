"""Plugin base class and shared context for Eigy plugin system."""

from __future__ import annotations

import asyncio
import logging
import queue
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Coroutine

if TYPE_CHECKING:
    from audio_player import AudioPlayer
    from memory.database import Database
    from memory.memory_manager import MemoryManager
    from session_logger import SessionLogger
    from tts_engine import TTSEngine

logger = logging.getLogger(__name__)


@dataclass
class PluginContext:
    """Shared context passed to all plugin hooks.

    Replaces the 10+ individual parameters threaded through main.py.
    Plugins store their own mutable state via get_state(name).
    """

    db: Database
    memory: MemoryManager
    tts: TTSEngine
    audio_player: AudioPlayer
    avatar_queue: queue.Queue
    event_queue: asyncio.Queue
    current_messages: list[dict]
    slog: SessionLogger | None = None
    speak: Callable[..., Coroutine] | None = None
    proactive: Callable[..., Coroutine] | None = None
    plugin_state: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get_state(self, plugin_name: str) -> dict[str, Any]:
        """Get or create mutable state dict for a plugin."""
        if plugin_name not in self.plugin_state:
            self.plugin_state[plugin_name] = {}
        return self.plugin_state[plugin_name]


@dataclass
class CommandResult:
    """Result from detect_command hook."""

    handled: bool = False
    skip_llm: bool = False


@dataclass
class PreResponseResult:
    """Result from pre_response hook — context to inject before LLM call."""

    context_messages: list[dict] = field(default_factory=list)
    show_thinking: bool = False


class Plugin:
    """Base class for Eigy plugins.

    Subclass and override the hooks you need. All hooks are async.
    Every hook call is wrapped in try/except by PluginManager.
    """

    name: str = "unnamed"
    priority: int = 100
    enabled: bool = True

    async def detect_command(
        self, ctx: PluginContext, user_input: str
    ) -> CommandResult:
        """Detect and handle a plugin-specific command.

        Called BEFORE LLM processing. Return CommandResult(handled=True)
        to claim the input.
        """
        return CommandResult()

    async def pre_response(
        self, ctx: PluginContext, user_input: str
    ) -> PreResponseResult:
        """Inject context before the LLM call.

        Called for every user message that reaches the LLM.
        Return PreResponseResult with context_messages to inject.
        """
        return PreResponseResult()

    async def start_background(
        self, ctx: PluginContext
    ) -> list[asyncio.Task]:
        """Start background tasks (watchers, monitors).

        Called once during chat_loop initialization.
        Return a list of asyncio.Tasks that PluginManager will track.
        """
        return []

    async def handle_event(self, ctx: PluginContext, event: dict) -> bool:
        """Handle an event from the event_queue.

        Return True if the event was consumed (no other plugins see it).
        """
        return False

    async def shutdown(self, ctx: PluginContext) -> None:
        """Clean up resources on exit."""
        pass

    def get_help_entries(self) -> list[tuple[str, str]]:
        """Return (command, description) pairs for /help display."""
        return []

    def has_active_task(self, ctx: PluginContext) -> bool:
        """Whether this plugin has an active background activity.

        Used by main.py to check if idle proactive messages should
        be suppressed (e.g. during book reading).
        """
        return False

    def should_interrupt_on_input(
        self, ctx: PluginContext, user_input: str
    ) -> bool:
        """Whether this plugin's activity should stop on user input.

        Default: False. Book reader overrides to stop reading on
        chat input but not on slash commands.
        """
        return False
