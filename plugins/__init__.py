"""Plugin manager — discovers, loads, and orchestrates Eigy plugins."""

from __future__ import annotations

import asyncio
import importlib
import logging
from pathlib import Path

from plugins.base import Plugin, PluginContext, CommandResult, PreResponseResult

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent


class PluginManager:
    """Discovers, loads, and orchestrates plugin lifecycle.

    Every plugin call is wrapped in try/except so a buggy plugin
    cannot crash the main application.
    """

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []
        self._background_tasks: list[asyncio.Task] = []

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def register(self, plugin: Plugin) -> None:
        """Register a plugin instance."""
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)
        logger.info("Registered plugin: %s (priority=%d)", plugin.name, plugin.priority)

    def discover(self) -> None:
        """Auto-discover and register plugins from the plugins/ directory.

        Scans for *_plugin.py files, imports them, and looks for a
        create_plugin() factory function that returns a Plugin instance.
        """
        for path in sorted(PLUGIN_DIR.glob("*_plugin.py")):
            module_name = f"plugins.{path.stem}"
            try:
                mod = importlib.import_module(module_name)
                if hasattr(mod, "create_plugin"):
                    plugin = mod.create_plugin()
                    if isinstance(plugin, Plugin) and plugin.enabled:
                        self.register(plugin)
                    elif not plugin.enabled:
                        logger.info("Plugin %s is disabled, skipping", plugin.name)
                else:
                    logger.warning(
                        "Plugin %s has no create_plugin() function", module_name
                    )
            except Exception as e:
                logger.error("Failed to load plugin %s: %s", module_name, e)

    # ── Hook dispatchers ─────────────────────────────────────────

    async def detect_command(
        self, ctx: PluginContext, user_input: str
    ) -> CommandResult:
        """Run detect_command on each plugin until one claims the input."""
        for plugin in self._plugins:
            try:
                result = await plugin.detect_command(ctx, user_input)
                if result.handled:
                    return result
            except Exception as e:
                logger.error(
                    "Plugin %s.detect_command failed: %s", plugin.name, e
                )
        return CommandResult()

    async def pre_response(
        self, ctx: PluginContext, user_input: str
    ) -> PreResponseResult:
        """Collect context injections from ALL plugins (results merged)."""
        merged = PreResponseResult()
        for plugin in self._plugins:
            try:
                result = await plugin.pre_response(ctx, user_input)
                merged.context_messages.extend(result.context_messages)
                merged.show_thinking = merged.show_thinking or result.show_thinking
            except Exception as e:
                logger.error(
                    "Plugin %s.pre_response failed: %s", plugin.name, e
                )
        return merged

    async def start_backgrounds(self, ctx: PluginContext) -> None:
        """Start all plugin background tasks."""
        for plugin in self._plugins:
            try:
                tasks = await plugin.start_background(ctx)
                self._background_tasks.extend(tasks)
            except Exception as e:
                logger.error(
                    "Plugin %s.start_background failed: %s", plugin.name, e
                )

    async def handle_event(self, ctx: PluginContext, event: dict) -> bool:
        """Dispatch event to plugins. First one to return True consumes it."""
        for plugin in self._plugins:
            try:
                consumed = await plugin.handle_event(ctx, event)
                if consumed:
                    return True
            except Exception as e:
                logger.error(
                    "Plugin %s.handle_event failed: %s", plugin.name, e
                )
        return False

    async def shutdown_all(self, ctx: PluginContext) -> None:
        """Shutdown all plugins and cancel background tasks."""
        for task in self._background_tasks:
            if not task.done():
                task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        for plugin in self._plugins:
            try:
                await plugin.shutdown(ctx)
            except Exception as e:
                logger.error(
                    "Plugin %s.shutdown failed: %s", plugin.name, e
                )

    def get_all_help_entries(self) -> list[tuple[str, str]]:
        """Collect help entries from all plugins."""
        entries = []
        for plugin in self._plugins:
            try:
                entries.extend(plugin.get_help_entries())
            except Exception as e:
                logger.error(
                    "Plugin %s.get_help_entries failed: %s", plugin.name, e
                )
        return entries

    def any_active_task(self, ctx: PluginContext) -> bool:
        """Check if any plugin has an active background activity."""
        for plugin in self._plugins:
            try:
                if plugin.has_active_task(ctx):
                    return True
            except Exception as e:
                logger.error(
                    "Plugin %s.has_active_task failed: %s", plugin.name, e
                )
        return False

    def check_interrupt(self, ctx: PluginContext, user_input: str) -> bool:
        """Check if any plugin wants its activity interrupted on this input."""
        for plugin in self._plugins:
            try:
                if plugin.should_interrupt_on_input(ctx, user_input):
                    return True
            except Exception as e:
                logger.error(
                    "Plugin %s.should_interrupt failed: %s", plugin.name, e
                )
        return False
