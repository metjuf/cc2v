"""Eigy AI Assistant — Terminal display module.

Rich-formatted output and prompt_toolkit input.
Supports dual assistants with per-assistant colors.
"""

from __future__ import annotations

import asyncio
from functools import partial

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config

console = Console()

# ── Prompt toolkit session (lazy-init, thread-safe) ────────────────

_prompt_session: PromptSession | None = None


def _get_prompt_session() -> PromptSession:
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession()
    return _prompt_session


# ── Per-assistant styling ──────────────────────────────────────────


def _assistant_color(assistant_id: str) -> str:
    """Get Rich color for a given assistant."""
    return config.ASSISTANTS.get(assistant_id, {}).get("color", "cyan")


def _assistant_name(assistant_id: str) -> str:
    """Get display name for a given assistant."""
    return config.ASSISTANTS.get(assistant_id, {}).get("name", "Assistant")


# ── Output ─────────────────────────────────────────────────────────


def show_welcome_banner() -> None:
    """Display the welcome panel on first launch."""
    panel = Panel(
        Text.from_markup(
            "[bold cyan]Eigy[/bold cyan] & [bold magenta]Delan[/bold magenta]"
            " — osobní AI asistenti.\n"
            "[bold white]K vašim službám.[/bold white]"
        ),
        title="[bold white]Eigy & Delan[/bold white]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()


def show_assistant(text: str, assistant_id: str = "eigy") -> None:
    """Display an assistant message (non-streaming)."""
    name = _assistant_name(assistant_id)
    color = _assistant_color(assistant_id)
    console.print(Text(f"{name} > ", style=f"bold {color}"), end="")
    console.print(Markdown(text))
    console.print()


def show_user(text: str) -> None:
    """Display a user message."""
    console.print(Text(f"Ty > {text}", style="bold green"))
    console.print()


def show_system(text: str) -> None:
    """Display a system/info message."""
    console.print(Text(f"  {text}", style="yellow"))
    console.print()


def show_error(text: str) -> None:
    """Display an error message."""
    console.print(Text(f"  Chyba: {text}", style="bold red"))
    console.print()


# ── Streaming output ───────────────────────────────────────────────


class StreamingDisplay:
    """Manages streaming token-by-token display of assistant's response."""

    def __init__(self, assistant_id: str = "eigy"):
        self._started = False
        self._assistant_id = assistant_id

    def start(self) -> None:
        name = _assistant_name(self._assistant_id)
        color = _assistant_color(self._assistant_id)
        console.print(Text(f"{name} > ", style=f"bold {color}"), end="")
        self._started = True

    def token(self, text: str) -> None:
        if not self._started:
            self.start()
        console.print(text, end="", highlight=False)

    def end(self) -> None:
        if self._started:
            console.print()  # final newline
            console.print()
            self._started = False


# ── Spinner ────────────────────────────────────────────────────────


def show_thinking(assistant_id: str = "eigy") -> None:
    """Show a thinking indicator."""
    name = _assistant_name(assistant_id)
    color = _assistant_color(assistant_id)
    console.print(Text(f"  {name} přemýšlí...", style=f"dim {color}"))


# ── Input ──────────────────────────────────────────────────────────


async def get_user_input() -> str | None:
    """Get user input via prompt_toolkit (async-friendly).

    Returns None on EOF (Ctrl+D).
    """
    session = _get_prompt_session()
    try:
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None,
            partial(session.prompt, HTML("<ansigreen><b>ED &gt; </b></ansigreen>")),
        )
        return text.strip()
    except EOFError:
        return None
    except KeyboardInterrupt:
        return None


# ── Help ───────────────────────────────────────────────────────────


def show_help() -> None:
    """Show available commands."""
    table = Table(title="Eigy & Delan — Příkazy", border_style="cyan")
    table.add_column("Příkaz", style="bold cyan")
    table.add_column("Popis", style="white")

    commands = [
        ("ED zpráva", "Zpráva pro oba asistenty (výchozí)"),
        ("E zpráva", "Zpráva jen pro Eigy"),
        ("D zpráva", "Zpráva jen pro Delana"),
        ("discussion mode [téma]", "Eigy & Delan si začnou povídat"),
        ("end discussion mode", "Ukončit diskuzní mód"),
        ("", ""),
        ("/voice on/off", "Zapnout/vypnout hlas"),
        ("/voice [jméno]", "Přepnout TTS hlas"),
        ("/volume [0-100]", "Nastavit hlasitost"),
        ("/avatar", "Přepnout okno avatara"),
        ("/emotion [emoce]", "Ručně nastavit emoci avatara"),
        ("/model [název]", "Přepnout primární chat model"),
        ("/memory", "Ukázat, co si pamatuji"),
        ("/forget", "Smazat všechny vzpomínky (s potvrzením)"),
        ("/timer", "Zobrazit aktivní timery"),
        ("/timer cancel [id]", "Zrušit timer"),
        ("/history", "Zobrazit historii aktuální relace"),
        ("/export", "Exportovat vše do JSON"),
        ("/help", "Zobrazit tuto nápovědu"),
        ("", ""),
        ("zobraz imessage [N]", "Zobrazit příchozí iMessage zprávy"),
        ("odepiš na imessage X", "Odpovědět na iMessage zprávu"),
        ("ulož kontakt X Jméno", "Uložit iMessage kontakt"),
        ("kontakty", "Zobrazit uložené kontakty"),
        ("", ""),
        ("\"vyhledej X\"", "Automatický web search (DuckDuckGo)"),
        ("\"cena bitcoinu\"", "Živá cena kryptoměny (CoinGecko)"),
        ("\"stopni mi 10 minut\"", "Nastavit odpočet/timer"),
        ("exit / quit / konec", "Ukončit (automaticky uloží relaci)"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)

    console.print(table)
    console.print()
