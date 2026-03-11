# Claude Code Panel

A native GTK3 desktop panel for [Claude Code](https://claude.ai/code) on Linux. System tray icon, session browser, agent swarm monitor, cost tracking, event viewer — all in one place.

Built for Pop!\_OS / COSMIC Desktop, works on any Linux with GTK3.

## Features

- **System Tray Icon** — always-on indicator with quick access menu (AyatanaAppIndicator)
- **Hub Tab** — daily cost, top tools, active sessions at a glance
- **Monitor Tab** — signal cards, usage timeline, provider cost breakdown, phase badge
- **Session Browser** — search, filter, and resume sessions with one click
- **Process Manager** — Claude-related processes (MCP servers, CLI), ghost detection, kill button
- **Agent Swarm Tab** — live view of Agent Team pipelines (Scout → Weaver → Builder → Validator → Prüfer)
- **Swarm Visual** — HTML agent communication graph (hub-and-spoke, Bézier curves, particles)
- **Event Viewer** — live hook events from `~/.claude/events/` with filters + auto-scroll
- **Shortcut Counter** — keyboard shortcut usage stats from ShortcutCounter DB
- **Project Dashboard** — embedded WebKit2 view of project dashboards
- **Log Viewer** — filterable usage + coaching log entries
- **Settings & Hooks** — read/write Claude Code config with atomic file writes
- **Cost Tracking** — daily token costs, 7-day bar chart, per-provider breakdown
- **Light/Dark Mode** — auto-detects COSMIC Desktop theme, Catppuccin Mocha/Latte, live switching
- **Singleton Lock** — only one instance runs at a time (fcntl.flock)
- **Portrait Monitor** — auto-moves window to portrait display if available

## Requirements

- Python 3.10+
- GTK 3.0
- AyatanaAppIndicator3 (system tray)
- WebKit2 (optional, for Project Dashboard tab)

```bash
# Pop!_OS / Ubuntu / Debian
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-webkit2-4.1
```

## Installation

```bash
git clone https://github.com/smlfg/ClaudeCodePanel.git
cd ClaudeCodePanel

# Run directly
python3 panel.py

# Or install as systemd user service (auto-start on login)
cp claude-panel.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now claude-panel.service
```

## Architecture

```
panel.py (2373 LOC) — Main window, 7+ tabs, tray icon, singleton lock
  ├── monitor.py (729)        — Data layer: costs, tools, sessions (TTL cache 30s)
  ├── config_io.py (168)      — Atomic config read/write (backup → temp → rename)
  ├── theme.py (620)          — COSMIC theme detection + Catppuccin CSS
  ├── session_browser.py (1039) — Session list, search, resume
  ├── process_manager.py (588)  — Process scan, ghost detection, kill
  ├── log_viewer.py (435)       — Usage + coaching log viewer
  ├── swarm_tab.py (974)        — Agent Team pipeline viewer
  ├── swarm_visual.py (708)     — HTML agent communication graph
  ├── event_tab.py (426)        — Live hook event viewer
  ├── shortcut_counter_tab.py (450) — Keyboard shortcut stats
  ├── project_dashboard_tab.py (254) — Embedded project dashboard (WebKit2)
  └── utils.py (33)            — Helper functions
```

### tools-gui/ (separate window)

```
tools-gui/main.py (83)           — Standalone GTK3 window
  ├── tabs/sessions_tab.py (146) — Session list with search
  ├── tabs/costs_tab.py (216)    — Daily costs + 7-day bar chart (Cairo)
  └── tabs/tools_tab.py (178)    — Tool + skill usage breakdown
```

**Total: ~9,700 LOC across 19 Python files.**

## Theme System

The panel auto-detects your desktop theme:

- **COSMIC Desktop**: reads `~/.config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark`
- **Other GTK desktops**: uses `gtk-application-prefer-dark-theme` setting

Switching your system theme updates the panel in real-time (no restart needed).

Color palettes: [Catppuccin Mocha](https://github.com/catppuccin/catppuccin) (dark) / [Catppuccin Latte](https://github.com/catppuccin/catppuccin) (light).

## License

MIT
