"""iMessage plugin — read, reply, contact management, background watcher."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import display
from plugins.base import Plugin, PluginContext, CommandResult
from imessage_bot import MessagesDB, IMessage, send_imessage, ContactBook

logger = logging.getLogger(__name__)

# ── Command detection regexes (moved from main.py) ──────────────

_IMESSAGE_SHOW_RE = re.compile(
    r"^zobraz\s+imessage(?:\s+(\d+))?$", re.IGNORECASE
)
_IMESSAGE_REPLY_RE = re.compile(
    r"^odep(?:is|iš)\s+na\s+imessage\s+(\d+)$", re.IGNORECASE
)
_IMESSAGE_SAVE_RE = re.compile(
    r"^ulo[žz]\s+kontakt\s+(\d+)\s+(.+)$", re.IGNORECASE
)
_IMESSAGE_CONTACTS_RE = re.compile(
    r"^kontakty?$", re.IGNORECASE
)

_WATCH_INTERVAL = 5  # seconds


def detect_imessage_command(text: str) -> tuple[str, str] | None:
    """Detect iMessage command in user input. Returns (cmd, arg) or None."""
    text = text.strip()

    m = _IMESSAGE_SHOW_RE.match(text)
    if m:
        return ("zobraz", m.group(1) or "5")

    m = _IMESSAGE_REPLY_RE.match(text)
    if m:
        return ("reply", m.group(1))

    m = _IMESSAGE_SAVE_RE.match(text)
    if m:
        return ("save_contact", f"{m.group(1)} {m.group(2)}")

    if _IMESSAGE_CONTACTS_RE.match(text):
        return ("list_contacts", "")

    return None


class IMessagePlugin(Plugin):
    name = "imessage"
    priority = 70

    def _state(self, ctx: PluginContext) -> dict:
        """Get plugin state, initializing defaults on first access."""
        state = ctx.get_state(self.name)
        if "initialized" not in state:
            state["initialized"] = True
            state["db"] = None  # lazy init
            state["cache"] = []
            state["contacts"] = ContactBook()
        return state

    # ── Hooks ────────────────────────────────────────────────────

    async def detect_command(
        self, ctx: PluginContext, user_input: str
    ) -> CommandResult:
        cmd = detect_imessage_command(user_input)
        if cmd is None:
            return CommandResult()

        state = self._state(ctx)
        await self._handle_command(cmd[0], cmd[1], state, ctx)
        return CommandResult(handled=True, skip_llm=True)

    async def start_background(
        self, ctx: PluginContext
    ) -> list[asyncio.Task]:
        """Start the iMessage watcher background task."""
        state = self._state(ctx)
        task = asyncio.create_task(
            self._watcher_loop(ctx.event_queue, state)
        )
        return [task]

    async def handle_event(self, ctx: PluginContext, event: dict) -> bool:
        if event.get("type") != "imessage_new":
            return False

        msg = event.get("message")
        if msg:
            state = self._state(ctx)
            contacts = state["contacts"]
            name = contacts.get_name(msg.sender)
            notification = f"Nová zpráva od {name}: {msg.text}"
            display.show_system(f"  iMessage od {name}: {msg.text}")

            if ctx.slog:
                ctx.slog.log(
                    "imessage_received", sender=name, text=msg.text
                )

            if ctx.proactive:
                await ctx.proactive(notification)
        return True

    async def shutdown(self, ctx: PluginContext) -> None:
        state = ctx.get_state(self.name)
        db = state.get("db")
        if db is not None:
            db.close()
            state["db"] = None

    def get_help_entries(self) -> list[tuple[str, str]]:
        return [
            ("zobraz imessage [N]", "Zobrazit příchozí iMessage zprávy"),
            ("odepiš na imessage X", "Odpovědět na iMessage zprávu"),
            ("ulož kontakt X Jméno", "Uložit iMessage kontakt"),
            ("kontakty", "Zobrazit uložené kontakty"),
        ]

    # ── Internal helpers (moved from main.py) ────────────────────

    def _ensure_db(self, state: dict) -> MessagesDB | None:
        """Lazy-init iMessage DB on first use."""
        if state["db"] is not None:
            return state["db"]
        db_path = Path.home() / "Library" / "Messages" / "chat.db"
        if not db_path.exists():
            display.show_system("Chyba: Databáze iMessage nenalezena.")
            return None
        try:
            state["db"] = MessagesDB(db_path)
            return state["db"]
        except Exception as e:
            display.show_system(
                f"Chyba: Nelze otevřít iMessage DB: {e}\n"
                "  Zkontroluj Full Disk Access pro Terminal."
            )
            return None

    async def _handle_command(
        self, cmd: str, arg: str, state: dict, ctx: PluginContext
    ) -> None:
        """Handle iMessage command — exact replica of main.py logic."""
        contacts = state["contacts"]
        cache = state["cache"]

        # Contact listing doesn't need DB
        if cmd == "list_contacts":
            all_c = contacts.all_contacts()
            if not all_c:
                display.show_system("Žádné uložené kontakty.")
            else:
                display.show_system("Uložené kontakty:")
                for phone, name in all_c.items():
                    display.show_system(f"  {name} ({phone})")
            return

        # Save contact uses cache but not DB
        if cmd == "save_contact":
            parts = arg.split(maxsplit=1)
            if len(parts) < 2:
                display.show_system("Použití: ulož kontakt X Jméno")
                return
            try:
                idx = int(parts[0])
            except ValueError:
                display.show_system(f"Neplatné číslo: {parts[0]}")
                return
            if not cache or idx < 1 or idx > len(cache):
                display.show_system(
                    'Nejdřív "zobraz imessage", pak ulož kontakt.'
                )
                return
            target = cache[idx - 1]
            name = parts[1].strip()
            contacts.set_contact(target.sender, name)
            display.show_system(f"Uloženo: {target.sender} → {name}")
            return

        # Lazy-init DB on first use
        imessage_db = self._ensure_db(state)
        if imessage_db is None:
            return

        if cmd == "zobraz":
            count = max(1, min(int(arg), 50))
            messages = imessage_db.get_recent_incoming(count)
            if not messages:
                display.show_system("Žádné příchozí zprávy.")
            else:
                cache.clear()
                cache.extend(messages)
                display.show_system(
                    f"Posledních {len(messages)} iMessage zpráv:"
                )
                for i, msg in enumerate(messages, 1):
                    ts = msg.timestamp.strftime("%d.%m. %H:%M")
                    name = contacts.get_name(msg.sender)
                    display.show_system(f"  [{i}] {name} ({ts})")
                    display.show_system(f"      {msg.text}")
            return

        if cmd == "reply":
            num = int(arg)
            if not cache:
                display.show_system(
                    'Nejdřív napiš "zobraz imessage" pro načtení zpráv.'
                )
                return
            if num < 1 or num > len(cache):
                display.show_system(
                    f"Neplatné číslo. Zadej 1–{len(cache)}."
                )
                return

            msg = cache[num - 1]
            ts = msg.timestamp.strftime("%d.%m. %H:%M")
            name = contacts.get_name(msg.sender)
            display.show_system(f"Odpovědět na zprávu od {name} ({ts}):")
            display.show_system(f'  "{msg.text}"')
            display.show_system(
                "Napiš odpověď (nebo prázdný řádek pro zrušení):"
            )

            reply_text = await display.get_user_input()
            if not reply_text:
                display.show_system("Zrušeno.")
                return

            display.show_system(
                f'Odeslat "{reply_text}" → {name}? (a/n)'
            )
            confirm = await display.get_user_input()
            if confirm and confirm.lower() in ("a", "ano", "y", "yes"):
                display.show_system("Odesílám...")
                if send_imessage(msg.sender, reply_text):
                    display.show_system("Zpráva odeslána!")
                else:
                    display.show_system(
                        "Chyba: Odeslání selhalo. "
                        "Zkontroluj Messages.app."
                    )
            else:
                display.show_system("Zrušeno.")
            return

    async def _watcher_loop(
        self, event_queue: asyncio.Queue, state: dict
    ) -> None:
        """Polls iMessage DB for new messages."""
        db_path = Path.home() / "Library" / "Messages" / "chat.db"
        if not db_path.exists():
            return

        try:
            watcher_db = MessagesDB(db_path)
        except Exception:
            return

        last_rowid = watcher_db.get_latest_rowid()
        # Share DB instance
        if not state.get("db"):
            state["db"] = watcher_db

        while True:
            await asyncio.sleep(_WATCH_INTERVAL)
            try:
                new_msgs = watcher_db.get_messages_since(last_rowid)
                for msg in new_msgs:
                    last_rowid = max(last_rowid, msg.rowid)
                    await event_queue.put({
                        "type": "imessage_new",
                        "message": msg,
                    })
            except Exception as e:
                logger.warning("iMessage watcher error: %s", e)


def create_plugin() -> Plugin:
    return IMessagePlugin()
