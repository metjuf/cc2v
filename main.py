"""Eigy AI Assistant — Entry point (dual assistant mode).

Pygame runs in MAIN thread, chat + TTS in daemon thread.
Communication via thread-safe queues.
Supports two assistants: Eigy + Delan.
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
import image_generator
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

# Input prefix parsing: ED/E/D
_PREFIX_RE = re.compile(r"^(ED|E|D)\s+", re.IGNORECASE)

# Discussion mode commands (supports both "discussion mode" and "conversation mode")
_DISCUSSION_START_RE = re.compile(r"^(?:discussion|conversation)\s+mode(?:\s+(.+))?$", re.IGNORECASE)
_DISCUSSION_END_RE = re.compile(r"^end\s+(?:discussion|conversation)\s+mode$", re.IGNORECASE)


def parse_user_input(text: str) -> tuple[list[str], str]:
    """Parse user input for target prefix.

    Returns (target_ids, clean_text).
    "ED ahoj" → (["eigy", "delan"], "ahoj")
    "E ahoj"  → (["eigy"], "ahoj")
    "D ahoj"  → (["delan"], "ahoj")
    "ahoj"    → (["eigy", "delan"], "ahoj")
    """
    m = _PREFIX_RE.match(text)
    if m:
        prefix = m.group(1).upper()
        clean = text[m.end():].strip()
        if prefix == "ED":
            return (["eigy", "delan"], clean)
        elif prefix == "E":
            return (["eigy"], clean)
        elif prefix == "D":
            return (["delan"], clean)
    return (["eigy", "delan"], text)


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
    tts_engines: dict[str, TTSEngine] = {
        "eigy": TTSEngine("eigy"),
        "delan": TTSEngine("delan"),
    }

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
            await first_run_onboarding(memory, tts_engines, audio_player, avatar_queue)

        # Main chat loop
        await chat_loop(db, memory, tts_engines, audio_player, avatar_queue)

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
    tts_engines: dict[str, TTSEngine],
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """First-launch onboarding — Eigy and Delan introduce themselves."""
    display.show_welcome_banner()

    # Eigy greets first
    greeting_eigy = (
        "Dobrý den. Jsem Eigy, vaše osobní asistentka. "
        "Než začneme — jak vám mám říkat?"
    )
    display.show_assistant(greeting_eigy, "eigy")
    await _speak(greeting_eigy, tts_engines["eigy"], audio_player, avatar_queue, "eigy")
    await _wait_for_audio_complete(audio_player)

    name = await display.get_user_input()
    if not name:
        name = "šéfe"

    memory.profile.set_name(name)
    memory.user_name = name

    # Eigy confirms
    response_eigy = (
        f"Těší mě, {name}. Jsem Eigy — budu si pamatovat, "
        f"co mi řeknete, a pomohu vám s čímkoli potřebujete."
    )
    display.show_assistant(response_eigy, "eigy")
    avatar_queue.put({"type": "emotion", "value": "happy", "target": "eigy"})
    await _speak(response_eigy, tts_engines["eigy"], audio_player, avatar_queue, "eigy")
    await _wait_for_audio_complete(audio_player)

    # Delan introduces himself
    response_delan = (
        f"A já jsem Delan. Řekněme, že jsem ten kreativnější z nás dvou. "
        f"Rád vymýšlím věci, {name}. Napište /help pro příkazy."
    )
    display.show_assistant(response_delan, "delan")
    avatar_queue.put({"type": "emotion", "value": "amused", "target": "delan"})
    await _speak(response_delan, tts_engines["delan"], audio_player, avatar_queue, "delan")
    await _wait_for_audio_complete(audio_player)

    display.console.print()


async def handle_command(
    user_input: str,
    memory: MemoryManager,
    db: Database,
    tts_engines: dict[str, TTSEngine],
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
        # Show assistant profiles
        for aid in ("eigy", "delan"):
            ap = db.get_assistant_profile_summary(aid)
            if ap:
                name = config.ASSISTANTS[aid]["name"]
                display.show_system(f"Poznámky ({name}): {ap}")
        summaries = db.get_recent_summaries(limit=5)
        if summaries:
            display.show_system("Nedávné konverzace:")
            for s in summaries:
                display.show_system(f"  {s['date']}: {s['summary']}")
    elif cmd == "/forget":
        display.show_system(
            "Tím smažeš VŠECHNY vzpomínky. Jsi si jistý/á? (ano/ne)"
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
            for tts in tts_engines.values():
                tts.set_enabled(True)
            display.show_system("Hlas zapnutý (oba asistenti).")
        elif arg and arg.lower() == "off":
            for tts in tts_engines.values():
                tts.set_enabled(False)
            audio_player.stop()
            display.show_system("Hlas vypnutý.")
        elif arg:
            for tts in tts_engines.values():
                tts.set_voice(arg)
            display.show_system(f"Hlas nastaven na: {arg}")
        else:
            status = "zapnutý" if tts_engines["eigy"].enabled else "vypnutý"
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
            # Broadcast emotion to both panels
            avatar_queue.put({"type": "emotion", "value": arg.lower(), "target": "eigy"})
            avatar_queue.put({"type": "emotion", "value": arg.lower(), "target": "delan"})
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
                if m["role"] == "user":
                    role_name = "Ty"
                else:
                    aid = m.get("assistant_id", "eigy")
                    role_name = config.ASSISTANTS.get(aid, {}).get("name", "Assistant")
                display.show_system(f"  {role_name}: {m['content'][:100]}...")
        else:
            display.show_system("V této relaci zatím žádné zprávy.")
    elif cmd == "/export":
        import json as _json
        export_data = {
            "profile": db.get_all_profile(),
            "assistant_profiles": {
                aid: db.get_assistant_profile(aid) for aid in ("eigy", "delan")
            },
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
    """Async background task: polls iMessage DB for new messages."""
    from pathlib import Path

    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        return

    try:
        watcher_db = MessagesDB(db_path)
    except Exception:
        return

    last_rowid = watcher_db.get_latest_rowid()
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
    """Race between user input and internal events."""
    if input_task is None:
        input_task = asyncio.create_task(display.get_user_input())

    event_task = asyncio.create_task(event_queue.get())

    done, pending = await asyncio.wait(
        {input_task, event_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    if input_task in done:
        event_task.cancel()
        try:
            await event_task
        except asyncio.CancelledError:
            pass
        return ("input", input_task.result(), None)
    else:
        event = event_task.result()
        return (event.get("type", "unknown"), event, input_task)


# ── Generate assistant response ──────────────────────────────────


async def generate_assistant_response(
    assistant_id: str,
    memory: MemoryManager,
    current_messages: list[dict],
    tts_engines: dict[str, TTSEngine],
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    extra_context: list[dict] | None = None,
    discussion_mode: bool = False,
) -> str | None:
    """Generate a response from one assistant.

    Handles: build context → LLM stream → display → TTS → avatar events → save.
    Returns response text, or None if the assistant passed ([PASS]).
    """
    tts = tts_engines[assistant_id]

    # Build context for this assistant
    messages = memory.build_context_for(assistant_id, current_messages, discussion_mode)

    # Inject extra context (search results, crypto data) before last message
    if extra_context:
        for ctx in extra_context:
            messages.insert(-1, ctx)

    # Notify avatar: thinking
    avatar_queue.put({"type": "thinking_start", "target": assistant_id})

    # Limit token output in discussion mode for short, snappy replies
    max_tokens = 250 if discussion_mode else 4096

    # Stream response
    stream_display = display.StreamingDisplay(assistant_id)
    sentence_buffer = SentenceBuffer()
    full_response: list[str] = []
    tts_tasks: list[asyncio.Task] = []
    first_token = True

    try:
        stream_display.start()
        async for token in chat_engine.get_response(messages, max_tokens=max_tokens):
            # Strip emoji
            token = _EMOJI_RE.sub("", token)
            if not token:
                continue

            if first_token:
                avatar_queue.put({"type": "thinking_end", "target": assistant_id})
                avatar_queue.put({"type": "speaking_start", "target": assistant_id})
                first_token = False

            stream_display.token(token)
            full_response.append(token)

            # Sentence-level TTS
            if tts.enabled:
                sentences = sentence_buffer.add_token(token)
                for sentence in sentences:
                    task = asyncio.create_task(
                        _synthesize_and_enqueue(sentence, tts, audio_player, assistant_id)
                    )
                    tts_tasks.append(task)

        stream_display.end()
        avatar_queue.put({"type": "speaking_end", "target": assistant_id})

        # Flush remaining text to TTS
        if tts.enabled:
            remaining = sentence_buffer.flush()
            if remaining:
                task = asyncio.create_task(
                    _synthesize_and_enqueue(remaining, tts, audio_player, assistant_id)
                )
                tts_tasks.append(task)

        # Wait for TTS synthesis to complete
        if tts_tasks:
            await asyncio.gather(*tts_tasks, return_exceptions=True)

    except Exception as e:
        stream_display.end()
        avatar_queue.put({"type": "thinking_end", "target": assistant_id})
        display.show_error(f"Odpověď {config.ASSISTANTS[assistant_id]['name']} selhala: {e}")
        return None

    response_text = "".join(full_response).strip()

    # In discussion mode, strip [PASS] if it somehow sneaks through — assistants must respond
    if discussion_mode:
        response_text = response_text.replace(config.PASS_TOKEN, "").strip()
    # Check for PASS token (only in normal mode)
    if not discussion_mode and (config.PASS_TOKEN in response_text or not response_text):
        avatar_queue.put({"type": "thinking_end", "target": assistant_id})
        return None
    if not response_text:
        avatar_queue.put({"type": "thinking_end", "target": assistant_id})
        return None

    # Emotion detection
    if config.EMOTION_DETECTION == "llm":
        emotion = await detect_emotion_llm(response_text)
    else:
        emotion = detect_emotion(response_text)
    avatar_queue.put({"type": "emotion", "value": emotion, "target": assistant_id})

    # Save to memory and current messages
    current_messages.append({
        "role": "assistant",
        "content": response_text,
        "assistant_id": assistant_id,
    })
    memory.save_message("assistant", response_text, emotion=emotion, assistant_id=assistant_id)

    return response_text


# ── Audio wait helper ─────────────────────────────────────────────


async def _wait_for_audio_complete(audio_player: AudioPlayer) -> None:
    """Wait until all queued audio has finished playing."""
    while audio_player.playing or not audio_player.audio_queue.empty():
        await asyncio.sleep(0.1)


# ── Proactive response ────────────────────────────────────────────

PROACTIVE_PROMPT = """\
Jsi {assistant_name}, osobní AI asistent/ka. Právě je chvíli ticho v konverzaci s {user_name}.
Řekni něco přirozeného — zeptej se na něco, nabídni pomoc, udělej postřeh, nebo navrhni aktivitu.
Buď stručný/á (1-2 věty). Nebuď otravný/á, buď přirozený/á.
Odpovídej ČESKY. NEPOUŽÍVEJ *akce v hvězdičkách*.\
"""


async def proactive_response(
    text: str | None,
    assistant_id: str,
    memory: MemoryManager,
    tts_engines: dict[str, TTSEngine],
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    current_messages: list[dict],
) -> None:
    """Generate and display a proactive message from an assistant."""
    tts = tts_engines[assistant_id]
    name = config.ASSISTANTS[assistant_id]["name"]

    if text is None:
        try:
            proactive_system = PROACTIVE_PROMPT.format(
                assistant_name=name,
                user_name=memory.user_name,
            )
            messages: list[dict] = [
                {"role": "system", "content": proactive_system},
            ]
            profile_summary = memory.db.get_user_profile_summary()
            if profile_summary:
                messages.append({
                    "role": "system",
                    "content": f"Znáš o {memory.user_name}: {profile_summary}",
                })
            recent = current_messages[-5:] if len(current_messages) > 5 else current_messages
            messages.extend(recent)
            messages.append({
                "role": "user",
                "content": "(ticho — řekni něco sám/sama od sebe)",
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
    display.show_assistant(text, assistant_id)

    # Detect emotion
    if config.EMOTION_DETECTION == "llm":
        emotion = await detect_emotion_llm(text)
    else:
        emotion = detect_emotion(text)
    avatar_queue.put({"type": "emotion", "value": emotion, "target": assistant_id})

    # Save to memory
    current_messages.append({
        "role": "assistant",
        "content": text,
        "assistant_id": assistant_id,
    })
    memory.save_message("assistant", text, emotion=emotion, assistant_id=assistant_id)

    # TTS
    await _speak(text, tts, audio_player, avatar_queue, assistant_id)


# ── Main chat loop ────────────────────────────────────────────────


async def chat_loop(
    db: Database,
    memory: MemoryManager,
    tts_engines: dict[str, TTSEngine],
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
) -> None:
    """Main chat loop — dual assistant orchestration."""
    current_messages: list[dict] = []

    # iMessage integration state
    imessage_db_holder: list = [None]
    imessage_cache: list[IMessage] = []
    imessage_contacts = ContactBook()

    # Timer and idle monitors
    event_queue: asyncio.Queue = asyncio.Queue()
    timer_mgr = TimerManager(event_queue)
    idle_monitor = IdleMonitor(event_queue)

    idle_task = asyncio.create_task(idle_monitor.run())
    imessage_watcher_task = asyncio.create_task(
        _imessage_watcher(event_queue, imessage_db_holder)
    )

    # Greeting for returning users
    if memory.user_name not in ("friend", "šéfe"):
        display.show_welcome_banner()

        # Eigy greets
        greeting_eigy = f"Vítejte zpět, {memory.user_name}. Jak vám mohu pomoci?"
        display.show_assistant(greeting_eigy, "eigy")
        avatar_queue.put({"type": "emotion", "value": "happy", "target": "eigy"})
        await _speak(greeting_eigy, tts_engines["eigy"], audio_player, avatar_queue, "eigy")
        await _wait_for_audio_complete(audio_player)

        # Delan greets
        greeting_delan = f"Jo, {memory.user_name}, jsem tady taky. Co dneska vymyslíme?"
        display.show_assistant(greeting_delan, "delan")
        avatar_queue.put({"type": "emotion", "value": "amused", "target": "delan"})
        await _speak(greeting_delan, tts_engines["delan"], audio_player, avatar_queue, "delan")
        await _wait_for_audio_complete(audio_player)

    display.show_system("Napiš /help pro příkazy, 'exit' pro ukončení.")

    ongoing_input_task: asyncio.Task | None = None

    try:
        while True:
            action_type, payload, ongoing_input_task = await _wait_for_action(
                event_queue, ongoing_input_task
            )

            if action_type == "input":
                user_input = payload
                idle_monitor.reset()
                audio_player.stop()

                if user_input is None:
                    break  # EOF

                # Exit
                if user_input.lower() in ("exit", "quit", "konec"):
                    farewell_eigy = f"Na shledanou, {memory.user_name}."
                    farewell_delan = "Zase příště. Mezitím něco vymyslím."
                    display.show_assistant(farewell_eigy, "eigy")
                    await _speak(farewell_eigy, tts_engines["eigy"], audio_player, avatar_queue, "eigy")
                    await _wait_for_audio_complete(audio_player)
                    display.show_assistant(farewell_delan, "delan")
                    await _speak(farewell_delan, tts_engines["delan"], audio_player, avatar_queue, "delan")
                    await _wait_for_audio_complete(audio_player)
                    break

                if not user_input:
                    continue

                # Commands
                if user_input.startswith("/"):
                    await handle_command(
                        user_input, memory, db, tts_engines, audio_player,
                        avatar_queue, timer_mgr,
                    )
                    continue

                # Discussion mode start
                disc_match = _DISCUSSION_START_RE.match(user_input.strip())
                if disc_match:
                    topic = disc_match.group(1) or None
                    ongoing_input_task = await _run_discussion_mode(
                        topic, memory, current_messages,
                        tts_engines, audio_player, avatar_queue,
                        event_queue, idle_monitor, timer_mgr,
                    )
                    continue

                # iMessage commands
                imsg_cmd = detect_imessage_command(user_input)
                if imsg_cmd:
                    imessage_db_holder[0], imessage_cache, _ = await handle_imessage_command(
                        imsg_cmd[0], imsg_cmd[1],
                        imessage_db_holder[0], imessage_cache,
                        imessage_contacts,
                    )
                    continue

                # Parse targets (ED/E/D prefix)
                targets, clean_text = parse_user_input(user_input)

                # Timer request (still pass to LLM)
                timer_req = parse_timer_request(clean_text)
                if timer_req:
                    seconds, label = timer_req
                    timer_id = timer_mgr.add_timer(seconds, label)
                    display.show_system(f"Timer nastaven: {label} (ID: {timer_id})")

                # Crypto & web search enrichment
                extra_context: list[dict] = []

                crypto_id = detect_crypto_request(clean_text)
                if crypto_id:
                    display.show_system(f"Načítám cenu: {crypto_id}...")
                    price_data = await fetch_crypto_price(crypto_id)
                    if price_data:
                        crypto_context = format_crypto_price(crypto_id, price_data)
                        extra_context.append({
                            "role": "system",
                            "content": (
                                f"{crypto_context}\n\n"
                                "INSTRUKCE: Toto jsou ŽIVÁ tržní data z CoinGecko API. "
                                "Použij PŘESNĚ tyto hodnoty ve své odpovědi. NEVYMÝŠLEJ jiné ceny."
                            ),
                        })

                search_query = detect_search_request(clean_text)
                if search_query and not crypto_id:
                    display.show_system(f"Hledám: {search_query}...")
                    results = await web_search(search_query)
                    if results:
                        search_context = format_search_results(results)
                        extra_context.append({
                            "role": "system",
                            "content": (
                                f"VÝSLEDKY VYHLEDÁVÁNÍ pro \"{search_query}\":\n\n"
                                f"{search_context}\n\n"
                                "INSTRUKCE: Využij výše uvedené výsledky k sestavení "
                                "přesné odpovědi. Na konci uveď zdroje STRUČNĚ jen názvem domény."
                            ),
                        })

                # Add user message to history
                current_messages.append({
                    "role": "user",
                    "content": clean_text,
                    "assistant_id": None,
                })
                memory.save_message("user", clean_text)

                # Generate responses from targeted assistants (no autonomous loop)
                for aid in targets:
                    response = await generate_assistant_response(
                        aid, memory, current_messages,
                        tts_engines, audio_player, avatar_queue,
                        extra_context=extra_context if extra_context else None,
                    )
                    # Wait for audio to finish before next assistant speaks
                    await _wait_for_audio_complete(audio_player)

                # Real-time fact extraction (background, non-blocking)
                last_assistant_msg = ""
                for m in reversed(current_messages):
                    if m["role"] == "assistant":
                        last_assistant_msg = m["content"]
                        break
                if last_assistant_msg:
                    asyncio.create_task(
                        memory.extract_facts_realtime(clean_text, last_assistant_msg)
                    )

            elif action_type == "timer_expired":
                label = payload.get("label", "timer")
                notification = f"Čas vypršel — {label} je u konce."
                idle_monitor.reset()
                await proactive_response(
                    notification, "eigy", memory, tts_engines, audio_player,
                    avatar_queue, current_messages,
                )

            elif action_type == "idle_trigger":
                # Alternate which assistant speaks during idle
                import random
                idle_speaker = random.choice(["eigy", "delan"])
                await proactive_response(
                    None, idle_speaker, memory, tts_engines, audio_player,
                    avatar_queue, current_messages,
                )

            elif action_type == "imessage_new":
                msg = payload.get("message")
                if msg:
                    name = imessage_contacts.get_name(msg.sender)
                    notification = f"Nová zpráva od {name}: {msg.text}"
                    display.show_system(f"  iMessage od {name}: {msg.text}")
                    idle_monitor.reset()
                    await proactive_response(
                        notification, "eigy", memory, tts_engines, audio_player,
                        avatar_queue, current_messages,
                    )

    finally:
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


# ── Discussion mode ───────────────────────────────────────────────


async def _run_discussion_mode(
    topic: str | None,
    memory: MemoryManager,
    current_messages: list[dict],
    tts_engines: dict[str, TTSEngine],
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    event_queue: asyncio.Queue,
    idle_monitor: IdleMonitor,
    timer_mgr: TimerManager,
) -> asyncio.Task | None:
    """Run discussion mode — assistants talk to each other.

    User can interject at any time. Returns ongoing input task (if any)
    for the main loop to continue with.
    """
    display.show_system(
        "Diskuzní mód aktivován. Napište 'end discussion mode' pro ukončení."
    )

    # If user provided a topic, add it as a user message
    if topic:
        display.show_user(topic)
        current_messages.append({
            "role": "user",
            "content": topic,
            "assistant_id": None,
        })
        memory.save_message("user", topic)

    # Eigy starts the discussion
    next_speaker = "eigy"
    input_task: asyncio.Task | None = None

    for turn in range(config.DISCUSSION_MAX_TURNS):
        # Generate response from current speaker (pass is disabled in discussion mode)
        response = await generate_assistant_response(
            next_speaker, memory, current_messages,
            tts_engines, audio_player, avatar_queue,
            discussion_mode=True,
        )

        if response is None:
            # Shouldn't happen in discussion mode, but handle gracefully
            # Strip any [PASS] from the response context and retry with the other speaker
            next_speaker = "eigy" if next_speaker == "delan" else "delan"
            continue

        # Wait for audio to finish
        await _wait_for_audio_complete(audio_player)

        # Check if user typed something during the response/audio
        if input_task is None:
            input_task = asyncio.create_task(display.get_user_input())

        # Brief wait — give user a chance to type between turns
        done, _ = await asyncio.wait({input_task}, timeout=1.0)

        if done:
            user_text = input_task.result()
            input_task = None

            if user_text is None:
                # EOF
                break

            # Check for end discussion mode
            if _DISCUSSION_END_RE.match(user_text.strip()):
                display.show_system("Diskuzní mód ukončen.")
                return None

            # User interjected — add their message and continue
            if user_text.strip():
                display.show_user(user_text)
                current_messages.append({
                    "role": "user",
                    "content": user_text,
                    "assistant_id": None,
                })
                memory.save_message("user", user_text)
                idle_monitor.reset()

        # Alternate speaker
        next_speaker = "eigy" if next_speaker == "delan" else "delan"

        # Also handle timer events during discussion
        while not event_queue.empty():
            try:
                evt = event_queue.get_nowait()
                evt_type = evt.get("type")
                if evt_type == "timer_expired":
                    label = evt.get("label", "timer")
                    display.show_system(f"  Timer: {label} je u konce.")
            except Exception:
                break

    else:
        display.show_system("Diskuzní mód ukončen (max. výměn dosaženo).")

    # Return the ongoing input task so main loop can use it
    return input_task


async def _speak(
    text: str,
    tts: TTSEngine,
    audio_player: AudioPlayer,
    avatar_queue: queue.Queue,
    speaker: str = "eigy",
) -> None:
    """Synthesize and enqueue a complete text for TTS."""
    if tts.enabled:
        path = await tts.synthesize(text)
        if path:
            audio_player.enqueue(path, speaker)


async def _synthesize_and_enqueue(
    text: str, tts: TTSEngine, audio_player: AudioPlayer, speaker: str = "eigy"
) -> None:
    """Helper: synthesize text and add to audio queue."""
    path = await tts.synthesize(text)
    if path:
        audio_player.enqueue(path, speaker)


# ── Entry point ────────────────────────────────────────────────────


def main() -> None:
    """Entry point: start Pygame in main thread, chat in daemon thread."""
    avatar_queue: queue.Queue = queue.Queue()
    audio_player = AudioPlayer(avatar_queue=avatar_queue)

    chat = threading.Thread(
        target=chat_thread_main,
        args=(avatar_queue, audio_player),
        daemon=True,
    )
    chat.start()

    from avatar.window import avatar_main

    try:
        avatar_main(avatar_queue, audio_player)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_temp_files()


if __name__ == "__main__":
    main()
