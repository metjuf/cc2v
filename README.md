# Eigy

Osobní AI asistentka pro macOS. Mluví česky, pamatuje si, reaguje na náladu, umí vyhledávat na internetu a má vlastní vizuální přítomnost v podobě zvukového spektra.

---

## Jak Eigy funguje

Eigy je terminálová aplikace se dvěma vlákny. V hlavním vlákně běží Pygame okno se spectrum vizualizérem — animované zvukové pruhy, které reagují na její hlas, dýchají, když přemýšlí, a mění barvy podle emocí. Ve druhém vlákně běží celý mozek: chat, paměť, hlas, vyhledávání.

Když napíšete zprávu, Eigy ji nepošle rovnou jazykovému modelu. Nejdřív sestaví kontext — jedenáct vrstev informací, které modelu řeknou, kdo jste, co se dělo minule, jakou máte náladu, kolik je hodin a na co by si měla dát pozor. Teprve pak model dostane vaši zprávu, zasazenou do celého kontextu vašeho vztahu s Eigy.

### Mozek — jedenáct vrstev kontextu

Každý požadavek na model prochází tímto pipeline:

1. **Systémový prompt** — osobnost Eigy, pravidla komunikace, styl odpovědí
2. **Čas a datum** — aktuální české datum, den v týdnu, denní doba, víkend, český svátek
3. **Nálada uživatele** — detekovaná z posledních zpráv, s instrukcí jak reagovat (frustrovaný → buď empatická, zvědavý → jdi do hloubky)
4. **Profil uživatele** — jméno, zájmy, práce, rodina, preference — vše co se Eigy dozvěděla
5. **Postřehy o uživateli** — vlastní poznámky Eigy o vzorcích chování a komunikace
6. **Souhrny minulých konverzací** — 2-3 věty z každé z posledních 15 relací
7. **Epizodická paměť** — sémantické vyhledávání v ChromaDB: nejrelevantnější výměny z minulosti
8. **Konec minulé relace** — posledních 20 zpráv, pro plynulou návaznost
9. **Průběžné souhrny** — pokud je konverzace dlouhá, staré zprávy se sumarizují
10. **Aktuální zprávy** — probíhající konverzace
11. **Stylistické tipy a přemýšlení** — varuje model před monotónností, může obsahovat předpřipravené úvahy

Celý kontext má token budget (výchozí 20 000 tokenů). Když je moc velký, vrstvy se automaticky ořezávají podle priority — nejdřív stylistické tipy, nakonec profil.

### Paměť

Eigy si pamatuje dvěma způsoby:

**Strukturovaný profil** — SQLite databáze s JSON profilem rozděleným do kategorií: basic, personality, life, interests, preferences, goals, health, context, eigy_observations. Po každé výměně zpráv běží na pozadí extrakce faktů přes auxiliární model (Gemini 2.5 Flash). Na konci relace se profil zkondenzuje — odstraní duplicity a zastaralé záznamy.

**Epizodická paměť** — ChromaDB s embeddingy (multilingual-e5-large). Každá výměna se uloží jako epizoda s metadaty: důležitost, nálada, intenty asistentky. Při nové zprávě se sémanticky vyhledají nejrelevantnější epizody z minulosti. Staré epizody s nízkou důležitostí se automaticky mažou po 180 dnech.

### Lidské chování

Sedm funkcí, které dělají Eigy méně robotickou:

- **Vnímání času** — ví, že je pondělní ráno nebo sobotní večer, zná české svátky
- **Reakce na náladu** — rozpozná frustraci, smutek, zvědavost, stres, nadšení; přizpůsobí tón
- **Chytré proaktivní zprávy** — když je ticho, zeptá se kontextově (ne genericky), využije epizodickou paměť
- **Variabilita stylu** — střídá délku odpovědí, občas položí otázku, přizpůsobí se vašemu stylu
- **Přemýšlení předem** — volitelně (defaultně vypnuto) pošle analýzu situace aux modelu před odpovědí
- **Paměť vlastních výroků** — taguje si doporučení, sliby, návrhy v epizodické paměti
- **Vlastní postřehy** — zapisuje si poznámky o vašich vzorcích chování

Každá z těchto funkcí má toggle v `.env` a logování v debug režimu.

---

## Spuštění

### Co potřebujete

- **macOS** (Apple Silicon doporučen)
- **Python 3.11+**
- **ffmpeg** (volitelné) — `brew install ffmpeg` — pro přesnou extrakci amplitudy hlasu

### Instalace

```bash
git clone <repo>
cd cc2v

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### API klíče

Otevřete `.env` a nastavte minimálně jeden LLM klíč:

| Klíč | Kde získat | K čemu |
|------|-----------|--------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Hlavní chat (Claude Sonnet 4) |
| `OPENROUTER_API_KEY` | [openrouter.ai](https://openrouter.ai) | Záložní chat + auxiliární úlohy (Gemini 2.5 Flash) |

Stačí jeden z nich. Pokud máte oba, Anthropic je primární a OpenRouter slouží jako fallback + aux model.

### Databáze

Nevyžaduje žádné nastavení. SQLite databáze (`data/eigy.db`) se vytvoří automaticky při prvním spuštění. ChromaDB pro epizodickou paměť si vytvoří složku `data/chroma/`.

Pokud chcete začít s čistým štítem, smažte složku `data/`.

### Start

```bash
source venv/bin/activate
python3 main.py
```

Otevře se Pygame okno se spectrum vizualizérem a v terminálu začne chat. Při prvním spuštění se Eigy představí a zeptá se na jméno.

---

## Konfigurace

Vše se nastavuje v `.env`. Ukázkový soubor s komentáři: `.env.example`.

### Modely

| Proměnná | Výchozí | Popis |
|----------|---------|-------|
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Hlavní chat model |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-4` | Záložní chat model |
| `AUX_MODEL` | `google/gemini-2.5-flash-preview` | Sumarizace, extrakce, emoce |

### Hlas

| Proměnná | Výchozí | Popis |
|----------|---------|-------|
| `TTS_VOICE` | `cs-CZ-VlastaNeural` | Hlas (edge-tts, zdarma) |
| `TTS_ENABLED` | `true` | Zapnout/vypnout hlas |

### Paměť

| Proměnná | Výchozí | Popis |
|----------|---------|-------|
| `DATABASE_PATH` | `data/eigy.db` | Cesta k SQLite DB |
| `EPISODIC_MEMORY_ENABLED` | `true` | Epizodická paměť (ChromaDB) |
| `CHROMADB_PATH` | `data/chroma` | Úložiště ChromaDB |
| `PROFILE_EVICTION_ENABLED` | `true` | Kondenzace profilu na konci relace |

### Lidské chování (toggles)

| Proměnná | Výchozí | Popis |
|----------|---------|-------|
| `TEMPORAL_AWARENESS_ENABLED` | `true` | Vnímání času a data |
| `EMOTIONAL_ADAPTATION_ENABLED` | `true` | Reakce na náladu |
| `STYLE_VARIATION_ENABLED` | `true` | Variabilita stylu odpovědí |
| `CHAIN_OF_THOUGHT_ENABLED` | `false` | Přemýšlení předem (+1-2s latence) |
| `EIGY_OBSERVATIONS_ENABLED` | `true` | Vlastní postřehy o uživateli |
| `ASSISTANT_INTENT_TAGGING_ENABLED` | `true` | Tagování vlastních výroků |
| `SMART_PROACTIVE_ENABLED` | `true` | Chytré proaktivní zprávy |

---

## Příkazy

| Příkaz | Popis |
|--------|-------|
| `/help` | Nápověda |
| `/memory` | Co si Eigy pamatuje |
| `/forget` | Vymazat paměť |
| `/oprav [instrukce]` | Opravit profil (např. `/oprav nejsem programátor, jsem designer`) |
| `/debug` | Zapnout/vypnout debug logování |
| `/voice on/off` | Hlas |
| `/volume 0-100` | Hlasitost |
| `/emotion [emoce]` | Manuální emoce avatara |
| `/model [název]` | Změnit model za běhu |
| `/history` | Historie aktuální relace |
| `/export` | Export profilu a historie |

Eigy rozumí i přirozeným příkazům — vyhledávání na internetu, ceny kryptoměn, iMessage zprávy.

---

## Struktura projektu

```
main.py                  Vstupní bod, chat smyčka, proaktivní chování
config.py                Konfigurace, system prompt, toggles
chat_engine.py           Anthropic + OpenRouter streaming, failover
display.py               Rich terminálový výstup, prompt_toolkit vstup
tts_engine.py            edge-tts, sentence-level streaming
audio_player.py          pygame.mixer, RMS amplituda pro vizualizér
web_search.py            DuckDuckGo + CoinGecko kryptoměny
proactive.py             3-tier IdleMonitor (ticho → check-in → rozloučení)
imessage_bot.py          iMessage integrace (čtení, odpovídání, kontakty)

memory/
  database.py            SQLite schema v2, JSON profil, migrace, snapshoty
  memory_manager.py      11-vrstvý kontext, extrakce, sumarizace, lidské chování
  user_profile.py        Strukturovaný JSON profil, deep merge
  episodic.py            ChromaDB + e5-large, intent tagging, TTL pruning

avatar/
  window.py              Pygame spectrum vizualizér, částice, glow, viněta
  animator.py            Stavy (idle/thinking/speaking), dýchání
  emotion_detector.py    Detekce emocí + nálady (keyword + LLM)

tests/                   73 unit testů (pytest)
data/                    SQLite DB + ChromaDB (auto-created)
```

---

## Testy

```bash
pip install pytest pytest-asyncio
python3 -m pytest tests/ -v
```

73 testů pokrývajících paměťový systém, profil, detekci emocí a nálady, epizodickou paměť, proaktivní chování a kontextový builder.
