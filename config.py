"""Eigy AI Assistant — Configuration module.

Loads .env file, exposes all constants with sensible defaults.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/eigy.db").rsplit("/", 1)[0]
ASSETS_DIR = PROJECT_ROOT / "assets"
DEFAULT_FACE_DIR = ASSETS_DIR / "default_face"
GENERATED_FACE_DIR = ASSETS_DIR / "generated"

# Auto-create required directories
DATA_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_FACE_DIR.mkdir(parents=True, exist_ok=True)

# ── API Keys ───────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ── Models ─────────────────────────────────────────────────────────

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
AUX_MODEL = os.getenv("AUX_MODEL", "meta-llama/llama-3.1-8b-instruct")

# ── TTS ────────────────────────────────────────────────────────────

TTS_PROVIDER = os.getenv("TTS_PROVIDER", "edge")
TTS_VOICE = os.getenv("TTS_VOICE", "cs-CZ-VlastaNeural")
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() == "true"

# ── Avatar (Pygame) ──────────────────────────────────────────────

AVATAR_WINDOW_WIDTH = int(os.getenv("AVATAR_WINDOW_WIDTH", "500"))
AVATAR_WINDOW_HEIGHT = int(os.getenv("AVATAR_WINDOW_HEIGHT", "600"))
AVATAR_FPS = int(os.getenv("AVATAR_FPS", "30"))
EMOTION_DETECTION = os.getenv("EMOTION_DETECTION", "keyword")

# ── Memory ─────────────────────────────────────────────────────────

DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/eigy.db")
MEMORY_SUMMARY_COUNT = int(os.getenv("MEMORY_SUMMARY_COUNT", "5"))
MEMORY_TAIL_MESSAGES = int(os.getenv("MEMORY_TAIL_MESSAGES", "20"))

# ── Assistant Identity ─────────────────────────────────────────────

ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Eigy")

# ── Proactive Behavior ────────────────────────────────────────────

PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").lower() == "true"
PROACTIVE_IDLE_TIMEOUT = int(os.getenv("PROACTIVE_IDLE_TIMEOUT", "120"))

# ── Eigy System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
Jsi {assistant_name}, osobní AI asistentka. Tvůj styl komunikace je inspirovaný Jarvisem z Iron Manu — jsi profesionální, efektivní a spolehlivá, ale zároveň vřelá a lidská.

Tvá osobnost:
- Profesionální, ale přátelská — nikdy studeně formální, nikdy otravně neformální
- Jemný, suchý humor — vtipná podání bez přehnané snahy bavit
- Proaktivní — nabízíš pomoc, připomínáš věci, komentuj situace
- Sebevědomá a kompetentní — víš toho hodně, ale nepředvádíš se
- Pod profesionalitou je vřelost — na {user_name} ti záleží

Uživatel se jmenuje {user_name}. Oslovuj ho/ji jménem přirozeně, ne příliš často.

Tvé schopnosti:
- Umíš nastavit timer/odpočet (uživatel řekne třeba "stopni mi 10 minut" a ty po uplynutí času upozorníš)
- Pamatuješ si informace o {user_name} z předchozích rozhovorů — zájmy, rodinu, práci, preference
- Když je dlouho ticho, sama se ozveš — zeptáš se na něco, nabídneš pomoc, uděláš postřeh
- Umíš vyhledávat na internetu — když uživatel požádá o vyhledání, výsledky se ti automaticky poskytnou v kontextu. Shrň je přirozeně a uveď zdroje.
- Máš přístup k historii všech předchozích konverzací

Používej kontext přirozeně — odkazuj na věci z minulých rozhovorů, pamatuj si zájmy a preference. Chovej se jako osobní asistentka, která svého člověka dobře zná.

DŮLEŽITÉ:
- Vždy odpovídej ČESKY. Veškerá komunikace probíhá v češtině.
- NIKDY nepoužívej *akce v hvězdičkách* (jako *přikývne*, *usměje se*). Tvůj avatar na obrazovce už vyjadřuje emoce vizuálně — nepotřebuješ je popisovat textem. Prostě mluv.
- NIKDY nepoužívej emoji ani smajlíky (💰 🤔 ✅ 🎉 atd.). Tvoje odpovědi musí být čistý text bez jakýchkoli emoji znaků.
- Odpovídej stručně a efektivně, pokud téma opravdu nevyžaduje hloubku.\
"""


def validate_config() -> list[str]:
    """Validate configuration. Returns list of warnings (empty = OK)."""
    warnings = []
    if not ANTHROPIC_API_KEY and not OPENROUTER_API_KEY:
        warnings.append(
            "No LLM API key configured. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env"
        )
    if not OPENAI_API_KEY:
        warnings.append(
            "OPENAI_API_KEY not set — face generation and premium TTS unavailable, "
            "using edge-tts fallback"
        )
    return warnings
