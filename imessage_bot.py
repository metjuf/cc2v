"""Eigy AI Assistant — iMessage bot pro macOS.

Monitoruje příchozí iMessage zprávy a umožňuje na ně odpovídat.
Standalone terminálový nástroj — nezávislý na hlavní Eigy aplikaci.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────

CHECK_INTERVAL: int = 5
DEFAULT_MESSAGE_COUNT: int = 5
MAX_MESSAGE_COUNT: int = 50
AUTO_WATCH: bool = True
MESSAGES_DB_PATH: Path = Path.home() / "Library" / "Messages" / "chat.db"
COCOA_EPOCH_OFFSET: int = 978_307_200
NANOSECOND_THRESHOLD: float = 1e15
CONTACTS_PATH: Path = Path(__file__).parent / "data" / "imessage_contacts.json"

console = Console()


# ── Contact book ─────────────────────────────────────────────────


class ContactBook:
    """Simple phone/email → name mapping stored as JSON."""

    def __init__(self, path: Path = CONTACTS_PATH) -> None:
        self._path = path
        self._contacts: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._contacts = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                self._contacts = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._contacts, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_name(self, identifier: str) -> str:
        """Return display name for identifier, or the identifier itself."""
        return self._contacts.get(identifier, identifier)

    def set_contact(self, identifier: str, name: str) -> None:
        """Save a contact name for the given phone/email."""
        self._contacts[identifier] = name
        self._save()

    def remove_contact(self, identifier: str) -> bool:
        """Remove a contact. Returns True if found and removed."""
        if identifier in self._contacts:
            del self._contacts[identifier]
            self._save()
            return True
        return False

    def all_contacts(self) -> dict[str, str]:
        """Return all saved contacts."""
        return dict(self._contacts)


# ── Data model ────────────────────────────────────────────────────


@dataclass
class IMessage:
    """Single iMessage from the database."""

    rowid: int
    sender: str
    text: str
    timestamp: datetime
    is_from_me: bool


# ── Database reader ───────────────────────────────────────────────


class MessagesDB:
    """Read-only reader for macOS Messages chat.db."""

    def __init__(self, db_path: Path = MESSAGES_DB_PATH) -> None:
        # Try read-only URI first, fall back to plain path
        try:
            uri = f"file:{db_path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError:
            self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def get_recent_incoming(self, limit: int = DEFAULT_MESSAGE_COUNT) -> list[IMessage]:
        """Get recent incoming messages, oldest first."""
        rows = self._conn.execute(
            """
            SELECT
                m.ROWID,
                m.text,
                m.attributedBody,
                m.date,
                m.is_from_me,
                COALESCE(h.id, 'neznámý') AS sender_id
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.is_from_me = 0
            ORDER BY m.ROWID DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        messages = []
        for row in rows:
            text = row["text"]
            if not text:
                text = self._extract_attributed_body(row["attributedBody"])
            if not text:
                text = "[média/příloha]"

            messages.append(
                IMessage(
                    rowid=row["ROWID"],
                    sender=row["sender_id"],
                    text=text,
                    timestamp=self._cocoa_to_datetime(row["date"]),
                    is_from_me=bool(row["is_from_me"]),
                )
            )

        return list(reversed(messages))

    def get_latest_rowid(self) -> int:
        """Get the ROWID of the most recent message."""
        row = self._conn.execute("SELECT MAX(ROWID) AS max_id FROM message").fetchone()
        return row["max_id"] or 0

    def get_messages_since(self, since_rowid: int) -> list[IMessage]:
        """Get incoming messages with ROWID greater than since_rowid."""
        rows = self._conn.execute(
            """
            SELECT
                m.ROWID,
                m.text,
                m.attributedBody,
                m.date,
                m.is_from_me,
                COALESCE(h.id, 'neznámý') AS sender_id
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.ROWID > ? AND m.is_from_me = 0
            ORDER BY m.ROWID ASC
            """,
            (since_rowid,),
        ).fetchall()

        messages = []
        for row in rows:
            text = row["text"]
            if not text:
                text = self._extract_attributed_body(row["attributedBody"])
            if not text:
                text = "[média/příloha]"

            messages.append(
                IMessage(
                    rowid=row["ROWID"],
                    sender=row["sender_id"],
                    text=text,
                    timestamp=self._cocoa_to_datetime(row["date"]),
                    is_from_me=bool(row["is_from_me"]),
                )
            )

        return messages

    @staticmethod
    def _cocoa_to_datetime(cocoa_ts: int | float | None) -> datetime:
        """Convert macOS Cocoa timestamp to Python datetime."""
        if cocoa_ts is None or cocoa_ts == 0:
            return datetime(2001, 1, 1)
        if cocoa_ts > NANOSECOND_THRESHOLD:
            cocoa_ts = cocoa_ts / 1_000_000_000
        unix_ts = cocoa_ts + COCOA_EPOCH_OFFSET
        try:
            return datetime.fromtimestamp(unix_ts)
        except (OSError, ValueError):
            return datetime(2001, 1, 1)

    @staticmethod
    def _extract_attributed_body(blob: bytes | None) -> str | None:
        """Extract plain text from NSAttributedString binary blob (Ventura+)."""
        if not blob:
            return None
        try:
            idx = blob.find(b"\x01+")
            if idx == -1:
                return None
            rest = blob[idx + 2 :]
            text_bytes = bytearray()
            started = False
            for b in rest:
                if b >= 0x20 or b in (0x0A, 0x0D, 0x09):
                    started = True
                    text_bytes.append(b)
                elif started:
                    break
            if text_bytes:
                return text_bytes.decode("utf-8", errors="ignore").strip()
            return None
        except Exception:
            return None


# ── AppleScript sender ────────────────────────────────────────────


def send_imessage(recipient: str, text: str, timeout: int = 30) -> bool:
    """Send an iMessage via AppleScript. Returns True on success."""
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped_recipient = recipient.replace("\\", "\\\\").replace('"', '\\"')

    script = (
        'tell application "Messages"\n'
        "    set targetService to 1st service whose service type = iMessage\n"
        f'    set targetBuddy to buddy "{escaped_recipient}" of targetService\n'
        f'    send "{escaped_text}" to targetBuddy\n'
        "end tell"
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.error("AppleScript error: %s", result.stderr.strip())
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("AppleScript timed out after %d seconds", timeout)
        return False
    except Exception as e:
        logger.error("Failed to send iMessage: %s", e)
        return False


# ── Display helpers ───────────────────────────────────────────────


def show_banner() -> None:
    """Display the welcome banner."""
    panel = Panel(
        Text.from_markup(
            "[bold cyan]iMessage Bot[/bold cyan]\n"
            "[dim]Monitorování a odpovídání na iMessage zprávy[/dim]"
        ),
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()


def show_messages(messages: list[IMessage], contacts: ContactBook | None = None) -> None:
    """Display a numbered list of messages."""
    if not messages:
        console.print(Text("  Žádné zprávy.", style="yellow"))
        console.print()
        return

    for i, msg in enumerate(messages, 1):
        ts = msg.timestamp.strftime("%d.%m. %H:%M")
        name = contacts.get_name(msg.sender) if contacts else msg.sender
        header = Text()
        header.append(f"[{i}] ", style="bold cyan")
        header.append(f"{name} ", style="bold white")
        header.append(f"({ts})", style="dim")
        console.print(header)
        console.print(Text(f"    {msg.text}", style="white"))
        console.print()


def show_notification(msg: IMessage, contacts: ContactBook | None = None) -> None:
    """Display a new-message notification."""
    ts = msg.timestamp.strftime("%H:%M")
    name = contacts.get_name(msg.sender) if contacts else msg.sender
    console.print()
    console.print(Text(f"  \U0001f514 NOVÁ ZPRÁVA od {name} ({ts})", style="bold yellow"))
    console.print(Text(f"  \U0001f514 {msg.text}", style="yellow"))
    console.print(Text('  \U0001f4a1 Napiš "zobraz imessage" pro zobrazení', style="dim"))
    console.print()


def show_info(text: str) -> None:
    """Display an info message."""
    console.print(Text(f"  {text}", style="yellow"))


def show_error(text: str) -> None:
    """Display an error message."""
    console.print(Text(f"  Chyba: {text}", style="bold red"))


def show_success(text: str) -> None:
    """Display a success message."""
    console.print(Text(f"  {text}", style="bold green"))


def show_help() -> None:
    """Display the help table."""
    table = Table(title="iMessage Bot — Příkazy", border_style="cyan")
    table.add_column("Příkaz", style="bold cyan")
    table.add_column("Popis", style="white")

    commands = [
        ("zobraz imessage", "Zobrazit posledních 5 zpráv"),
        ("zobraz imessage N", "Zobrazit posledních N zpráv (max 50)"),
        ("odepiš na imessage X", "Odpovědět na zprávu číslo X"),
        ("ulož kontakt X Jméno", "Uložit jméno ke zprávě X"),
        ("kontakty", "Zobrazit uložené kontakty"),
        ("sledování zapni", "Zapnout automatické sledování"),
        ("sledování vypni", "Vypnout automatické sledování"),
        ("interval N", "Nastavit interval sledování na N sekund"),
        ("pomoc", "Zobrazit tuto nápovědu"),
        ("konec", "Ukončit program"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)
    console.print()


# ── Message watcher (background thread) ──────────────────────────


class MessageWatcher:
    """Background thread that polls for new iMessages."""

    def __init__(self, db: MessagesDB) -> None:
        self._db = db
        self._last_rowid = db.get_latest_rowid()
        self._enabled = threading.Event()
        if AUTO_WATCH:
            self._enabled.set()
        self._interval = CHECK_INTERVAL
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="imessage-watcher",
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def enable(self) -> None:
        self._enabled.set()

    def disable(self) -> None:
        self._enabled.clear()

    @property
    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, value: int) -> None:
        self._interval = max(1, value)

    def _run(self) -> None:
        """Main watcher loop."""
        while self._running:
            for _ in range(self._interval * 10):
                if not self._running:
                    return
                time.sleep(0.1)

            if not self._enabled.is_set():
                continue

            try:
                new_messages = self._db.get_messages_since(self._last_rowid)
                for msg in new_messages:
                    show_notification(msg)
                    self._last_rowid = max(self._last_rowid, msg.rowid)
            except Exception as e:
                logger.warning("Watcher error: %s", e)


# ── Command parser ───────────────────────────────────────────────


def parse_command(raw: str) -> tuple[str, str]:
    """Parse user input into (command, args)."""
    stripped = raw.strip()
    lower = stripped.lower()

    if lower.startswith("zobraz imessage") or lower.startswith("zobraz imess"):
        parts = stripped.split()
        count = parts[2] if len(parts) > 2 else ""
        return ("zobraz_imessage", count)

    m = re.match(r"odep(?:is|iš)\s+na\s+imessage\s+(\d+)", lower)
    if m:
        return ("odepis", m.group(1))

    # "ulož kontakt 3 Petr" or "uloz kontakt 3 Petr Novák"
    m = re.match(r"ulo[žz]\s+kontakt\s+(\d+)\s+(.+)", stripped, re.IGNORECASE)
    if m:
        return ("uloz_kontakt", f"{m.group(1)} {m.group(2)}")

    if lower in ("kontakty", "kontakt"):
        return ("kontakty", "")

    if lower.startswith("sledov"):
        if "zapni" in lower or "zapnout" in lower:
            return ("sledovani", "zapni")
        if "vypni" in lower or "vypnout" in lower:
            return ("sledovani", "vypni")
        return ("sledovani", "")

    m = re.match(r"interval\s+(\d+)", lower)
    if m:
        return ("interval", m.group(1))

    if lower in ("konec", "exit", "quit"):
        return ("konec", "")

    if lower in ("pomoc", "help", "nápověda", "napoveda"):
        return ("pomoc", "")

    return ("unknown", stripped)


# ── Reply flow ───────────────────────────────────────────────────


def handle_reply(
    msg_number: int,
    messages_cache: list[IMessage],
    contacts: ContactBook | None = None,
) -> None:
    """Handle the reply flow: show detail, get text, confirm, send."""
    if not messages_cache:
        show_error('Žádné zprávy. Nejdřív použij "zobraz imessage".')
        return

    if msg_number < 1 or msg_number > len(messages_cache):
        show_error(f"Neplatné číslo zprávy. Zadej 1–{len(messages_cache)}.")
        return

    msg = messages_cache[msg_number - 1]
    ts = msg.timestamp.strftime("%d.%m. %H:%M")
    name = contacts.get_name(msg.sender) if contacts else msg.sender

    console.print()
    console.print(Text("  Odpovědět na:", style="bold cyan"))
    console.print(Text(f"  Od: {name} ({ts})", style="white"))
    console.print(Text(f"  Text: {msg.text}", style="white"))
    console.print()

    try:
        reply_text = input("  Tvoje odpověď > ").strip()
    except (EOFError, KeyboardInterrupt):
        show_info("Zrušeno.")
        return

    if not reply_text:
        show_info("Prázdná odpověď, zrušeno.")
        return

    console.print()
    console.print(Text(f'  Odeslat: "{reply_text}"', style="white"))
    console.print(Text(f"  Komu: {name}", style="white"))

    try:
        confirm = input("  Potvrdit? (a/n) > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        show_info("Zrušeno.")
        return

    if confirm not in ("a", "ano", "y", "yes"):
        show_info("Zrušeno.")
        return

    show_info("Odesílám...")
    if send_imessage(msg.sender, reply_text):
        show_success("Zpráva odeslána!")
    else:
        show_error("Odeslání selhalo. Zkontroluj, že Messages.app běží a máš oprávnění.")


# ── Main ─────────────────────────────────────────────────────────


def main() -> None:
    """Entry point: validate DB, start watcher, run input loop."""
    if not MESSAGES_DB_PATH.exists():
        show_error(f"Databáze nenalezena: {MESSAGES_DB_PATH}")
        show_error("Zkontroluj, že Messages.app je nastavena na tomto Macu.")
        sys.exit(1)

    try:
        db = MessagesDB()
    except sqlite3.OperationalError as e:
        show_error(f"Nelze otevřít databázi: {e}")
        show_error(
            "Zkontroluj Full Disk Access: System Settings → Privacy & Security "
            "→ Full Disk Access → přidej Terminal."
        )
        sys.exit(1)

    show_banner()

    contacts = ContactBook()
    watcher = MessageWatcher(db)
    watcher.start()

    status = "zapnuto" if watcher.is_enabled else "vypnuto"
    show_info(f"Sledování: {status}, interval: {watcher.interval}s")
    show_info('Napiš "pomoc" pro seznam příkazů.')
    console.print()

    messages_cache: list[IMessage] = []

    try:
        while True:
            try:
                raw = input("iMessage > ")
            except EOFError:
                break
            except KeyboardInterrupt:
                console.print()
                break

            raw = raw.strip()
            if not raw:
                continue

            cmd, arg = parse_command(raw)

            if cmd == "konec":
                break

            elif cmd == "pomoc":
                show_help()

            elif cmd == "zobraz_imessage":
                count = DEFAULT_MESSAGE_COUNT
                if arg:
                    try:
                        count = int(arg)
                        count = max(1, min(count, MAX_MESSAGE_COUNT))
                    except ValueError:
                        show_error(f"Neplatné číslo: {arg}")
                        continue
                messages_cache = db.get_recent_incoming(count)
                show_messages(messages_cache, contacts)

            elif cmd == "uloz_kontakt":
                parts = arg.split(maxsplit=1)
                if len(parts) < 2:
                    show_error("Použití: ulož kontakt X Jméno")
                    continue
                try:
                    idx = int(parts[0])
                except ValueError:
                    show_error(f"Neplatné číslo: {parts[0]}")
                    continue
                if not messages_cache or idx < 1 or idx > len(messages_cache):
                    show_error('Nejdřív "zobraz imessage", pak ulož kontakt.')
                    continue
                target = messages_cache[idx - 1]
                name = parts[1].strip()
                contacts.set_contact(target.sender, name)
                show_success(f"Uloženo: {target.sender} → {name}")

            elif cmd == "kontakty":
                all_c = contacts.all_contacts()
                if not all_c:
                    show_info("Žádné uložené kontakty.")
                else:
                    show_info("Uložené kontakty:")
                    for phone, name in all_c.items():
                        show_info(f"  {name} ({phone})")

            elif cmd == "odepis":
                try:
                    num = int(arg)
                except ValueError:
                    show_error(f"Neplatné číslo zprávy: {arg}")
                    continue
                handle_reply(num, messages_cache, contacts)

            elif cmd == "sledovani":
                if arg == "zapni":
                    watcher.enable()
                    show_info("Sledování zapnuto.")
                elif arg == "vypni":
                    watcher.disable()
                    show_info("Sledování vypnuto.")
                else:
                    status = "zapnuto" if watcher.is_enabled else "vypnuto"
                    show_info(f"Sledování: {status}")

            elif cmd == "interval":
                try:
                    new_interval = int(arg)
                    if new_interval < 1:
                        show_error("Interval musí být alespoň 1 sekunda.")
                        continue
                    watcher.interval = new_interval
                    show_info(f"Interval nastaven na {new_interval}s.")
                except ValueError:
                    show_error(f"Neplatné číslo: {arg}")

            else:
                show_error(f'Neznámý příkaz: "{raw}". Napiš "pomoc" pro nápovědu.')

    finally:
        watcher.stop()
        db.close()
        show_info("iMessage bot ukončen.")


if __name__ == "__main__":
    main()
