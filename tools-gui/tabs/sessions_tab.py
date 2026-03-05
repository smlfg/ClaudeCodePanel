#!/usr/bin/env python3
"""Sessions Tab — Recent sessions with search and sort."""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

sys.path.insert(0, str(Path.home() / "Projekte" / "ClaudeCodePanel"))
from theme import get_palette
from monitor import get_recent_sessions


class SessionsTab(Gtk.Box):
    """Sessions list with search filter and sort options."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_margin_top(8)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._sessions: list[dict] = []
        self._filter_text = ""
        self._sort_key = "mtime"

        self._build_ui()

    def _build_ui(self):
        # Toolbar: search + sort
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        toolbar.get_style_context().add_class("base-toolbar")

        search = Gtk.SearchEntry()
        search.set_placeholder_text("Filter by project...")
        search.connect("search-changed", self._on_filter_changed)
        toolbar.pack_start(search, True, True, 0)

        sort_combo = Gtk.ComboBoxText()
        sort_combo.append("mtime", "Newest first")
        sort_combo.append("project", "By project")
        sort_combo.set_active(0)
        sort_combo.connect("changed", self._on_sort_changed)
        toolbar.pack_start(sort_combo, False, False, 0)

        self.pack_start(toolbar, False, False, 0)

        # Scrolled list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self._listbox)
        self.pack_start(scrolled, True, True, 0)

    def refresh(self) -> None:
        """Reload session data from monitor.py."""
        self._sessions = get_recent_sessions(n=20)
        self._rebuild_list()

    def _rebuild_list(self):
        p = get_palette()

        for child in self._listbox.get_children():
            self._listbox.remove(child)

        sessions = self._sessions
        if self._filter_text:
            ft = self._filter_text.lower()
            sessions = [s for s in sessions if ft in s.get("project", "").lower()
                        or ft in s.get("preview", "").lower()]

        if self._sort_key == "project":
            sessions = sorted(sessions, key=lambda s: s.get("project", "").lower())

        if not sessions:
            row = Gtk.ListBoxRow()
            lbl = Gtk.Label()
            lbl.set_markup(f'<span foreground="{p["dim"]}">No sessions found.</span>')
            lbl.set_margin_top(20)
            row.add(lbl)
            self._listbox.add(row)
            self._listbox.show_all()
            return

        for s in sessions:
            row = Gtk.ListBoxRow()
            row.add(self._build_session_row(s, p))
            self._listbox.add(row)

        self._listbox.show_all()

    def _build_session_row(self, s: dict, p: dict) -> Gtk.Box:
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.get_style_context().add_class("session-row")
        hbox.set_margin_top(2)
        hbox.set_margin_bottom(2)

        # Left: project + preview
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        proj_lbl = Gtk.Label()
        proj_lbl.set_markup(
            f'<span font_weight="bold">{GLib.markup_escape_text(s.get("project", "?"))}</span>'
        )
        proj_lbl.set_xalign(0)
        proj_lbl.get_style_context().add_class("session-project")
        left.pack_start(proj_lbl, False, False, 0)

        preview = s.get("preview", "")
        if preview:
            prev_lbl = Gtk.Label()
            prev_lbl.set_markup(
                f'<span foreground="{p["subtext1"]}" size="small">'
                f'{GLib.markup_escape_text(preview)}</span>'
            )
            prev_lbl.set_xalign(0)
            prev_lbl.set_line_wrap(True)
            prev_lbl.set_max_width_chars(60)
            left.pack_start(prev_lbl, False, False, 0)

        hbox.pack_start(left, True, True, 0)

        # Right: time
        time_lbl = Gtk.Label()
        time_lbl.set_markup(
            f'<span foreground="{p["dim"]}" size="small">'
            f'{GLib.markup_escape_text(s.get("time_str", ""))}</span>'
        )
        time_lbl.set_valign(Gtk.Align.CENTER)
        hbox.pack_end(time_lbl, False, False, 0)

        return hbox

    def _on_filter_changed(self, entry):
        self._filter_text = entry.get_text()
        self._rebuild_list()

    def _on_sort_changed(self, combo):
        self._sort_key = combo.get_active_id() or "mtime"
        self._rebuild_list()
