# iMessage Bot — Dokumentace

## Popis

Modul pro čtení a odesílání iMessage zpráv na macOS. Funguje dvěma způsoby:

1. **Integrovaný v Eigy** — příkazy `zobraz imessage`, `odepiš na imessage` přímo v hlavní aplikaci, sledování nových zpráv na pozadí s TTS čtením nahlas
2. **Standalone terminálový bot** — `python3 imessage_bot.py` jako nezávislý nástroj

## Požadavky

- **macOS** (čte z `~/Library/Messages/chat.db`)
- **Full Disk Access** — Terminal / VS Code musí mít povolen přístup:
  `System Settings → Privacy & Security → Full Disk Access → přidat Terminal.app`
- **Messages.app** nastavena na tomto Macu (iMessage aktivní)

## Příkazy (v Eigy)

| Příkaz | Popis |
|--------|-------|
| `zobraz imessage` | Zobrazit posledních 5 příchozích zpráv |
| `zobraz imessage N` | Zobrazit posledních N zpráv (max 50) |
| `odepiš na imessage X` | Odpovědět na zprávu číslo X |
| `ulož kontakt X Jméno` | Uložit jméno ke zprávě číslo X |
| `kontakty` | Zobrazit uložené kontakty |

## Příkazy (standalone bot)

| Příkaz | Popis |
|--------|-------|
| `zobraz imessage [N]` | Zobrazit příchozí zprávy |
| `odepiš na imessage X` | Odpovědět na zprávu X |
| `ulož kontakt X Jméno` | Uložit kontakt |
| `kontakty` | Zobrazit kontakty |
| `sledování zapni/vypni` | Zapnout/vypnout sledování nových zpráv |
| `interval N` | Nastavit interval sledování (sekundy) |
| `pomoc` | Nápověda |
| `konec` | Ukončit |

## Architektura

### Hlavní komponenty (`imessage_bot.py`)

**`IMessage`** — dataclass pro jednu zprávu (`rowid`, `sender`, `text`, `timestamp`, `is_from_me`)

**`MessagesDB`** — read-only SQLite čtečka `chat.db`:
- Připojení přes URI `?mode=ro` (s fallbackem na plain path)
- Cocoa timestamp konverze (offset 978 307 200, detekce nanosekund)
- Parsování `attributedBody` (NSKeyedArchiver blob, macOS Ventura+)
- `get_recent_incoming(limit)` — posledních N příchozích zpráv
- `get_latest_rowid()` + `get_messages_since(rowid)` — pro sledování nových zpráv

**`send_imessage(recipient, text)`** — odeslání přes AppleScript (`osascript`), timeout 30s

**`ContactBook`** — JSON mapování telefon/email → jméno:
- Uloženo v `data/imessage_contacts.json`
- `get_name()`, `set_contact()`, `remove_contact()`, `all_contacts()`

**`MessageWatcher`** — daemon thread pro standalone bot:
- Polluje DB každých N sekund (výchozí 5s)
- `threading.Event` pro zapnutí/vypnutí
- Sleep v 100ms krocích pro rychlé ukončení

### Integrace v main.py

- **Detekce příkazů** — regex parser (`_IMESSAGE_SHOW_RE`, `_IMESSAGE_REPLY_RE`, `_IMESSAGE_SAVE_RE`, `_IMESSAGE_CONTACTS_RE`) zachytí příkazy před odesláním do LLM
- **Async watcher** (`_imessage_watcher`) — polluje DB každých 5s, pushuje `imessage_new` eventy do event queue
- **TTS notifikace** — nová zpráva se automaticky přečte nahlas přes `proactive_response()`
- **Lazy-init DB** — `imessage_db_holder` (mutable list) pro sdílení DB mezi watcherem a příkazy

## Troubleshooting

| Problém | Řešení |
|---------|--------|
| `unable to open database file` | Přidej Terminal do Full Disk Access |
| `Databáze nenalezena` | Zkontroluj, že Messages.app je nastavena |
| Odeslání selhalo | Ověř, že Messages.app běží a iMessage je aktivní |
| Zprávy se nezobrazují | Některé zprávy (média, přílohy) se zobrazí jako `[média/příloha]` |
