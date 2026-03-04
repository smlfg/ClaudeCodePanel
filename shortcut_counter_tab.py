#!/usr/bin/env python3
"""Shortcut Counter Tab — GTK3 widget for ClaudeCodePanel.

Reads directly from ~/.local/share/shortcut-counter/shortcuts.db (read-only).
Refreshes every 30s via GLib timer registered in panel.py.

Provides:
    build_shortcut_counter_tab() -> Gtk.ScrolledWindow
    refresh_shortcut_counter(tab) -> bool
"""

import sqlite3
import tomllib
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from theme import get_palette, hex_to_pango_rgb

# ---------------------------------------------------------------------------
# DB path (XDG)
# ---------------------------------------------------------------------------
_DB_PATH = Path.home() / ".local" / "share" / "shortcut-counter" / "shortcuts.db"

# Config path for "unused" recommendations
_CONFIG_PATH = Path.home() / "Projekte" / "ShortcutCounter" / "config.toml"

# Thresholds
_MASTERED = 50
_LEARNING_MIN = 1

# ---------------------------------------------------------------------------
# Module-level widget refs (stable outer structure)
# ---------------------------------------------------------------------------
_header_label: Gtk.Label | None = None
_progress_bar: Gtk.ProgressBar | None = None
_cards_container: Gtk.Box | None = None   # replaces TreeView
_learn_box: Gtk.Box | None = None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_rows() -> list[dict]:
    """Read all rows from shortcuts.db. Returns [] if DB missing."""
    if not _DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True,
                               detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT combo, count, last_used, category FROM shortcuts ORDER BY count DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return []


def _load_config_combos() -> list[str]:
    """Load all combos from config.toml. Returns [] on any error."""
    if not _CONFIG_PATH.exists():
        return []
    try:
        with open(_CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
        combos = []
        for cat in cfg.get("shortcuts", {}).values():
            combos.extend(cat.get("combos", []))
        return combos
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def _status_label(count: int) -> str:
    if count >= _MASTERED:
        return "Mastered"
    elif count >= _LEARNING_MIN:
        return "Learning"
    else:
        return "Not Used"


def _status_css_class(count: int) -> str:
    if count >= _MASTERED:
        return "sc-badge-mastered"
    elif count >= _LEARNING_MIN:
        return "sc-badge-learning"
    else:
        return "sc-badge-unused"


def _fmt_last_used(last_used) -> str:
    if last_used is None:
        return "—"
    if isinstance(last_used, datetime):
        return last_used.strftime("%d.%m %H:%M")
    try:
        dt = datetime.fromisoformat(str(last_used))
        return dt.strftime("%d.%m %H:%M")
    except (ValueError, TypeError):
        return str(last_used)[:10]


def _find_category(combo: str, _all_combos: list[str]) -> str:
    """Try to find category for a combo via config.toml."""
    if not _CONFIG_PATH.exists():
        return "Sonstige"
    try:
        with open(_CONFIG_PATH, "rb") as f:
            cfg = tomllib.load(f)
        for cat_name, cat_data in cfg.get("shortcuts", {}).items():
            if combo in cat_data.get("combos", []):
                return cat_name
    except Exception:
        pass
    return "Sonstige"


# ---------------------------------------------------------------------------
# Card builders
# ---------------------------------------------------------------------------

def _build_shortcut_row(r: dict, p: dict) -> Gtk.Box:
    """Build one horizontal shortcut row inside a category card."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.set_margin_top(2)
    row.set_margin_bottom(2)

    # Combo label (monospace bold)
    combo_lbl = Gtk.Label(label=r["combo"])
    combo_lbl.set_halign(Gtk.Align.START)
    combo_lbl.set_markup(
        f'<tt><b>{GLib.markup_escape_text(r["combo"])}</b></tt>'
    )
    combo_lbl.set_width_chars(18)
    combo_lbl.set_xalign(0.0)
    row.pack_start(combo_lbl, False, False, 0)

    # Count badge (dim text)
    count_lbl = Gtk.Label(label=str(r["count"]))
    count_lbl.set_markup(
        f'<span foreground="{p["overlay"]}" size="small">{r["count"]}×</span>'
    )
    row.pack_start(count_lbl, False, False, 0)

    # Status badge
    status_lbl = Gtk.Label(label=_status_label(r["count"]))
    status_lbl.get_style_context().add_class(_status_css_class(r["count"]))
    row.pack_start(status_lbl, False, False, 0)

    # Last-used (right-aligned, dim)
    last_lbl = Gtk.Label()
    last_lbl.set_markup(
        f'<span foreground="{p["dim"]}" size="small">{_fmt_last_used(r.get("last_used"))}</span>'
    )
    last_lbl.set_halign(Gtk.Align.END)
    row.pack_end(last_lbl, False, False, 0)

    return row


def _build_category_card(cat_name: str, cat_rows: list[dict], p: dict) -> Gtk.Box:
    """Build a full category card widget."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.get_style_context().add_class("sc-card")

    # Category header: bold name + progress "X/Y"
    mastered = sum(1 for r in cat_rows if r["count"] >= _MASTERED)
    used = sum(1 for r in cat_rows if r["count"] >= _LEARNING_MIN)
    total = len(cat_rows)

    header = Gtk.Label()
    header.set_markup(
        f'<b>{GLib.markup_escape_text(cat_name)}</b>  '
        f'<span foreground="{p["overlay"]}" size="small">'
        f'{mastered} mastered · {used}/{total} used</span>'
    )
    header.set_halign(Gtk.Align.START)
    header.set_margin_bottom(4)
    card.pack_start(header, False, False, 0)

    # Shortcut rows sorted by count desc
    for r in sorted(cat_rows, key=lambda x: -x["count"]):
        card.pack_start(_build_shortcut_row(r, p), False, False, 0)

    return card


def _build_learn_card(combo: str, cat_name: str, p: dict) -> Gtk.Box:
    """Build a single Learn Next card."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    card.get_style_context().add_class("sc-learn-card")

    combo_lbl = Gtk.Label()
    combo_lbl.set_markup(
        f'<tt><b><span foreground="{p["text"]}">'
        f'{GLib.markup_escape_text(combo)}</span></b></tt>'
    )
    combo_lbl.set_halign(Gtk.Align.START)
    combo_lbl.get_style_context().add_class("section-title")
    card.pack_start(combo_lbl, False, False, 0)

    cat_lbl = Gtk.Label()
    cat_lbl.set_markup(
        f'<span foreground="{p["dim"]}" size="small">'
        f'{GLib.markup_escape_text(cat_name)}</span>'
    )
    cat_lbl.set_halign(Gtk.Align.START)
    card.pack_start(cat_lbl, False, False, 0)

    return card


# ---------------------------------------------------------------------------
# UI update (always called on GTK main thread via idle_add)
# ---------------------------------------------------------------------------

def _do_refresh() -> bool:
    """Actual UI update — must run on GTK thread. Returns False (one-shot)."""
    rows = _load_rows()
    config_combos = _load_config_combos()
    p = get_palette()

    total_config = len(config_combos)
    used_count = sum(1 for r in rows if r["count"] >= _LEARNING_MIN)
    mastered_count = sum(1 for r in rows if r["count"] >= _MASTERED)

    # --- Header ---
    if _header_label is not None:
        if total_config > 0:
            _header_label.set_markup(
                f'<b>Shortcut Counter</b>  '
                f'<span foreground="{p["green"]}">{mastered_count} Mastered</span>  '
                f'<span foreground="{p["yellow"]}">{used_count} Learning</span>  '
                f'<span foreground="{p["overlay"]}">/ {total_config} konfiguriert</span>'
            )
        else:
            _header_label.set_markup(
                f'<b>Shortcut Counter</b>  '
                f'<span foreground="{p["overlay"]}">{len(rows)} gespeichert</span>'
            )

    # --- Progress bar ---
    if _progress_bar is not None:
        if total_config > 0:
            frac = min(used_count / total_config, 1.0)
            _progress_bar.set_fraction(frac)
            _progress_bar.set_text(
                f"{used_count} / {total_config} mindestens 1x benutzt ({frac*100:.0f}%)"
            )
        else:
            _progress_bar.set_fraction(0.0)
            _progress_bar.set_text("Keine Config-Datei gefunden")

    # --- Category cards ---
    if _cards_container is not None:
        for child in _cards_container.get_children():
            _cards_container.remove(child)

        # Group by category
        categories: dict[str, list[dict]] = {}
        for r in rows:
            cat = r.get("category") or "Sonstige"
            categories.setdefault(cat, []).append(r)

        # Add config combos never used (not in DB)
        db_combos = {r["combo"] for r in rows}
        for combo in config_combos:
            if combo not in db_combos:
                cat = _find_category(combo, config_combos)
                categories.setdefault(cat, []).append({
                    "combo": combo, "count": 0,
                    "last_used": None, "category": cat,
                })

        for cat_name, cat_rows in sorted(categories.items()):
            card = _build_category_card(cat_name, cat_rows, p)
            _cards_container.pack_start(card, False, False, 0)

        _cards_container.show_all()

    # --- Learn Next section ---
    if _learn_box is not None:
        for child in _learn_box.get_children():
            _learn_box.remove(child)

        lbl = Gtk.Label()
        lbl.set_markup(
            f'<b>Learn Next</b>  '
            f'<span foreground="{p["overlay"]}">Top 3 ungenutzte Shortcuts</span>'
        )
        lbl.set_halign(Gtk.Align.START)
        lbl.set_margin_bottom(4)
        _learn_box.pack_start(lbl, False, False, 0)

        db_combos = {r["combo"] for r in rows}
        unused = [c for c in config_combos if c not in db_combos or
                  next((r["count"] for r in rows if r["combo"] == c), 0) == 0]

        if unused:
            for combo in unused[:3]:
                cat = _find_category(combo, config_combos)
                card = _build_learn_card(combo, cat, p)
                _learn_box.pack_start(card, False, False, 0)
        else:
            done = Gtk.Label()
            done.set_markup(
                f'<span foreground="{p["green"]}">'
                f'Alle konfigurierten Shortcuts mindestens 1x benutzt!</span>'
            )
            done.set_halign(Gtk.Align.START)
            _learn_box.pack_start(done, False, False, 0)

        _learn_box.show_all()

    return False  # one-shot


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh_shortcut_counter(_tab=None) -> bool:
    """Called by GLib timer from panel.py. Schedules UI update via idle_add."""
    GLib.idle_add(_do_refresh)
    return True  # keep timer alive


def build_shortcut_counter_tab() -> Gtk.ScrolledWindow:
    """Build and return the Shortcut Counter tab widget."""
    global _header_label, _progress_bar, _cards_container, _learn_box

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    vbox.set_margin_top(10)
    vbox.set_margin_bottom(10)
    vbox.set_margin_start(10)
    vbox.set_margin_end(10)
    scrolled.add(vbox)

    p = get_palette()

    # --- Header ---
    _header_label = Gtk.Label()
    _header_label.set_markup(
        f'<b>Shortcut Counter</b>  <span foreground="{p["overlay"]}">Lade...</span>'
    )
    _header_label.set_halign(Gtk.Align.START)
    _header_label.set_margin_bottom(6)
    header_attrs = Pango.AttrList()
    header_attrs.insert(Pango.attr_scale_new(1.1))
    _header_label.set_attributes(header_attrs)
    vbox.pack_start(_header_label, False, False, 0)

    # --- Progress bar ---
    _progress_bar = Gtk.ProgressBar()
    _progress_bar.set_show_text(True)
    _progress_bar.set_fraction(0.0)
    _progress_bar.set_text("Lade...")
    _progress_bar.set_margin_bottom(10)
    vbox.pack_start(_progress_bar, False, False, 0)

    # --- Category cards container ---
    _cards_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    _cards_container.set_margin_bottom(8)
    vbox.pack_start(_cards_container, False, False, 0)

    # --- Learn Next section ---
    sep = Gtk.Separator()
    sep.set_margin_top(4)
    sep.set_margin_bottom(8)
    vbox.pack_start(sep, False, False, 0)

    _learn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    vbox.pack_start(_learn_box, False, False, 0)

    # Initial load
    GLib.idle_add(_do_refresh)

    scrolled.show_all()
    return scrolled
