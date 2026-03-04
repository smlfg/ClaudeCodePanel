"""Log viewer widget for Claude Code Control Panel.

TreeView-based log viewer with sortable columns, filtering, and cost tracking.
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

# Column indices for ListStore
_COL_TIME = 0       # str  HH:MM:SS
_COL_TOOL = 1       # str  tool name / label
_COL_DETAIL = 2     # str  file path, cmd_cat, or hook text
_COL_COST = 3       # str  formatted cost string
_COL_STATUS = 4     # str  "✓" / "✗" / "—"
_COL_TYPE = 5       # str  entry type: "tool" / "error" / "hook" / "llm"
_COL_TOOLTIP = 6    # str  full detail for tooltip
_COL_COST_F = 7     # float  numeric cost for sorting
_COL_SORTKEY = 8    # str  ISO timestamp for sort

# Module-level widget references
_treeview = None
_liststore = None
_model_filter = None
_filter_combo = None
_search_entry = None
_stats_label = None
_last_fingerprint = ("", 0)


def build_logs_tab() -> Gtk.ScrolledWindow:
    """Build and return the Logs tab widget."""
    global _treeview, _liststore, _model_filter
    global _filter_combo, _search_entry, _stats_label

    outer = Gtk.ScrolledWindow()
    outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    main_box.set_margin_top(10)
    main_box.set_margin_bottom(10)
    main_box.set_margin_start(10)
    main_box.set_margin_end(10)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

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
    _stats_label.set_xalign(1)
    _stats_label.set_hexpand(True)
    toolbar.pack_start(_stats_label, True, True, 0)

    main_box.pack_start(toolbar, False, False, 0)
    main_box.pack_start(
        Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 0
    )

    # --- ListStore: 9 columns ---
    _liststore = Gtk.ListStore(str, str, str, str, str, str, str, float, str)

    # --- Filter model ---
    _model_filter = Gtk.TreeModelFilter(child_model=_liststore)
    _model_filter.set_visible_func(_row_visible)

    # --- Sort model on top of filter ---
    sort_model = Gtk.TreeModelSort(model=_model_filter)

    # --- TreeView ---
    _treeview = Gtk.TreeView(model=sort_model)
    _treeview.set_headers_clickable(True)
    _treeview.set_rules_hint(True)
    _treeview.set_tooltip_column(_COL_TOOLTIP)

    # Column: Zeit
    col_time = _make_text_column("Zeit", _COL_TIME, sort_col=_COL_SORTKEY,
                                 sort_model=sort_model, width=75)
    _treeview.append_column(col_time)

    # Column: Tool
    col_tool = _make_text_column("Tool", _COL_TOOL, sort_col=_COL_TOOL,
                                 sort_model=sort_model, width=120, monospace=True)
    _treeview.append_column(col_tool)

    # Column: Detail (file/cmd_cat/hook text)
    col_detail = _make_text_column("Detail", _COL_DETAIL, sort_col=None,
                                   sort_model=sort_model, width=200, ellipsize=True)
    _treeview.append_column(col_detail)

    # Column: Cost
    col_cost = _make_text_column("Cost", _COL_COST, sort_col=_COL_COST_F,
                                 sort_model=sort_model, width=70, xalign=1.0)
    _treeview.append_column(col_cost)

    # Column: Status — with cell data function for coloring
    renderer_status = Gtk.CellRendererText()
    renderer_status.set_property("xalign", 0.5)
    col_status = Gtk.TreeViewColumn("Sta", renderer_status, text=_COL_STATUS)
    col_status.set_fixed_width(40)
    col_status.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
    col_status.set_cell_data_func(renderer_status, _status_cell_data_func)
    _treeview.append_column(col_status)

    # Scroll for TreeView
    tv_scroll = Gtk.ScrolledWindow()
    tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    tv_scroll.set_vexpand(True)
    tv_scroll.add(_treeview)

    main_box.pack_start(tv_scroll, True, True, 0)
    outer.add(main_box)

    idle_once(refresh_logs)
    return outer


def refresh_logs() -> bool:
    """Refresh log data from disk. Returns True to keep timer alive."""
    global _last_fingerprint
    try:
        if _liststore is None:
            return True

        entries = _read_usage_entries() + _read_coaching_entries() + _read_provider_costs_today()
        entries.sort(key=lambda e: e.get("_sort_key", ""), reverse=True)
        entries = entries[:_MAX_ENTRIES]

        fingerprint = (entries[0].get("_sort_key", "") if entries else "", len(entries))
        if fingerprint == _last_fingerprint:
            _update_stats(entries)
            return True

        _liststore.clear()
        for entry in entries:
            _liststore.append(_entry_to_row(entry))

        _last_fingerprint = fingerprint
        _update_stats(entries)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Data reading
# ---------------------------------------------------------------------------

def _read_usage_entries() -> list[dict]:
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


def _read_coaching_entries() -> list[dict]:
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


def _read_provider_costs_today() -> list[dict]:
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

def _entry_to_row(entry: dict) -> list:
    """Convert an entry dict to a ListStore row (9 values)."""
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
        # Shorten absolute paths for display
        if detail.startswith("/home/smlflg/"):
            detail = "~/" + detail[len("/home/smlflg/"):]
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
        cost_f = float(entry.get("cost_usd", 0.0))
        cost_str = f"${cost_f:.4f}" if cost_f else ""
        elapsed = entry.get("elapsed_s")
        detail = f"{elapsed:.1f}s" if elapsed else ""
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

    return [
        time_str,   # _COL_TIME
        tool,       # _COL_TOOL
        detail,     # _COL_DETAIL
        cost_str,   # _COL_COST
        status,     # _COL_STATUS
        row_type,   # _COL_TYPE
        tooltip,    # _COL_TOOLTIP
        cost_f,     # _COL_COST_F
        sort_key,   # _COL_SORTKEY
    ]


# ---------------------------------------------------------------------------
# TreeView helpers
# ---------------------------------------------------------------------------

def _make_text_column(title: str, text_col: int, sort_col, sort_model,
                      width: int = 100, monospace: bool = False,
                      xalign: float = 0.0, ellipsize: bool = False) -> Gtk.TreeViewColumn:
    renderer = Gtk.CellRendererText()
    renderer.set_property("xalign", xalign)
    if monospace:
        renderer.set_property("font", "Monospace 9")
    if ellipsize:
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)

    col = Gtk.TreeViewColumn(title, renderer, text=text_col)
    col.set_fixed_width(width)
    col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
    col.set_resizable(True)

    if sort_col is not None:
        # Map from filter-model column to sort-model column (same indices)
        col.set_sort_column_id(sort_col)

    return col


def _status_cell_data_func(col, renderer, model, it, data):
    """Color the status cell based on value."""
    status = model.get_value(it, _COL_STATUS)
    if status == "✓":
        renderer.set_property("foreground", "#a6e3a1")  # Catppuccin green
    elif status == "✗":
        renderer.set_property("foreground", "#f38ba8")  # Catppuccin red
    else:
        renderer.set_property("foreground", "#585b70")  # dim


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _row_visible(model, it, data) -> bool:
    """Visibility function for Gtk.TreeModelFilter."""
    if _filter_combo is None:
        return True

    active = _filter_combo.get_active_text() or "Alle"
    type_map = {
        "Alle": None,
        "Tools": "tool",
        "LLM": "llm",
        "Errors": "error",
        "Hooks": "hook",
    }
    filter_type = type_map.get(active)
    row_type = model.get_value(it, _COL_TYPE)

    if filter_type is not None and row_type != filter_type:
        return False

    search_text = ""
    if _search_entry is not None:
        search_text = (_search_entry.get_text() or "").lower().strip()

    if search_text:
        tool = (model.get_value(it, _COL_TOOL) or "").lower()
        detail = (model.get_value(it, _COL_DETAIL) or "").lower()
        if search_text not in tool and search_text not in detail:
            return False

    return True


def _on_filter_changed(_widget):
    """Refilter when combo or search entry changes."""
    if _model_filter is not None:
        _model_filter.refilter()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _update_stats(entries: list[dict]):
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
