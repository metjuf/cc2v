"""Eigy AI Assistant — Entry point.

Pygame runs in MAIN thread, chat + TTS in daemon thread.
Communication via thread-safe queues.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import re
import sys
import threading

import config
import chat_engine
import display
from avatar.emotion_detector import detect_emotion, detect_emotion_llm
from memory.database import Database
from memory.memory_manager import MemoryManager
from timer_manager import TimerManager, parse_timer_request
from proactive import IdleMonitor
from web_search import (
    detect_search_request, search as web_search, format_results as format_search_results,
    detect_crypto_request, fetch_crypto_price, format_crypto_price,
)
from tts_engine import TTSEngine, SentenceBuffer, cleanup_temp_files
from audio_player import AudioPlayer
from imessage_bot import MessagesDB, IMessage, send_imessage, ContactBook

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
# Suppress noisy warnings from search dependencies
logging.getLogger("primp").setLevel(logging.ERROR)
logging.getLogger("ddgs").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

_NAME = config.ASSISTANT_NAME

# Strip emoji/emoticons from LLM output (prevents TTS reading them)
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U000020E3"             # combining enclosing keycap
    "\U00002600-\U000026FF"  # misc symbols
    "\U00002300-\U000023FF"  # misc technical
    "]+",
)


# ── Chat thread (daemon) ──────────────────────────────────────────


def chat_thread_main(
    avatar_queue: queue.Queue,
    audio_player: AudioPlayer,
) -> None:
    """Daemon thread entry point: runs the async chat loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(chat_main(avatar_queue, audio_player))
    except Exception as e:
        logger.error("Chat thread error: %s", e)
    finally:
        # Signal avatar window to close
        avatar_queue.put({"type": "quit"})
        loop.close()


async def chat_main(
    avatar_queue: queue.Queue,
    audio_player: AudioPlayer,
) -> None:
    """Async chat main — init memory, onboard, run chat loop."""
    tts = TTSEngine()

    # Validate configuration
    warnings = config.validate_config()
    for w in warnings:
        display.show_system(f"Warning: {w}")

    if not config.ANTHROPIC_API_KEY and not config.OPENROUTER_API_KEY:
        display.show_error(
            "Není nastaven žádný LLM API klíč. "
            "Zkopíruj .env.example do .env a přidej svůj API klíč."
        )
        avatar_queue.put({"type": "quit"})
        return

    # Initialize database and memory
    db = Database(config.DATABASE_PATH)
    memory = MemoryManager(db)

    try:
        # First-run onboarding
        if db.is_first_run():
            await first_run_onboarding(memory, tts, audio_player, avatar_queue)

        # Main chat loop
        await chat_loop(db, memory, tts, audio_player, avatar_queue)

        # End session
        display.show_system("Ukládám relaci...")
        await memory.end_session()
        display.show_system("Relace uložena. Tak zase příště.")
    except Exception as e:
        logger.error("Chat loop error: %s", e)
        display.show_error(f"Unexpected error: {e}")
    finally:
        db.close()
        cleanup_temp_files()


async def first_run_onboarding(
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """First-launch onboarding — Eigy introduces herself and asks for user's name."""
    display.show_welcome_banner()

    greeting = (
        "Dobrý den. Jsem vaše osobní asistentka. "
        "Než začneme — jak vám mám říkat?"
    )
    display.show_assistant(greeting)
    await _speak(greeting, tts, audio_player, avatar_queue)

    name = await display.get_user_input()
    if not name:
        name = "šéfe"

    memory.profile.set_name(name)
    memory.user_name = name

    response = (
        f"Těší mě, {name}. Jsem {_NAME} — vaše osobní AI asistentka. "
        f"Budu si pamatovat, co mi řeknete, a pomohu vám s čímkoli potřebujete. "
        "Napište /help pro seznam příkazů."
    )
    display.show_assistant(response)
    avatar_queue.put({"type": "emotion", "value": "happy"})
    await _speak(response, tts, audio_player, avatar_queue)

    display.console.print()


async def handle_command(
    user_input: str,
    memory: MemoryManager,
    db: Database,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    timer_mgr: TimerManager,
) -> bool:
    """Handle slash commands. Returns True if handled."""
    parts = user_input.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        display.show_help()
    elif cmd == "/memory":
        summary = memory.profile.get_summary()
        if summary:
            display.show_system(f"Co o tobě vím: {summary}")
        else:
            display.show_system("Zatím o tobě nic nemám uložené.")
        summaries = db.get_recent_summaries(limit=5)
        if summaries:
            display.show_system("Nedávné konverzace:")
            for s in summaries:
                display.show_system(f"  {s['date']}: {s['summary']}")
    elif cmd == "/forget":
        display.show_system(
            "Tím smažeš VŠECHNY moje vzpomínky na tebe. Jsi si jistý/á? (ano/ne)"
        )
        confirm = await display.get_user_input()
        if confirm and confirm.lower() in ("ano", "a", "yes", "y"):
            db.clear_all()
            display.show_system("Paměť vymazána. Jako bychom se nikdy nepotkali.")
            memory.user_name = "šéfe"
        else:
            display.show_system("Zrušeno.")
    elif cmd == "/voice":
        if arg and arg.lower() == "on":
            tts.set_enabled(True)
            display.show_system("Hlas zapnutý.")
        elif arg and arg.lower() == "off":
            tts.set_enabled(False)
            audio_player.stop()
            display.show_system("Hlas vypnutý.")
        elif arg:
            tts.set_voice(arg)
            display.show_system(f"Hlas nastaven na: {arg}")
        else:
            status = "zapnutý" if tts.enabled else "vypnutý"
            display.show_system(f"Hlas je {status}. Použití: /voice on|off|<jméno>")
    elif cmd == "/volume":
        if arg:
            try:
                level = int(arg)
                audio_player.set_volume(level)
                display.show_system(f"Hlasitost nastavena na {level} %.")
            except ValueError:
                display.show_system("Použití: /volume 0-100")
        else:
            display.show_system(f"Hlasitost: {int(audio_player.volume * 100)} %")
    elif cmd == "/emotion":
        if arg:
            avatar_queue.put({"type": "emotion", "value": arg.lower()})
            display.show_system(f"Emoce nastavena na: {arg}")
        else:
            display.show_system("Použití: /emotion neutral|amused|happy|concerned|surprised|thinking")
    elif cmd == "/avatar":
        avatar_queue.put({"type": "toggle_avatar"})
        display.show_system("Okno avatara přepnuto.")
    elif cmd == "/timer":
        if arg and arg.lower().startswith("cancel"):
            cancel_parts = arg.split(maxsplit=1)
            if len(cancel_parts) > 1:
                tid = cancel_parts[1].strip()
                if timer_mgr.cancel_timer(tid):
                    display.show_system(f"Timer {tid} zrušen.")
                else:
                    display.show_system(f"Timer {tid} nenalezen.")
            else:
                timer_mgr.cancel_all()
                display.show_system("Všechny timery zrušeny.")
        else:
            timers = timer_mgr.list_timers()
            if timers:
                display.show_system("Aktivní timery:")
                for t in timers:
                    remaining = int(t["remaining"])
                    mins, secs = divmod(remaining, 60)
                    display.show_system(
                        f"  [{t['id']}] {t['label']} — zbývá {mins}m {secs}s"
                    )
            else:
                display.show_system("Žádné aktivní timery.")
    elif cmd == "/model":
        if arg:
            config.ANTHROPIC_MODEL = arg
            display.show_system(f"Primární model nastaven na: {arg}")
        else:
            display.show_system(f"Aktuální model: {config.ANTHROPIC_MODEL}")
    elif cmd == "/history":
        msgs = db.get_session_messages(memory.session_id)
        if msgs:
            for m in msgs:
                role = "Ty" if m["role"] == "user" else _NAME
                display.show_system(f"  {role}: {m['content'][:100]}...")
        else:
            display.show_system("V této relaci zatím žádné zprávy.")
    elif cmd == "/export":
        import json as _json
        export_data = {
            "profile": db.get_all_profile(),
            "conversations": [],
        }
        summaries = db.get_recent_summaries(limit=100)
        for s in summaries:
            export_data["conversations"].append({
                "date": str(s["date"]),
                "summary": s["summary"],
            })
        export_path = config.PROJECT_ROOT / "eigy_export.json"
        export_path.write_text(_json.dumps(export_data, indent=2, ensure_ascii=False))
        display.show_system(f"Exportováno do {export_path}")
    else:
        display.show_system(f"Příkaz '{cmd}' ještě není implementován.")

    return True


# ── iMessage integration ──────────────────────────────────────────

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


async def handle_imessage_command(
    cmd: str,
    arg: str,
    imessage_db: MessagesDB | None,
    imessage_cache: list[IMessage],
    contacts: ContactBook,
) -> tuple[MessagesDB | None, list[IMessage], bool]:
    """Handle iMessage command. Returns (db, cache, handled)."""
    from pathlib import Path

    # Contact listing doesn't need DB
    if cmd == "list_contacts":
        all_c = contacts.all_contacts()
        if not all_c:
            display.show_system("Žádné uložené kontakty.")
        else:
            display.show_system("Uložené kontakty:")
            for phone, name in all_c.items():
                display.show_system(f"  {name} ({phone})")
        return imessage_db, imessage_cache, True

    # Save contact uses cache but not DB
    if cmd == "save_contact":
        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            display.show_system("Použití: ulož kontakt X Jméno")
            return imessage_db, imessage_cache, True
        try:
            idx = int(parts[0])
        except ValueError:
            display.show_system(f"Neplatné číslo: {parts[0]}")
            return imessage_db, imessage_cache, True
        if not imessage_cache or idx < 1 or idx > len(imessage_cache):
            display.show_system('Nejdřív "zobraz imessage", pak ulož kontakt.')
            return imessage_db, imessage_cache, True
        target = imessage_cache[idx - 1]
        name = parts[1].strip()
        contacts.set_contact(target.sender, name)
        display.show_system(f"Uloženo: {target.sender} → {name}")
        return imessage_db, imessage_cache, True

    # Lazy-init DB on first use
    if imessage_db is None:
        db_path = Path.home() / "Library" / "Messages" / "chat.db"
        if not db_path.exists():
            display.show_system("Chyba: Databáze iMessage nenalezena.")
            return None, imessage_cache, True
        try:
            imessage_db = MessagesDB(db_path)
        except Exception as e:
            display.show_system(
                f"Chyba: Nelze otevřít iMessage DB: {e}\n"
                "  Zkontroluj Full Disk Access pro Terminal."
            )
            return None, imessage_cache, True

    if cmd == "zobraz":
        count = max(1, min(int(arg), 50))
        messages = imessage_db.get_recent_incoming(count)
        if not messages:
            display.show_system("Žádné příchozí zprávy.")
        else:
            imessage_cache.clear()
            imessage_cache.extend(messages)
            display.show_system(f"Posledních {len(messages)} iMessage zpráv:")
            for i, msg in enumerate(messages, 1):
                ts = msg.timestamp.strftime("%d.%m. %H:%M")
                name = contacts.get_name(msg.sender)
                display.show_system(f"  [{i}] {name} ({ts})")
                display.show_system(f"      {msg.text}")
        return imessage_db, imessage_cache, True

    if cmd == "reply":
        num = int(arg)
        if not imessage_cache:
            display.show_system('Nejdřív napiš "zobraz imessage" pro načtení zpráv.')
            return imessage_db, imessage_cache, True
        if num < 1 or num > len(imessage_cache):
            display.show_system(f"Neplatné číslo. Zadej 1–{len(imessage_cache)}.")
            return imessage_db, imessage_cache, True

        msg = imessage_cache[num - 1]
        ts = msg.timestamp.strftime("%d.%m. %H:%M")
        name = contacts.get_name(msg.sender)
        display.show_system(f"Odpovědět na zprávu od {name} ({ts}):")
        display.show_system(f"  \"{msg.text}\"")
        display.show_system("Napiš odpověď (nebo prázdný řádek pro zrušení):")

        reply_text = await display.get_user_input()
        if not reply_text:
            display.show_system("Zrušeno.")
            return imessage_db, imessage_cache, True

        display.show_system(f'Odeslat "{reply_text}" → {name}? (a/n)')
        confirm = await display.get_user_input()
        if confirm and confirm.lower() in ("a", "ano", "y", "yes"):
            display.show_system("Odesílám...")
            if send_imessage(msg.sender, reply_text):
                display.show_system("Zpráva odeslána!")
            else:
                display.show_system("Chyba: Odeslání selhalo. Zkontroluj Messages.app.")
        else:
            display.show_system("Zrušeno.")

        return imessage_db, imessage_cache, True

    return imessage_db, imessage_cache, False


# ── iMessage async watcher ───────────────────────────────────────

_IMESSAGE_WATCH_INTERVAL = 5  # seconds


async def _imessage_watcher(
    event_queue: asyncio.Queue,
    imessage_db_holder: list,
) -> None:
    """Async background task: polls iMessage DB for new messages.

    Pushes {"type": "imessage_new", "messages": [...]} events.
    imessage_db_holder is a 1-element list so we can lazy-init and share.
    """
    from pathlib import Path

    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        return

    # Try to open DB
    try:
        watcher_db = MessagesDB(db_path)
    except Exception:
        return

    last_rowid = watcher_db.get_latest_rowid()
    # Share DB instance so zobraz/reply can reuse it
    if not imessage_db_holder[0]:
        imessage_db_holder[0] = watcher_db

    while True:
        await asyncio.sleep(_IMESSAGE_WATCH_INTERVAL)
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


# ── Input/event race ──────────────────────────────────────────────


async def _wait_for_action(
    event_queue: asyncio.Queue,
    input_task: asyncio.Task | None = None,
) -> tuple[str, object, asyncio.Task | None]:
    """Race between user input and internal events (timers, idle).

    Returns (action_type, payload, ongoing_input_task).
    - ("input", user_text, None) — user typed something
    - ("timer_expired", event_dict, input_task) — timer fired
    - ("idle_trigger", event_dict, input_task) — idle timeout
    """
    if input_task is None:
        input_task = asyncio.create_task(display.get_user_input())

    event_task = asyncio.create_task(event_queue.get())

    done, pending = await asyncio.wait(
        {input_task, event_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if input_task in done:
        # User typed something — cancel event wait
        event_task.cancel()
        try:
            await event_task
        except asyncio.CancelledError:
            pass
        return ("input", input_task.result(), None)
    else:
        # Internal event fired — DON'T cancel input (user may be typing)
        event = event_task.result()
        return (event.get("type", "unknown"), event, input_task)


# ── Proactive response ────────────────────────────────────────────

PROACTIVE_PROMPT = """\
Jsi {assistant_name}, osobní AI asistentka. Právě je chvíli ticho v konverzaci s {user_name}.
Řekni něco přirozeného — zeptej se na něco, nabídni pomoc, udělej postřeh, nebo navrhni aktivitu.
Buď stručná (1-2 věty). Nebuď otravná, buď přirozená.
Odpovídej ČESKY. NEPOUŽÍVEJ *akce v hvězdičkách*.\
"""


async def proactive_response(
    text: str | None,
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    current_messages: list[dict],
) -> None:
    """Generate and display a proactive message from Eigy.

    If text is provided, use it directly (e.g., timer notification).
    If text is None, ask the LLM to generate a contextual message.
    """
    if text is None:
        # Generate contextual message via LLM
        try:
            proactive_system = PROACTIVE_PROMPT.format(
                assistant_name=config.ASSISTANT_NAME,
                user_name=memory.user_name,
            )
            # Build slim context: system + last few messages
            messages: list[dict] = [
                {"role": "system", "content": proactive_system},
            ]
            profile_summary = memory.db.get_user_profile_summary()
            if profile_summary:
                messages.append({
                    "role": "system",
                    "content": f"Znáš o {memory.user_name}: {profile_summary}",
                })
            # Add last 5 messages for context
            recent = current_messages[-5:] if len(current_messages) > 5 else current_messages
            messages.extend(recent)
            messages.append({
                "role": "user",
                "content": "(ticho — řekni něco sama od sebe)",
            })

            response_parts: list[str] = []
            async for token in chat_engine.get_response(messages):
                response_parts.append(token)
            text = "".join(response_parts).strip()

            if not text:
                return
        except Exception as e:
            logger.warning("Failed to generate proactive message: %s", e)
            return

    # Display and speak
    display.show_assistant(text)

    # Detect emotion
    if config.EMOTION_DETECTION == "llm":
        emotion = await detect_emotion_llm(text)
    else:
        emotion = detect_emotion(text)
    avatar_queue.put({"type": "emotion", "value": emotion})

    # Save to memory
    current_messages.append({"role": "assistant", "content": text})
    memory.save_message("assistant", text, emotion=emotion)

    # TTS
    await _speak(text, tts, audio_player, avatar_queue)


# ── Main chat loop ────────────────────────────────────────────────


async def chat_loop(
    db: Database,
    memory: MemoryManager,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """Main chat loop — input, stream response, display, TTS, avatar events."""
    current_messages: list[dict] = []

    # iMessage integration state (lazy-initialized on first use)
    imessage_db_holder: list = [None]  # mutable holder for lazy-init sharing
    imessage_cache: list[IMessage] = []
    imessage_contacts = ContactBook()

    # Initialize timer manager and idle monitor
    event_queue: asyncio.Queue = asyncio.Queue()
    timer_mgr = TimerManager(event_queue)
    idle_monitor = IdleMonitor(event_queue)

    # Start idle monitor background task
    idle_task = asyncio.create_task(idle_monitor.run())

    # Start iMessage watcher (pushes imessage_new events to queue)
    imessage_watcher_task = asyncio.create_task(
        _imessage_watcher(event_queue, imessage_db_holder)
    )

    # Greeting for returning users
    if memory.user_name not in ("friend", "šéfe"):
        display.show_welcome_banner()
        greeting = f"Vítejte zpět, {memory.user_name}. Jak vám mohu pomoci?"
        display.show_assistant(greeting)
        avatar_queue.put({"type": "emotion", "value": "happy"})
        await _speak(greeting, tts, audio_player, avatar_queue)

    display.show_system("Napiš /help pro příkazy, 'exit' pro ukončení.")

    ongoing_input_task: asyncio.Task | None = None

    try:
        while True:
            # Wait for user input OR internal event
            action_type, payload, ongoing_input_task = await _wait_for_action(
                event_queue, ongoing_input_task
            )

            if action_type == "input":
                user_input = payload
                idle_monitor.reset()
                audio_player.stop()  # interrupt any playing speech

                if user_input is None:
                    break  # EOF / Ctrl+D

                # Exit commands
                if user_input.lower() in ("exit", "quit", "konec"):
                    farewell = (
                        f"Na shledanou, {memory.user_name}. "
                        "Kdybyste cokoli potřebovali, jsem tu."
                    )
                    display.show_assistant(farewell)
                    await _speak(farewell, tts, audio_player, avatar_queue)
                    break

                # Empty input
                if not user_input:
                    continue

                # Commands
                if user_input.startswith("/"):
                    await handle_command(
                        user_input, memory, db, tts, audio_player,
                        avatar_queue, timer_mgr,
                    )
                    continue

                # iMessage commands (zobraz imessage, odepiš na imessage, kontakty)
                imsg_cmd = detect_imessage_command(user_input)
                if imsg_cmd:
                    imessage_db_holder[0], imessage_cache, _ = await handle_imessage_command(
                        imsg_cmd[0], imsg_cmd[1],
                        imessage_db_holder[0], imessage_cache,
                        imessage_contacts,
                    )
                    continue

                # Check for timer request in natural language
                timer_req = parse_timer_request(user_input)
                if timer_req:
                    seconds, label = timer_req
                    timer_id = timer_mgr.add_timer(seconds, label)
                    display.show_system(
                        f"Timer nastaven: {label} (ID: {timer_id})"
                    )
                    # Still pass to LLM so Eigy can respond naturally

                # Check for crypto price request (before web search)
                crypto_context = None
                crypto_id = detect_crypto_request(user_input)
                if crypto_id:
                    display.show_system(f"Načítám cenu: {crypto_id}...")
                    avatar_queue.put({"type": "thinking_start"})
                    price_data = await fetch_crypto_price(crypto_id)
                    if price_data:
                        crypto_context = format_crypto_price(crypto_id, price_data)

                # Check for web search request
                search_query = detect_search_request(user_input)
                search_context = None
                if search_query and not crypto_context:
                    # Skip web search if we already have live crypto data
                    display.show_system(f"Hledám: {search_query}...")
                    if not crypto_id:
                        avatar_queue.put({"type": "thinking_start"})
                    results = await web_search(search_query)
                    if results:
                        search_context = format_search_results(results)

                # Add user message
                current_messages.append({"role": "user", "content": user_input})
                memory.save_message("user", user_input)

                # Build context with memory
                messages = memory.build_context(current_messages)

                # Inject crypto price data
                if crypto_context:
                    messages.insert(-1, {
                        "role": "system",
                        "content": (
                            f"{crypto_context}\n\n"
                            "INSTRUKCE: Toto jsou ŽIVÁ tržní data z CoinGecko API. "
                            "Použij PŘESNĚ tyto hodnoty ve své odpovědi. NEVYMÝŠLEJ jiné ceny."
                        ),
                    })

                # Inject search results into context (before the last user message)
                if search_context:
                    messages.insert(-1, {
                        "role": "system",
                        "content": (
                            f"VÝSLEDKY VYHLEDÁVÁNÍ pro \"{search_query}\":\n\n"
                            f"{search_context}\n\n"
                            "INSTRUKCE: Využij výše uvedené výsledky a obsah stránek k sestavení "
                            "přesné a informativní odpovědi. Uváděj konkrétní fakta z obsahu. "
                            "Na konci uveď zdroje STRUČNĚ jen názvem domény (např. 'Zdroje: mobilmania.cz, itmix.cz') — "
                            "NIKDY nevypisuj celé URL adresy. Pokud výsledky nejsou relevantní, "
                            "řekni to a odpověz z vlastních znalostí."
                        ),
                    })

                # Notify avatar: thinking
                if not search_query:
                    avatar_queue.put({"type": "thinking_start"})

                # Stream response with sentence-level TTS
                stream_display = display.StreamingDisplay()
                sentence_buffer = SentenceBuffer()
                full_response: list[str] = []
                first_token = True

                # Sequential TTS worker — synthesizes sentences in order
                tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
                tts_worker: asyncio.Task | None = None
                if tts.enabled:
                    tts_worker = asyncio.create_task(
                        _tts_sequential_worker(tts_queue, tts, audio_player)
                    )

                try:
                    stream_display.start()
                    async for token in chat_engine.get_response(messages):
                        # Strip emoji from output
                        token = _EMOJI_RE.sub("", token)
                        if not token:
                            continue

                        if first_token:
                            avatar_queue.put({"type": "thinking_end"})
                            avatar_queue.put({"type": "speaking_start"})
                            first_token = False

                        stream_display.token(token)
                        full_response.append(token)

                        # Sentence-level TTS (in-order via queue)
                        if tts.enabled:
                            sentences = sentence_buffer.add_token(token)
                            for sentence in sentences:
                                await tts_queue.put(sentence)

                    stream_display.end()
                    avatar_queue.put({"type": "speaking_end"})

                    # Flush remaining text to TTS
                    if tts.enabled:
                        remaining = sentence_buffer.flush()
                        if remaining:
                            await tts_queue.put(remaining)
                        # Signal worker to stop and wait for it
                        await tts_queue.put(None)
                        if tts_worker:
                            await tts_worker

                except Exception as e:
                    stream_display.end()
                    avatar_queue.put({"type": "thinking_end"})
                    if tts_worker and not tts_worker.done():
                        await tts_queue.put(None)
                        await tts_worker
                    display.show_error(f"Odpověď selhala: {e}")
                    current_messages.pop()
                    continue

                # Save assistant response and detect emotion
                response_text = "".join(full_response)
                current_messages.append({"role": "assistant", "content": response_text})

                # Emotion detection
                if config.EMOTION_DETECTION == "llm":
                    emotion = await detect_emotion_llm(response_text)
                else:
                    emotion = detect_emotion(response_text)
                avatar_queue.put({"type": "emotion", "value": emotion})

                memory.save_message("assistant", response_text, emotion=emotion)

                # Real-time fact extraction (background, non-blocking)
                asyncio.create_task(
                    memory.extract_facts_realtime(user_input, response_text)
                )

            elif action_type == "timer_expired":
                # Timer expired — Eigy proactively notifies
                label = payload.get("label", "timer")
                notification = f"Čas vypršel — {label} je u konce."
                idle_monitor.reset()
                await proactive_response(
                    notification, memory, tts, audio_player,
                    avatar_queue, current_messages,
                )

            elif action_type == "idle_trigger":
                # Idle timeout — Eigy says something contextual
                await proactive_response(
                    None, memory, tts, audio_player,
                    avatar_queue, current_messages,
                )

            elif action_type == "imessage_new":
                # New iMessage arrived — display and read aloud
                msg = payload.get("message")
                if msg:
                    name = imessage_contacts.get_name(msg.sender)
                    notification = f"Nová zpráva od {name}: {msg.text}"
                    display.show_system(f"  iMessage od {name}: {msg.text}")
                    idle_monitor.reset()
                    await proactive_response(
                        notification, memory, tts, audio_player,
                        avatar_queue, current_messages,
                    )

    finally:
        # Cleanup
        idle_monitor.stop()
        timer_mgr.cancel_all()
        idle_task.cancel()
        imessage_watcher_task.cancel()
        if imessage_db_holder[0] is not None:
            imessage_db_holder[0].close()
        try:
            await idle_task
        except asyncio.CancelledError:
            pass
        try:
            await imessage_watcher_task
        except asyncio.CancelledError:
            pass


async def _speak(
    text: str,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """Synthesize and enqueue a complete text for TTS."""
    if tts.enabled:
        path = await tts.synthesize(text)
        if path:
            audio_player.enqueue(path)


async def _tts_sequential_worker(
    sentence_queue: asyncio.Queue,
    tts: TTSEngine,
    audio_player: AudioPlayer,
) -> None:
    """Sequential TTS worker — synthesizes sentences in order.

    Reads sentences from the queue one by one and enqueues audio
    in the same order, preventing out-of-order playback.
    Stops when it receives None as sentinel.
    """
    while True:
        sentence = await sentence_queue.get()
        if sentence is None:
            break
        path = await tts.synthesize(sentence)
        if path:
            audio_player.enqueue(path)


# ── Entry point ────────────────────────────────────────────────────


def main() -> None:
    """Entry point: start Pygame in main thread, chat in daemon thread."""
    # Create thread-safe queues
    avatar_queue: queue.Queue = queue.Queue()

    # Create audio player
    audio_player = AudioPlayer(avatar_queue=avatar_queue)

    # Start chat in daemon thread
    chat = threading.Thread(
        target=chat_thread_main,
        args=(avatar_queue, audio_player),
        daemon=True,
    )
    chat.start()

    # Run Pygame avatar in main thread (REQUIRED on macOS)
    from avatar.window import avatar_main

    try:
        avatar_main(avatar_queue, audio_player)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_temp_files()


if __name__ == "__main__":
    main()
