# Plan: ClaudeCodePanel Quick Wins

Erstellt: 2026-03-13 — nach Deep Review + Performance Fixes

## Erledigt

- [x] `_scan_session_jsonls()` in Background Thread (437ms UI-Freeze eliminiert)
- [x] `_scan_processes()` in Background Thread (39ms UI-Freeze eliminiert)
- [x] Commit `3d4f46d` gepusht
- [x] Baseline-Messungs-Regel in `~/.claude/CLAUDE.md` hinzugefügt
- [x] Shortcuts-Tab: 43→108 Shortcuts (ClaudeCLI +16, ClaudeCLI_Vim +20, COSMIC +14, Workspaces +10)
- [x] 7 falsche COSMIC-Beschreibungen korrigiert (waren GNOME-Defaults statt COSMIC)
- [x] Case-insensitive Matching fix (DB: TAB vs Config: Tab)
- [x] 2 neue Anti-Pattern-Regeln in CLAUDE.md
- [x] Commits `20b7ba0` + `fd9b8ef` gepusht

## Offen — Quick Wins (je <30 Min)

### 1. `constants.py` extrahieren (10 Min)
5 Module definieren PROJECTS_DIR, USAGE_DIR redundant:
- `monitor.py:66-67`
- `session_browser.py:40`
- `log_viewer.py:19`
- `config_io.py:14`

Neues `constants.py` mit allen Pfaden, Module importieren daraus.

### 2. `providers.jsonl` Cache-Sharing (15 Min)
`log_viewer.py:172` liest `providers.jsonl` eigenständig.
`monitor.py:600` cached dieselbe Datei bereits.
Fix: log_viewer importiert monitor.get_provider_costs() oder neue
monitor.get_provider_entries_today() Funktion.

### 3. WebKit2 lazy-loading (30 Min)
`swarm_tab.py` und `project_dashboard_tab.py` spawnen je einen WebProcess
beim Start (~50-100MB RAM pro Stück). Fix: WebView erst bei Tab-Switch
erstellen via `notebook.connect("switch-page", ...)`.

## Offen — Features

### 4. Ideen/Gedanken Tab (Parking Lot)
- Codex baut das Parking Lot Backend (parking_lot mit park/unpark/park-list)
- Panel braucht Tab zur Darstellung: Claude Code Plaene, eigene Plaene, geparkte Gedanken
- Samuel gibt Pfade wenn Codex fertig ist — FRAGEN nicht selbst suchen
- Existierendes Vorbild: `~/Projekte/MyAIGame/tui/adhs/parking.py` (SQLite-backed)

### 5. Cloudbot Tab (groß)
Vollständig recherchiert in `CLOUDBOT_RESEARCH.md`.
MVP-Scope: Session Quality Scoring als `cloudbot_tab.py`.
