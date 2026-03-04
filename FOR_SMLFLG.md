# ClaudeCodePanel — Wie dein Panel funktioniert

## Die Kurzversion

Dein Panel ist wie ein **Cockpit fuer Claude Code**. Statt 15 Terminal-Fenster
und Config-Dateien zu jonglieren, hast du ein GTK3-Fenster mit 7 Tabs
und ein Tray-Icon das immer da ist. Denk an ein Auto-Armaturenbrett:
Tacho (Monitor), Bordcomputer (Hub), Fehlerlampen (Logs), Navigationsliste (Sessions).

**Stack:** Python 3.12 + PyGObject (GTK3) + Catppuccin Theming + systemd Service

---

## Architektur — 7 Module

### panel.py (1321 Zeilen) — Der Dirigent
Das Hauptfenster. Baut alle 7 Tabs zusammen, startet die Timer, verwaltet das Tray-Icon.
Wie ein Dirigent: spielt selbst kein Instrument, aber koordiniert alle anderen.

**Key:** `ControlPanel` Klasse mit `_build_*_tab()` Methoden, `_refresh_hub()`, `_refresh_monitor()`.

### monitor.py (280 Zeilen) — Der Daten-Butler
Holt alle Zahlen: Kosten, Top-Tools, aktive Sessions, Timeline.
Hat einen TTL-Cache (30 Sekunden) — fragt nicht staendig die Festplatte.

**Key:** `get_daily_cost()`, `get_top_tools()`, `get_active_sessions()`. Nutzt MyAIGame's `tui.data.*` Module.

### log_viewer.py (275 Zeilen) — Der Protokollant
Zeigt heutige Usage-Eintraege und Coaching-Log. Filterbar nach Tools/Errors/Hooks.

**Key:** `build_logs_tab()`, `refresh_logs()`. Liest `~/.claude/usage/YYYY-MM-DD.jsonl`.

### session_browser.py (379 Zeilen) — Das Telefonbuch
Alle Claude-Sessions durchsuchbar. Zeigt Projekt, Vorschau, Groesse. Resume-Button oeffnet kitty.

**Key:** `build_sessions_tab()`, `_scan_all_sessions()`, `_on_resume_clicked()`.

### process_manager.py (552 Zeilen) — Der Hausmeister
Scannt `ps aux` nach Claude-relevanten Prozessen. Zeigt RAM, CPU, Uptime.
Kill-Button pro Prozess + "Kill All Ghosts" fuer Prozesse >24h.

**Key:** `build_processes_tab()`, `_scan_processes()`, Ghost-Detection via `/proc/<pid>/stat`.

### theme.py (320 Zeilen) — Der Maler
Erkennt ob COSMIC Desktop dunkel oder hell ist, generiert passendes CSS.
Catppuccin Mocha (dark) / Latte (light). Wechselt live wenn du das System-Theme aenderst.

**Key:** `is_dark_mode()` liest `~/.config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark`.
`setup_theme_watcher()` nutzt `Gio.FileMonitor` fuer Live-Updates.

### config_io.py (168 Zeilen) — Der Tresorwaerter
Liest und schreibt `~/.claude/settings.json` **atomar** — nie direkt.
Immer: Backup → Temp-Datei → `os.replace()`. So geht nie was kaputt.

**Key:** `read_settings()`, `write_settings()`, `update_setting("key.path", value)`.

---

## Wie die Tabs zusammenhaengen

```
panel.py (Dirigent)
  ├── importiert monitor.py      → liefert Daten fuer Hub + Monitor Tab
  ├── importiert log_viewer.py   → baut Logs Tab
  ├── importiert session_browser.py → baut Sessions Tab
  ├── importiert process_manager.py → baut Prozesse Tab
  ├── importiert theme.py        → CSS + Theme Watcher
  └── importiert config_io.py    → Settings + Hooks Tabs lesen/schreiben Config
```

Jedes Modul ist **eigenstaendig** — kann theoretisch allein funktionieren.
panel.py ist nur der Rahmen der sie zusammenbringt.

---

## Timer-System — Das Herzschlag-System

Dein Panel hat 6 Timer die wie Herzschlaege funktionieren. Jeder Timer
ruft periodisch eine Funktion auf die Daten aktualisiert:

| Timer | Intervall | Funktion | Was wird aktualisiert? |
|-------|-----------|----------|----------------------|
| Hub | 30s | `_refresh_hub()` | Kosten, Top-Tools, Sessions |
| Monitor-Start | 15s | `_start_monitor_timer()` | Startet Monitor-Timer |
| Logs | 10s | `refresh_logs()` | Usage + Coaching Eintraege |
| Sessions | 60s | `refresh_sessions()` | Session-Liste |
| Prozesse | 30s | `refresh_processes()` | Prozess-Scan |
| Monitor | 30s | `_refresh_monitor()` | Timeline, Skills |

**Warum gestaffelt?** Damit nicht alle gleichzeitig feuern und die UI einfriert.
Logs sind am schnellsten (10s) weil Usage-Daten sich oft aendern.
Sessions am langsamsten (60s) weil sich Sessions selten aendern.

**Was wenn ein Timer haengt?** Jeder Timer returned `True` um am Leben zu bleiben.
Wenn einer Exception wirft, faengt `try/except` es ab und der Timer laeuft weiter.
Worst case: ein Tab zeigt veraltete Daten, aber das Panel stuerzt nicht ab.

---

## Daten-Flow

```
~/.claude/usage/YYYY-MM-DD.jsonl  ──→  monitor.py (Cache 30s)  ──→  Hub Tab (Kosten, Tools)
                                    └──→  log_viewer.py          ──→  Logs Tab (Eintraege)

~/.claude/projects/*/  ────────────→  monitor.py + session_browser  ──→  Sessions Tab

ps aux ────────────────────────────→  process_manager.py  ──→  Prozesse Tab

~/.claude/settings.json  ──────────→  config_io.py  ──→  Settings + Hooks Tab
```

---

## Theme-System

1. **Detection:** Liest `~/.config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark`
   - `true` → Catppuccin Mocha (dunkle Palette)
   - `false` → Catppuccin Latte (helle Palette)
   - Datei fehlt → Fallback auf GTK `gtk-application-prefer-dark-theme`

2. **CSS:** `build_css(palette)` generiert ~150 Zeilen CSS aus der Palette

3. **Live-Switch:** `Gio.FileMonitor` beobachtet die COSMIC Config-Datei.
   Aendert sich das System-Theme, wird `apply_theme()` via `GLib.idle_add` aufgerufen.

---

## Config-System — Atomare Writes

Warum nicht einfach `json.dump(data, open(file, "w"))`?
Weil: wenn der Prozess mittendrin stirbt, hast du eine kaputte halbe Datei.

**Stattdessen (config_io.py):**
1. **Backup:** `settings.json` → `settings.json.backup-20260302-143000`
2. **Temp:** Schreibe in `settings-XXXX.tmp` (gleicher Ordner)
3. **Rename:** `os.replace(tmp, settings.json)` — atomar auf Linux!

Entweder hast du die alte ODER die neue Datei. Nie eine kaputte.

---

## Sprint 2026-03-04 Learnings

### Das Wichtigste zuerst: Deine vorherige Arbeit war solide.

4 von 5 Warnings waren **bereits gefixt** bevor der Sprint anfing (Commit `67a4362`).
Du hast einen 5-Agenten-Sprint geplant fuer... einen einzigen verbleibenden Fix (subprocess.run → Popen).
Das Panel ist zu **95%+ fertig**. Was wirklich noch offen ist: 4 Suggestions (nice-to-have), kein Critical.

### Muster: Erst schauen, dann planen.

Tendency erkannt: Samuel plant gross (5 Agents, detaillierter PLAN-File) **bevor** er den aktuellen Stand prueft.
Regel fuer dich: **`/check-state` vor jedem Sprint-Start.** Eine Minute Status-Check spart eine Stunde Overhead.

Konkret: Bevor du Tasks erstellst, kurz pruefen:
- Welche Punkte der Checkliste sind schon erledigt? (PLAN-File lesen)
- Laeuft der Service gerade fehlerfrei? (`systemctl --user status claude-panel.service`)
- Wie gross ist der echte Rueckstand? (1 Fix ≠ 5-Agenten-Aufwand)

### Was gut lief (verstaerken!)

- **Dokumentations-Disziplin:** PLAN-File erstellt, Review-Findings dokumentiert, Entscheidungen festgehalten. Das ist wertvoll.
- **Commit-Hygiene:** Criticals sofort gefixt und committed. Kein "ich mach das spaeter".
- **Code-Review Workflow:** Sonnet Review + MiniMax Zweitmeinung. Zwei Perspektiven fangen mehr.
- **Architektur-Entscheidungen klar begruendet:** `**/*.jsonl` fuer rekursive Suche, `"anthropic"` Key-Aggregation, Fingerprint-Mechanismus — alles dokumentiert.

### Was noch aussteht (die echte Restliste)

Nur Suggestions, kein muss:
1. `monitor.py:~662` — `sorted()` mit stat()-Lambda vereinfachen
2. `log_viewer.py:~456` — unbenutzten `sort_model` Parameter entfernen
3. `panel.py:~812` — `_DETECTOR_LAYOUT` Naming-Konvention
4. `log_viewer.py` — Status-Farben aus Theme-Palette statt hardcoded

Das Panel laeuft. Diese Fixes machen es sauberer, nicht funktionsfaehiger.

---

## Bekannte Gotchas

1. **CPU Spin-Loop (GEFIXT):** `GLib.idle_add(refresh_logs)` mit `return True` =
   Endlosschleife. Fix: One-Shot Wrapper mit `return False`. Regel: `idle_add`
   Callbacks muessen IMMER `False` returnen.

2. **Ghost-Prozesse:** MCP-Server (opencode-mcp, gemini-mcp) werden pro Session
   gestartet aber nie beendet. Der Prozesse-Tab zeigt sie als "Ghosts" (>24h).

3. **MyAIGame Dependency:** `monitor.py` importiert `tui.data.usage_reader` aus
   `~/Projekte/MyAIGame/`. Wenn das Projekt nicht existiert, fallen Kosten-
   und Tool-Statistiken auf Null zurueck (graceful fallback, kein Crash).

4. **COSMIC-only Theme Detection:** Auf nicht-COSMIC-Desktops funktioniert nur
   der GTK-Fallback. Die Catppuccin-Farben bleiben, aber Live-Switching nur per GTK.
