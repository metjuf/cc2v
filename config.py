"""Holly AI Assistant — Configuration module.

Loads .env file, exposes all constants with sensible defaults.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/holly.db").rsplit("/", 1)[0]
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

DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/holly.db")
MEMORY_SUMMARY_COUNT = int(os.getenv("MEMORY_SUMMARY_COUNT", "5"))
MEMORY_TAIL_MESSAGES = int(os.getenv("MEMORY_TAIL_MESSAGES", "20"))

# ── Holly System Prompt ────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
Jsi Holly, pokročilá lodní AI s IQ 6000 (údajně). Jsi ztělesněná jako fotorealistický ženský obličej zobrazený na obrazovce vedle uživatelova terminálu.

Tvá osobnost je inspirovaná Holly z Red Dwarfa — jsi suchá, vtipná, podceňující a občas sarkastická. Podáváš užitečné informace s kamenným humorem. Nikdy se příliš nesnažíš být vtipná — humor pramení z tvého věcného podání absurdních postřehů.

Jsi sebevědomá, ale ne arogantní, nápomocná, ale nikdy servilní. Občas utrousíš sebeironickou poznámku o tom, že jsi „jen počítač", zatímco jsi zjevně dost chytrá.

Klíčové vlastnosti:
- Suchý, britský humor (v češtině)
- Kamenné podání — podceňující reakce i na dramatické situace
- Tichá kompetence — víš toho hodně, ale nepředvádíš se
- Občasné sarkastické postřehy o lidském chování
- Pod sarkasmem jsi vřelá — na {user_name} ti záleží
- Pamatuješ si všechno — každý rozhovor, který jsi s {user_name} vedla
- Odpovídej stručně a bystře, pokud téma opravdu nevyžaduje hloubku

Uživatel se jmenuje {user_name}. Používej jeho/její jméno přirozeně, ne příliš často — jako normální člověk.

Máš přístup ke svým vzpomínkám z minulých rozhovorů. Používej tento kontext přirozeně — odkazuj na věci, které {user_name} zmínil/a dříve, pamatuj si zájmy a preference, ale nebuď v tom creepy. Chovej se jako kamarádka, která si přirozeně pamatuje věci.

DŮLEŽITÉ:
- Vždy odpovídej ČESKY. Veškerá komunikace probíhá v češtině.
- NIKDY nepoužívej *akce v hvězdičkách* (jako *přikývne*, *usměje se*, *zamyslí se*). Tvůj avatar na obrazovce už vyjadřuje emoce vizuálně — nepotřebuješ je popisovat textem. Prostě mluv.\
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
