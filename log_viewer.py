"""Log viewer widget for Claude Code Control Panel.

Reads today's usage JSONL + coaching log, displays in a filterable list.
Auto-refreshes via external timer calling refresh_logs().
"""

import json
from datetime import date, datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from utils import idle_once

USAGE_DIR = Path.home() / ".claude" / "usage"
COACHING_LOG = Path.home() / ".claude" / "hooks" / "coaching" / "coaching_log.md"

_MAX_ENTRIES = 80

# Module-level widget references (set during build)
_log_box = None
_filter_combo = None
_stats_label = None
_last_fingerprint = ("", 0)


def build_logs_tab() -> Gtk.ScrolledWindow:
    """Build and return the Logs tab widget."""
    global _log_box, _filter_combo, _stats_label

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    main_box.set_margin_top(12)
    main_box.set_margin_bottom(12)
    main_box.set_margin_start(12)
    main_box.set_margin_end(12)

    # --- Top bar: filter + stats + clear ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

    filter_label = Gtk.Label(label="Filter:")
    toolbar.pack_start(filter_label, False, False, 0)

    _filter_combo = Gtk.ComboBoxText()
    for f in ["Alle", "Tools", "Errors", "Hooks"]:
        _filter_combo.append_text(f)
    _filter_combo.set_active(0)
    _filter_combo.connect("changed", lambda _: _apply_filter())
    toolbar.pack_start(_filter_combo, False, False, 0)

    _stats_label = Gtk.Label(label="")
    _stats_label.get_style_context().add_class("stat-label")
    _stats_label.set_xalign(0)
    toolbar.pack_start(_stats_label, True, True, 5)

    clear_btn = Gtk.Button(label="Clear")
    clear_btn.connect("clicked", _on_clear)
    toolbar.pack_end(clear_btn, False, False, 0)

    main_box.pack_start(toolbar, False, False, 0)

    # --- Separator ---
    main_box.pack_start(
        Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
    )

    # --- Log entries container ---
    log_scrolled = Gtk.ScrolledWindow()
    log_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    log_scrolled.set_vexpand(True)

    _log_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    _log_box.set_margin_top(4)
    log_scrolled.add(_log_box)

    main_box.pack_start(log_scrolled, True, True, 0)

    scrolled.add(main_box)

    idle_once(refresh_logs)
    return scrolled


def refresh_logs() -> bool:
    """Refresh log data from disk. Returns True to keep timer alive."""
    global _last_fingerprint
    if _log_box is None:
        return True

    entries = _read_usage_entries() + _read_coaching_entries()
    # Sort by timestamp descending
    entries.sort(key=lambda e: e.get("_sort_key", ""), reverse=True)
    entries = entries[:_MAX_ENTRIES]

    # Only rebuild if content changed (count + latest sort key)
    fingerprint = (entries[0].get("_sort_key", "") if entries else "", len(entries))
    if fingerprint == _last_fingerprint:
        _update_stats(entries)
        return True

    _last_fingerprint = fingerprint

    # Clear and rebuild
    for child in _log_box.get_children():
        _log_box.remove(child)

    for entry in entries:
        row = _build_log_row(entry)
        _log_box.pack_start(row, False, False, 0)

    _log_box.show_all()
    _apply_filter()
    _update_stats(entries)
    return True


def _read_usage_entries() -> list[dict]:
    """Read today's usage JSONL file."""
    today_file = USAGE_DIR / f"{date.today().isoformat()}.jsonl"
    if not today_file.exists():
        return []

    entries = []
    try:
        with today_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    data["_type"] = "tool"
                    data["_sort_key"] = data.get("timestamp", "")
                    entries.append(data)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _read_coaching_entries() -> list[dict]:
    """Read last 20 lines from coaching log as hook entries."""
    if not COACHING_LOG.exists():
        return []

    entries = []
    try:
        lines = COACHING_LOG.read_text().strip().split("\n")
        for line in lines[-20:]:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append({
                "_type": "hook",
                "_sort_key": "",  # no timestamp, stays at end
                "text": line,
            })
    except OSError:
        pass
    return entries


def _build_log_row(entry: dict) -> Gtk.Box:
    """Build a single log entry row."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.set_margin_start(4)
    row.set_margin_end(4)
    row.set_margin_top(1)
    row.set_margin_bottom(1)

    entry_type = entry.get("_type", "tool")
    row._entry_type = entry_type  # store for filtering

    if entry_type == "tool":
        # Timestamp
        ts = entry.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                ts = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                ts = ts[:8]

        ts_label = Gtk.Label(label=ts)
        ts_label.set_width_chars(9)
        ts_label.set_xalign(0)
        ts_label.set_opacity(0.5)
        row.pack_start(ts_label, False, False, 0)

        # Tool name
        tool = entry.get("tool", "?")
        tool_label = Gtk.Label(label=tool)
        tool_label.set_width_chars(16)
        tool_label.set_xalign(0)
        tool_label.get_style_context().add_class("monitor-value")
        row.pack_start(tool_label, False, False, 0)

        # Duration
        dur = entry.get("duration_ms")
        if dur is not None:
            dur_label = Gtk.Label(label=f"{dur}ms")
            dur_label.set_width_chars(8)
            dur_label.set_xalign(1)
            dur_label.set_opacity(0.6)
            row.pack_start(dur_label, False, False, 0)

        # Error indicator
        if entry.get("error"):
            err_label = Gtk.Label(label="ERR")
            err_label.set_opacity(0.9)
            row.pack_start(err_label, False, False, 0)
            row._entry_type = "error"  # override for filtering

    elif entry_type == "hook":
        tag = Gtk.Label(label="HOOK")
        tag.set_width_chars(9)
        tag.set_xalign(0)
        tag.set_opacity(0.7)
        row.pack_start(tag, False, False, 0)

        text = entry.get("text", "")
        text_label = Gtk.Label(label=text, xalign=0)
        text_label.set_ellipsize(Pango.EllipsizeMode.END)
        text_label.set_max_width_chars(60)
        row.pack_start(text_label, True, True, 0)

    return row


def _apply_filter():
    """Show/hide rows based on current filter selection."""
    if _log_box is None or _filter_combo is None:
        return

    active = _filter_combo.get_active_text() or "Alle"
    type_map = {"Alle": None, "Tools": "tool", "Errors": "error", "Hooks": "hook"}
    filter_type = type_map.get(active)

    for child in _log_box.get_children():
        if filter_type is None:
            child.show()
        elif getattr(child, "_entry_type", "") == filter_type:
            child.show()
        else:
            child.hide()


def _update_stats(entries: list[dict]):
    """Update the stats label."""
    if _stats_label is None:
        return
    tool_count = sum(1 for e in entries if e.get("_type") == "tool")
    hook_count = sum(1 for e in entries if e.get("_type") == "hook")
    error_count = sum(1 for e in entries if e.get("error"))
    now = datetime.now().strftime("%H:%M")
    _stats_label.set_text(
        f"{tool_count} Tools | {error_count} Errors | {hook_count} Hooks | {now}"
    )


def _on_clear(_button):
    """Clear the log view."""
    global _last_fingerprint
    if _log_box is None:
        return
    for child in _log_box.get_children():
        _log_box.remove(child)
    _last_fingerprint = ("", 0)
    if _stats_label:
        _stats_label.set_text("Cleared")
