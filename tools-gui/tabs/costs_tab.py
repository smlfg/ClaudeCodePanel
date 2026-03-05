#!/usr/bin/env python3
"""Costs Tab — Daily cost stats + 7-day bar chart (Cairo) + tool rankings."""

import math
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

sys.path.insert(0, str(Path.home() / "Projekte" / "ClaudeCodePanel"))
from theme import get_palette
from monitor import get_daily_cost, get_usage_timeline, get_provider_costs, get_top_tools, format_cost


class CostsTab(Gtk.Box):
    """Cost overview with stats, bar chart, and tool rankings."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_margin_top(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._stat_labels: dict[str, Gtk.Label] = {}
        self._timeline_data: list[dict] = []
        self._chart_area = None
        self._tools_box = None
        self._providers_box = None

        self._build_ui()

    def _build_ui(self):
        p = get_palette()

        # --- Stats row ---
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        stats_box.get_style_context().add_class("base-card")

        for key, label in [("today", "Today"), ("calls", "Calls"), ("tools", "Tools")]:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            val_lbl = Gtk.Label(label="--")
            val_lbl.get_style_context().add_class("stat-value" if key == "today" else "monitor-value")
            vbox.pack_start(val_lbl, False, False, 0)
            name_lbl = Gtk.Label(label=label)
            name_lbl.get_style_context().add_class("stat-label")
            vbox.pack_start(name_lbl, False, False, 0)
            stats_box.pack_start(vbox, True, True, 0)
            self._stat_labels[key] = val_lbl

        self.pack_start(stats_box, False, False, 0)

        # --- Bar chart (7 days) ---
        chart_label = Gtk.Label()
        chart_label.set_markup('<b>Last 7 Days</b>')
        chart_label.set_halign(Gtk.Align.START)
        chart_label.get_style_context().add_class("section-title")
        self.pack_start(chart_label, False, False, 0)

        self._chart_area = Gtk.DrawingArea()
        self._chart_area.set_size_request(-1, 120)
        self._chart_area.connect("draw", self._draw_chart)
        self.pack_start(self._chart_area, False, False, 0)

        # --- Provider costs ---
        prov_label = Gtk.Label()
        prov_label.set_markup('<b>Provider Costs</b>')
        prov_label.set_halign(Gtk.Align.START)
        prov_label.get_style_context().add_class("section-title")
        self.pack_start(prov_label, False, False, 0)

        self._providers_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._providers_box.get_style_context().add_class("base-card")
        self.pack_start(self._providers_box, False, False, 0)

        # --- Top tools ---
        tools_label = Gtk.Label()
        tools_label.set_markup('<b>Top Tools</b>')
        tools_label.set_halign(Gtk.Align.START)
        tools_label.get_style_context().add_class("section-title")
        self.pack_start(tools_label, False, False, 0)

        self._tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._tools_box.get_style_context().add_class("base-card")
        self.pack_start(self._tools_box, False, False, 0)

    def refresh(self) -> None:
        """Reload cost data from monitor.py."""
        p = get_palette()

        # Daily cost
        daily = get_daily_cost()
        cost = daily.get("cost_estimate_usd", 0)
        self._stat_labels["today"].set_text(format_cost(cost))
        self._stat_labels["calls"].set_text(str(daily.get("total_calls", 0)))
        self._stat_labels["tools"].set_text(str(daily.get("unique_tools", 0)))

        # Timeline for chart
        self._timeline_data = get_usage_timeline()
        self._chart_area.queue_draw()

        # Provider costs
        for child in self._providers_box.get_children():
            self._providers_box.remove(child)

        providers = get_provider_costs()
        if providers:
            for prov, cost_val in sorted(providers.items(), key=lambda x: -x[1]):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name_lbl = Gtk.Label()
                name_lbl.set_markup(f'<tt>{GLib.markup_escape_text(prov)}</tt>')
                name_lbl.set_xalign(0)
                name_lbl.set_width_chars(15)
                row.pack_start(name_lbl, False, False, 0)
                cost_lbl = Gtk.Label()
                cost_lbl.set_markup(
                    f'<span foreground="{p["yellow"]}" font_family="monospace">{format_cost(cost_val)}</span>'
                )
                cost_lbl.set_halign(Gtk.Align.END)
                row.pack_end(cost_lbl, False, False, 0)
                self._providers_box.pack_start(row, False, False, 0)
        else:
            lbl = Gtk.Label()
            lbl.set_markup(f'<span foreground="{p["dim"]}">No cost data yet</span>')
            self._providers_box.pack_start(lbl, False, False, 0)

        self._providers_box.show_all()

        # Top tools
        for child in self._tools_box.get_children():
            self._tools_box.remove(child)

        top = get_top_tools(n=8)
        if top:
            for tool_name, count in top:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                name_lbl = Gtk.Label()
                name_lbl.set_markup(f'<tt>{GLib.markup_escape_text(tool_name)}</tt>')
                name_lbl.set_xalign(0)
                name_lbl.set_width_chars(20)
                row.pack_start(name_lbl, False, False, 0)
                count_lbl = Gtk.Label()
                count_lbl.set_markup(
                    f'<span foreground="{p["accent"]}" font_family="monospace">{count}</span>'
                )
                row.pack_end(count_lbl, False, False, 0)
                self._tools_box.pack_start(row, False, False, 0)
        else:
            lbl = Gtk.Label()
            lbl.set_markup(f'<span foreground="{p["dim"]}">No tool data yet</span>')
            self._tools_box.pack_start(lbl, False, False, 0)

        self._tools_box.show_all()

    def _draw_chart(self, widget, cr):
        """Draw 7-day bar chart using Cairo."""
        p = get_palette()
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()

        data = list(reversed(self._timeline_data))  # oldest first for left-to-right
        if not data:
            # Draw placeholder
            cr.set_source_rgba(*_hex_rgb(p["dim"]), 0.5)
            cr.select_font_face("monospace", 0, 0)
            cr.set_font_size(11)
            cr.move_to(width / 2 - 40, height / 2)
            cr.show_text("No data yet")
            return False

        n = len(data)
        max_cost = max((d.get("cost_est", 0) for d in data), default=1) or 0.01
        bar_width = max(20, (width - 40) / n - 8)
        gap = 8

        x_start = 20
        chart_height = height - 30  # leave room for labels

        for i, d in enumerate(data):
            cost = d.get("cost_est", 0)
            bar_h = max(2, (cost / max_cost) * (chart_height - 10))
            x = x_start + i * (bar_width + gap)
            y = chart_height - bar_h

            # Bar
            r, g, b = _hex_rgb(p["accent"])
            cr.set_source_rgba(r, g, b, 0.7)
            cr.rectangle(x, y, bar_width, bar_h)
            cr.fill()

            # Date label
            cr.set_source_rgba(*_hex_rgb(p["dim"]), 1)
            cr.select_font_face("monospace", 0, 0)
            cr.set_font_size(9)
            date_str = d.get("date", "")[-5:]  # MM-DD
            cr.move_to(x, height - 4)
            cr.show_text(date_str)

            # Cost label on top of bar
            cr.set_source_rgba(*_hex_rgb(p["text"]), 1)
            cr.set_font_size(9)
            cr.move_to(x, y - 3)
            cr.show_text(format_cost(cost))

        return False


def _hex_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert #rrggbb to (r, g, b) floats 0-1."""
    return (
        int(hex_color[1:3], 16) / 255,
        int(hex_color[3:5], 16) / 255,
        int(hex_color[5:7], 16) / 255,
    )
