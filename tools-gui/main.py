#!/usr/bin/env python3
"""Claude Code Tools — GTK3 standalone GUI.

Session history, cost tracking, and tool/skill usage analytics.
Data source: monitor.py from ClaudeCodePanel (TTL-cached, 30s).
"""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

# Import shared base
sys.path.insert(0, str(Path.home() / "Projekte" / "shared-gui"))
from gui_base import BaseApp

# Import monitor for status bar info
sys.path.insert(0, str(Path.home() / "Projekte" / "ClaudeCodePanel"))
from monitor import get_daily_cost, format_cost, get_recent_sessions

from tabs.sessions_tab import SessionsTab
from tabs.costs_tab import CostsTab
from tabs.tools_tab import ToolsTab


class ToolsApp(BaseApp):
    """Main Claude Code Tools application."""

    def __init__(self):
        super().__init__("Claude Code Tools", 800, 600, icon_name="utilities-system-monitor")

        self._sessions_tab = SessionsTab()
        self._costs_tab = CostsTab()
        self._tools_tab = ToolsTab()

        self._build_ui()

        # Initial load
        GLib.idle_add(self._refresh_all)

        # Auto-refresh every 30 seconds (matches monitor.py TTL cache)
        self.start_refresh(30, self._refresh_all)

    def _build_ui(self):
        # Refresh button in header
        ref_btn = Gtk.Button(label="Refresh")
        ref_btn.get_style_context().add_class("shortcut-btn")
        ref_btn.connect("clicked", lambda _: self._refresh_all())
        self.add_header_widget(ref_btn)

        # Notebook with 3 tabs
        notebook = Gtk.Notebook()
        notebook.append_page(self._sessions_tab, Gtk.Label(label="Sessions"))
        notebook.append_page(self._costs_tab, Gtk.Label(label="Costs"))
        notebook.append_page(self._tools_tab, Gtk.Label(label="Tools"))

        self.content_box.pack_start(notebook, True, True, 0)

    def _refresh_all(self) -> bool:
        """Refresh all tabs and status bar."""
        self._sessions_tab.refresh()
        self._costs_tab.refresh()
        self._tools_tab.refresh()

        # Update status bar
        daily = get_daily_cost()
        cost = format_cost(daily.get("cost_estimate_usd", 0))
        sessions = get_recent_sessions(n=100)
        self.set_status(f"{len(sessions)} sessions | {cost} today")

        return True  # keep timer


def main():
    app = ToolsApp()
    app.run()


if __name__ == "__main__":
    main()
