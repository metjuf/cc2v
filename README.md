# Eigy — AI Asistentka s Animovaným Avatarem a Hlasem

Terminálová chatovací aplikace pro macOS s **Eigy**, osobní AI asistentkou s fotorealistickým animovaným avatarem, hlasovým výstupem, persistentní pamětí, vyhledáváním na internetu, sledováním kryptoměn a integrací s iMessage.

## Funkce

- **Chat v terminálu** se streamovanými odpověďmi a Rich formátováním
- **Fotorealistický animovaný avatar** (Pygame okno) — mrkání, dýchání, lip sync, emoční reakce
- **Text-to-speech** — Eigy mluví česky (edge-tts / OpenAI TTS HD)
- **Persistentní paměť** — Eigy si pamatuje jméno, zájmy a celou historii konverzací
- **Vyhledávání na internetu** — automatická detekce dotazů, DuckDuckGo + stahování obsahu stránek
- **Kryptoměny** — živé ceny z CoinGecko API (Bitcoin, Ethereum, Solana a dalších 15+)
- **iMessage integrace** — čtení příchozích zpráv, odpovídání, sledování nových zpráv s TTS
- **Timery a odpočty** — „stopni mi 10 minut" a Eigy upozorní po uplynutí
- **Proaktivní chování** — Eigy se sama ozve, když je dlouho ticho
- **Generování obličejů** — nové fotorealistické tváře na vyžádání (DALL-E 3)
- **Failover** — Anthropic API primárně, OpenRouter jako záloha

## Požadavky

- **macOS** (Apple Silicon doporučen)
- **Python 3.11+**
- **ffmpeg** (`brew install ffmpeg`)
- **lxml** (součást requirements.txt, vyžaduje `libxml2` — na macOS předinstalováno)

### Volitelné

- **Full Disk Access** pro iMessage funkce (System Settings → Privacy & Security → Full Disk Access → Terminal)
- **OPENAI_API_KEY** pro generování obličejů (DALL-E 3) a premium TTS hlas

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

# 4. Nainstalovat ffmpeg (pro zpracování audia)
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
| `OPENAI_API_KEY` | Ne | Generování obličejů (DALL-E 3) + premium TTS |
| `ANTHROPIC_MODEL` | Ne | Model Anthropic (výchozí: `claude-sonnet-4-20250514`) |
| `OPENROUTER_MODEL` | Ne | Model OpenRouter (výchozí: `anthropic/claude-sonnet-4`) |
| `AUX_MODEL` | Ne | Levný model pro emoce/sumarizaci (výchozí: `llama-3.1-8b-instruct`) |
| `TTS_PROVIDER` | Ne | `edge` (zdarma, výchozí) nebo `openai` (nejlepší kvalita) |
| `TTS_VOICE` | Ne | Hlas TTS (výchozí: `cs-CZ-VlastaNeural`) |
| `TTS_ENABLED` | Ne | `true`/`false` (výchozí: `true`) |
| `ASSISTANT_NAME` | Ne | Jméno asistentky (výchozí: `Eigy`) |
| `PROACTIVE_ENABLED` | Ne | Proaktivní zprávy (výchozí: `true`) |
| `PROACTIVE_IDLE_TIMEOUT` | Ne | Sekundy ticha před proaktivní zprávou (výchozí: `300`) |
| `EMOTION_DETECTION` | Ne | `keyword` (rychlé) nebo `llm` (přesnější) |
| `DATABASE_PATH` | Ne | Cesta k SQLite DB (výchozí: `data/eigy.db`) |

*Alespoň jeden z `ANTHROPIC_API_KEY` nebo `OPENROUTER_API_KEY` musí být nastaven.

## Spuštění

```bash
source venv/bin/activate
python3 main.py
```

Při prvním spuštění se Eigy představí a zeptá se na jméno. Otevře se Pygame okno s avatarem, chat běží v terminálu.

## Přehled funkcí

### Chat a konverzace

Eigy odpovídá česky se streamovanými odpověďmi. Styl komunikace inspirovaný Jarvisem — profesionální, efektivní, ale vřelá. Používá Claude (Anthropic) jako primární LLM s automatickým failoverem na OpenRouter.

### Vyhledávání na internetu

Eigy automaticky rozpozná dotazy vyžadující aktuální informace (česky i anglicky):
- `„co je nového o iPhone 16"` → vyhledá na DuckDuckGo
- `„najdi mi recenze MacBook Air"` → vyhledá a shrne výsledky
- `„jak nainstalovat Docker"` → najde návod

Stahuje obsah z top 3 výsledků pro přesnější odpovědi. Výsledky cituje zkráceně názvem domény (např. „Zdroje: mobilmania.cz, itmix.cz").

Časově citlivé dotazy automaticky obohacuje o aktuální datum.

### Kryptoměny (CoinGecko API)

Živé tržní data pro 15+ kryptoměn:
- `„cena bitcoinu"` → aktuální cena v USD, CZK, EUR + 24h změna + market cap
- `„kolik stojí ethereum"` → totéž pro ETH

Podporované: Bitcoin, Ethereum, Solana, Cardano, Dogecoin, Ripple/XRP, Litecoin, Polkadot, Chainlink, Avalanche, Polygon/MATIC, Tron, Shiba Inu a další. Rozumí českým tvarům (bitcoinu, etheru, solany...).

### iMessage integrace

Čtení a odesílání iMessage zpráv přímo z terminálu (vyžaduje Full Disk Access):
- `zobraz imessage [N]` — zobrazit příchozí zprávy
- `odepiš na imessage X` — odpovědět na zprávu
- `ulož kontakt X Jméno` — uložit kontakt
- `kontakty` — zobrazit kontakty

Nové příchozí zprávy se automaticky zobrazí a přečtou nahlas. Podrobná dokumentace: [IMESSAGE.md](IMESSAGE.md)

### Timery a odpočty

Eigy rozpozná požadavky na timer v přirozené řeči:
- `„stopni mi 10 minut"` → nastaví timer, po uplynutí upozorní nahlas
- `„připomeň mi za 30 sekund"` → totéž
- `„set timer for 1 hour"` → funguje i anglicky

Příkazy: `/timer` (seznam aktivních), `/timer cancel [ID]` (zrušit)

### Proaktivní chování

Když je dlouho ticho (výchozí 5 minut), Eigy se sama ozve — zeptá se, nabídne pomoc, udělá postřeh. Konfigurovatelné přes `PROACTIVE_IDLE_TIMEOUT`.

### Paměť a profil

- Eigy si pamatuje jméno, zájmy, preference a fakta z konverzací
- Automatická extrakce faktů v reálném čase
- Sumarizace konverzací při ukončení relace
- Kontext okno: profil + souhrny posledních 5 relací + ocas předchozí relace + aktuální zprávy

### Avatar (Pygame)

Fotorealistický animovaný obličej v samostatném okně:
- Mrkání (každé 3-6s)
- Dýchání (jemný vertikální pohyb)
- Lip sync (synchronizace rtů s TTS na základě amplitudy zvuku)
- Emoční reakce (happy, amused, concerned, surprised, thinking)
- Generování nových obličejů přes `/face [popis]`

### Text-to-speech

- **edge-tts** (výchozí, zdarma) — český hlas `cs-CZ-VlastaNeural`
- **OpenAI TTS HD** (volitelné) — anglické hlasy (`nova`, `shimmer` aj.)
- Sentence-level streaming — TTS začíná mluvit ještě před dokončením odpovědi
- `/voice on/off` — zapnutí/vypnutí
- `/volume 0-100` — hlasitost

## Příkazy

| Příkaz | Popis |
|--------|-------|
| `/help` | Zobrazit nápovědu |
| `/memory` | Zobrazit co si Eigy pamatuje |
| `/forget` | Vymazat veškerou paměť (s potvrzením) |
| `/voice on/off` | Zapnout/vypnout TTS |
| `/voice [jméno]` | Změnit TTS hlas |
| `/volume [0-100]` | Nastavit hlasitost |
| `/emotion [emoce]` | Ručně nastavit emoci avatara |
| `/avatar` | Přepnout okno avatara |
| `/face [popis]` | Vygenerovat nový obličej (DALL-E 3) |
| `/model [název]` | Změnit primární chat model |
| `/timer` | Zobrazit aktivní timery |
| `/timer cancel [ID]` | Zrušit timer |
| `/history` | Zobrazit historii aktuální relace |
| `/export` | Exportovat historii do JSON |
| `zobraz imessage [N]` | Zobrazit iMessage zprávy |
| `odepiš na imessage X` | Odpovědět na iMessage |
| `ulož kontakt X Jméno` | Uložit iMessage kontakt |
| `kontakty` | Zobrazit kontakty |
| `exit` / `quit` / `konec` | Ukončit (automaticky uloží relaci) |

## Architektura

```
main.py              Vstupní bod — Pygame (hlavní vlákno), chat (daemon vlákno)
config.py            Konfigurace (.env loading, konstanty)
chat_engine.py       LLM komunikace (Anthropic + OpenRouter failover)
display.py           Terminálový výstup (Rich) + vstup (prompt_toolkit)
tts_engine.py        Text-to-speech (OpenAI TTS HD + edge-tts)
audio_player.py      Přehrávání audia + extrakce amplitudy pro lip sync
image_generator.py   Generování obličejů (DALL-E 3)
web_search.py        Vyhledávání (DuckDuckGo + stahování obsahu stránek)
timer_manager.py     Správa timerů (asyncio)
proactive.py         Proaktivní chování (idle monitor)
imessage_bot.py      iMessage integrace (čtení DB, odesílání, kontakty)
memory/
  database.py        SQLite databázová vrstva
  user_profile.py    Správa uživatelského profilu
  memory_manager.py  Konstrukce kontext okna s historií
avatar/
  window.py          Pygame okno + render loop
  face_renderer.py   Kompozice PNG vrstev
  animator.py        Animační systém (mrkání, dýchání, lip sync)
  emotion_detector.py Text → klasifikace emocí
  face_slicer.py     Řezání portrétu na animovatelné vrstvy
```

### Threading model

```
Main thread (Pygame)          Daemon thread (asyncio)
  avatar_main()                 chat_main()
  render loop 30 FPS              ├── chat_loop()
  zpracování avatar eventů        ├── _imessage_watcher()
                                  ├── idle_monitor.run()
                                  └── timer tasks
       ← avatar_queue ←
       → audio_player →
```

## Závislosti

```
httpx          HTTP klient (async)
rich           Formátování terminálu
prompt-toolkit Vstup v terminálu
python-dotenv  Načítání .env
Pillow         Zpracování obrázků
pygame         Avatar okno + audio
edge-tts       TTS zdarma (Microsoft Azure)
pydub          Audio zpracování
numpy          Výpočet amplitudy pro lip sync
anthropic      Anthropic API SDK
ddgs           DuckDuckGo vyhledávání
lxml           HTML parsování
```

Systémová závislost: `ffmpeg` (`brew install ffmpeg`)

## Troubleshooting

| Problém | Řešení |
|---------|--------|
| Pygame okno se neotevře | Spusť přímo `python3 main.py` (Pygame vyžaduje hlavní vlákno na macOS) |
| Žádný zvuk | `brew install ffmpeg`, zkontroluj `/voice on` |
| API chyby | Ověř klíče v `.env` — Eigy automaticky přepne na OpenRouter |
| Generování obličeje selhalo | Vyžaduje `OPENAI_API_KEY` v `.env` |
| iMessage: `unable to open database file` | Přidej Terminal do Full Disk Access |
| `command not found: python` | Použij `python3 main.py` (macOS nemá `python`) |
| `ModuleNotFoundError` | Aktivuj venv: `source venv/bin/activate` |
| iMessage odeslání selhalo | Ověř, že Messages.app běží a iMessage je aktivní |
