# ClaudeCodePanel — Wie dein Panel funktioniert

## Die Kurzversion

Dein Panel ist wie ein **Cockpit fuer Claude Code**. Statt 15 Terminal-Fenster
und Config-Dateien zu jonglieren, hast du ein GTK3-Fenster mit 7 Tabs
und ein Tray-Icon das immer da ist. Denk an ein Auto-Armaturenbrett:
Tacho (Monitor), Bordcomputer (Hub), Fehlerlampen (Logs), Navigationsliste (Sessions).

**Stack:** Python 3.12 + PyGObject (GTK3) + Catppuccin Theming + systemd Service

---

## Architektur — 14 Module

### panel.py (2373 Zeilen) — Der Dirigent
Das Hauptfenster. Baut alle Tabs zusammen, startet die Timer, verwaltet das Tray-Icon.
Wie ein Dirigent: spielt selbst kein Instrument, aber koordiniert alle anderen.

**Key:** `ControlPanel` Klasse mit `_build_*_tab()` Methoden, `_refresh_hub()`, `_refresh_monitor()`.

### monitor.py (729 Zeilen) — Der Daten-Butler
Holt alle Zahlen: Kosten, Top-Tools, aktive Sessions, Timeline.
Hat einen TTL-Cache (30 Sekunden) — fragt nicht staendig die Festplatte.

**Key:** `get_daily_cost()`, `get_top_tools()`, `get_active_sessions()`. Nutzt MyAIGame's `tui.data.*` Module.

### log_viewer.py (435 Zeilen) — Der Protokollant
Zeigt heutige Usage-Eintraege und Coaching-Log. Filterbar nach Tools/Errors/Hooks.

**Key:** `build_logs_tab()`, `refresh_logs()`. Liest `~/.claude/usage/YYYY-MM-DD.jsonl`.

### session_browser.py (1039 Zeilen) — Das Telefonbuch
Alle Claude-Sessions durchsuchbar. Zeigt Projekt, Vorschau, Groesse. Resume-Button oeffnet kitty.

**Key:** `build_sessions_tab()`, `_scan_all_sessions()`, `_on_resume_clicked()`.

### process_manager.py (588 Zeilen) — Der Hausmeister
Scannt `ps aux` nach Claude-relevanten Prozessen. Zeigt RAM, CPU, Uptime.
Kill-Button pro Prozess + "Kill All Ghosts" fuer Prozesse >24h.

**Key:** `build_processes_tab()`, `_scan_processes()`, Ghost-Detection via `/proc/<pid>/stat`.

### theme.py (620 Zeilen) — Der Maler
Erkennt ob COSMIC Desktop dunkel oder hell ist, generiert passendes CSS.
Catppuccin Mocha (dark) / Latte (light). Wechselt live wenn du das System-Theme aenderst.

**Key:** `is_dark_mode()` liest `~/.config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark`.
`setup_theme_watcher()` nutzt `Gio.FileMonitor` fuer Live-Updates.

### config_io.py (168 Zeilen) — Der Tresorwaerter
Liest und schreibt `~/.claude/settings.json` **atomar** — nie direkt.
Immer: Backup → Temp-Datei → `os.replace()`. So geht nie was kaputt.

**Key:** `read_settings()`, `write_settings()`, `update_setting("key.path", value)`.

### swarm_tab.py (974 Zeilen) — Der Lageplaner
Zeigt die laufende Agent Team Pipeline in Echtzeit. Liest `~/.claude/teams/` und
`~/.claude/tasks/` und stellt Scout→Weaver→Builder→Validator→Pruefer als Karten dar.

**Key:** `build_swarm_tab()`, `_scan_teams()`, `_refresh_swarm()`.

### swarm_visual.py (708 Zeilen) — Der Kartograf
Generiert HTML-Seiten mit interaktiven Agent-Kommunikationsgraphen: Hub-and-Spoke-Layout,
Bezier-Kurven, Partikel-Animationen. Wird von swarm_tab.py aufgerufen und in WebKit2 eingebettet.

**Key:** `generate_swarm_html()`, Bezier + Partikel-Rendering via Canvas.

### event_tab.py (426 Zeilen) — Der Horcher
Live-Viewer fuer Hook-Events aus `~/.claude/events/YYYY-MM-DD.jsonl`. Filterbar
nach Event-Typ, Auto-Scroll wenn neue Events reinkommen.

**Key:** `build_events_tab()`, `_tail_events()`, Filter-Logik nach Typ/Quelle.

### shortcut_counter_tab.py (450 Zeilen) — Der Strichlisten-Fuehrer
Zeigt Keyboard-Shortcut-Statistiken aus der ShortcutCounter-Datenbank. Wie oft hast
du Ctrl+C vs. Ctrl+Z genutzt? Welcher Shortcut spart am meisten Zeit?

**Key:** `build_shortcut_tab()`, liest ShortcutCounter SQLite DB.

### project_dashboard_tab.py (254 Zeilen) — Das Fenster
Bettet das Project Dashboard als WebKit2-Webview direkt ins Panel ein, statt
einen Browser oeffnen zu muessen. Ein Tab = ein komplettes Web-Dashboard.

**Key:** `build_project_dashboard_tab()`, `WebKit2.WebView` Setup + Reload-Button.

### utils.py (33 Zeilen) — Der Werkzeugkasten
Kleine Hilfsfunktionen die ueberall gebraucht werden (Format-Helpers, Pfad-Utilities).
Klein aber da wenn man ihn braucht.

**Key:** Shared Utilities fuer alle anderen Module.

### tools-gui/ (623 Zeilen) — Das Zweite Cockpit
Separates GTK3-Fenster mit 3 eigenen Tabs: Kosten-Analyse, Session-Uebersicht, Tool-Statistiken.
Laeuft als eigenstaendiges Fenster neben dem Hauptpanel (main.py + 3 Tab-Module).

**Key:** `tools-gui/main.py` als Einstiegspunkt, 3 Tab-Module fuer costs/sessions/tools.

---

## Wie die Tabs zusammenhaengen

```
panel.py (Dirigent)
  ├── importiert monitor.py        → liefert Daten fuer Hub + Monitor Tab
  ├── importiert log_viewer.py     → baut Logs Tab
  ├── importiert session_browser.py → baut Sessions Tab
  ├── importiert process_manager.py → baut Prozesse Tab
  ├── importiert swarm_tab.py      → baut Agent Swarm Tab
  ├── importiert swarm_visual.py   → generiert Swarm-Graphen (HTML)
  ├── importiert event_tab.py      → baut Events Tab
  ├── importiert shortcut_counter_tab.py → baut Shortcut Counter Tab
  ├── importiert project_dashboard_tab.py → baut Project Dashboard Tab (WebKit2)
  ├── importiert theme.py          → CSS + Theme Watcher
  └── importiert config_io.py      → Settings + Hooks Tabs lesen/schreiben Config
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

5. **Singleton Lock (GEFIXT):** Frueher konnten mehrere Panel-Instanzen gleichzeitig
   laufen (systemd restart + manueller Start). Fix: `fcntl.flock` auf `/tmp/claude-panel.lock`.
   Zweite Instanz erkennt Lock und beendet sich mit `sys.exit(0)`.
