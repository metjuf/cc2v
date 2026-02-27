"""Tests for plugin system — base class, PluginManager, and all plugins."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from plugins import PluginManager
from plugins.base import Plugin, PluginContext, CommandResult, PreResponseResult


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ctx():
    """Minimal PluginContext for testing."""
    return PluginContext(
        db=MagicMock(),
        memory=MagicMock(),
        tts=MagicMock(),
        audio_player=MagicMock(),
        avatar_queue=MagicMock(),
        event_queue=asyncio.Queue(),
        current_messages=[],
        slog=None,
    )


# ── Plugin base class tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_defaults(ctx):
    """Default plugin hooks return no-op results."""
    p = Plugin()
    assert p.name == "unnamed"
    assert p.priority == 100
    assert p.enabled is True

    cmd = await p.detect_command(ctx, "hello")
    assert cmd.handled is False

    pre = await p.pre_response(ctx, "hello")
    assert pre.context_messages == []
    assert pre.show_thinking is False

    tasks = await p.start_background(ctx)
    assert tasks == []

    consumed = await p.handle_event(ctx, {"type": "test"})
    assert consumed is False

    await p.shutdown(ctx)  # no error

    assert p.get_help_entries() == []
    assert p.has_active_task(ctx) is False
    assert p.should_interrupt_on_input(ctx, "hello") is False


def test_plugin_context_get_state(ctx):
    """PluginContext.get_state creates isolated state per plugin."""
    s1 = ctx.get_state("plugin_a")
    s2 = ctx.get_state("plugin_b")
    s1["key"] = "val_a"
    s2["key"] = "val_b"

    assert ctx.get_state("plugin_a")["key"] == "val_a"
    assert ctx.get_state("plugin_b")["key"] == "val_b"


# ── PluginManager tests ─────────────────────────────────────────


def test_register_and_priority_ordering(ctx):
    """Plugins are sorted by priority after registration."""
    pm = PluginManager()

    p_high = Plugin()
    p_high.name = "high"
    p_high.priority = 200

    p_low = Plugin()
    p_low.name = "low"
    p_low.priority = 10

    p_mid = Plugin()
    p_mid.name = "mid"
    p_mid.priority = 50

    pm.register(p_high)
    pm.register(p_low)
    pm.register(p_mid)

    names = [p.name for p in pm.plugins]
    assert names == ["low", "mid", "high"]


@pytest.mark.asyncio
async def test_detect_command_first_wins(ctx):
    """First plugin to return handled=True claims the input."""
    pm = PluginManager()

    class ClaimPlugin(Plugin):
        name = "claimer"
        priority = 10

        async def detect_command(self, ctx, user_input):
            return CommandResult(handled=True, skip_llm=True)

    class NeverPlugin(Plugin):
        name = "never"
        priority = 20

        async def detect_command(self, ctx, user_input):
            raise AssertionError("Should not be called")

    pm.register(ClaimPlugin())
    pm.register(NeverPlugin())

    result = await pm.detect_command(ctx, "test")
    assert result.handled is True


@pytest.mark.asyncio
async def test_pre_response_collects_all(ctx):
    """Pre-response merges context from all plugins."""
    pm = PluginManager()

    class PluginA(Plugin):
        name = "a"

        async def pre_response(self, ctx, user_input):
            return PreResponseResult(
                context_messages=[{"role": "system", "content": "from A"}]
            )

    class PluginB(Plugin):
        name = "b"

        async def pre_response(self, ctx, user_input):
            return PreResponseResult(
                context_messages=[{"role": "system", "content": "from B"}],
                show_thinking=True,
            )

    pm.register(PluginA())
    pm.register(PluginB())

    result = await pm.pre_response(ctx, "test")
    assert len(result.context_messages) == 2
    assert result.show_thinking is True


@pytest.mark.asyncio
async def test_handle_event_first_consumes(ctx):
    """First plugin returning True consumes the event."""
    pm = PluginManager()

    class Consumer(Plugin):
        name = "consumer"
        priority = 10

        async def handle_event(self, ctx, event):
            return event.get("type") == "mine"

    class Listener(Plugin):
        name = "listener"
        priority = 20
        called = False

        async def handle_event(self, ctx, event):
            self.called = True
            return False

    consumer = Consumer()
    listener = Listener()
    pm.register(consumer)
    pm.register(listener)

    consumed = await pm.handle_event(ctx, {"type": "mine"})
    assert consumed is True
    assert listener.called is False  # Not reached

    consumed2 = await pm.handle_event(ctx, {"type": "other"})
    assert consumed2 is False
    assert listener.called is True


@pytest.mark.asyncio
async def test_error_isolation_detect_command(ctx):
    """A plugin that raises doesn't crash PluginManager."""
    pm = PluginManager()

    class BrokenPlugin(Plugin):
        name = "broken"
        priority = 10

        async def detect_command(self, ctx, user_input):
            raise RuntimeError("I'm broken!")

    class GoodPlugin(Plugin):
        name = "good"
        priority = 20

        async def detect_command(self, ctx, user_input):
            return CommandResult(handled=True)

    pm.register(BrokenPlugin())
    pm.register(GoodPlugin())

    # Broken plugin is caught, good plugin still runs
    result = await pm.detect_command(ctx, "test")
    assert result.handled is True


@pytest.mark.asyncio
async def test_error_isolation_pre_response(ctx):
    """Broken plugin in pre_response doesn't prevent others."""
    pm = PluginManager()

    class BrokenPlugin(Plugin):
        name = "broken"

        async def pre_response(self, ctx, user_input):
            raise ValueError("Crash!")

    class GoodPlugin(Plugin):
        name = "good"

        async def pre_response(self, ctx, user_input):
            return PreResponseResult(
                context_messages=[{"role": "system", "content": "ok"}]
            )

    pm.register(BrokenPlugin())
    pm.register(GoodPlugin())

    result = await pm.pre_response(ctx, "test")
    assert len(result.context_messages) == 1


@pytest.mark.asyncio
async def test_error_isolation_shutdown(ctx):
    """Broken shutdown doesn't prevent others from shutting down."""
    pm = PluginManager()
    shutdown_calls = []

    class BrokenPlugin(Plugin):
        name = "broken"

        async def shutdown(self, ctx):
            raise RuntimeError("Shutdown crash!")

    class GoodPlugin(Plugin):
        name = "good"

        async def shutdown(self, ctx):
            shutdown_calls.append("good")

    pm.register(BrokenPlugin())
    pm.register(GoodPlugin())

    await pm.shutdown_all(ctx)
    assert "good" in shutdown_calls


@pytest.mark.asyncio
async def test_shutdown_cancels_background_tasks(ctx):
    """Background tasks are cancelled on shutdown."""
    pm = PluginManager()

    class BgPlugin(Plugin):
        name = "bg"

        async def start_background(self, ctx):
            async def forever():
                while True:
                    await asyncio.sleep(1)

            return [asyncio.create_task(forever())]

    pm.register(BgPlugin())
    await pm.start_backgrounds(ctx)
    assert len(pm._background_tasks) == 1
    assert not pm._background_tasks[0].done()

    await pm.shutdown_all(ctx)
    assert len(pm._background_tasks) == 0


def test_get_all_help_entries():
    """Help entries collected from all plugins."""
    pm = PluginManager()

    class HelpPlugin(Plugin):
        name = "helper"

        def get_help_entries(self):
            return [("cmd1", "desc1"), ("cmd2", "desc2")]

    pm.register(HelpPlugin())
    entries = pm.get_all_help_entries()
    assert len(entries) == 2


# ── WebSearchPlugin tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_plugin_no_detection(ctx):
    """No detection → empty pre_response."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value=None), \
         patch("plugins.web_search_plugin._find_crypto_mention", return_value=None), \
         patch("plugins.web_search_plugin.detect_search_request", return_value=None):
        result = await plugin.pre_response(ctx, "ahoj jak se máš")

    assert result.context_messages == []
    assert result.show_thinking is False


@pytest.mark.asyncio
async def test_web_search_plugin_crypto_detection(ctx):
    """Crypto detected → context message injected."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()
    ctx.slog = MagicMock()

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value="bitcoin"), \
         patch("plugins.web_search_plugin.fetch_crypto_price", new_callable=AsyncMock, return_value={"usd": 50000}), \
         patch("plugins.web_search_plugin.format_crypto_price", return_value="BTC: $50,000"), \
         patch("plugins.web_search_plugin.detect_search_request", return_value=None), \
         patch("plugins.web_search_plugin.display"):
        result = await plugin.pre_response(ctx, "cena bitcoinu")

    assert len(result.context_messages) == 1
    assert "50,000" in result.context_messages[0]["content"]
    assert result.show_thinking is True


@pytest.mark.asyncio
async def test_web_search_plugin_search_detection(ctx):
    """Search detected → context message injected."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value=None), \
         patch("plugins.web_search_plugin.detect_search_request", return_value="python tutorial"), \
         patch("plugins.web_search_plugin.web_search", new_callable=AsyncMock, return_value=[{"title": "T"}]), \
         patch("plugins.web_search_plugin.format_search_results", return_value="Results..."), \
         patch("plugins.web_search_plugin.display"):
        result = await plugin.pre_response(ctx, "vyhledej python tutorial")

    assert len(result.context_messages) == 1
    assert "python tutorial" in result.context_messages[0]["content"]


@pytest.mark.asyncio
async def test_web_search_plugin_crypto_suppresses_search(ctx):
    """When crypto found, web search is skipped."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()
    search_called = False

    async def fake_search(q):
        nonlocal search_called
        search_called = True
        return []

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value="bitcoin"), \
         patch("plugins.web_search_plugin.fetch_crypto_price", new_callable=AsyncMock, return_value={"usd": 50000}), \
         patch("plugins.web_search_plugin.format_crypto_price", return_value="BTC: $50,000"), \
         patch("plugins.web_search_plugin.detect_search_request", return_value="bitcoin price"), \
         patch("plugins.web_search_plugin.web_search", side_effect=fake_search), \
         patch("plugins.web_search_plugin.display"):
        result = await plugin.pre_response(ctx, "cena bitcoinu")

    assert not search_called
    assert len(result.context_messages) == 1  # only crypto


def test_web_search_plugin_help():
    """WebSearchPlugin provides help entries."""
    from plugins.web_search_plugin import WebSearchPlugin

    entries = WebSearchPlugin().get_help_entries()
    assert len(entries) == 2


# ── Crypto LLM fallback tests ───────────────────────────────────


def test_find_crypto_mention():
    """_find_crypto_mention finds known crypto names in text."""
    from web_search import _find_crypto_mention

    assert _find_crypto_mention("jak je na tom bitcoin?") == "bitcoin"
    assert _find_crypto_mention("co dělá ETH dneska") == "ethereum"
    assert _find_crypto_mention("ahoj jak se máš") is None
    assert _find_crypto_mention("ten solana je fajn") == "solana"


def test_detect_crypto_regex_direct():
    """Regex detects standard price patterns."""
    from web_search import detect_crypto_request

    assert detect_crypto_request("cena bitcoinu") == "bitcoin"
    assert detect_crypto_request("kolik stojí ETH") == "ethereum"
    assert detect_crypto_request("kurz solany") == "solana"


def test_detect_crypto_regex_with_price_word():
    """Regex detects crypto + price word combo."""
    from web_search import detect_crypto_request

    assert detect_crypto_request("aktuální cena bitcoin") == "bitcoin"
    assert detect_crypto_request("kolik je doge") == "dogecoin"


def test_detect_crypto_regex_miss():
    """Regex misses indirect price queries."""
    from web_search import detect_crypto_request

    # These should NOT be detected by regex (no price pattern)
    assert detect_crypto_request("jak je na tom bitcoin") is None
    assert detect_crypto_request("co dělá ethereum") is None
    assert detect_crypto_request("bitcoin šel nahoru ne") is None


@pytest.mark.asyncio
async def test_detect_crypto_llm_fallback_positive():
    """LLM fallback detects indirect crypto price queries."""
    from web_search import detect_crypto_request_llm

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock, return_value="ano"):
        result = await detect_crypto_request_llm("jak je na tom bitcoin?")

    assert result == "bitcoin"


@pytest.mark.asyncio
async def test_detect_crypto_llm_fallback_negative():
    """LLM fallback rejects non-price crypto mentions."""
    from web_search import detect_crypto_request_llm

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock, return_value="ne"):
        result = await detect_crypto_request_llm("co je bitcoin a jak funguje?")

    assert result is None


@pytest.mark.asyncio
async def test_detect_crypto_llm_fallback_no_mention():
    """LLM fallback skips when no crypto is mentioned."""
    from web_search import detect_crypto_request_llm

    # Should return None without even calling aux model
    result = await detect_crypto_request_llm("ahoj jak se máš")
    assert result is None


@pytest.mark.asyncio
async def test_detect_crypto_llm_fallback_error():
    """LLM fallback returns None on error."""
    from web_search import detect_crypto_request_llm

    with patch("chat_engine.get_auxiliary_response", new_callable=AsyncMock, side_effect=RuntimeError("API down")):
        result = await detect_crypto_request_llm("jak je na tom bitcoin")

    assert result is None


@pytest.mark.asyncio
async def test_web_search_plugin_llm_fallback_flow(ctx):
    """Plugin uses LLM fallback when regex misses but crypto is mentioned."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()
    ctx.slog = MagicMock()

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value=None), \
         patch("plugins.web_search_plugin._find_crypto_mention", return_value="bitcoin"), \
         patch("plugins.web_search_plugin.detect_crypto_request_llm", new_callable=AsyncMock, return_value="bitcoin"), \
         patch("plugins.web_search_plugin.fetch_crypto_price", new_callable=AsyncMock, return_value={"usd": 50000}), \
         patch("plugins.web_search_plugin.format_crypto_price", return_value="BTC: $50,000"), \
         patch("plugins.web_search_plugin.detect_search_request", return_value=None), \
         patch("plugins.web_search_plugin.display"):
        result = await plugin.pre_response(ctx, "jak je na tom bitcoin")

    assert len(result.context_messages) == 1
    assert "50,000" in result.context_messages[0]["content"]


@pytest.mark.asyncio
async def test_web_search_plugin_llm_fallback_rejects(ctx):
    """Plugin doesn't inject crypto when LLM says no."""
    from plugins.web_search_plugin import WebSearchPlugin

    plugin = WebSearchPlugin()

    with patch("plugins.web_search_plugin.detect_crypto_request", return_value=None), \
         patch("plugins.web_search_plugin._find_crypto_mention", return_value="bitcoin"), \
         patch("plugins.web_search_plugin.detect_crypto_request_llm", new_callable=AsyncMock, return_value=None), \
         patch("plugins.web_search_plugin.detect_search_request", return_value=None):
        result = await plugin.pre_response(ctx, "co je bitcoin a jak funguje")

    assert result.context_messages == []


# ── BookReaderPlugin tests ──────────────────────────────────────


def test_book_reader_detect_read():
    """Book reader detects read commands."""
    from plugins.book_reader_plugin import detect_book_command

    assert detect_book_command("čti knihu slova") == ("read", "slova")
    assert detect_book_command("přečti knihu Duna") == ("read", "Duna")


def test_book_reader_detect_stop():
    """Book reader detects stop commands."""
    from plugins.book_reader_plugin import detect_book_command

    assert detect_book_command("zastav čtení") == ("stop", "")
    assert detect_book_command("stop čtení") == ("stop", "")


def test_book_reader_detect_delete():
    """Book reader detects delete bookmark commands."""
    from plugins.book_reader_plugin import detect_book_command

    assert detect_book_command("vymaž záložku slova") == ("delete", "slova")


def test_book_reader_detect_none():
    """Normal text doesn't match book commands."""
    from plugins.book_reader_plugin import detect_book_command

    assert detect_book_command("ahoj") is None
    assert detect_book_command("co je nového") is None


@pytest.mark.asyncio
async def test_book_reader_plugin_command_detection(ctx):
    """Plugin claims book commands via detect_command."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()

    with patch.object(plugin, "_handle_book_command", new_callable=AsyncMock):
        result = await plugin.detect_command(ctx, "čti knihu test")

    assert result.handled is True
    assert result.skip_llm is True


@pytest.mark.asyncio
async def test_book_reader_plugin_no_command(ctx):
    """Plugin doesn't claim non-book input."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()
    result = await plugin.detect_command(ctx, "ahoj jak se máš")
    assert result.handled is False


@pytest.mark.asyncio
async def test_book_reader_handle_progress_event(ctx):
    """Plugin handles book_progress events."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()
    ctx.memory.save_message = MagicMock()

    with patch("plugins.book_reader_plugin.display"):
        consumed = await plugin.handle_event(ctx, {
            "type": "book_progress", "title": "Duna", "page": 5, "total": 100
        })

    assert consumed is True
    assert len(ctx.current_messages) == 1
    assert "Duna" in ctx.current_messages[0]["content"]


@pytest.mark.asyncio
async def test_book_reader_handle_finished_event(ctx):
    """Plugin handles book_finished events and clears state."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()
    state = ctx.get_state("book_reader")
    state["task"] = "dummy"
    ctx.memory.save_message = MagicMock()

    with patch("plugins.book_reader_plugin.display"):
        consumed = await plugin.handle_event(ctx, {
            "type": "book_finished", "title": "Duna", "total": 100
        })

    assert consumed is True
    assert state.get("task") is None  # cleared


def test_book_reader_has_active_task(ctx):
    """has_active_task reflects reading state."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()
    assert plugin.has_active_task(ctx) is False

    ctx.get_state("book_reader")["task"] = "some_task"
    assert plugin.has_active_task(ctx) is True


def test_book_reader_should_interrupt(ctx):
    """Interrupt on normal input but not slash/book commands."""
    from plugins.book_reader_plugin import BookReaderPlugin

    plugin = BookReaderPlugin()

    # No active task → never interrupt
    assert plugin.should_interrupt_on_input(ctx, "hello") is False

    # Active task
    ctx.get_state("book_reader")["task"] = "some_task"
    assert plugin.should_interrupt_on_input(ctx, "hello") is True
    assert plugin.should_interrupt_on_input(ctx, "/help") is False
    assert plugin.should_interrupt_on_input(ctx, "zastav čtení") is False


def test_book_reader_help():
    """BookReaderPlugin provides help entries."""
    from plugins.book_reader_plugin import BookReaderPlugin

    entries = BookReaderPlugin().get_help_entries()
    assert len(entries) == 3


# ── IMessagePlugin tests ────────────────────────────────────────


def test_imessage_detect_zobraz():
    """iMessage detects 'zobraz imessage' commands."""
    from plugins.imessage_plugin import detect_imessage_command

    assert detect_imessage_command("zobraz imessage") == ("zobraz", "5")
    assert detect_imessage_command("zobraz imessage 10") == ("zobraz", "10")


def test_imessage_detect_reply():
    """iMessage detects reply commands."""
    from plugins.imessage_plugin import detect_imessage_command

    assert detect_imessage_command("odepiš na imessage 3") == ("reply", "3")
    assert detect_imessage_command("odepis na imessage 1") == ("reply", "1")


def test_imessage_detect_save_contact():
    """iMessage detects save contact commands."""
    from plugins.imessage_plugin import detect_imessage_command

    result = detect_imessage_command("ulož kontakt 2 Jan Novák")
    assert result == ("save_contact", "2 Jan Novák")


def test_imessage_detect_contacts():
    """iMessage detects contact list command."""
    from plugins.imessage_plugin import detect_imessage_command

    assert detect_imessage_command("kontakty") == ("list_contacts", "")
    assert detect_imessage_command("kontakt") == ("list_contacts", "")


def test_imessage_detect_none():
    """Normal text doesn't match iMessage commands."""
    from plugins.imessage_plugin import detect_imessage_command

    assert detect_imessage_command("ahoj") is None
    assert detect_imessage_command("pošli zprávu") is None


@pytest.mark.asyncio
async def test_imessage_plugin_command_detection(ctx):
    """Plugin claims iMessage commands."""
    from plugins.imessage_plugin import IMessagePlugin

    plugin = IMessagePlugin()

    with patch.object(plugin, "_handle_command", new_callable=AsyncMock):
        result = await plugin.detect_command(ctx, "kontakty")

    assert result.handled is True


@pytest.mark.asyncio
async def test_imessage_plugin_no_command(ctx):
    """Plugin doesn't claim non-iMessage input."""
    from plugins.imessage_plugin import IMessagePlugin

    plugin = IMessagePlugin()
    result = await plugin.detect_command(ctx, "ahoj")
    assert result.handled is False


@pytest.mark.asyncio
async def test_imessage_handle_event(ctx):
    """Plugin handles imessage_new events."""
    from plugins.imessage_plugin import IMessagePlugin

    plugin = IMessagePlugin()
    ctx.proactive = AsyncMock()

    msg_mock = MagicMock()
    msg_mock.sender = "+420123456789"
    msg_mock.text = "Ahoj!"

    with patch("plugins.imessage_plugin.display"):
        consumed = await plugin.handle_event(ctx, {
            "type": "imessage_new", "message": msg_mock
        })

    assert consumed is True
    ctx.proactive.assert_called_once()


@pytest.mark.asyncio
async def test_imessage_ignores_other_events(ctx):
    """Plugin ignores non-iMessage events."""
    from plugins.imessage_plugin import IMessagePlugin

    plugin = IMessagePlugin()
    consumed = await plugin.handle_event(ctx, {"type": "idle_trigger"})
    assert consumed is False


@pytest.mark.asyncio
async def test_imessage_lazy_state_init(ctx):
    """State is initialized on first access."""
    from plugins.imessage_plugin import IMessagePlugin

    plugin = IMessagePlugin()
    state = plugin._state(ctx)
    assert state["initialized"] is True
    assert state["db"] is None
    assert state["cache"] == []


def test_imessage_help():
    """IMessagePlugin provides help entries."""
    from plugins.imessage_plugin import IMessagePlugin

    entries = IMessagePlugin().get_help_entries()
    assert len(entries) == 4


# ── Discovery tests ─────────────────────────────────────────────


def test_discover_loads_plugins():
    """PluginManager.discover() finds and loads all *_plugin.py files."""
    pm = PluginManager()
    pm.discover()

    names = {p.name for p in pm.plugins}
    assert "web_search" in names
    assert "book_reader" in names
    assert "imessage" in names


def test_discover_priority_order():
    """Plugins discovered in correct priority order."""
    pm = PluginManager()
    pm.discover()

    priorities = [p.priority for p in pm.plugins]
    assert priorities == sorted(priorities)
