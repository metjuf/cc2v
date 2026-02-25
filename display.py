"""Eigy AI Assistant — Terminal display module.

Rich-formatted output and prompt_toolkit input.
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


# ── Output ─────────────────────────────────────────────────────────

_NAME = config.ASSISTANT_NAME


def show_welcome_banner() -> None:
    """Display the welcome panel on first launch."""
    panel = Panel(
        Text.from_markup(
            f"[bold cyan]{_NAME} — osobní AI asistentka.\n"
            "K vašim službám.[/bold cyan]"
        ),
        title=f"[bold white]{_NAME}[/bold white]",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)
    console.print()


def show_assistant(text: str) -> None:
    """Display an assistant message (non-streaming)."""
    console.print(Text(f"{_NAME} > ", style="bold cyan"), end="")
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

    def __init__(self):
        self._started = False

    def start(self) -> None:
        console.print(Text(f"{_NAME} > ", style="bold cyan"), end="")
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


def show_thinking() -> None:
    """Show a thinking indicator."""
    console.print(Text(f"  {_NAME} přemýšlí...", style="dim cyan"))


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
            partial(session.prompt, HTML("<ansigreen><b>Ty &gt; </b></ansigreen>")),
        )
        return text.strip()
    except EOFError:
        return None
    except KeyboardInterrupt:
        return None


# ── Help ───────────────────────────────────────────────────────────


def show_help() -> None:
    """Show available commands."""
    table = Table(title=f"{_NAME} — Příkazy", border_style="cyan")
    table.add_column("Příkaz", style="bold cyan")
    table.add_column("Popis", style="white")

    commands = [
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
