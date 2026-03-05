#!/usr/bin/env python3
"""Tools Tab — Tool usage breakdown + skill usage."""

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

sys.path.insert(0, str(Path.home() / "Projekte" / "ClaudeCodePanel"))
from theme import get_palette
from monitor import get_top_tools, get_skill_usage, get_missed_skills_summary


class ToolsTab(Gtk.Box):
    """Tool + skill usage display."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._tools_box = None
        self._skills_box = None
        self._missed_box = None

        self._build_ui()

    def _build_ui(self):
        p = get_palette()

        # --- Tool usage ---
        tools_lbl = Gtk.Label()
        tools_lbl.set_markup('<b>Tool Usage (Today)</b>')
        tools_lbl.set_halign(Gtk.Align.START)
        tools_lbl.get_style_context().add_class("section-title")
        self.pack_start(tools_lbl, False, False, 0)

        self._tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._tools_box.get_style_context().add_class("base-card")
        self.pack_start(self._tools_box, False, False, 0)

        # --- Skill usage ---
        skills_lbl = Gtk.Label()
        skills_lbl.set_markup('<b>Skill Usage (Today)</b>')
        skills_lbl.set_halign(Gtk.Align.START)
        skills_lbl.get_style_context().add_class("section-title")
        self.pack_start(skills_lbl, False, False, 0)

        self._skills_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._skills_box.get_style_context().add_class("base-card")
        self.pack_start(self._skills_box, False, False, 0)

        # --- Missed skills ---
        missed_lbl = Gtk.Label()
        missed_lbl.set_markup('<b>Missed Skills</b>')
        missed_lbl.set_halign(Gtk.Align.START)
        missed_lbl.get_style_context().add_class("section-title")
        self.pack_start(missed_lbl, False, False, 0)

        self._missed_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._missed_box.get_style_context().add_class("base-card")
        self.pack_start(self._missed_box, False, False, 0)

    def refresh(self) -> None:
        """Reload tool/skill data from monitor.py."""
        p = get_palette()

        # Tool usage
        self._clear_box(self._tools_box)
        tools = get_top_tools(n=15)
        if tools:
            max_count = tools[0][1] if tools else 1
            for name, count in tools:
                row = self._build_bar_row(name, count, max_count, p["accent"], p)
                self._tools_box.pack_start(row, False, False, 0)
        else:
            self._add_empty(self._tools_box, "No tool data yet", p)
        self._tools_box.show_all()

        # Skill usage
        self._clear_box(self._skills_box)
        skills = get_skill_usage(days=1)
        if skills:
            # Flatten today's skills
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            today_skills = skills.get(today, {})
            if today_skills:
                sorted_skills = sorted(today_skills.items(), key=lambda x: -x[1])
                max_s = sorted_skills[0][1] if sorted_skills else 1
                for name, count in sorted_skills:
                    row = self._build_bar_row(name, count, max_s, p["mauve"], p)
                    self._skills_box.pack_start(row, False, False, 0)
            else:
                self._add_empty(self._skills_box, "No skills used today", p)
        else:
            self._add_empty(self._skills_box, "No skill data yet", p)
        self._skills_box.show_all()

        # Missed skills
        self._clear_box(self._missed_box)
        missed = get_missed_skills_summary()
        if missed:
            for name, count in missed[:8]:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name_lbl = Gtk.Label()
                name_lbl.set_markup(f'<tt>{GLib.markup_escape_text(name)}</tt>')
                name_lbl.set_xalign(0)
                name_lbl.set_width_chars(20)
                row.pack_start(name_lbl, False, False, 0)
                count_lbl = Gtk.Label()
                count_lbl.set_markup(
                    f'<span foreground="{p["red"]}" size="small">{count} missed</span>'
                )
                row.pack_end(count_lbl, False, False, 0)
                self._missed_box.pack_start(row, False, False, 0)
        else:
            self._add_empty(self._missed_box, "No missed skills", p)
        self._missed_box.show_all()

    def _build_bar_row(self, name: str, count: int, max_count: int, bar_color: str, p: dict) -> Gtk.Box:
        """Build a row with name, count, and a proportional bar."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        name_lbl = Gtk.Label()
        name_lbl.set_markup(f'<tt>{GLib.markup_escape_text(name)}</tt>')
        name_lbl.set_xalign(0)
        name_lbl.set_width_chars(20)
        row.pack_start(name_lbl, False, False, 0)

        # Visual bar
        bar = Gtk.DrawingArea()
        frac = count / max_count if max_count > 0 else 0
        bar.set_size_request(max(4, int(frac * 150)), 12)
        bar.connect("draw", self._draw_mini_bar, bar_color)
        bar.set_valign(Gtk.Align.CENTER)
        row.pack_start(bar, False, False, 0)

        count_lbl = Gtk.Label()
        count_lbl.set_markup(
            f'<span foreground="{p["overlay"]}" font_family="monospace" size="small">{count}</span>'
        )
        row.pack_start(count_lbl, False, False, 0)

        return row

    @staticmethod
    def _draw_mini_bar(widget, cr, color_hex):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        r = int(color_hex[1:3], 16) / 255
        g = int(color_hex[3:5], 16) / 255
        b = int(color_hex[5:7], 16) / 255
        cr.set_source_rgba(r, g, b, 0.6)
        # Rounded rectangle
        radius = 3
        cr.arc(radius, radius, radius, 3.14159, 1.5 * 3.14159)
        cr.arc(width - radius, radius, radius, 1.5 * 3.14159, 0)
        cr.arc(width - radius, height - radius, radius, 0, 0.5 * 3.14159)
        cr.arc(radius, height - radius, radius, 0.5 * 3.14159, 3.14159)
        cr.close_path()
        cr.fill()
        return False

    @staticmethod
    def _clear_box(box):
        for child in box.get_children():
            box.remove(child)

    @staticmethod
    def _add_empty(box, text, p):
        lbl = Gtk.Label()
        lbl.set_markup(f'<span foreground="{p["dim"]}">{text}</span>')
        box.pack_start(lbl, False, False, 0)
