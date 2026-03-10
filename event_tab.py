"""Event tab widget for Claude Code Control Panel.

Live hook event viewer with filtering and auto-scroll.
Reads from ~/.claude/events/YYYY-MM-DD.jsonl (day-file tracking).
Auto-refreshes via external timer calling refresh_events().
"""

import json
from datetime import date, datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from theme import get_palette
from utils import idle_once

EVENTS_DIR = Path.home() / ".claude" / "events"

_MAX_ROWS = 500

# Module-level widget references
_list_box = None
_filter_combo = None
_search_entry = None
_auto_scroll_btn = None
_scrolled_window = None
_stats_label = None

# State
_rows = []           # list of parsed event dicts (most recent last)
_file_offset = 0     # byte offset into current day file
_current_day = None  # date string "YYYY-MM-DD" of last read

# All known hook event types (for filter combo population)
_KNOWN_TYPES = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "Notification",
    "PermissionRequest",
    "Stop",
]


def _type_color(event_type: str) -> str:
    """Return palette hex color for a given event type badge."""
    p = get_palette()
    mapping = {
        "PreToolUse": p["accent"],
        "PostToolUse": p["green"],
        "PostToolUseFailure": p["red"],
        "UserPromptSubmit": p["mauve"],
        "SubagentStart": p["peach"],
        "SubagentStop": p["yellow"],
        "SessionStart": p["teal"],
        "SessionEnd": p["teal"],
        "PreCompact": p["peach"],
        "Notification": p["lavender"],
        "PermissionRequest": p["red"],
        "Stop": p["dim"],
    }
    return mapping.get(event_type, p["overlay"])


def build_events_tab() -> Gtk.ScrolledWindow:
    """Build the Events tab widget."""
    global _list_box, _filter_combo, _search_entry, _auto_scroll_btn
    global _scrolled_window, _stats_label

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.get_style_context().add_class("log-toolbar")
    toolbar.set_margin_top(8)
    toolbar.set_margin_bottom(6)
    toolbar.set_margin_start(10)
    toolbar.set_margin_end(10)

    filter_label = Gtk.Label(label="Type:")
    toolbar.pack_start(filter_label, False, False, 0)

    _filter_combo = Gtk.ComboBoxText()
    _filter_combo.append_text("All")
    for t in _KNOWN_TYPES:
        _filter_combo.append_text(t)
    _filter_combo.set_active(0)
    _filter_combo.connect("changed", _on_filter_changed)
    toolbar.pack_start(_filter_combo, False, False, 0)

    session_label = Gtk.Label(label="Session:")
    toolbar.pack_start(session_label, False, False, 0)

    _search_entry = Gtk.SearchEntry()
    _search_entry.set_placeholder_text("session ID...")
    _search_entry.set_size_request(140, -1)
    _search_entry.connect("search-changed", _on_filter_changed)
    toolbar.pack_start(_search_entry, False, False, 0)

    # Spacer
    spacer = Gtk.Label(label="")
    spacer.set_hexpand(True)
    toolbar.pack_start(spacer, True, True, 0)

    _stats_label = Gtk.Label(label="")
    _stats_label.set_xalign(1.0)
    _stats_label.get_style_context().add_class("log-stats")
    toolbar.pack_start(_stats_label, False, False, 0)

    _auto_scroll_btn = Gtk.ToggleButton(label="Auto-scroll")
    _auto_scroll_btn.set_active(True)
    toolbar.pack_start(_auto_scroll_btn, False, False, 0)

    clear_btn = Gtk.Button(label="Clear")
    clear_btn.connect("clicked", _on_clear_clicked)
    toolbar.pack_start(clear_btn, False, False, 0)

    outer.pack_start(toolbar, False, False, 0)
    outer.pack_start(
        Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
    )

    # --- Scrolled list ---
    _scrolled_window = Gtk.ScrolledWindow()
    _scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    _scrolled_window.set_vexpand(True)

    _list_box = Gtk.ListBox()
    _list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    _scrolled_window.add(_list_box)

    outer.pack_start(_scrolled_window, True, True, 0)

    # Wrap in a ScrolledWindow for the tab API contract
    wrapper = Gtk.ScrolledWindow()
    wrapper.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
    wrapper.add(outer)

    idle_once(refresh_events)
    return wrapper


def refresh_events() -> bool:
    """Called by panel.py timer (2s). Reads new JSONL lines. Returns True to keep timer."""
    global _file_offset, _current_day, _rows

    if _list_box is None:
        return True

    try:
        today_str = date.today().isoformat()

        # Day rollover — reset offset
        if _current_day != today_str:
            _file_offset = 0
            _current_day = today_str

        today_file = EVENTS_DIR / f"{today_str}.jsonl"
        if not today_file.exists():
            _update_stats()
            return True

        new_events = []
        try:
            with today_file.open("rb") as f:
                f.seek(_file_offset)
                while True:
                    line = f.readline()
                    if not line:
                        break
                    _file_offset = f.tell()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        new_events.append(data)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return True

        if not new_events:
            return True

        for event in new_events:
            _rows.append(event)

        # Trim to max rows
        if len(_rows) > _MAX_ROWS:
            excess = len(_rows) - _MAX_ROWS
            _rows = _rows[excess:]
            # Remove oldest rows from list_box
            children = _list_box.get_children()
            for i in range(min(excess, len(children))):
                _list_box.remove(children[i])

        # Add new rows to list_box
        active_type = _current_filter_type()
        session_filter = _current_session_filter()
        for event in new_events:
            if _event_matches(event, active_type, session_filter):
                row = _build_row(event)
                _list_box.add(row)

        _list_box.show_all()
        _update_stats()

        # Auto-scroll
        if _auto_scroll_btn is not None and _auto_scroll_btn.get_active():
            _scroll_to_bottom()

    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _build_row(event: dict) -> Gtk.ListBoxRow:
    """Create a single ListBoxRow from an event dict."""
    row = Gtk.ListBoxRow()
    row.set_activatable(False)

    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    hbox.set_margin_top(3)
    hbox.set_margin_bottom(3)
    hbox.set_margin_start(8)
    hbox.set_margin_end(8)

    # Time label
    time_str = _parse_time(event)
    time_lbl = Gtk.Label(label=time_str)
    time_lbl.set_size_request(65, -1)
    time_lbl.set_xalign(0.0)
    time_lbl.get_style_context().add_class("log-time")
    hbox.pack_start(time_lbl, False, False, 0)

    # Type badge (colored via Pango markup)
    event_type = event.get("hook_event_name", event.get("type", "Unknown"))
    color = _type_color(event_type)
    badge = Gtk.Label()
    badge.set_markup(f'<span foreground="{color}" font_weight="bold">{GLib.markup_escape_text(event_type)}</span>')
    badge.set_size_request(160, -1)
    badge.set_xalign(0.0)
    badge.set_ellipsize(Pango.EllipsizeMode.END)
    hbox.pack_start(badge, False, False, 0)

    # Session ID (first 8 chars)
    session_id = event.get("session_id", "")
    session_short = session_id[:8] if session_id else "—"
    session_lbl = Gtk.Label(label=session_short)
    session_lbl.set_size_request(72, -1)
    session_lbl.set_xalign(0.0)
    session_lbl.get_style_context().add_class("log-time")
    hbox.pack_start(session_lbl, False, False, 0)

    # Detail text
    detail = _event_detail(event)
    detail_lbl = Gtk.Label(label=detail)
    detail_lbl.set_hexpand(True)
    detail_lbl.set_xalign(0.0)
    detail_lbl.set_ellipsize(Pango.EllipsizeMode.END)
    detail_lbl.get_style_context().add_class("log-detail")
    hbox.pack_start(detail_lbl, True, True, 0)

    row.add(hbox)

    # Tooltip: full event JSON summary
    tooltip_parts = []
    if session_id:
        tooltip_parts.append(f"session: {session_id}")
    tool = event.get("tool_name") or event.get("tool")
    if tool:
        tooltip_parts.append(f"tool: {tool}")
    if tooltip_parts:
        row.set_tooltip_text(" | ".join(tooltip_parts))

    return row


def _parse_time(event: dict) -> str:
    """Extract HH:MM:SS from event timestamp."""
    ts = event.get("timestamp", event.get("time", ""))
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S")
    except (ValueError, TypeError):
        return ts[:8] if len(ts) >= 8 else ts


def _event_detail(event: dict) -> str:
    """Extract a human-readable detail string from an event."""
    # Tool name takes priority
    tool = event.get("tool_name") or event.get("tool")
    if tool:
        path = event.get("tool_input", {}).get("path", "") if isinstance(event.get("tool_input"), dict) else ""
        if path:
            home = str(Path.home())
            if path.startswith(home + "/"):
                path = "~/" + path[len(home) + 1:]
            return f"{tool}  {path}"
        return tool

    # Fallback: pick meaningful fields
    for key in ("message", "text", "prompt", "summary", "reason", "content"):
        val = event.get(key, "")
        if val and isinstance(val, str):
            return val[:120]

    # Last resort: show non-meta keys
    skip = {"timestamp", "time", "session_id", "hook_event_name", "type"}
    parts = []
    for k, v in event.items():
        if k not in skip and not k.startswith("_"):
            parts.append(f"{k}={v!r}")
        if len(parts) >= 4:
            break
    return "  ".join(parts)[:120]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _current_filter_type() -> str | None:
    if _filter_combo is None:
        return None
    active = _filter_combo.get_active_text() or "All"
    return None if active == "All" else active


def _current_session_filter() -> str:
    if _search_entry is None:
        return ""
    return (_search_entry.get_text() or "").strip().lower()


def _event_matches(event: dict, filter_type: str | None, session_filter: str) -> bool:
    if filter_type is not None:
        etype = event.get("hook_event_name", event.get("type", ""))
        if etype != filter_type:
            return False
    if session_filter:
        sid = (event.get("session_id", "") or "").lower()
        if session_filter not in sid:
            return False
    return True


def _on_filter_changed(_widget):
    """Rebuild visible rows when filter combo or search entry changes."""
    _rebuild_list()


def _rebuild_list():
    """Clear and re-populate list_box from _rows with current filters."""
    if _list_box is None:
        return
    for child in _list_box.get_children():
        _list_box.remove(child)

    active_type = _current_filter_type()
    session_filter = _current_session_filter()

    for event in _rows:
        if _event_matches(event, active_type, session_filter):
            row = _build_row(event)
            _list_box.add(row)

    _list_box.show_all()
    _update_stats()

    if _auto_scroll_btn is not None and _auto_scroll_btn.get_active():
        _scroll_to_bottom()


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _on_clear_clicked(_btn):
    global _rows, _file_offset
    _rows = []
    _file_offset = 0  # re-read from start on next refresh (soft clear)
    if _list_box is not None:
        for child in _list_box.get_children():
            _list_box.remove(child)
    _update_stats()


def _scroll_to_bottom():
    if _scrolled_window is None:
        return
    adj = _scrolled_window.get_vadjustment()
    if adj is not None:
        GLib.idle_add(_do_scroll, adj)


def _do_scroll(adj) -> bool:
    adj.set_value(adj.get_upper() - adj.get_page_size())
    return False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _update_stats():
    if _stats_label is None:
        return
    total = len(_rows)
    now = datetime.now().strftime("%H:%M")
    _stats_label.set_text(f"{total} events | {now}")
