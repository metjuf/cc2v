"""Book reader plugin — EPUB reading with TTS, bookmark management."""

from __future__ import annotations

import asyncio
import logging
import re

import config
import display
from plugins.base import Plugin, PluginContext, CommandResult

logger = logging.getLogger(__name__)

# ── Command detection regexes (moved from main.py) ──────────────

_BOOK_READ_RE = re.compile(
    r"^(?:čti|přečti|pokračuj\s+(?:v\s+)?(?:čtení|četbě))\s+(?:knihu?\s+)?(.+)$",
    re.IGNORECASE,
)
_BOOK_STOP_RE = re.compile(
    r"^(?:zastav|stop|přestaň|pozastav)\s+(?:čtení|četbu)$",
    re.IGNORECASE,
)
_BOOK_DELETE_RE = re.compile(
    r"^(?:vymaž|smaž|odstraň)\s+záložku\s+(.+)$",
    re.IGNORECASE,
)


def detect_book_command(text: str) -> tuple[str, str] | None:
    """Detect book reader command. Returns (cmd, arg) or None."""
    text = text.strip()

    m = _BOOK_READ_RE.match(text)
    if m:
        return ("read", m.group(1).strip())

    if _BOOK_STOP_RE.match(text):
        return ("stop", "")

    m = _BOOK_DELETE_RE.match(text)
    if m:
        return ("delete", m.group(1).strip())

    return None


class BookReaderPlugin(Plugin):
    name = "book_reader"
    priority = 80

    def _state(self, ctx: PluginContext) -> dict:
        return ctx.get_state(self.name)

    # ── Hooks ────────────────────────────────────────────────────

    async def detect_command(
        self, ctx: PluginContext, user_input: str
    ) -> CommandResult:
        cmd = detect_book_command(user_input)
        if cmd is None:
            return CommandResult()

        await self._handle_book_command(cmd[0], cmd[1], ctx)
        return CommandResult(handled=True, skip_llm=True)

    async def handle_event(self, ctx: PluginContext, event: dict) -> bool:
        etype = event.get("type")

        if etype == "book_progress":
            title = event.get("title", "?")
            page = event.get("page", 0)
            total = event.get("total", 0)
            msg = f"Čtu ti '{title}', stránka {page} z {total}."
            display.show_system(msg)
            ctx.current_messages.append({"role": "assistant", "content": msg})
            ctx.memory.save_message("assistant", msg)
            if ctx.slog:
                ctx.slog.log(
                    "book_progress", title=title, page=page, total=total
                )
            return True

        if etype == "book_finished":
            title = event.get("title", "?")
            total = event.get("total", 0)
            msg = f"Dočetla jsem '{title}' — {total} stránek."
            display.show_assistant(msg)
            ctx.current_messages.append({"role": "assistant", "content": msg})
            ctx.memory.save_message("assistant", msg)
            self._state(ctx).clear()
            if ctx.slog:
                ctx.slog.log("book_finished", title=title, total=total)
            return True

        return False

    def has_active_task(self, ctx: PluginContext) -> bool:
        return bool(self._state(ctx).get("task"))

    def should_interrupt_on_input(
        self, ctx: PluginContext, user_input: str
    ) -> bool:
        """Stop reading on chat input, but not on slash/book commands."""
        state = self._state(ctx)
        if not state.get("task"):
            return False
        is_slash = user_input.startswith("/")
        is_book_cmd = detect_book_command(user_input) is not None
        return not is_slash and not is_book_cmd

    async def shutdown(self, ctx: PluginContext) -> None:
        await self._stop_reading(self._state(ctx), ctx.audio_player)

    def get_help_entries(self) -> list[tuple[str, str]]:
        return [
            ("čti knihu X", "Začít/pokračovat ve čtení EPUB knihy"),
            ("zastav čtení", "Pozastavit čtení (záložka se uloží)"),
            ("vymaž záložku X", "Smazat záložku pro knihu"),
        ]

    # ── Internal helpers (moved from main.py) ────────────────────

    async def _stop_reading(self, state: dict, audio_player) -> int | None:
        """Cancel active book reading task. Returns last page or None."""
        if not state.get("task"):
            return None

        cancel_event = state.get("cancel_event")
        task = state["task"]

        if cancel_event:
            cancel_event.set()
        audio_player.stop()

        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

        last_page = state.get("current_page", 0)
        state.clear()
        return last_page

    async def _handle_book_command(
        self, cmd: str, arg: str, ctx: PluginContext
    ) -> None:
        """Handle book reader commands (read/stop/delete)."""
        from book_reader import find_book, parse_epub, book_reading_task

        state = self._state(ctx)

        if cmd == "read":
            book_name = arg

            path = find_book(book_name)
            if not path:
                display.show_system(
                    f"Kniha '{book_name}' nenalezena v {config.BOOKS_DIR}/"
                )
                return

            # Stop any existing reading
            await self._stop_reading(state, ctx.audio_player)

            try:
                book = parse_epub(path)
            except Exception as e:
                display.show_error(f"Nepodařilo se načíst knihu: {e}")
                logger.error("EPUB parse failed: %s", e)
                return

            # Load or create bookmark
            bookmark = ctx.db.get_bookmark(book_name.lower())
            start_page = bookmark["current_page"] if bookmark else 0

            if start_page >= book.total_pages:
                start_page = 0

            if start_page > 0:
                msg = (
                    f"Pokračuji ve čtení '{book.title}' "
                    f"od stránky {start_page + 1} z {book.total_pages}."
                )
            else:
                msg = (
                    f"Začínám číst '{book.title}' — "
                    f"{book.total_pages} stránek."
                )

            display.show_assistant(msg)
            ctx.current_messages.append({"role": "assistant", "content": msg})
            ctx.memory.save_message("assistant", msg)
            if ctx.speak:
                await ctx.speak(msg)

            cancel_event = asyncio.Event()

            def update_bookmark(page: int) -> None:
                state["current_page"] = page
                ctx.db.save_bookmark(
                    book_name.lower(), page, book.total_pages
                )

            ctx.db.save_bookmark(
                book_name.lower(), start_page, book.total_pages
            )

            task = asyncio.create_task(
                book_reading_task(
                    book=book,
                    start_page=start_page,
                    tts=ctx.tts,
                    audio_player=ctx.audio_player,
                    event_queue=ctx.event_queue,
                    cancel_event=cancel_event,
                    update_bookmark=update_bookmark,
                )
            )

            state["task"] = task
            state["cancel_event"] = cancel_event
            state["book"] = book
            state["current_page"] = start_page
            state["book_name"] = book_name.lower()

            if ctx.slog:
                ctx.slog.log(
                    "book_start",
                    title=book.title,
                    page=start_page,
                    total=book.total_pages,
                )

        elif cmd == "stop":
            if not state.get("task"):
                display.show_system("Momentálně nic nečtu.")
                return

            book = state.get("book")
            last_page = await self._stop_reading(state, ctx.audio_player)

            title = book.title if book else "knihu"
            total = book.total_pages if book else "?"
            msg = (
                f"Zastavila jsem čtení '{title}' "
                f"na stránce {last_page} z {total}."
            )

            display.show_assistant(msg)
            ctx.current_messages.append({"role": "assistant", "content": msg})
            ctx.memory.save_message("assistant", msg)

            if ctx.slog:
                ctx.slog.log("book_stop", title=title, page=last_page)

        elif cmd == "delete":
            book_name = arg
            existed = ctx.db.delete_bookmark(book_name.lower())

            if existed:
                msg = f"Záložka pro '{book_name}' smazána."
            else:
                msg = f"Žádná záložka pro '{book_name}' neexistuje."

            display.show_assistant(msg)
            ctx.current_messages.append({"role": "assistant", "content": msg})
            ctx.memory.save_message("assistant", msg)

            if ctx.slog:
                ctx.slog.log(
                    "book_delete_bookmark",
                    book_name=book_name,
                    existed=existed,
                )


def create_plugin() -> Plugin:
    return BookReaderPlugin()
