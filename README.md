# Claude Code Panel

A native GTK3 desktop panel for [Claude Code](https://claude.ai/code) on Linux. System tray icon, session browser, process manager, cost monitoring — all in one place.

Built for Pop!\_OS / COSMIC Desktop, works on any Linux with GTK3.

## Features

- **System Tray Icon** — always-on indicator with quick access menu (via AyatanaAppIndicator)
- **Session Browser** — browse, search, and resume Claude Code sessions with one click
- **Process Manager** — monitor Claude-related processes (MCP servers, CLI instances), kill ghost processes eating RAM
- **Cost Monitor** — daily token cost tracking from usage logs
- **Tool Stats** — see which tools are used most
- **Shortcuts** — one-click access to docs, projects, configs
- **Light/Dark Mode** — auto-detects COSMIC Desktop theme, switches between Catppuccin Mocha (dark) and Latte (light)

## Screenshots

*Coming soon*

## Requirements

- Python 3.10+
- GTK 3.0
- AyatanaAppIndicator3 (for system tray)

```bash
# Pop!_OS / Ubuntu / Debian
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
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

## Files

| File | Purpose |
|------|---------|
| `panel.py` | Main window, tabs, tray icon |
| `theme.py` | Light/dark theme detection + CSS generation |
| `session_browser.py` | Session list widget (scan, search, resume) |
| `process_manager.py` | Process list widget (scan, kill, ghost detection) |
| `config_io.py` | Read/write Claude Code settings.json + hooks |
| `monitor.py` | Parse usage logs for cost/tool stats |
| `log_viewer.py` | Log viewer widget |
| `claude-panel.desktop` | Desktop entry for app launchers |
| `claude-panel.service` | systemd user service file |

## Theme System

The panel auto-detects your desktop theme:

- **COSMIC Desktop**: reads `~/.config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark`
- **Other GTK desktops**: uses `gtk-application-prefer-dark-theme` setting

Switching your system theme updates the panel in real-time (no restart needed).

Color palettes: [Catppuccin Mocha](https://github.com/catppuccin/catppuccin) (dark) / [Catppuccin Latte](https://github.com/catppuccin/catppuccin) (light) with WCAG AA contrast adjustments for the light theme.

## License

MIT
