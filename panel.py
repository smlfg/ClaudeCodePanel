#!/usr/bin/env python3
"""Claude Code Control Panel — GTK3 Desktop App.

A native desktop panel with sliders, toggles, dropdowns, session browser,
and shortcuts to the entire Vibe Coding setup.

Performance: Fixed label widgets (no rebuild), separate timers for Hub/Monitor,
data fetching via GLib.idle_add for non-blocking UI.
"""

import shutil
import subprocess
from pathlib import Path
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, GLib, Gdk, Pango, Gio, AyatanaAppIndicator3

from config_io import (
    read_settings,
    write_settings,
    read_hook_list,
    read_coaching_rate_limit,
    write_coaching_rate_limit,
)
from monitor import (
    get_daily_cost,
    get_top_tools,
    get_active_sessions,
    get_recent_sessions,
    get_missed_skills_summary,
    get_usage_timeline,
    get_provider_costs,
    get_anthropic_session_cost,
    get_skill_usage,
    get_sidecar_status,
    format_cost,
)
from log_viewer import build_logs_tab, refresh_logs
from session_browser import build_sessions_tab, refresh_sessions
from process_manager import build_processes_tab, refresh_processes
from theme import build_css, setup_theme_watcher
from utils import idle_once
from swarm_tab import build_swarm_tab, refresh_swarm
from shortcut_counter_tab import build_shortcut_counter_tab, refresh_shortcut_counter


# ---------------------------------------------------------------------------
# Shortcuts: clickable links to docs, projects, tools
# ---------------------------------------------------------------------------
# Categories for color-coding: "service", "docs", "folder", "config"
SHORTCUTS = [
    # --- Services (Blue #89b4fa) — interactive apps, APIs, dashboards ---
    {
        "label": "MAS Dashboard",
        "icon": "utilities-system-monitor",
        "path": "~/ClaudesReich/Visualisierung/",
        "tooltip": "Multi-Agent System — War Room, Timeline, Cost Analysis",
        "command": "cd ~/ClaudesReich/Visualisierung && python3 -m streamlit run app.py --server.port 8501 2>/dev/null & sleep 2 && xdg-open http://localhost:8501",
        "category": "service",
    },
    {
        "label": "Session Browser",
        "icon": "system-search",
        "path": "~/ClaudesReich/SessionBrowser/",
        "tooltip": "Wo war ich? — Session-Uebersicht + Wiedereinstieg",
        "command": "cd ~/ClaudesReich/SessionBrowser && python3 -m streamlit run app.py --server.port 8502 2>/dev/null & sleep 2 && xdg-open http://localhost:8502",
        "category": "service",
    },
    {
        "label": "Florian TTS",
        "icon": "audio-speakers",
        "path": "~/Projekte/MyAIGame/voicemode-edge-tts/",
        "tooltip": "Edge TTS API — localhost:5050/docs",
        "command": "xdg-open http://localhost:5050/docs",
        "category": "service",
    },
    {
        "label": "MultiKanal",
        "icon": "applications-science",
        "path": "~/AiSystemForVibeCoding/",
        "tooltip": "MultiKanal Agent Daemon — Narration + TTS",
        "command": "xdg-open http://localhost:8000/docs",
        "category": "service",
    },
    {
        "label": "Swarm Dashboard",
        "icon": "network-workgroup",
        "path": "~/Projekte/AgentSwarmDashboard/",
        "tooltip": "Agent Swarm Dashboard — Team Graph, Kanban, Feed",
        "command": "xdg-open http://localhost:5111",
        "category": "service",
    },
    # --- Referenz-Diagramme (Blue — interactive HTML dashboards) ---
    {
        "label": "Session Lifecycle",
        "icon": "emblem-synchronizing",
        "path": "~/.agent/diagrams/claude-session-lifecycle.html",
        "tooltip": "Session Lifecycle — Phasen, Hooks, Checkpoints",
        "category": "service",
    },
    {
        "label": "Delegation Map",
        "icon": "emblem-shared",
        "path": "~/.agent/diagrams/claude-delegation-architecture.html",
        "tooltip": "Delegation Architecture — Opus→Sonnet→Haiku→Gemini",
        "category": "service",
    },
    {
        "label": "Skill Ecosystem",
        "icon": "emblem-package",
        "path": "~/.agent/diagrams/claude-skill-ecosystem.html",
        "tooltip": "Skill Ecosystem — Alle Skills + Trigger + Kosten",
        "category": "service",
    },
    {
        "label": "Cost Dashboard",
        "icon": "emblem-money",
        "path": "~/.agent/diagrams/claude-cost-dashboard.html",
        "tooltip": "Cost Dashboard — Kosten-Analyse + Optimierung",
        "category": "service",
    },
    # --- Docs (Yellow #f9e2af) — reference, guides, knowledge ---
    {
        "label": "CLAUDE.md",
        "icon": "document-properties",
        "path": "~/.claude/CLAUDE.md",
        "tooltip": "Hauptkonfiguration — alle Regeln",
        "category": "docs",
    },
    {
        "label": "Skills",
        "icon": "help-contents",
        "path": "~/.claude/Skilluebersicht.md",
        "tooltip": "Alle Skills mit Beschreibung + Kosten",
        "category": "docs",
    },
    {
        "label": "Wie arbeite ich",
        "icon": "dialog-information",
        "path": "~/.claude/WieArbeitestDuMitSamuel.md",
        "tooltip": "Samuel-Workflow, Plan Mode, Anticipation",
        "category": "docs",
    },
    {
        "label": "Fehler",
        "icon": "dialog-warning",
        "path": "~/.claude/WelcheFehlerVermeiden.md",
        "tooltip": "Top 10 Fehler + Fix Chains",
        "category": "docs",
    },
    {
        "label": "Coaching Log",
        "icon": "appointment-new",
        "path": "~/.claude/hooks/coaching/coaching_log.md",
        "tooltip": "Git/Test Micro-Lessons Log",
        "category": "docs",
    },
    # --- Folders (Green #a6e3a1) — project navigation ---
    {
        "label": "Projekte",
        "icon": "folder",
        "path": "~/Projekte/",
        "tooltip": "Projektordner oeffnen",
        "category": "folder",
    },
    {
        "label": "ClaudesReich",
        "icon": "user-home",
        "path": "~/ClaudesReich/",
        "tooltip": "Alles was Claude gehoert",
        "category": "folder",
    },
    {
        "label": "MyAIGame",
        "icon": "applications-games",
        "path": "~/Projekte/MyAIGame/",
        "tooltip": "Multikanal + TUI + Hooks",
        "category": "folder",
    },
    {
        "label": "Hook Plugins",
        "icon": "preferences-plugin",
        "path": "~/Dokumente/Pläne/ClaudeCodeWorks/plugins/claude-hook/hooks/",
        "tooltip": "Alle Hook-Scripts",
        "category": "folder",
    },
    {
        "label": "Plaene",
        "icon": "text-x-generic",
        "path": "~/Dokumente/Pläne/",
        "tooltip": "Alle Plaene + ClaudeCodeWorks Plugins",
        "category": "folder",
    },
    {
        "label": "Session Archiv",
        "icon": "folder-documents",
        "path": "~/ClaudesReich/EveryClaudeCodeSessionfromeverydeviceever/",
        "tooltip": "Alle Sessions von allen Geraeten",
        "category": "folder",
    },
    {
        "label": "Archiv Plaene",
        "icon": "folder-visiting",
        "path": "~/ClaudesReich/ArchivPläne/",
        "tooltip": "Archivierte Session-Plaene",
        "category": "folder",
    },
    # --- Config (Mauve #cba6f7) ---
    {
        "label": "Settings",
        "icon": "preferences-system",
        "path": "~/.claude/settings.json",
        "tooltip": "settings.json — Hooks, MCP, Env",
        "category": "config",
    },
]

# ---------------------------------------------------------------------------
# Skills: complete list organized by category
# ---------------------------------------------------------------------------
SKILLS = {
    "Coding & Delegation": [
        ("/chef", "~$3", "Delegation an OpenCode MCP mit Context-Gathering"),
        ("/chef-lite", "~$3", "Direkter OpenCode-Call ohne Context-Gathering"),
        ("/chef-async", "~$3", "Wie /chef aber non-blocking"),
        ("/chef-subagent", "~$3.25", "Haiku-SubAgent der OpenCode steuert"),
        ("/batch", "~$3", "Mehrere Tasks in EINEN opencode_run Call"),
        ("/auto", "variabel", "Smart auto-delegation — waehlt beste Methode"),
        ("/codex", "~$0.05", "Codex Sparringspartner — Zweitmeinung oder Review"),
    ],
    "Research & Analyse": [
        ("/research", "~$0.10", "Gemini MCP Deep Web Research"),
        ("/research-subagent", "~$0.25", "Gemini-Research als Haiku-SubAgent"),
        ("/research-swarm", "~$0.30", "3 parallele Gemini-Agents fuer Mega-Research"),
        ("/swarm", "~$0.75", "3 Haiku-Agents fuer Dokumentanalyse"),
        ("/litellm", "~$0.05", "1-shot Call an cheap LLM — kein Multi-Agent"),
    ],
    "Testing": [
        ("/test", "~$3", "Cascading Test Pipeline via OpenCode + Auto-Fix"),
        ("/test-crew", "~$0.27", "Gemini plant Tests, OpenCode fuehrt aus + fixt"),
    ],
    "CLI ($0)": [
        ("/cli lint", "$0", "Auto-detect + run Linter"),
        ("/cli format", "$0", "Auto-detect + run Formatter"),
        ("/cli test", "$0", "Auto-detect + run Tests ohne Analyse"),
        ("/cli deps", "$0", "Dependency-Check (outdated + audit)"),
        ("/cli git", "$0", "Git-Uebersicht (branches, stash, log)"),
        ("/cli find", "$0", "Smart Code-Suche mit ripgrep"),
        ("/cli project", "$0", "Projekt-Info (Stack, LOC, Configs)"),
        ("/cli health", "$0", "System-Gesundheit (Disk, RAM, Ports)"),
    ],
    "Utilities": [
        ("/check-state", "$0", "pwd, ls, git status — Orientierung"),
        ("/validate-config", "$0", "Config pruefen + Backup"),
        ("/review", "~$3", "Code Review der Aenderungen"),
        ("/debug-loop", "~$3", "Max 5 Iterationen: Diagnose-Fix-Test"),
        ("/explore-first", "~$0.50", "Parallele Codebase-Erkundung"),
        ("/selfimprove", "$0", "CLAUDE.md + Companion Files verbessern"),
        ("/recap", "$0", "Session zusammenfassen → SESSION_LOG.md"),
        ("/quickwin", "$0", "3 kleine Tasks gegen Task-Paralysis"),
        ("/checkpoint", "$0", "Git-Snapshot + Summary + Dopamin"),
        ("/learn", "$0", "Nach Vibe-Coding: Wissen sichern"),
        ("/focus", "$0", "EIN Ziel, Abschweifung bremsen"),
        ("/zuendebringe", "$0", "Offene Faesser auflisten + abarbeiten"),
    ],
    "Multi-Agent": [
        ("/crew", "~$0.60", "CrewAI mit MiniMax+Haiku Workern"),
        ("/smol", "~$0.10", "Smolagents Code-Agent mit Tool-Use"),
        ("/openhands", "~$0.30", "Autonomer Dev Agent via Docker"),
    ],
}


# CSS is generated dynamically by theme.py (Catppuccin Mocha / Latte)

_MAX_SESSION_ROWS = 5
_MAX_TIMELINE_ROWS = 7


def _open_path(path: str) -> None:
    """Open a file or directory with xdg-open."""
    resolved = Path(path).expanduser()
    if not resolved.exists():
        return  # silently skip missing paths
    subprocess.Popen(["xdg-open", str(resolved)], start_new_session=True)


def _run_command(cmd: str) -> None:
    """Run a shell command detached. Only call with trusted, hardcoded commands."""
    subprocess.Popen(["bash", "-c", cmd], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _resume_session(session_id: str) -> None:
    """Resume a Claude Code session in kitty terminal."""
    subprocess.Popen(
        ["kitty", "-e", "claude", "-r", session_id],
        start_new_session=True,
    )


class ControlPanel(Gtk.Window):
    def __init__(self):
        super().__init__(title="Claude Code Control Panel")
        self.set_default_size(580, 720)
        self.set_resizable(True)

        # Apply theme-aware CSS (auto-detects COSMIC light/dark)
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(build_css())
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Watch for COSMIC theme changes and re-apply CSS automatically
        setup_theme_watcher(css_provider)

        # Load current settings
        self.settings = read_settings()

        # Main layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(vbox)

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        vbox.pack_start(self.notebook, True, True, 0)

        # Build tabs
        self._build_hub_tab()
        self._build_settings_tab()
        self._build_hooks_tab()
        self._build_monitor_tab()
        self._build_cost_tab()
        self.notebook.append_page(build_logs_tab(), Gtk.Label(label="Logs"))
        self.notebook.append_page(build_sessions_tab(), Gtk.Label(label="Sessions"))
        self.notebook.append_page(build_processes_tab(), Gtk.Label(label="Prozesse"))
        self.notebook.append_page(build_swarm_tab(), Gtk.Label(label="Swarm"))
        self.notebook.append_page(build_shortcut_counter_tab(), Gtk.Label(label="Shortcuts"))

        # Bottom status bar
        status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        status_bar.get_style_context().add_class("status-bar")
        status_bar.set_margin_start(12)
        status_bar.set_margin_end(12)
        status_bar.set_margin_top(6)
        status_bar.set_margin_bottom(6)

        self.status_label = Gtk.Label(label="")
        self.status_label.set_xalign(0)
        status_bar.pack_start(self.status_label, True, True, 0)

        save_btn = Gtk.Button(label="Speichern")
        save_btn.get_style_context().add_class("suggested-action")
        save_btn.connect("clicked", self.on_save)

        reset_btn = Gtk.Button(label="Reset")
        reset_btn.connect("clicked", self.on_reset)

        status_bar.pack_end(save_btn, False, False, 0)
        status_bar.pack_end(reset_btn, False, False, 0)

        vbox.pack_start(status_bar, False, False, 0)

        # Separate timers — Hub 30s, Monitor 30s offset by 15s, Logs 10s, Sessions 60s
        GLib.timeout_add_seconds(30, self._refresh_hub)
        GLib.timeout_add_seconds(15, self._start_monitor_timer)
        GLib.timeout_add_seconds(10, refresh_logs)
        GLib.timeout_add_seconds(60, refresh_sessions)
        GLib.timeout_add_seconds(30, refresh_processes)
        GLib.timeout_add_seconds(30, self._refresh_cost)
        GLib.timeout_add_seconds(30, refresh_swarm)
        GLib.timeout_add_seconds(30, refresh_shortcut_counter)

    def _start_monitor_timer(self) -> bool:
        """One-shot: starts the 30s monitor timer (offset from hub by 15s)."""
        GLib.timeout_add_seconds(30, self._refresh_monitor)
        self._refresh_monitor()
        return False  # don't repeat this one-shot

    # -----------------------------------------------------------------------
    # Tab 1: Hub — Dashboard, Sessions, Shortcuts
    # -----------------------------------------------------------------------
    def _build_hub_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # --- Quick Stats Row ---
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        stats_box.set_halign(Gtk.Align.CENTER)

        self.hub_cost_label = Gtk.Label(label="...")
        self.hub_cost_label.get_style_context().add_class("cost-value")
        self.hub_cost_sublabel = Gtk.Label(label="Heutige Kosten")
        self.hub_cost_sublabel.get_style_context().add_class("stat-label")

        self.hub_calls_label = Gtk.Label(label="...")
        self.hub_calls_label.get_style_context().add_class("stat-value")
        self.hub_calls_sublabel = Gtk.Label(label="Tool Calls")
        self.hub_calls_sublabel.get_style_context().add_class("stat-label")

        self.hub_sessions_label = Gtk.Label(label="...")
        self.hub_sessions_label.get_style_context().add_class("stat-value")
        self.hub_sessions_sublabel = Gtk.Label(label="Aktive Sessions")
        self.hub_sessions_sublabel.get_style_context().add_class("stat-label")

        for val_label, sub_label in [
            (self.hub_cost_label, self.hub_cost_sublabel),
            (self.hub_calls_label, self.hub_calls_sublabel),
            (self.hub_sessions_label, self.hub_sessions_sublabel),
        ]:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.set_halign(Gtk.Align.CENTER)
            col.pack_start(val_label, False, False, 0)
            col.pack_start(sub_label, False, False, 0)
            stats_box.pack_start(col, True, True, 0)

        vbox.pack_start(stats_box, False, False, 0)

        # --- Provider Cost Breakdown (in Frame for visibility) ---
        provider_frame = Gtk.Frame(label="  Kosten nach Provider  ")
        self.provider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.provider_box.set_margin_top(8)
        self.provider_box.set_margin_bottom(8)
        self.provider_box.set_margin_start(10)
        self.provider_box.set_margin_end(10)
        provider_frame.add(self.provider_box)
        vbox.pack_start(provider_frame, False, False, 4)

        # Pre-create 4 provider rows (anthropic, minimax, codex, gemini)
        self._provider_slots = {}
        provider_colors = {
            "anthropic": "#89b4fa",  # blue
            "minimax": "#a6e3a1",    # green
            "codex": "#fab387",      # peach/orange
            "gemini": "#f9e2af",     # yellow
        }
        for provider_name in ["anthropic", "minimax", "codex", "gemini"]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.set_margin_start(8)

            name_lbl = Gtk.Label(label=provider_name.capitalize(), xalign=0)
            name_lbl.set_width_chars(12)
            row.pack_start(name_lbl, False, False, 0)

            # Color bar (using a Label with block characters)
            bar_lbl = Gtk.Label(label="")
            bar_lbl.set_xalign(0)
            color = provider_colors.get(provider_name, "#cdd6f4")
            bar_lbl.set_markup(f'<span foreground="{color}"></span>')
            row.pack_start(bar_lbl, True, True, 0)

            cost_lbl = Gtk.Label(label="$0.00", xalign=1)
            cost_lbl.set_width_chars(12)
            cost_lbl.set_opacity(0.7)
            row.pack_start(cost_lbl, False, False, 0)

            self.provider_box.pack_start(row, False, False, 0)
            row.set_no_show_all(True)

            self._provider_slots[provider_name] = {
                "row": row,
                "name": name_lbl,
                "bar": bar_lbl,
                "cost": cost_lbl,
                "color": color,
            }

        # --- Sidecar Watcher ---
        watcher_label = Gtk.Label(label="Sidecar Watcher", xalign=0)
        watcher_label.get_style_context().add_class("section-title")
        vbox.pack_start(watcher_label, False, False, 4)

        watcher_frame = Gtk.Frame()
        watcher_frame.get_style_context().add_class("section-frame")
        watcher_grid = Gtk.Grid()
        watcher_grid.set_column_spacing(8)
        watcher_grid.set_row_spacing(4)
        watcher_grid.set_margin_top(8)
        watcher_grid.set_margin_bottom(8)
        watcher_grid.set_margin_start(10)
        watcher_grid.set_margin_end(10)

        # Detector grid layout: (name, abbrev, row, col)
        detector_layout = [
            ("ERROR-CASCADE", "ERR-CASC", 0, 0),
            ("YOLO",          "YOLO",     0, 1),
            ("THRASH",        "THRSH",    0, 2),
            ("LOOP",          "LOOP",     1, 0),
            ("DRIFT",         "DRIFT",    1, 1),
            ("ANTI-PATTERN",  "ANTI",     1, 2),
            ("READ-STORM",    "READ-S",   2, 0),
            ("STALL",         "STALL",    2, 1),
            ("SKILL-SUGGEST", "SKILL",    2, 2),
        ]

        self._watcher_slots = {}
        for det_name, abbrev, row_idx, col_idx in detector_layout:
            cell = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

            dot = Gtk.Label(label="\u25CF")
            dot.get_style_context().add_class("watcher-dot-inactive")
            cell.pack_start(dot, False, False, 0)

            name_lbl = Gtk.Label(label=abbrev, xalign=0)
            name_lbl.get_style_context().add_class("watcher-name")
            cell.pack_start(name_lbl, False, False, 0)

            watcher_grid.attach(cell, col_idx, row_idx, 1, 1)
            self._watcher_slots[det_name] = {"dot": dot, "label": name_lbl}

        watcher_frame.add(watcher_grid)
        vbox.pack_start(watcher_frame, False, False, 4)

        # Set up Gio.FileMonitor on specific sidecar log file (not whole /tmp)
        self._sidecar_monitor = None
        try:
            gio_file = Gio.File.new_for_path("/tmp/claude-sidecar.log")
            self._sidecar_monitor = gio_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._sidecar_monitor.connect("changed", self._on_sidecar_changed)
        except Exception:
            pass

        # --- Diagrams (compact chip-style FlowBox) ---
        diagrams_label = Gtk.Label(label="Diagramme", xalign=0)
        diagrams_label.get_style_context().add_class("section-title")
        vbox.pack_start(diagrams_label, False, False, 4)

        diagrams_flow = Gtk.FlowBox()
        diagrams_flow.set_max_children_per_line(5)
        diagrams_flow.set_min_children_per_line(3)
        diagrams_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        diagrams_flow.set_homogeneous(False)
        diagrams_flow.set_row_spacing(4)
        diagrams_flow.set_column_spacing(6)

        _DIAGRAMS = [
            ("Lifecycle", "claude-session-lifecycle.html"),
            ("Delegation", "claude-delegation-architecture.html"),
            ("Skills", "claude-skill-ecosystem.html"),
            ("Kosten", "claude-cost-dashboard.html"),
            ("Oktopus", "oktopus-visual.html"),
            ("UX Guide", "panel-ux-guide.html"),
        ]
        _DIAGRAMS_DIR = str(Path.home() / ".agent" / "diagrams")
        for label, filename in _DIAGRAMS:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("shortcut-btn")
            btn.get_style_context().add_class("shortcut-docs")
            filepath = f"{_DIAGRAMS_DIR}/{filename}"
            btn.connect("clicked", lambda _, p=filepath: _open_path(p))
            btn.set_tooltip_text(filename)
            diagrams_flow.add(btn)

        vbox.pack_start(diagrams_flow, False, False, 0)

        # --- Recent Sessions (pre-created fixed rows) ---
        sessions_label = Gtk.Label(label="Letzte Sessions", xalign=0)
        sessions_label.get_style_context().add_class("section-title")
        vbox.pack_start(sessions_label, False, False, 4)

        self.sessions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.pack_start(self.sessions_box, False, False, 0)

        self._session_slots = []
        for _ in range(_MAX_SESSION_ROWS):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.get_style_context().add_class("session-row")
            row.set_margin_start(4)
            row.set_margin_end(4)

            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            project_lbl = Gtk.Label(label="", xalign=0)
            project_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            project_lbl.set_max_width_chars(20)
            project_lbl.get_style_context().add_class("section-title")
            info_box.pack_start(project_lbl, False, False, 0)

            preview_lbl = Gtk.Label(label="", xalign=0)
            preview_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            preview_lbl.set_max_width_chars(45)
            preview_lbl.set_opacity(0.7)
            info_box.pack_start(preview_lbl, False, False, 0)

            row.pack_start(info_box, True, True, 0)

            time_lbl = Gtk.Label(label="")
            time_lbl.set_opacity(0.5)
            row.pack_start(time_lbl, False, False, 0)

            resume_btn = Gtk.Button(label="Resume")
            resume_btn.set_size_request(70, -1)
            row.pack_start(resume_btn, False, False, 0)

            self.sessions_box.pack_start(row, False, False, 0)
            row.set_no_show_all(True)  # hidden by default until data arrives

            self._session_slots.append({
                "row": row,
                "project": project_lbl,
                "preview": preview_lbl,
                "time": time_lbl,
                "resume": resume_btn,
                "_handler_id": None,
            })

        self._no_sessions_label = Gtk.Label(label="Keine Sessions gefunden")
        self.sessions_box.pack_start(self._no_sessions_label, False, False, 0)

        # --- Shortcuts Grid ---
        shortcuts_label = Gtk.Label(label="Schnellzugriff", xalign=0)
        shortcuts_label.get_style_context().add_class("section-title")
        vbox.pack_start(shortcuts_label, False, False, 8)

        shortcuts_flow = Gtk.FlowBox()
        shortcuts_flow.set_max_children_per_line(5)
        shortcuts_flow.set_min_children_per_line(4)
        shortcuts_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        shortcuts_flow.set_homogeneous(True)
        shortcuts_flow.set_row_spacing(8)
        shortcuts_flow.set_column_spacing(8)

        for sc in SHORTCUTS:
            btn = Gtk.Button()
            btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            btn_box.set_halign(Gtk.Align.CENTER)

            # Try to load icon, fallback to label only
            try:
                icon = Gtk.Image.new_from_icon_name(sc["icon"], Gtk.IconSize.LARGE_TOOLBAR)
                btn_box.pack_start(icon, False, False, 0)
            except Exception:
                pass

            lbl = Gtk.Label(label=sc["label"])
            lbl.set_ellipsize(Pango.EllipsizeMode.END)
            lbl.set_max_width_chars(12)
            btn_box.pack_start(lbl, False, False, 0)

            btn.add(btn_box)
            btn.get_style_context().add_class("shortcut-btn")
            category = sc.get("category", "folder")
            btn.get_style_context().add_class(f"shortcut-{category}")

            # Check path existence for non-command shortcuts and dim if missing
            if "command" not in sc:
                resolved_path = Path(sc["path"]).expanduser()
                if not resolved_path.exists():
                    btn.set_sensitive(False)
                    btn.set_opacity(0.4)
                    btn.set_tooltip_text(f"{sc['tooltip']}  [Pfad nicht gefunden: {sc['path']}]")
                else:
                    btn.set_tooltip_text(sc["tooltip"])
            else:
                btn.set_tooltip_text(sc["tooltip"])

            # Connect click handler
            if "command" in sc:
                btn.connect("clicked", lambda _, cmd=sc["command"]: _run_command(cmd))
            else:
                btn.connect("clicked", lambda _, p=sc["path"]: _open_path(p))

            shortcuts_flow.add(btn)

        vbox.pack_start(shortcuts_flow, False, False, 0)

        # --- Usage Timeline (pre-created fixed rows) ---
        timeline_label = Gtk.Label(label="Usage (letzte 7 Tage)", xalign=0)
        timeline_label.get_style_context().add_class("section-title")
        vbox.pack_start(timeline_label, False, False, 8)

        self.timeline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        vbox.pack_start(self.timeline_box, False, False, 0)

        self._timeline_slots = []
        for _ in range(_MAX_TIMELINE_ROWS):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            date_lbl = Gtk.Label(label="")
            date_lbl.set_width_chars(12)
            date_lbl.set_xalign(0)
            row.pack_start(date_lbl, False, False, 0)

            bar_lbl = Gtk.Label(label="")
            bar_lbl.get_style_context().add_class("monitor-value")
            bar_lbl.set_xalign(0)
            row.pack_start(bar_lbl, True, True, 0)

            count_lbl = Gtk.Label(label="")
            count_lbl.set_opacity(0.6)
            count_lbl.set_width_chars(10)
            count_lbl.set_xalign(1)
            row.pack_start(count_lbl, False, False, 0)

            self.timeline_box.pack_start(row, False, False, 0)
            row.set_no_show_all(True)

            self._timeline_slots.append({
                "row": row,
                "date": date_lbl,
                "bar": bar_lbl,
                "count": count_lbl,
            })

        # --- Skills Section (expandable by category) ---
        skills_header = Gtk.Label(label="Skills", xalign=0)
        skills_header.get_style_context().add_class("section-title")
        vbox.pack_start(skills_header, False, False, 8)

        for category, skills in SKILLS.items():
            expander = Gtk.Expander(label=f"  {category}  ({len(skills)} Skills)")
            expander.set_margin_start(4)

            skill_grid = Gtk.Grid()
            skill_grid.set_column_spacing(12)
            skill_grid.set_row_spacing(4)
            skill_grid.set_margin_top(6)
            skill_grid.set_margin_bottom(6)
            skill_grid.set_margin_start(16)

            for i, (name, cost, desc) in enumerate(skills):
                name_lbl = Gtk.Label(label=name, xalign=0)
                name_lbl.get_style_context().add_class("section-title")
                name_lbl.set_width_chars(18)
                skill_grid.attach(name_lbl, 0, i, 1, 1)

                cost_lbl = Gtk.Label(label=cost, xalign=0)
                cost_lbl.set_opacity(0.6)
                cost_lbl.set_width_chars(10)
                skill_grid.attach(cost_lbl, 1, i, 1, 1)

                desc_lbl = Gtk.Label(label=desc, xalign=0)
                desc_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                desc_lbl.set_max_width_chars(40)
                desc_lbl.set_hexpand(True)
                skill_grid.attach(desc_lbl, 2, i, 1, 1)

            expander.add(skill_grid)
            vbox.pack_start(expander, False, False, 0)

        # --- Archived Skills (from commands-archive/) ---
        archive_dir = Path.home() / ".claude" / "commands-archive"
        commands_dir = Path.home() / ".claude" / "commands"
        archived = sorted(
            f for f in archive_dir.glob("*.md")
            if f.name != "INDEX.md"
        ) if archive_dir.exists() else []

        if archived:
            archive_exp = Gtk.Expander(
                label=f"  Archiviert  ({len(archived)} Skills)"
            )
            archive_exp.set_margin_start(4)

            archive_grid = Gtk.Grid()
            archive_grid.set_column_spacing(12)
            archive_grid.set_row_spacing(4)
            archive_grid.set_margin_top(6)
            archive_grid.set_margin_bottom(6)
            archive_grid.set_margin_start(16)

            for i, skill_file in enumerate(archived):
                name = skill_file.stem
                is_active = (commands_dir / f"{name}.md").exists()

                name_lbl = Gtk.Label(label=f"/{name}", xalign=0)
                name_lbl.set_width_chars(18)
                archive_grid.attach(name_lbl, 0, i, 1, 1)

                btn = Gtk.Button(label="Aktiv" if is_active else "Aktivieren")
                btn.set_sensitive(not is_active)
                btn.connect(
                    "clicked", self._activate_archived_skill,
                    name, archive_dir, commands_dir,
                )
                archive_grid.attach(btn, 1, i, 1, 1)

            archive_exp.add(archive_grid)
            vbox.pack_start(archive_exp, False, False, 0)

        # --- Missed Skills Today ---
        missed_label = Gtk.Label(label="Verpasste Skills (heute)", xalign=0)
        missed_label.get_style_context().add_class("section-title")
        vbox.pack_start(missed_label, False, False, 8)

        self.hub_missed_label = Gtk.Label(label="Lade...", xalign=0)
        self.hub_missed_label.set_margin_start(8)
        vbox.pack_start(self.hub_missed_label, False, False, 0)

        scrolled.add(vbox)
        self.notebook.append_page(scrolled, Gtk.Label(label="Hub"))

        idle_once(self._refresh_hub)

    def _activate_archived_skill(self, button, name, archive_dir, commands_dir):
        """Copy a skill from commands-archive/ to commands/ to activate it."""
        src = archive_dir / f"{name}.md"
        dst = commands_dir / f"{name}.md"
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            button.set_label("Aktiviert!")
            button.set_sensitive(False)

    def _refresh_hub(self) -> bool:
        """Refresh hub tab data. Updates fixed labels — no widget rebuild."""
        try:
            # Quick stats
            cost_data = get_daily_cost()
            if "error" not in cost_data:
                self.hub_cost_label.set_text(format_cost(cost_data["cost_estimate_usd"]))
                self.hub_calls_label.set_text(str(cost_data["total_calls"]))
            else:
                self.hub_cost_label.set_text("N/A")
                self.hub_calls_label.set_text("N/A")

            active = get_active_sessions()
            self.hub_sessions_label.set_text(str(len(active)))

            # Provider cost breakdown
            provider_costs = get_provider_costs()
            total_provider = sum(provider_costs.values()) or 0.001  # avoid div by zero

            for provider_name, slot in self._provider_slots.items():
                cost = provider_costs.get(provider_name, 0.0)
                if cost > 0:
                    pct = cost / total_provider * 100
                    bar_len = max(1, int(pct / 100 * 30))
                    bar_text = "\u2588" * bar_len
                    color = slot["color"]
                    slot["bar"].set_markup(f'<span foreground="{color}">{bar_text}</span>')
                    slot["cost"].set_text(f"${cost:.4f} ({pct:.0f}%)")
                    slot["row"].show_all()
                else:
                    slot["row"].hide()

            # Recent sessions — update fixed slots
            sessions = get_recent_sessions(_MAX_SESSION_ROWS)
            for i, slot in enumerate(self._session_slots):
                if i < len(sessions):
                    s = sessions[i]
                    slot["project"].set_text(s["project"])
                    slot["preview"].set_text(s["preview"])
                    slot["time"].set_text(s["time_str"])

                    # Reconnect resume button to new session ID
                    if slot["_handler_id"] is not None:
                        slot["resume"].disconnect(slot["_handler_id"])
                    slot["_handler_id"] = slot["resume"].connect(
                        "clicked",
                        lambda _, sid=s["session_id"]: _resume_session(sid),
                    )
                    slot["row"].show_all()
                else:
                    slot["row"].hide()

            if sessions:
                self._no_sessions_label.hide()
            else:
                self._no_sessions_label.show()

            # Usage timeline — update fixed slots (token-based costs)
            timeline = get_usage_timeline()
            max_cost = max((t["cost_est"] for t in timeline), default=0.01) or 0.01

            for i, slot in enumerate(self._timeline_slots):
                if i < len(timeline):
                    t = timeline[i]
                    slot["date"].set_text(t["date"])
                    bar_len = max(1, int((t["cost_est"] / max_cost) * 30)) if t["cost_est"] > 0 else 0
                    slot["bar"].set_text("\u2588" * bar_len)
                    slot["count"].set_text(f"${t['cost_est']:.2f}")
                    slot["row"].show_all()
                else:
                    slot["row"].hide()

            # Missed skills in hub
            missed = get_missed_skills_summary()
            if missed:
                lines = [f"  {skill}: {count}x verpasst" for skill, count in missed[:5]]
                self.hub_missed_label.set_text("\n".join(lines))
            else:
                self.hub_missed_label.set_text("Keine verpassten Skills heute")

            # Sidecar watcher
            sidecar = get_sidecar_status()
            self._update_watcher(sidecar)
        except Exception:
            pass  # keep timer alive
        return True  # keep 30s timer alive

    def _update_watcher(self, data: dict) -> None:
        """Update watcher dot colors and tooltips from sidecar status dict."""
        detectors = data.get("detectors", {})
        for det_name, slot in self._watcher_slots.items():
            det = detectors.get(det_name, {})
            active = det.get("active", False)
            severity = det.get("severity", "info")
            last_seen = det.get("last_seen")
            count = det.get("count", 0)

            # Remove all existing style classes from dot
            ctx = slot["dot"].get_style_context()
            for cls in ("watcher-dot-critical", "watcher-dot-warning",
                        "watcher-dot-info", "watcher-dot-inactive"):
                ctx.remove_class(cls)

            if active:
                ctx.add_class(f"watcher-dot-{severity}")
                tooltip = f"{det_name}: {count}x"
                if last_seen:
                    tooltip += f" (zuletzt {last_seen})"
            else:
                ctx.add_class("watcher-dot-inactive")
                tooltip = f"{det_name}: inaktiv"

            slot["dot"].set_tooltip_text(tooltip)
            slot["label"].set_tooltip_text(tooltip)

    def _on_sidecar_changed(self, _monitor, file_obj, _other, event_type) -> None:
        """Called when a file in /tmp changes. Trigger watcher refresh if sidecar file."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            return
        name = file_obj.get_basename() if file_obj else ""
        if name and (name.startswith("sidecar-") and name.endswith(".json")):
            GLib.idle_add(self._refresh_watcher_once)

    def _refresh_watcher_once(self) -> bool:
        """One-shot idle callback: refresh watcher section only."""
        try:
            self._update_watcher(get_sidecar_status())
        except Exception:
            pass
        return False  # one-shot

    # -----------------------------------------------------------------------
    # Tab 2: Settings
    # -----------------------------------------------------------------------
    def _build_settings_tab(self):
        grid = Gtk.Grid()
        grid.set_column_spacing(15)
        grid.set_row_spacing(18)
        grid.set_margin_top(20)
        grid.set_margin_bottom(20)
        grid.set_margin_start(20)
        grid.set_margin_end(20)

        row = 0

        # --- Model ---
        grid.attach(self._label("Model"), 0, row, 1, 1)
        self.model_combo = Gtk.ComboBoxText()
        models = ["opus", "sonnet", "haiku"]
        for m in models:
            self.model_combo.append_text(m)
        current_model = self.settings.get("model", "opus")
        if current_model in models:
            self.model_combo.set_active(models.index(current_model))
        else:
            self.model_combo.set_active(0)
        self.model_combo.set_hexpand(True)
        grid.attach(self.model_combo, 1, row, 2, 1)

        row += 1

        # --- Autonomie ---
        grid.attach(self._label("Autonomie"), 0, row, 1, 1)
        self.autonomy_combo = Gtk.ComboBoxText()
        for m in ["Balanced", "Sprint", "Conserve"]:
            self.autonomy_combo.append_text(m)
        self.autonomy_combo.set_active(0)
        self.autonomy_combo.set_hexpand(True)
        grid.attach(self.autonomy_combo, 1, row, 2, 1)

        row += 1

        # --- Max Subagents ---
        grid.attach(self._label("Max Subagents"), 0, row, 1, 1)
        self.subagents_adj = Gtk.Adjustment(value=8, lower=4, upper=16, step_increment=1)
        self.subagents_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.subagents_adj
        )
        self.subagents_scale.set_digits(0)
        self.subagents_scale.set_hexpand(True)
        for v in [4, 8, 12, 16]:
            self.subagents_scale.add_mark(v, Gtk.PositionType.BOTTOM, str(v))
        grid.attach(self.subagents_scale, 1, row, 2, 1)

        row += 1

        # --- Tool Budget ---
        grid.attach(self._label("Tool Budget"), 0, row, 1, 1)
        current_budget = int(
            self.settings.get("env", {}).get("SLASH_COMMAND_TOOL_CHAR_BUDGET", "10000")
        )
        self.budget_adj = Gtk.Adjustment(
            value=current_budget, lower=1000, upper=50000, step_increment=1000
        )
        self.budget_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.budget_adj
        )
        self.budget_scale.set_digits(0)
        self.budget_scale.set_hexpand(True)
        for v in [1000, 10000, 25000, 50000]:
            self.budget_scale.add_mark(v, Gtk.PositionType.BOTTOM, f"{v // 1000}k")
        grid.attach(self.budget_scale, 1, row, 2, 1)

        row += 1

        # --- Agent Teams ---
        grid.attach(self._label("Agent Teams"), 0, row, 1, 1)
        self.teams_switch = Gtk.Switch()
        teams_val = self.settings.get("env", {}).get(
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "0"
        )
        self.teams_switch.set_active(teams_val == "1")
        teams_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        teams_box.pack_start(self.teams_switch, False, False, 0)
        teams_status = Gtk.Label(label=" An" if teams_val == "1" else " Aus")
        teams_status.set_opacity(0.6)
        teams_box.pack_start(teams_status, False, False, 5)
        self.teams_switch.connect(
            "notify::active",
            lambda sw, _: teams_status.set_text(" An" if sw.get_active() else " Aus"),
        )
        grid.attach(teams_box, 1, row, 1, 1)

        row += 1

        # --- Voice Service ---
        grid.attach(self._label("Voice Service"), 0, row, 1, 1)
        self.voice_switch = Gtk.Switch()
        # Check if voicemode is running
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "voicemode-edge-tts"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.voice_switch.set_active(result.stdout.strip() == "active")
        except Exception:
            self.voice_switch.set_active(False)
        voice_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        voice_box.pack_start(self.voice_switch, False, False, 0)
        self.voice_status = Gtk.Label(
            label=" Running" if self.voice_switch.get_active() else " Stopped"
        )
        self.voice_status.set_opacity(0.6)
        voice_box.pack_start(self.voice_status, False, False, 5)
        grid.attach(voice_box, 1, row, 1, 1)

        row += 1

        # --- Separator ---
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep, 0, row, 3, 1)
        row += 1

        # --- Section: MCP Servers ---
        mcp_header = Gtk.Label(label="MCP Server", xalign=0)
        mcp_header.get_style_context().add_class("section-title")
        grid.attach(mcp_header, 0, row, 3, 1)
        row += 1

        mcp_servers = self.settings.get("mcpServers", {})
        self.mcp_switches = {}
        for server_name in ["opencode", "gemini", "voicemode"]:
            grid.attach(self._label(f"  {server_name}"), 0, row, 1, 1)
            sw = Gtk.Switch()
            sw.set_active(server_name in mcp_servers)
            sw_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            sw_box.pack_start(sw, False, False, 0)
            status_lbl = Gtk.Label(
                label=" Aktiv" if server_name in mcp_servers else " Aus"
            )
            status_lbl.set_opacity(0.6)
            sw_box.pack_start(status_lbl, False, False, 5)
            sw.connect(
                "notify::active",
                lambda s, _, l=status_lbl: l.set_text(
                    " Aktiv" if s.get_active() else " Aus"
                ),
            )
            grid.attach(sw_box, 1, row, 1, 1)
            self.mcp_switches[server_name] = sw
            row += 1

        # --- Enable All Project MCP Servers ---
        grid.attach(self._label("Projekt-MCPs"), 0, row, 1, 1)
        self.project_mcp_switch = Gtk.Switch()
        self.project_mcp_switch.set_active(
            self.settings.get("enableAllProjectMcpServers", False)
        )
        proj_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        proj_box.pack_start(self.project_mcp_switch, False, False, 0)
        proj_lbl = Gtk.Label(label=" Alle Projekt-MCPs erlauben")
        proj_lbl.set_opacity(0.6)
        proj_box.pack_start(proj_lbl, False, False, 5)
        grid.attach(proj_box, 1, row, 2, 1)
        row += 1

        # --- Separator ---
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep2, 0, row, 3, 1)
        row += 1

        # --- Section: Erweitert ---
        adv_header = Gtk.Label(label="Erweitert", xalign=0)
        adv_header.get_style_context().add_class("section-title")
        grid.attach(adv_header, 0, row, 3, 1)
        row += 1

        # Tool Search
        grid.attach(self._label("Tool Search"), 0, row, 1, 1)
        self.tool_search_combo = Gtk.ComboBoxText()
        for opt in ["auto:5", "auto:3", "auto:10", "disabled"]:
            self.tool_search_combo.append_text(opt)
        current_ts = self.settings.get("env", {}).get("ENABLE_TOOL_SEARCH", "auto:5")
        ts_options = ["auto:5", "auto:3", "auto:10", "disabled"]
        if current_ts in ts_options:
            self.tool_search_combo.set_active(ts_options.index(current_ts))
        else:
            self.tool_search_combo.set_active(0)
        self.tool_search_combo.set_hexpand(True)
        grid.attach(self.tool_search_combo, 1, row, 2, 1)
        row += 1

        # Status Line toggle
        grid.attach(self._label("Status Line"), 0, row, 1, 1)
        self.statusline_switch = Gtk.Switch()
        self.statusline_switch.set_active("statusLine" in self.settings)
        sl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sl_box.pack_start(self.statusline_switch, False, False, 0)
        sl_lbl = Gtk.Label(label=" Custom Statusline")
        sl_lbl.set_opacity(0.6)
        sl_box.pack_start(sl_lbl, False, False, 5)
        grid.attach(sl_box, 1, row, 2, 1)

        # Wrap grid in ScrolledWindow (many settings now)
        settings_scroll = Gtk.ScrolledWindow()
        settings_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        settings_scroll.add(grid)
        self.notebook.append_page(settings_scroll, Gtk.Label(label="Settings"))

    # -----------------------------------------------------------------------
    # Tab 3: Hooks
    # -----------------------------------------------------------------------
    def _build_hooks_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        self.hook_widgets = []
        hooks = read_hook_list()

        for hook_info in hooks:
            frame = Gtk.Frame(
                label=f"  {hook_info['event']}  {hook_info['short_name']}  "
            )
            frame_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            frame_box.set_margin_top(6)
            frame_box.set_margin_bottom(6)
            frame_box.set_margin_start(8)
            frame_box.set_margin_end(8)

            # Matcher
            if hook_info["matcher"]:
                matcher_lbl = Gtk.Label(label=f"[{hook_info['matcher']}]")
                matcher_lbl.set_opacity(0.5)
                frame_box.pack_start(matcher_lbl, False, False, 0)

            # Timeout
            frame_box.pack_start(Gtk.Label(label="Timeout:"), False, False, 0)
            timeout_adj = Gtk.Adjustment(
                value=hook_info["timeout"], lower=3, upper=120, step_increment=1
            )
            timeout_spin = Gtk.SpinButton(
                adjustment=timeout_adj, climb_rate=1, digits=0
            )
            timeout_spin.set_width_chars(4)
            frame_box.pack_start(timeout_spin, False, False, 0)
            frame_box.pack_start(Gtk.Label(label="s"), False, False, 0)

            # Async
            frame_box.pack_start(Gtk.Label(label="Async:"), False, False, 5)
            async_switch = Gtk.Switch()
            async_switch.set_active(hook_info["async_"])
            frame_box.pack_start(async_switch, False, False, 0)

            frame.add(frame_box)
            vbox.pack_start(frame, False, False, 0)

            self.hook_widgets.append({
                "info": hook_info,
                "timeout_adj": timeout_adj,
                "async_switch": async_switch,
            })

        # --- Coaching Rate Limit ---
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep, False, False, 5)

        coach_frame = Gtk.Frame(label="  Coaching Rate Limit  ")
        coach_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        coach_box.set_margin_top(8)
        coach_box.set_margin_bottom(8)
        coach_box.set_margin_start(10)
        coach_box.set_margin_end(10)

        rate = read_coaching_rate_limit()
        self.coaching_adj = Gtk.Adjustment(
            value=rate, lower=60, upper=600, step_increment=30
        )
        self.coaching_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.coaching_adj
        )
        self.coaching_scale.set_digits(0)
        self.coaching_scale.set_hexpand(True)
        for v in [60, 180, 300, 600]:
            self.coaching_scale.add_mark(v, Gtk.PositionType.BOTTOM, f"{v}s")

        coach_box.pack_start(Gtk.Label(label="Rate:"), False, False, 0)
        coach_box.pack_start(self.coaching_scale, True, True, 0)

        coach_frame.add(coach_box)
        vbox.pack_start(coach_frame, False, False, 0)

        scrolled.add(vbox)
        self.notebook.append_page(scrolled, Gtk.Label(label="Hooks"))

    # -----------------------------------------------------------------------
    # Tab 4: Monitor
    # -----------------------------------------------------------------------
    def _build_monitor_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # Daily Cost
        cost_frame = Gtk.Frame(label="  Heutige Kosten  ")
        self.cost_label = Gtk.Label(label="Lade...")
        self.cost_label.get_style_context().add_class("monitor-value")
        self.cost_label.set_margin_top(10)
        self.cost_label.set_margin_bottom(10)
        cost_frame.add(self.cost_label)
        vbox.pack_start(cost_frame, False, False, 0)

        # Active Sessions
        sessions_frame = Gtk.Frame(label="  Aktive Sessions  ")
        self.monitor_sessions_label = Gtk.Label(label="Lade...")
        self.monitor_sessions_label.set_margin_top(8)
        self.monitor_sessions_label.set_margin_bottom(8)
        self.monitor_sessions_label.set_xalign(0)
        self.monitor_sessions_label.set_margin_start(10)
        sessions_frame.add(self.monitor_sessions_label)
        vbox.pack_start(sessions_frame, False, False, 0)

        # Top Tools
        tools_frame = Gtk.Frame(label="  Top 5 Tools  ")
        self.tools_label = Gtk.Label(label="Lade...")
        self.tools_label.get_style_context().add_class("monitor-value")
        self.tools_label.set_margin_top(8)
        self.tools_label.set_margin_bottom(8)
        self.tools_label.set_xalign(0)
        self.tools_label.set_margin_start(10)
        tools_frame.add(self.tools_label)
        vbox.pack_start(tools_frame, False, False, 0)

        # Missed Skills
        skills_frame = Gtk.Frame(label="  Verpasste Skills  ")
        self.skills_label = Gtk.Label(label="Lade...")
        self.skills_label.set_margin_top(8)
        self.skills_label.set_margin_bottom(8)
        self.skills_label.set_xalign(0)
        self.skills_label.set_margin_start(10)
        skills_frame.add(self.skills_label)
        vbox.pack_start(skills_frame, False, False, 0)

        scrolled.add(vbox)
        self.notebook.append_page(scrolled, Gtk.Label(label="Monitor"))

        idle_once(self._refresh_monitor)

    def _refresh_monitor(self) -> bool:
        """Refresh monitor tab data only. Does NOT call _refresh_hub."""
        try:
            # Cost
            cost_data = get_daily_cost()
            if "error" not in cost_data:
                self.cost_label.set_text(
                    f"{format_cost(cost_data['cost_estimate_usd'])}  "
                    f"({cost_data['total_calls']} Calls, {cost_data['unique_tools']} Tools)"
                )
            else:
                self.cost_label.set_text(cost_data.get("error", "N/A"))

            # Sessions
            sessions = get_active_sessions()
            if sessions:
                lines = [f"  {s['project']}  ({s['age_min']}min)" for s in sessions[:6]]
                self.monitor_sessions_label.set_text("\n".join(lines))
            else:
                self.monitor_sessions_label.set_text("Keine aktiven Sessions")

            # Top Tools
            tools = get_top_tools(5)
            if tools:
                lines = [f"  {name}: {count}x" for name, count in tools]
                self.tools_label.set_text("\n".join(lines))
            else:
                self.tools_label.set_text("Keine Daten")

            # Missed Skills
            missed = get_missed_skills_summary()
            if missed:
                lines = [f"  {skill}: {count}x" for skill, count in missed]
                self.skills_label.set_text("\n".join(lines))
            else:
                self.skills_label.set_text("Keine verpassten Skills heute")
        except Exception:
            pass  # keep timer alive
        return True  # keep 30s timer alive

    # -----------------------------------------------------------------------
    # Tab 5: Cost — Provider breakdown + usage timeline
    # -----------------------------------------------------------------------
    def _build_cost_tab(self):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # --- Total Daily Cost (big) ---
        self.cost_total_label = Gtk.Label(label="...")
        self.cost_total_label.get_style_context().add_class("cost-value")
        total_sub = Gtk.Label(label="Heutige Gesamtkosten")
        total_sub.get_style_context().add_class("stat-label")
        total_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        total_box.set_halign(Gtk.Align.CENTER)
        total_box.pack_start(self.cost_total_label, False, False, 0)
        total_box.pack_start(total_sub, False, False, 0)
        vbox.pack_start(total_box, False, False, 8)

        # --- Provider Breakdown ---
        provider_frame = Gtk.Frame(label="  Provider-Breakdown  ")
        provider_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        provider_inner.set_margin_top(10)
        provider_inner.set_margin_bottom(10)
        provider_inner.set_margin_start(12)
        provider_inner.set_margin_end(12)

        self._cost_provider_slots = {}
        provider_meta = [
            ("opus", "#89b4fa", "Claude Opus 4.6 ($5/$6.25/$0.50/$25)"),
            ("sonnet", "#b4befe", "Claude Sonnet 4.6 ($3/$3.75/$0.30/$15)"),
            ("haiku", "#94e2d5", "Claude Haiku 4.5 ($1/$1.25/$0.10/$5)"),
            ("minimax", "#a6e3a1", "MiniMax M2.5 (~$0.05/1M)"),
            ("codex", "#fab387", "Codex/OpenCode (~$0.10/1M)"),
            ("gemini", "#f9e2af", "Gemini Flash (~$0.10/1M)"),
        ]
        for name, color, desc in provider_meta:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            name_lbl = Gtk.Label(xalign=0)
            name_lbl.set_markup(f'<span foreground="{color}"><b>{name.capitalize()}</b></span>')
            name_lbl.set_width_chars(12)
            row.pack_start(name_lbl, False, False, 0)

            bar_lbl = Gtk.Label(label="")
            bar_lbl.set_xalign(0)
            row.pack_start(bar_lbl, True, True, 0)

            cost_lbl = Gtk.Label(label="—", xalign=1)
            cost_lbl.set_width_chars(16)
            row.pack_start(cost_lbl, False, False, 0)

            desc_lbl = Gtk.Label(label=desc)
            desc_lbl.set_opacity(0.5)
            desc_lbl.set_xalign(0)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.pack_start(row, False, False, 0)
            box.pack_start(desc_lbl, False, False, 0)

            provider_inner.pack_start(box, False, False, 0)
            self._cost_provider_slots[name] = {
                "bar": bar_lbl,
                "cost": cost_lbl,
                "color": color,
            }

        provider_frame.add(provider_inner)
        vbox.pack_start(provider_frame, False, False, 0)

        # --- Calls + Tools Summary ---
        stats_frame = Gtk.Frame(label="  Heutige Nutzung  ")
        stats_inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        stats_inner.set_halign(Gtk.Align.CENTER)
        stats_inner.set_margin_top(10)
        stats_inner.set_margin_bottom(10)

        self.cost_calls_label = Gtk.Label(label="...")
        self.cost_calls_label.get_style_context().add_class("stat-value")
        calls_sub = Gtk.Label(label="Tool Calls")
        calls_sub.get_style_context().add_class("stat-label")
        col1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col1.pack_start(self.cost_calls_label, False, False, 0)
        col1.pack_start(calls_sub, False, False, 0)
        stats_inner.pack_start(col1, True, True, 0)

        self.cost_tools_label = Gtk.Label(label="...")
        self.cost_tools_label.get_style_context().add_class("stat-value")
        tools_sub = Gtk.Label(label="Unique Tools")
        tools_sub.get_style_context().add_class("stat-label")
        col2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col2.pack_start(self.cost_tools_label, False, False, 0)
        col2.pack_start(tools_sub, False, False, 0)
        stats_inner.pack_start(col2, True, True, 0)

        stats_frame.add(stats_inner)
        vbox.pack_start(stats_frame, False, False, 0)

        # --- Skill Usage ---
        skill_frame = Gtk.Frame(label="  Skill-Nutzung  ")
        self.skill_usage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.skill_usage_box.set_margin_top(8)
        self.skill_usage_box.set_margin_bottom(8)
        self.skill_usage_box.set_margin_start(12)
        self.skill_usage_box.set_margin_end(12)

        _MAX_SKILL_ROWS = 8
        self._skill_slots = []
        for _ in range(_MAX_SKILL_ROWS):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name_lbl = Gtk.Label(label="", xalign=0)
            name_lbl.set_width_chars(18)
            row.pack_start(name_lbl, False, False, 0)

            today_lbl = Gtk.Label(label="", xalign=1)
            today_lbl.set_width_chars(12)
            row.pack_start(today_lbl, True, True, 0)

            week_lbl = Gtk.Label(label="", xalign=1)
            week_lbl.set_width_chars(10)
            week_lbl.set_opacity(0.6)
            row.pack_start(week_lbl, False, False, 0)

            self.skill_usage_box.pack_start(row, False, False, 0)
            row.set_no_show_all(True)
            self._skill_slots.append({
                "row": row, "name": name_lbl, "today": today_lbl, "week": week_lbl,
            })

        self.skill_no_data_label = Gtk.Label(label="Keine Skills genutzt")
        self.skill_no_data_label.set_opacity(0.4)
        self.skill_usage_box.pack_start(self.skill_no_data_label, False, False, 4)

        skill_frame.add(self.skill_usage_box)
        vbox.pack_start(skill_frame, False, False, 0)

        # --- 7-Day Timeline ---
        timeline_frame = Gtk.Frame(label="  Letzte 7 Tage  ")
        self.cost_timeline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.cost_timeline_box.set_margin_top(8)
        self.cost_timeline_box.set_margin_bottom(8)
        self.cost_timeline_box.set_margin_start(10)
        self.cost_timeline_box.set_margin_end(10)

        self._cost_timeline_slots = []
        for _ in range(7):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            date_lbl = Gtk.Label(label="", xalign=0)
            date_lbl.set_width_chars(12)
            row.pack_start(date_lbl, False, False, 0)

            bar_lbl = Gtk.Label(label="")
            bar_lbl.get_style_context().add_class("monitor-value")
            bar_lbl.set_xalign(0)
            row.pack_start(bar_lbl, True, True, 0)

            info_lbl = Gtk.Label(label="", xalign=1)
            info_lbl.set_width_chars(18)
            info_lbl.set_opacity(0.6)
            row.pack_start(info_lbl, False, False, 0)

            self.cost_timeline_box.pack_start(row, False, False, 0)
            row.set_no_show_all(True)
            self._cost_timeline_slots.append({
                "row": row, "date": date_lbl, "bar": bar_lbl, "info": info_lbl,
            })

        timeline_frame.add(self.cost_timeline_box)
        vbox.pack_start(timeline_frame, False, False, 0)

        scrolled.add(vbox)
        self.notebook.append_page(scrolled, Gtk.Label(label="Cost"))

        idle_once(self._refresh_cost)

    def _refresh_cost(self) -> bool:
        """Refresh cost tab data with real token-based costs."""
        try:
            cost_data = get_daily_cost()
            if "error" not in cost_data:
                self.cost_total_label.set_text(format_cost(cost_data["cost_estimate_usd"]))
                self.cost_calls_label.set_text(str(cost_data["total_calls"]))
                self.cost_tools_label.set_text(str(cost_data.get("unique_tools", "?")))
            else:
                self.cost_total_label.set_text("N/A")

            # Provider breakdown with token details
            provider_costs = get_provider_costs()
            total_p = sum(provider_costs.values()) or 0.001
            anthropic_data = get_anthropic_session_cost()
            anthropic_models = anthropic_data.get("models", {})
            # Providers with estimated costs get "~" prefix
            estimated_providers = {"codex", "gemini"}
            model_tiers = {"opus", "sonnet", "haiku"}

            for name, slot in self._cost_provider_slots.items():
                cost = provider_costs.get(name, 0.0)
                if cost > 0:
                    pct = cost / total_p * 100
                    bar_len = max(1, int(pct / 100 * 25))
                    slot["bar"].set_markup(
                        f'<span foreground="{slot["color"]}">{"\u2588" * bar_len}</span>'
                    )
                    prefix = "~" if name in estimated_providers else ""
                    if name in model_tiers and name in anthropic_models:
                        md = anthropic_models[name]
                        inp_k = md.get("input_tokens", 0) / 1000
                        cache_k = md.get("cache_read_tokens", 0) / 1000
                        out_k = md.get("output_tokens", 0) / 1000
                        slot["cost"].set_text(
                            f"${cost:.2f} ({inp_k:.0f}K in / {cache_k:.0f}K cache / {out_k:.0f}K out)"
                        )
                    else:
                        slot["cost"].set_text(f"{prefix}${cost:.4f} ({pct:.0f}%)")
                else:
                    slot["bar"].set_text("")
                    slot["cost"].set_text("—")

            # Skill usage — today + weekly totals
            today_str = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%d")
            skills_today = get_skill_usage(days=1).get(today_str, {})
            skills_week = get_skill_usage(days=7)

            # Aggregate weekly totals per skill
            week_totals: dict[str, int] = {}
            for day_skills in skills_week.values():
                for sk, cnt in day_skills.items():
                    week_totals[sk] = week_totals.get(sk, 0) + cnt

            # Sort by today's count (desc), then by weekly count
            all_skills = sorted(
                set(skills_today) | set(week_totals),
                key=lambda s: (skills_today.get(s, 0), week_totals.get(s, 0)),
                reverse=True,
            )

            has_skills = bool(all_skills)
            self.skill_no_data_label.set_visible(not has_skills)

            for i, slot in enumerate(self._skill_slots):
                if i < len(all_skills):
                    sk = all_skills[i]
                    t_cnt = skills_today.get(sk, 0)
                    w_cnt = week_totals.get(sk, 0)
                    slot["name"].set_markup(f'<span foreground="#cba6f7"><b>/{sk}</b></span>')
                    slot["today"].set_text(f"{t_cnt}x heute" if t_cnt > 0 else "—")
                    slot["week"].set_text(f"{w_cnt}x/Wo")
                    slot["row"].show_all()
                else:
                    slot["row"].hide()

            # Timeline — token-based costs
            timeline = get_usage_timeline()
            max_cost = max((t["cost_est"] for t in timeline), default=0.01) or 0.01
            for i, slot in enumerate(self._cost_timeline_slots):
                if i < len(timeline):
                    t = timeline[i]
                    slot["date"].set_text(t["date"])
                    bar_len = max(1, int((t["cost_est"] / max_cost) * 25))
                    slot["bar"].set_markup(
                        f'<span foreground="#89b4fa">{"\u2588" * bar_len}</span>'
                    )
                    slot["info"].set_text(
                        f'{t["calls"]} calls  ${t["cost_est"]:.3f}'
                    )
                    slot["row"].show_all()
                else:
                    slot["row"].hide()
        except Exception:
            pass
        return True

    # -----------------------------------------------------------------------
    # Save / Reset
    # -----------------------------------------------------------------------
    def on_save(self, _button):
        """Save all settings from the panel to config files."""
        try:
            settings = read_settings()

            # Model
            model = self.model_combo.get_active_text()
            if model:
                settings["model"] = model

            # Env vars
            if "env" not in settings:
                settings["env"] = {}

            # Autonomy mode (custom env var)
            autonomy = self.autonomy_combo.get_active_text()
            if autonomy:
                settings["env"]["CLAUDE_AUTONOMY_MODE"] = autonomy.lower()

            # Max subagents (custom env var)
            subagents = int(self.subagents_adj.get_value())
            settings["env"]["CLAUDE_MAX_SUBAGENTS"] = str(subagents)

            # Tool Budget
            budget = int(self.budget_adj.get_value())
            settings["env"]["SLASH_COMMAND_TOOL_CHAR_BUDGET"] = str(budget)

            # Agent Teams
            settings["env"]["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = (
                "1" if self.teams_switch.get_active() else "0"
            )

            # Tool Search
            ts_val = self.tool_search_combo.get_active_text()
            if ts_val:
                settings["env"]["ENABLE_TOOL_SEARCH"] = ts_val

            # enableAllProjectMcpServers
            settings["enableAllProjectMcpServers"] = (
                self.project_mcp_switch.get_active()
            )

            # Status Line
            if self.statusline_switch.get_active():
                if "statusLine" not in settings:
                    settings["statusLine"] = {
                        "type": "command",
                        "command": "~/.claude/statusline.sh",
                    }
            else:
                settings.pop("statusLine", None)

            # MCP Server toggles — store disabled servers separately, never delete
            if "mcpServers" in settings:
                if "_disabled" not in settings:
                    settings["_disabled"] = {}
                for name, sw in self.mcp_switches.items():
                    if not sw.get_active():
                        # Move to _disabled (preserve config for re-enable)
                        if name in settings["mcpServers"]:
                            settings["_disabled"][name] = settings["mcpServers"].pop(name)
                    else:
                        # Re-enable: move back from _disabled if it was there
                        if name in settings.get("_disabled", {}):
                            settings["mcpServers"][name] = settings["_disabled"].pop(name)
                # Clean up empty _disabled
                if not settings["_disabled"]:
                    del settings["_disabled"]

                # Sync voicemode systemd service with MCP toggle state
                if "voicemode" in settings.get("mcpServers", {}):
                    subprocess.Popen(["systemctl", "--user", "start", "voicemode-edge-tts"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif "voicemode" in settings.get("_disabled", {}):
                    subprocess.Popen(["systemctl", "--user", "stop", "voicemode-edge-tts"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Hook timeouts and async flags
            for hw in self.hook_widgets:
                info = hw["info"]
                new_timeout = int(hw["timeout_adj"].get_value())
                new_async = hw["async_switch"].get_active()

                event = info["event"]
                gi_idx = info["group_index"]

                if event in settings.get("hooks", {}) and gi_idx < len(
                    settings["hooks"][event]
                ):
                    group = settings["hooks"][event][gi_idx]
                    for hook in group.get("hooks", []):
                        hook["timeout"] = new_timeout
                        if new_async:
                            hook["async"] = True
                        elif "async" in hook:
                            del hook["async"]

            write_settings(settings)

            # Coaching rate limit
            new_rate = int(self.coaching_adj.get_value())
            write_coaching_rate_limit(new_rate)

            # Voice service (fire-and-forget to avoid blocking UI)
            try:
                if self.voice_switch.get_active():
                    subprocess.Popen(
                        ["systemctl", "--user", "start", "voicemode-edge-tts"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.voice_status.set_text(" Running")
                else:
                    subprocess.Popen(
                        ["systemctl", "--user", "stop", "voicemode-edge-tts"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    self.voice_status.set_text(" Stopped")
            except Exception:
                pass

            self._set_status("Gespeichert!", "status-saved")

        except Exception as e:
            self._set_status(f"Fehler: {e}", "status-error")

    def on_reset(self, _button):
        """Reload settings from disk and reset ALL UI widgets."""
        self.settings = read_settings()
        env = self.settings.get("env", {})

        # Model
        models = ["opus", "sonnet", "haiku"]
        current = self.settings.get("model", "opus")
        if current in models:
            self.model_combo.set_active(models.index(current))

        # Autonomy — items are capitalized, map via lowercase index
        autonomy_modes = ["balanced", "sprint", "conserve"]
        current_auto = env.get("CLAUDE_AUTONOMY_MODE", "balanced").lower()
        if current_auto in autonomy_modes:
            self.autonomy_combo.set_active(autonomy_modes.index(current_auto))
        else:
            self.autonomy_combo.set_active(0)  # fallback to "Balanced"

        # Max Subagents
        self.subagents_adj.set_value(int(env.get("CLAUDE_MAX_SUBAGENTS", "8")))

        # Budget
        self.budget_adj.set_value(int(env.get("SLASH_COMMAND_TOOL_CHAR_BUDGET", "10000")))

        # Teams
        self.teams_switch.set_active(env.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "0") == "1")

        # Tool Search
        ts_options = ["auto:5", "auto:10", "manual", "off"]
        current_ts = env.get("ENABLE_TOOL_SEARCH", "auto:5")
        if current_ts in ts_options:
            self.tool_search_combo.set_active(ts_options.index(current_ts))

        # Project MCP
        self.project_mcp_switch.set_active(self.settings.get("enableAllProjectMcpServers", False))

        # Status Line
        self.statusline_switch.set_active("statusLine" in self.settings)

        # MCP Switches — all active (config = enabled)
        mcp_servers = self.settings.get("mcpServers", {})
        for name, sw in self.mcp_switches.items():
            sw.set_active(name in mcp_servers)

        # Coaching
        rate = read_coaching_rate_limit()
        self.coaching_adj.set_value(rate)

        self._set_status("Zurueckgesetzt", "status-saved")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _label(text: str) -> Gtk.Label:
        """Create a left-aligned label for settings rows."""
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.set_width_chars(16)
        return lbl

    def _set_status(self, text: str, css_class: str = "") -> None:
        """Set status bar text with optional CSS class, auto-clears after 4s."""
        self.status_label.set_text(text)
        ctx = self.status_label.get_style_context()
        for cls in ["status-saved", "status-error"]:
            ctx.remove_class(cls)
        if css_class:
            ctx.add_class(css_class)
        GLib.timeout_add_seconds(4, self._clear_status)

    def _clear_status(self) -> bool:
        self.status_label.set_text("")
        return False


def _build_tray_indicator(win: ControlPanel) -> AyatanaAppIndicator3.Indicator:
    """Create a system tray icon with show/hide/quit menu and quick-stats."""
    from pathlib import Path as P
    icon_dir = str(P(__file__).resolve().parent)

    indicator = AyatanaAppIndicator3.Indicator.new(
        "claude-code-panel",
        "claude-panel-icon",
        AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_icon_theme_path(icon_dir)
    indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
    indicator.set_title("Claude Code Panel")

    menu = Gtk.Menu()

    # --- Quick Stats (updated on menu open) ---
    item_cost = Gtk.MenuItem(label="Kosten heute: ...")
    item_cost.set_sensitive(False)
    menu.append(item_cost)

    item_sessions = Gtk.MenuItem(label="Aktive Sessions: ...")
    item_sessions.set_sensitive(False)
    menu.append(item_sessions)

    menu.append(Gtk.SeparatorMenuItem())

    # --- Verstecken (window auto-shows on menu open, so only hide needed) ---
    item_hide = Gtk.MenuItem(label="Verstecken")
    item_hide.connect("activate", lambda _: win.hide())
    menu.append(item_hide)

    # --- Open specific tabs ---
    item_settings = Gtk.MenuItem(label="Einstellungen")
    def _show_tab(_, tab_idx):
        win.show_all()
        win.present()
        win.notebook.set_current_page(tab_idx)
    item_settings.connect("activate", _show_tab, 1)
    menu.append(item_settings)

    item_monitor = Gtk.MenuItem(label="Monitor")
    item_monitor.connect("activate", _show_tab, 3)
    menu.append(item_monitor)

    menu.append(Gtk.SeparatorMenuItem())

    # --- New session ---
    item_new = Gtk.MenuItem(label="Neue Session")
    item_new.connect(
        "activate",
        lambda _: subprocess.Popen(
            ["kitty", "-e", "claude"], start_new_session=True
        ),
    )
    menu.append(item_new)

    menu.append(Gtk.SeparatorMenuItem())

    # --- Quit ---
    item_quit = Gtk.MenuItem(label="Beenden")
    item_quit.connect("activate", lambda _: Gtk.main_quit())
    menu.append(item_quit)

    menu.show_all()
    indicator.set_menu(menu)

    # On menu show: auto-show window + refresh stats (1-click UX)
    def _on_menu_show(_menu):
        if not win.get_visible():
            win.show_all()
            win.present()
        cost_data = get_daily_cost()
        if "error" not in cost_data:
            item_cost.set_label(
                f"Kosten heute: {format_cost(cost_data['cost_estimate_usd'])}"
            )
        else:
            item_cost.set_label("Kosten heute: N/A")
        active = get_active_sessions()
        item_sessions.set_label(f"Aktive Sessions: {len(active)}")

    menu.connect("show", _on_menu_show)

    return indicator


def main():
    win = ControlPanel()

    # Close button hides to tray instead of quitting
    win.connect("delete-event", lambda w, e: (w.hide(), True)[-1])

    indicator = _build_tray_indicator(win)

    win.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
