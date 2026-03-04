"""Log viewer widget for Claude Code Control Panel.

Card-based log viewer with filtering and cost tracking.
Reads today's usage JSONL + provider costs + coaching log.
Auto-refreshes via external timer calling refresh_logs().
"""

import json
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango

from utils import idle_once

USAGE_DIR = Path.home() / ".claude" / "usage"
COACHING_LOG = Path.home() / ".claude" / "hooks" / "coaching" / "coaching_log.md"
PROVIDERS_FILE = USAGE_DIR / "providers.jsonl"

_MAX_ENTRIES = 500

# Module-level widget references
_filter_combo = None
_search_entry = None
_stats_label = None
_cards_container = None
_last_entries = []
_last_fingerprint = ("", 0)


def build_logs_tab() -> Gtk.ScrolledWindow:
    """Build and return the Logs tab widget."""
    global _filter_combo, _search_entry, _stats_label, _cards_container

    outer = Gtk.ScrolledWindow()
    outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    main_box.set_margin_top(10)
    main_box.set_margin_bottom(10)
    main_box.set_margin_start(10)
    main_box.set_margin_end(10)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.get_style_context().add_class("log-toolbar")

    filter_label = Gtk.Label(label="Filter:")
    toolbar.pack_start(filter_label, False, False, 0)

    _filter_combo = Gtk.ComboBoxText()
    for label in ["Alle", "Tools", "LLM", "Errors", "Hooks"]:
        _filter_combo.append_text(label)
    _filter_combo.set_active(0)
    _filter_combo.connect("changed", _on_filter_changed)
    toolbar.pack_start(_filter_combo, False, False, 0)

    _search_entry = Gtk.SearchEntry()
    _search_entry.set_placeholder_text("Suche...")
    _search_entry.set_size_request(160, -1)
    _search_entry.connect("search-changed", _on_filter_changed)
    toolbar.pack_start(_search_entry, False, False, 0)

    _stats_label = Gtk.Label(label="")
    _stats_label.set_xalign(1.0)
    _stats_label.set_hexpand(True)
    _stats_label.get_style_context().add_class("log-stats")
    toolbar.pack_start(_stats_label, True, True, 0)

    main_box.pack_start(toolbar, False, False, 0)
    main_box.pack_start(
        Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
    )

    # --- Inner scroll for cards ---
    inner_scroll = Gtk.ScrolledWindow()
    inner_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    inner_scroll.set_vexpand(True)

    _cards_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    inner_scroll.add(_cards_container)

    main_box.pack_start(inner_scroll, True, True, 0)
    outer.add(main_box)

    idle_once(refresh_logs)
    return outer


def refresh_logs() -> bool:
    """Refresh log data from disk. Returns True to keep timer alive."""
    global _last_fingerprint, _last_entries
    try:
        if _cards_container is None:
            return True

        entries = _read_usage_entries() + _read_coaching_entries() + _read_provider_costs_today()
        entries.sort(key=lambda e: e.get("_sort_key", ""), reverse=True)
        entries = entries[:_MAX_ENTRIES]

        fingerprint = (entries[0].get("_sort_key", "") if entries else "", len(entries))
        if fingerprint == _last_fingerprint:
            _update_stats(entries)
            return True

        _last_entries = entries
        _last_fingerprint = fingerprint
        _rebuild_cards()
        _update_stats(entries)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Data reading
# ---------------------------------------------------------------------------

def _read_usage_entries() -> list:
    """Read today's usage JSONL (tool calls)."""
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
                    ts = data.get("time", data.get("timestamp", ""))
                    data["_sort_key"] = ts
                    data["_type"] = "tool"
                    entries.append(data)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _read_coaching_entries() -> list:
    """Read last 20 lines from coaching log as hook entries."""
    if not COACHING_LOG.exists():
        return []

    entries = []
    try:
        with COACHING_LOG.open() as f:
            last_lines = deque(f, maxlen=20)
        for line in last_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append({
                "_type": "hook",
                "_sort_key": "",
                "text": line,
            })
    except OSError:
        pass
    return entries


def _read_provider_costs_today() -> list:
    """Read today's provider entries from providers.jsonl."""
    today = date.today().isoformat()
    if not PROVIDERS_FILE.exists():
        return []
    entries = []
    try:
        with PROVIDERS_FILE.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ts = data.get("time", data.get("timestamp", ""))
                    if ts[:10] == today:
                        data["_sort_key"] = ts
                        data["_type"] = "llm"
                        entries.append(data)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _entry_to_row(entry: dict) -> dict:
    """Convert an entry dict to a row dict."""
    etype = entry.get("_type", "tool")
    sort_key = entry.get("_sort_key", "")

    # Parse timestamp
    ts_raw = entry.get("time", entry.get("timestamp", sort_key))
    time_str = ""
    if ts_raw:
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            time_str = dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = ts_raw[:8]

    if etype == "tool":
        tool = entry.get("tool", "?")
        file_path = entry.get("file", "")
        cmd_cat = entry.get("cmd_cat", "")
        detail = file_path or cmd_cat or ""
        _home_prefix = str(Path.home()) + "/"
        if detail.startswith(_home_prefix):
            detail = "~/" + detail[len(_home_prefix):]
        tooltip = f"{tool} {file_path or cmd_cat or ''}".strip()
        cost_f = 0.0
        cost_str = ""
        if entry.get("error"):
            status = "✗"
            row_type = "error"
        else:
            status = "✓"
            row_type = "tool"

    elif etype == "llm":
        provider = entry.get("provider", "?")
        model = entry.get("model", "")
        tool = f"{provider}/{model}" if model else provider
        try:
            cost_f = float(entry.get("cost_usd", 0.0))
        except (TypeError, ValueError):
            cost_f = 0.0
        cost_str = f"${cost_f:.4f}" if cost_f else ""
        try:
            elapsed = float(entry.get("elapsed_s", 0))
            detail = f"{elapsed:.1f}s" if elapsed else ""
        except (TypeError, ValueError):
            elapsed = 0
            detail = ""
        tooltip = f"{provider} {model} cost={cost_str}".strip()
        status = "—"
        row_type = "llm"

    elif etype == "hook":
        tool = "HOOK"
        text = entry.get("text", "")
        detail = text[:80]
        tooltip = text
        cost_f = 0.0
        cost_str = ""
        status = "—"
        row_type = "hook"

    else:
        tool = etype
        detail = ""
        tooltip = ""
        cost_f = 0.0
        cost_str = ""
        status = "—"
        row_type = etype

    return {
        "time_str": time_str,
        "tool": tool,
        "detail": detail,
        "cost_str": cost_str,
        "status": status,
        "row_type": row_type,
        "tooltip": tooltip,
        "cost_f": cost_f,
        "sort_key": sort_key,
    }


# ---------------------------------------------------------------------------
# Card building
# ---------------------------------------------------------------------------

def _build_card(row: dict) -> Gtk.Box:
    """Create a single HBox card widget from a row dict."""
    row_type = row["row_type"]

    card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    ctx = card.get_style_context()
    ctx.add_class("log-card")
    ctx.add_class(f"log-card-{row_type}")

    if row["tooltip"]:
        card.set_tooltip_text(row["tooltip"])

    # Time label — fixed 65px
    time_lbl = Gtk.Label(label=row["time_str"])
    time_lbl.set_size_request(65, -1)
    time_lbl.set_xalign(0.0)
    time_lbl.get_style_context().add_class("log-time")
    card.pack_start(time_lbl, False, False, 0)

    # Tool label — fixed 120px, ellipsize
    tool_lbl = Gtk.Label(label=row["tool"])
    tool_lbl.set_size_request(120, -1)
    tool_lbl.set_xalign(0.0)
    tool_lbl.set_ellipsize(Pango.EllipsizeMode.END)
    tool_lbl.get_style_context().add_class("log-tool-name")
    card.pack_start(tool_lbl, False, False, 0)

    # Detail label — hexpand, ellipsize
    detail_lbl = Gtk.Label(label=row["detail"])
    detail_lbl.set_hexpand(True)
    detail_lbl.set_xalign(0.0)
    detail_lbl.set_ellipsize(Pango.EllipsizeMode.END)
    detail_lbl.get_style_context().add_class("log-detail")
    card.pack_start(detail_lbl, True, True, 0)

    # Status label — fixed 20px
    status = row["status"]
    status_lbl = Gtk.Label(label=status)
    status_lbl.set_size_request(20, -1)
    status_lbl.set_xalign(0.5)
    status_ctx = status_lbl.get_style_context()
    if status == "✓":
        status_ctx.add_class("log-status-ok")
    elif status == "✗":
        status_ctx.add_class("log-status-error")
    else:
        status_ctx.add_class("log-status-neutral")
    card.pack_start(status_lbl, False, False, 0)

    # Cost label — fixed 65px, right-aligned
    cost_lbl = Gtk.Label(label=row["cost_str"])
    cost_lbl.set_size_request(65, -1)
    cost_lbl.set_xalign(1.0)
    cost_lbl.get_style_context().add_class("log-cost")
    card.pack_start(cost_lbl, False, False, 0)

    return card


def _rebuild_cards():
    """Clear and rebuild the cards container from _last_entries."""
    if _cards_container is None:
        return
    for child in _cards_container.get_children():
        _cards_container.remove(child)

    filtered = _filter_entries(_last_entries)
    for entry in filtered:
        row = _entry_to_row(entry)
        card = _build_card(row)
        _cards_container.pack_start(card, False, False, 0)
    _cards_container.show_all()


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _filter_entries(entries: list) -> list:
    """Filter entries by combo selection and search text."""
    if _filter_combo is None:
        return entries

    active = _filter_combo.get_active_text() or "Alle"
    type_map = {
        "Alle": None,
        "Tools": "tool",
        "LLM": "llm",
        "Errors": "error",
        "Hooks": "hook",
    }
    filter_type = type_map.get(active)

    search_text = ""
    if _search_entry is not None:
        search_text = (_search_entry.get_text() or "").lower().strip()

    result = []
    for entry in entries:
        etype = entry.get("_type", "tool")
        # For error filtering: match on entry error field
        if filter_type == "error":
            if not entry.get("error"):
                continue
        elif filter_type is not None:
            if etype != filter_type:
                continue

        if search_text:
            tool_val = (entry.get("tool", "") or entry.get("provider", "") or etype or "").lower()
            detail_val = (
                entry.get("file", "") or
                entry.get("cmd_cat", "") or
                entry.get("text", "") or
                entry.get("model", "") or
                ""
            ).lower()
            if search_text not in tool_val and search_text not in detail_val:
                continue

        result.append(entry)
    return result


def _on_filter_changed(_widget):
    """Rebuild cards when combo or search entry changes."""
    _rebuild_cards()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _update_stats(entries: list):
    if _stats_label is None:
        return
    tool_count = sum(1 for e in entries if e.get("_type") == "tool")
    error_count = sum(1 for e in entries if e.get("error"))
    total_cost = sum(e.get("cost_usd", 0.0) for e in entries if e.get("_type") == "llm")
    now = datetime.now().strftime("%H:%M")
    parts = [f"{tool_count} calls"]
    if error_count:
        parts.append(f"{error_count} errors")
    if total_cost:
        parts.append(f"${total_cost:.3f}")
    parts.append(now)
    _stats_label.set_text(" | ".join(parts))
