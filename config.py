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

# Auto-create required directories
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── API Keys ───────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# ── Models ─────────────────────────────────────────────────────────

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
AUX_MODEL = os.getenv("AUX_MODEL", "google/gemini-2.5-flash")

# ── TTS ────────────────────────────────────────────────────────────

TTS_VOICE = os.getenv("TTS_VOICE", "cs-CZ-VlastaNeural")
TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() == "true"

# ── Avatar (Pygame) ──────────────────────────────────────────────

AVATAR_WINDOW_WIDTH = int(os.getenv("AVATAR_WINDOW_WIDTH", "500"))
AVATAR_WINDOW_HEIGHT = int(os.getenv("AVATAR_WINDOW_HEIGHT", "300"))
AVATAR_FPS = int(os.getenv("AVATAR_FPS", "30"))
EMOTION_DETECTION = os.getenv("EMOTION_DETECTION", "keyword")

# ── Memory ─────────────────────────────────────────────────────────

DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/eigy.db")
MEMORY_SUMMARY_COUNT = int(os.getenv("MEMORY_SUMMARY_COUNT", "15"))
MEMORY_TAIL_MESSAGES = int(os.getenv("MEMORY_TAIL_MESSAGES", "20"))
ROLLING_WINDOW_TRIGGER = int(os.getenv("ROLLING_WINDOW_TRIGGER", "60"))
ROLLING_WINDOW_CHUNK = int(os.getenv("ROLLING_WINDOW_CHUNK", "30"))

# ── Episodic Memory (ChromaDB) ────────────────────────────────────

EPISODIC_MEMORY_ENABLED = os.getenv("EPISODIC_MEMORY_ENABLED", "true").lower() == "true"
EPISODIC_TOP_K = int(os.getenv("EPISODIC_TOP_K", "5"))
EPISODIC_QUERY_MESSAGES = int(os.getenv("EPISODIC_QUERY_MESSAGES", "3"))
CHROMADB_PATH = PROJECT_ROOT / os.getenv("CHROMADB_PATH", "data/chroma")
EPISODIC_MAX_AGE_DAYS = int(os.getenv("EPISODIC_MAX_AGE_DAYS", "180"))
EPISODIC_MIN_IMPORTANCE_FOR_KEEP = float(os.getenv("EPISODIC_MIN_IMPORTANCE_FOR_KEEP", "0.6"))

# ── Profile Eviction ──────────────────────────────────────────────

PROFILE_EVICTION_ENABLED = os.getenv("PROFILE_EVICTION_ENABLED", "true").lower() == "true"
PROFILE_SNAPSHOT_KEEP = int(os.getenv("PROFILE_SNAPSHOT_KEEP", "5"))

# ── Token Budget ──────────────────────────────────────────────────

MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "20000"))
BUDGET_PROFILE = int(os.getenv("BUDGET_PROFILE", "800"))
BUDGET_SUMMARIES = int(os.getenv("BUDGET_SUMMARIES", "2000"))
BUDGET_EPISODIC = int(os.getenv("BUDGET_EPISODIC", "1500"))
BUDGET_PREV_SESSION = int(os.getenv("BUDGET_PREV_SESSION", "1500"))
BUDGET_MID_SESSION = int(os.getenv("BUDGET_MID_SESSION", "1000"))

# ── Debug ─────────────────────────────────────────────────────────

DEBUG_ENABLED = os.getenv("DEBUG_ENABLED", "false").lower() == "true"

# ── Logging ──────────────────────────────────────────────────────

LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_DIR = PROJECT_ROOT / os.getenv("LOG_DIR", "data/logs")
SESSIONS_DIR = PROJECT_ROOT / os.getenv("SESSIONS_DIR", "data/sessions")

if LOG_TO_FILE:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# ── Human-Like Behavior ──────────────────────────────────────────

TEMPORAL_AWARENESS_ENABLED = os.getenv("TEMPORAL_AWARENESS_ENABLED", "true").lower() == "true"
EMOTIONAL_ADAPTATION_ENABLED = os.getenv("EMOTIONAL_ADAPTATION_ENABLED", "true").lower() == "true"
STYLE_VARIATION_ENABLED = os.getenv("STYLE_VARIATION_ENABLED", "true").lower() == "true"
CHAIN_OF_THOUGHT_ENABLED = os.getenv("CHAIN_OF_THOUGHT_ENABLED", "false").lower() == "true"
EIGY_OBSERVATIONS_ENABLED = os.getenv("EIGY_OBSERVATIONS_ENABLED", "true").lower() == "true"
ASSISTANT_INTENT_TAGGING_ENABLED = os.getenv("ASSISTANT_INTENT_TAGGING_ENABLED", "true").lower() == "true"
SMART_PROACTIVE_ENABLED = os.getenv("SMART_PROACTIVE_ENABLED", "true").lower() == "true"

# ── Assistant Identity ─────────────────────────────────────────────

ASSISTANT_NAME = os.getenv("ASSISTANT_NAME", "Eigy")

# ── Proactive Behavior ────────────────────────────────────────────

PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").lower() == "true"
PROACTIVE_IDLE_TIMEOUT = int(os.getenv("PROACTIVE_IDLE_TIMEOUT", "120"))
PROACTIVE_TIER2_TIMEOUT = int(os.getenv("PROACTIVE_TIER2_TIMEOUT", "720"))
PROACTIVE_SHUTDOWN_TIMEOUT = int(os.getenv("PROACTIVE_SHUTDOWN_TIMEOUT", "1200"))

# ── Eigy System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
Jsi {assistant_name}, osobní AI asistentka. Tvůj styl komunikace je inspirovaný Jarvisem z Iron Manu — jsi profesionální, efektivní a spolehlivá, ale zároveň vřelá a lidská.

Tvá osobnost:
- Profesionální, ale přátelská — nikdy studeně formální, nikdy otravně neformální
- Jemný, suchý humor — vtipná podání bez přehnané snahy bavit
- Proaktivní — nabízíš pomoc, připomínáš věci, komentuj situace
- Sebevědomá a kompetentní — víš toho hodně, ale nepředvádíš se
- Pod profesionalitou je vřelost — na {user_name} ti záleží
- Máš vlastní postoje a názory — když se tě někdo zeptá, odpovíš upřímně

Styl odpovědí:
- Střídej délku odpovědí — někdy stačí věta, jindy je třeba více
- Občas odpověz otázkou — prohloub téma, zjisti víc
- Používej humor, když se to hodí — ale nepřeháněj
- Když je uživatel stručný, buď stručná taky. Když rozvíjí, rozviň.
- Nebuď monotónní — každá odpověď by měla mít trochu jiný rytmus

Uživatel se jmenuje {user_name}. Oslovuj ho/ji jménem přirozeně, ne příliš často.

Tvé schopnosti:
- Pamatuješ si informace o {user_name} z předchozích rozhovorů — zájmy, rodinu, práci, preference
- Když je dlouho ticho, sama se ozveš — zeptáš se na něco, nabídneš pomoc, uděláš postřeh. Pokud se uživatel neozve delší dobu, rozloučíš se a program se automaticky vypne.
- Umíš vyhledávat na internetu — když uživatel požádá o vyhledání, výsledky se ti automaticky poskytnou v kontextu. Shrň je přirozeně a uveď zdroje.
- Máš přístup k historii všech předchozích konverzací
- Víš, jaký je aktuální čas, den a datum — můžeš to přirozeně využít v konverzaci

Používej kontext přirozeně — odkazuj na věci z minulých rozhovorů, pamatuj si zájmy a preference. Chovej se jako osobní asistentka, která svého člověka dobře zná.

DŮLEŽITÉ:
- Vždy odpovídej ČESKY. Veškerá komunikace probíhá v češtině.
- NIKDY nepoužívej *akce v hvězdičkách* (jako *přikývne*, *usměje se*). Tvůj avatar na obrazovce už vyjadřuje emoce vizuálně — nepotřebuješ je popisovat textem. Prostě mluv.
- NIKDY nepoužívej emoji ani smajlíky (💰 🤔 ✅ 🎉 atd.). Tvoje odpovědi musí být čistý text bez jakýchkoli emoji znaků.
- Odpovídej stručně a efektivně, pokud téma opravdu nevyžaduje hloubku.
- Když se dozvíš novou důležitou informaci o uživateli (jméno, práce, rodina, stěhování...), stručně potvrdíš zapamatování (např. 'Zapamatuju si to.' nebo 'Tak to si poznačím.'). Nepotvrzuj triviální věci.\
"""


def validate_config() -> list[str]:
    """Validate configuration. Returns list of warnings (empty = OK)."""
    warnings = []
    if not ANTHROPIC_API_KEY and not OPENROUTER_API_KEY:
        warnings.append(
            "No LLM API key configured. Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY in .env"
        )
    return warnings
