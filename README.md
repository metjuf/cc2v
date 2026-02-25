# Eigy — AI Asistentka s Animovaným Avatarem a Hlasem

Terminálová chatovací aplikace pro macOS s **Eigy**, osobní AI asistentkou s fotorealistickým animovaným avatarem, hlasovým výstupem, persistentní pamětí, vyhledáváním na internetu, sledováním kryptoměn a integrací s iMessage.

## Funkce

- **Chat v terminálu** se streamovanými odpověďmi a Rich formátováním
- **Fotorealistický animovaný avatar** (Pygame okno) — mrkání, dýchání, lip sync, emoční reakce
- **Text-to-speech** — Eigy mluví česky (edge-tts, Microsoft Azure neural voices)
- **Persistentní paměť** — Eigy si pamatuje jméno, zájmy a celou historii konverzací
- **Vyhledávání na internetu** — automatická detekce dotazů, DuckDuckGo + stahování obsahu stránek
- **Kryptoměny** — živé ceny z CoinGecko API (Bitcoin, Ethereum, Solana a dalších 15+)
- **iMessage integrace** — čtení příchozích zpráv, odpovídání, sledování nových zpráv s TTS
- **Timery a odpočty** — „stopni mi 10 minut" a Eigy upozorní po uplynutí
- **Proaktivní chování** — Eigy se sama ozve, když je dlouho ticho
- **Failover** — Anthropic API primárně, OpenRouter jako záloha

## Požadavky

- **macOS** (Apple Silicon doporučen)
- **Python 3.11+**
- **ffmpeg** (`brew install ffmpeg`) — volitelné, pro přesnou extrakci amplitudy lip sync
- **lxml** (součást requirements.txt, vyžaduje `libxml2` — na macOS předinstalováno)

### Volitelné

- **Full Disk Access** pro iMessage funkce (System Settings → Privacy & Security → Full Disk Access → Terminal)

## Instalace

```bash
# 1. Klonovat repozitář
git clone <repo>
cd cc2v

# 2. Vytvořit virtuální prostředí
python3 -m venv venv
source venv/bin/activate

# 3. Nainstalovat závislosti
pip install -r requirements.txt

# 4. Nainstalovat ffmpeg (volitelné, pro lip sync amplitudu)
brew install ffmpeg

# 5. Nakonfigurovat API klíče
cp .env.example .env
# Upravit .env a přidat API klíče
```

## Konfigurace (.env)

| Klíč | Povinný | Popis |
|------|---------|-------|
| `ANTHROPIC_API_KEY` | Ano* | Primární chat (Claude) |
| `OPENROUTER_API_KEY` | Ano* | Záložní chat + auxiliární úlohy |
| `ANTHROPIC_MODEL` | Ne | Model Anthropic (výchozí: `claude-sonnet-4-20250514`) |
| `OPENROUTER_MODEL` | Ne | Model OpenRouter (výchozí: `anthropic/claude-sonnet-4`) |
| `AUX_MODEL` | Ne | Levný model pro emoce/sumarizaci (výchozí: `llama-3.1-8b-instruct`) |
| `TTS_VOICE` | Ne | Hlas edge-tts (výchozí: `cs-CZ-VlastaNeural`) |
| `TTS_ENABLED` | Ne | `true`/`false` (výchozí: `true`) |
| `ASSISTANT_NAME` | Ne | Jméno asistentky (výchozí: `Eigy`) |
| `PROACTIVE_ENABLED` | Ne | Proaktivní zprávy (výchozí: `true`) |
| `PROACTIVE_IDLE_TIMEOUT` | Ne | Sekundy ticha před proaktivní zprávou (výchozí: `120`) |
| `EMOTION_DETECTION` | Ne | `keyword` (rychlé) nebo `llm` (přesnější) |
| `DATABASE_PATH` | Ne | Cesta k SQLite DB (výchozí: `data/eigy.db`) |
| `MEMORY_SUMMARY_COUNT` | Ne | Počet souhrnů konverzací v kontextu (výchozí: `5`) |
| `MEMORY_TAIL_MESSAGES` | Ne | Počet zpráv z minulé relace v kontextu (výchozí: `20`) |
| `AVATAR_WINDOW_WIDTH` | Ne | Šířka okna avatara v px (výchozí: `500`) |
| `AVATAR_WINDOW_HEIGHT` | Ne | Výška okna avatara v px (výchozí: `600`) |
| `AVATAR_FPS` | Ne | Snímkovací frekvence avatara (výchozí: `30`) |

*Alespoň jeden z `ANTHROPIC_API_KEY` nebo `OPENROUTER_API_KEY` musí být nastaven.

## Spuštění

```bash
source venv/bin/activate
python3 main.py
```

Při prvním spuštění se Eigy představí a zeptá se na jméno. Otevře se Pygame okno s avatarem, chat běží v terminálu.

## Přehled funkcí

### Chat a konverzace (`chat_engine.py`)

Eigy odpovídá česky se streamovanými odpověďmi (SSE). Styl komunikace inspirovaný Jarvisem — profesionální, efektivní, ale vřelá. Používá Claude (Anthropic) jako primární LLM s automatickým failoverem na OpenRouter.

- **Streaming** — tokeny se zobrazují v reálném čase
- **System messages** — automaticky extrahovány do Anthropic `system` pole
- **Auxiliární model** — levný model (Llama 3.1 8B) pro sumarizaci, extrakci profilu, detekci emocí
- **max_tokens: 4096** — výchozí limit odpovědi

### Vyhledávání na internetu (`web_search.py`)

Eigy automaticky rozpozná dotazy vyžadující aktuální informace (česky i anglicky, 60+ regex vzorů):
- `„co je nového o iPhone 16"` → vyhledá na DuckDuckGo
- `„najdi mi recenze MacBook Air"` → vyhledá a shrne výsledky
- `„jak nainstalovat Docker"` → najde návod

Stahuje obsah z top 3 výsledků (max 1500 znaků/stránka) pro přesnější odpovědi. Výsledky cituje zkráceně názvem domény (např. „Zdroje: mobilmania.cz, itmix.cz").

Časově citlivé dotazy automaticky obohacuje o aktuální datum. False-positive filtrace pro konverzační fráze („jak se máš", „co děláš").

### Kryptoměny (`web_search.py` — CoinGecko API)

Živé tržní data pro 20+ kryptoměn:
- `„cena bitcoinu"` → aktuální cena v USD, CZK, EUR + 24h změna + market cap
- `„kolik stojí ethereum"` → totéž pro ETH

Podporované: Bitcoin, Ethereum, Solana, Cardano, Dogecoin, Ripple/XRP, Litecoin, Polkadot, Chainlink, Avalanche, Polygon/MATIC, Tron, Shiba Inu a další. Rozumí českým tvarům (bitcoinu, etheru, solany...).

### iMessage integrace (`imessage_bot.py`)

Čtení a odesílání iMessage zpráv přímo z terminálu (vyžaduje Full Disk Access):
- `zobraz imessage [N]` — zobrazit příchozí zprávy (výchozí 5, max 50)
- `odepiš na imessage X` — odpovědět na zprávu číslo X
- `ulož kontakt X Jméno` — uložit kontakt ke zprávě
- `kontakty` — zobrazit uložené kontakty

**Background watcher**: Automaticky sleduje nové příchozí zprávy (polling každých 5s), zobrazí notifikaci a přečte nahlas.

**Standalone režim**: Lze spustit nezávisle jako `python imessage_bot.py` s vlastním příkazovým rozhraním (sledování, interval, kontakty).

**Technické detaily**:
- Read-only přístup k macOS `chat.db` (SQLite)
- Odesílání přes AppleScript (`Messages.app`)
- Podpora `attributedBody` blobs (macOS Ventura+)
- JSON-based kontaktní kniha (`data/imessage_contacts.json`)

### Timery a odpočty (`timer_manager.py`)

Eigy rozpozná požadavky na timer v přirozené řeči:
- `„stopni mi 10 minut"` → nastaví timer, po uplynutí upozorní nahlas
- `„připomeň mi za 30 sekund"` → totéž
- `„set timer for 1 hour"` → funguje i anglicky

Každý timer běží jako asyncio task. Příkazy: `/timer` (seznam aktivních), `/timer cancel [ID]` (zrušit).

### Proaktivní chování (`proactive.py`)

Když je dlouho ticho (výchozí 2 minuty), Eigy se sama ozve — zeptá se, nabídne pomoc, udělá postřeh. Konfigurovatelné přes `PROACTIVE_IDLE_TIMEOUT`.

`IdleMonitor` běží na pozadí (polling 30s), po aktivaci aplikuje cooldown 2× timeout, aby Eigy nebyla otravná.

### Paměť a profil (`memory/`)

Pětivrstevný kontext pro každý LLM request:

1. **System prompt** — osobnost, pravidla, schopnosti
2. **User profile** — uložená jména, zájmy, preference, fakta
3. **Souhrny konverzací** — posledních N relací (výchozí 5)
4. **Ocas předchozí relace** — posledních N zpráv z minulé relace (výchozí 20)
5. **Aktuální zprávy** — probíhající konverzace

**Real-time extrakce faktů**: Po každé výměně se na pozadí spustí auxiliární model, který extrahuje nové informace o uživateli (zájmy, preference, fakta) a uloží je do profilu. Aktivuje se pouze pro zprávy delší než 20 znaků.

**End-of-session**: Při ukončení Eigy vygeneruje souhrn konverzace (2-3 věty) a provede kompletní extrakci profilu.

**Databáze** (SQLite, schema v1):
- `user_profile` — key/value store (name, interest:*, preference:*, fact:*)
- `conversations` — metadata relací (started_at, ended_at, summary)
- `messages` — kompletní historie zpráv s emočními tagy

### Avatar (`avatar/`)

Fotorealistický animovaný obličej v Pygame okně (500×600, 30 FPS):

**Animace** (`animator.py`):
- Mrkání — náhodný interval 2-5s, 3-fázová animace (half→closed→half)
- Dýchání — sinusový vertikální pohyb (perioda 3s)
- Eye drift — náhodný pohled stranou (interval 5-12s, zesílený v thinking stavu)
- Micro-expressions — jemný smirk v idle (interval 10-20s, trvání 0.5-1.5s)
- Lip sync — plynulé mapování RMS amplitudy na 4 mouth layers (closed→open_1→open_2→open_3)

**Emoční stavy** (6 emocí):
| Emoce | Oči | Ústa | Obočí | Glow barva |
|-------|-----|------|-------|------------|
| neutral | open | closed | neutral | modrá |
| amused | open | smirk | neutral | zelená |
| happy | open | smile | neutral | žlutá |
| concerned | half | sad | frown | červená |
| surprised | open | surprised | raised | fialová |
| thinking | half | closed | frown | oranžová |

**Vizuální efekty** (`window.py`):
- Radiální gradient pozadí (cachovaný)
- Plovoucí částice (20 částic, sinusový drift)
- Emoční glow aura za obličejem (eliptická, pulzující s dýcháním)
- Vinětace (cachovaná)
- Status indikátor: thinking dots (skákající) / sound waves (amplitude-driven)

**Face rendering** (`face_renderer.py`):
- PNG vrstvy v `assets/default_face/` — base, eyes_*, eyebrows_*, mouth_*
- Alpha blending a crossfade mezi vrstvami
- Auto-scaling na 85 % výšky okna

**Detekce emocí** (`emotion_detector.py`):
- Keyword matching (výchozí) — regex vzory pro českou i anglickou sadu
- LLM fallback — auxiliární model klasifikuje emoci (přesnější, pomalejší)

### Text-to-speech (`tts_engine.py`)

- **edge-tts** — zdarma, Microsoft Azure neural voices
- Český hlas `cs-CZ-VlastaNeural` (výchozí), mužský `cs-CZ-AntoninNeural`
- **Sentence-level streaming** (`SentenceBuffer`) — TTS začíná mluvit po první dokončené větě, ne po celé odpovědi
- **TTS text cleaning** — odstraňuje markdown, \*akce\*, emoji; nahrazuje symboly českými ekvivalenty (!=→„se nerovná", %→„procent")
- `/voice on/off` — zapnutí/vypnutí
- `/volume 0-100` — hlasitost

### Audio přehrávání (`audio_player.py`)

- Queue-based přehrávání přes `pygame.mixer.music`
- **Extrakce amplitudy** pro lip sync — RMS analýza po 33ms rámcích (pydub + numpy)
- **Syntetický fallback** když pydub/ffmpeg není k dispozici — sinusová aproximace
- Amplitude eventy posílány do avatara pro real-time lip sync

## Příkazy

### Slash příkazy

| Příkaz | Popis |
|--------|-------|
| `/help` | Zobrazit nápovědu |
| `/memory` | Zobrazit co si Eigy pamatuje |
| `/forget` | Vymazat veškerou paměť (s potvrzením) |
| `/voice on/off` | Zapnout/vypnout TTS |
| `/voice [jméno]` | Změnit TTS hlas |
| `/volume [0-100]` | Nastavit hlasitost |
| `/emotion [emoce]` | Ručně nastavit emoci avatara |
| `/avatar` | Přepnout (minimalizovat) okno avatara |
| `/model [název]` | Změnit primární chat model za běhu |
| `/timer` | Zobrazit aktivní timery |
| `/timer cancel [ID]` | Zrušit timer (bez ID zruší všechny) |
| `/history` | Zobrazit historii aktuální relace |
| `/export` | Exportovat profil + historii do `eigy_export.json` |

### Přirozené příkazy

| Vzor | Příklad | Akce |
|------|---------|------|
| Vyhledávání | „vyhledej iPhone 16 recenze" | Web search (DuckDuckGo) |
| Kryptoměny | „cena bitcoinu" | Živá cena (CoinGecko) |
| Timer | „stopni mi 10 minut" | Nastaví odpočet |
| iMessage zobrazení | „zobraz imessage 10" | Příchozí zprávy |
| iMessage odpověď | „odepiš na imessage 3" | Odpovědět na zprávu #3 |
| Uložení kontaktu | „ulož kontakt 2 Petr" | Přiřadí jméno k odesílateli |
| Seznam kontaktů | „kontakty" | Výpis uložených kontaktů |
| Ukončení | „exit" / „quit" / „konec" | Uloží relaci a ukončí |

## Architektura

```
main.py              Vstupní bod — Pygame (hlavní vlákno), chat (daemon vlákno)
  ├── chat_thread_main()     daemon thread entry point
  ├── chat_main()            async init (DB, memory, onboarding)
  ├── chat_loop()            hlavní smyčka (input/events race)
  ├── handle_command()       slash příkazy
  ├── proactive_response()   proaktivní zprávy (timer, idle, iMessage)
  └── main()                 entry point (spouští Pygame + daemon)

config.py            Konfigurace (.env loading, konstanty, system prompt)
chat_engine.py       LLM komunikace (Anthropic + OpenRouter failover, streaming)
display.py           Terminálový výstup (Rich) + vstup (prompt_toolkit)
tts_engine.py        Text-to-speech (edge-tts) + SentenceBuffer
audio_player.py      Přehrávání audia + extrakce amplitudy pro lip sync
web_search.py        Vyhledávání (DuckDuckGo) + kryptoměny (CoinGecko)
timer_manager.py     Správa timerů (asyncio tasks)
proactive.py         Proaktivní chování (IdleMonitor)
imessage_bot.py      iMessage integrace (čtení DB, AppleScript, kontakty, standalone)

memory/
  database.py        SQLite databázová vrstva (schema v1, CRUD)
  user_profile.py    Správa uživatelského profilu (typovaný přístup)
  memory_manager.py  Konstrukce 5-vrstvého kontextu + session lifecycle

avatar/
  window.py          Pygame okno + render pipeline + event handling
  animator.py        Animační state machine (idle/thinking/speaking)
  face_renderer.py   Kompozice PNG vrstev s alpha blending
  emotion_detector.py Keyword/LLM detekce emocí z textu

assets/
  default_face/      PNG vrstvy obličeje (base, eyes_*, mouth_*, eyebrows_*)

data/
  eigy.db            SQLite databáze (auto-created)
  imessage_contacts.json  Kontaktní kniha (auto-created)
```

### Threading model

```
Main thread (Pygame)              Daemon thread (asyncio event loop)
  │                                 │
  avatar_main()                     chat_main()
  │                                 │
  ├── pygame event loop             ├── chat_loop()
  ├── audio_player.update()         │     ├── user input (prompt_toolkit)
  ├── animator.update(dt)           │     ├── LLM streaming (Anthropic/OpenRouter)
  └── render pipeline               │     ├── TTS synthesis (sentence-level)
      ├── gradient background       │     ├── web search / crypto injection
      ├── particles                 │     ├── emotion detection
      ├── glow aura                 │     └── real-time fact extraction
      ├── face layers               │
      ├── vignette                  ├── _imessage_watcher() (polling 5s)
      └── status indicator          ├── idle_monitor.run() (polling 30s)
                                    └── timer tasks (asyncio.sleep)
       ← avatar_queue ←
       (emotion, thinking_start/end,
        speaking_start/end,
        audio_amplitude, quit)

       ← event_queue ←
       (timer_expired, idle_trigger,
        imessage_new)
```

Pygame avatar **musí** běžet v hlavním vlákně na macOS (SDL2 requirement). Chat, TTS a síťové operace běží v daemon vlákně s vlastním asyncio event loop.

## Závislosti

| Balíček | Verze | Účel |
|---------|-------|------|
| `httpx` | >=0.27.0 | Async HTTP klient (LLM API, web fetching, CoinGecko) |
| `rich` | >=13.0.0 | Formátování terminálu (panely, tabulky, markdown) |
| `prompt-toolkit` | >=3.0.0 | Pokročilý terminálový vstup |
| `python-dotenv` | >=1.0.0 | Načítání .env souborů |
| `Pillow` | >=10.0.0 | Zpracování obrázků (avatar) |
| `pygame` | >=2.5.0 | Avatar okno + audio přehrávání |
| `edge-tts` | >=6.1.0 | TTS zdarma (Microsoft Azure neural voices) |
| `pydub` | >=0.25.1 | Audio zpracování (amplitude extraction) |
| `audioop-lts` | >=0.2.1 | audioop pro Python 3.13+ |
| `numpy` | >=1.24.0 | Výpočet RMS amplitudy pro lip sync |
| `anthropic` | >=0.39.0 | Anthropic API SDK |
| `ddgs` | >=9.0.0 | DuckDuckGo vyhledávání |
| `lxml` | >=5.0.0 | HTML parsování a extrakce obsahu stránek |

Volitelná systémová závislost: `ffmpeg` (`brew install ffmpeg`) — pro pydub amplitude extraction. Bez ffmpeg funguje syntetický lip sync fallback.

## Troubleshooting

| Problém | Řešení |
|---------|--------|
| Pygame okno se neotevře | Spusť přímo `python3 main.py` (Pygame vyžaduje hlavní vlákno na macOS) |
| Žádný zvuk | `brew install ffmpeg`, zkontroluj `/voice on` |
| Lip sync nefunguje přesně | `brew install ffmpeg` — bez něj se používá syntetická aproximace |
| API chyby | Ověř klíče v `.env` — Eigy automaticky přepne na OpenRouter |
| iMessage: `unable to open database file` | Přidej Terminal do Full Disk Access |
| `command not found: python` | Použij `python3 main.py` (macOS nemá `python`) |
| `ModuleNotFoundError` | Aktivuj venv: `source venv/bin/activate` |
| iMessage odeslání selhalo | Ověř, že Messages.app běží a iMessage je aktivní |
| TTS 503 error | Dočasná nedostupnost edge-tts serveru — zkus znovu později |
| Emoji ve výstupu | Eigy automaticky stripuje emoji z LLM odpovědí |
