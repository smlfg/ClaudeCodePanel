#!/usr/bin/env python3
"""Session Browser — GTK3 widget module for Claude Code Control Panel.

Provides build_sessions_tab() returning a Gtk.ScrolledWindow with:
- Full session list from ~/.claude/projects/ (last 20 by mtime)
- Search/filter by project name or preview text
- Per-session resume button (kitty -e claude -r SESSION_ID)
- Stats bar: total sessions, sessions in last hour, unique projects
- Refresh button to re-scan

Theme: Catppuccin Mocha (dark) / Latte (light) via theme.py
"""

import json
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from utils import idle_once, short_name_from_path, HOME

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECTS_DIR = Path(HOME) / ".claude" / "projects"
MAX_SESSIONS = 20

# Colors are handled by CSS classes defined in theme.py

# ---------------------------------------------------------------------------
# Module-level state (kept between refreshes)
# Grouped into a single namespace dict to avoid scattered globals.
# ---------------------------------------------------------------------------
_state: dict = {
    "list_box": None,       # Gtk.ListBox | None
    "stats_label": None,    # Gtk.Label | None
    "search_entry": None,   # Gtk.SearchEntry | None
    "all_sessions": [],     # list[dict]
}


# ---------------------------------------------------------------------------
# Session scanning helpers
# ---------------------------------------------------------------------------

def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


_PREVIEW_SKIP_PREFIXES = (
    "[Request interrupted",
    "Implement the following plan",
)


def _get_session_meta(path: Path, max_len: int = 60) -> dict:
    """Read metadata from JSONL file: preview text, cwd, slug.

    Skips junk messages like '[Request interrupted by user for tool use]'
    and 'Implement the following plan:'.  For plan messages, extracts the
    plan title (the first '# ...' heading).  Falls back to the session slug.
    """
    slug: str = ""
    cwd: str = ""
    preview: str = ""
    lines_read = 0
    max_lines = 80  # scan deeper — real messages can be far down after compression
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                lines_read += 1
                if lines_read > max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Capture slug and cwd from any entry
                    if not slug and entry.get("slug"):
                        slug = entry["slug"]
                    if not cwd and entry.get("cwd"):
                        cwd = entry["cwd"]
                    if preview:
                        # Already found preview, but keep scanning for cwd/slug
                        if cwd and slug:
                            break
                        continue
                    if entry.get("type") != "user":
                        continue
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                content = block.get("text", "")
                                break
                        else:
                            content = str(content)
                    if not isinstance(content, str):
                        continue
                    content = content.strip()
                    # Skip junk messages
                    if any(content.startswith(p) for p in _PREVIEW_SKIP_PREFIXES):
                        # For plan messages, try to extract the title
                        if content.startswith("Implement the following plan"):
                            for plan_line in content.split("\n"):
                                plan_line = plan_line.strip()
                                if plan_line.startswith("# "):
                                    title = plan_line[2:].strip()
                                    if title.lower().startswith("plan:"):
                                        title = title[5:].strip()
                                    if len(title) > max_len:
                                        title = title[:max_len] + "..."
                                    preview = title
                                    break
                        continue
                    first_line = content.split("\n")[0]
                    if len(first_line) > max_len:
                        first_line = first_line[:max_len] + "..."
                    preview = first_line
                except json.JSONDecodeError:
                    continue
    except (OSError, PermissionError):
        pass
    if not preview:
        preview = slug if slug else "(keine Vorschau)"
    return {"preview": preview, "cwd": cwd, "slug": slug}


def _scan_all_sessions() -> list[dict]:
    """Scan ~/.claude/projects/ and return session dicts sorted by mtime desc."""
    sessions: list[dict] = []

    if not PROJECTS_DIR.exists():
        return sessions

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            try:
                stat = f.stat()
                meta = _get_session_meta(f)
                preview = meta["preview"]
                session_cwd = meta["cwd"]
                # Use real CWD for short name (preserves umlauts)
                short_name = short_name_from_path(session_cwd)
                # Build full project path from CWD for display
                if session_cwd and session_cwd != HOME:
                    project_name = session_cwd.replace(HOME, "~", 1)
                else:
                    project_name = "Home"
                sessions.append(
                    {
                        "path": str(f),
                        "project": project_name,
                        "short_name": short_name,
                        "session_id": f.stem,
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                        "time_str": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%d.%m %H:%M"
                        ),
                        "preview": preview,
                    }
                )
            except (OSError, PermissionError):
                continue

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def _compute_stats(sessions: list[dict], now: float | None = None) -> str:
    total = len(sessions)
    if now is None:
        now = time.time()
    active = sum(1 for s in sessions if now - s["mtime"] < 120)
    recent = sum(1 for s in sessions if now - s["mtime"] < 3600)
    projects = len({s["project"] for s in sessions})
    parts = [f"{total} Sessions"]
    if active:
        parts.append(f"{active} aktiv")
    parts.append(f"{recent} in letzter Stunde")
    parts.append(f"{projects} Projekte")
    return "  |  ".join(parts)


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_session_row(session: dict, now: float | None = None) -> Gtk.ListBoxRow:
    """Build a single styled ListBoxRow for one session."""
    if now is None:
        now = time.time()
    row = Gtk.ListBoxRow()
    row.set_name("session-row")
    row.get_style_context().add_class("session-row")

    is_active = (now - session["mtime"]) < 120  # 2 min
    if is_active:
        row.get_style_context().add_class("session-row-active")

    # Outer box
    outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    outer.set_margin_top(4)
    outer.set_margin_bottom(4)
    outer.set_margin_start(8)
    outer.set_margin_end(10)
    row.add(outer)

    # Left: short name + preview + full path stacked vertically
    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    info_box.set_hexpand(True)
    outer.pack_start(info_box, True, True, 0)

    # Short project name — bold, dedicated class
    name_label = Gtk.Label(label=session["short_name"])
    name_label.set_halign(Gtk.Align.START)
    name_label.set_ellipsize(Pango.EllipsizeMode.END)
    name_label.set_max_width_chars(45)
    name_label.get_style_context().add_class("session-project")
    info_box.pack_start(name_label, False, False, 0)

    # Preview — Subtext1 for better contrast
    preview_label = Gtk.Label(label=session["preview"])
    preview_label.set_halign(Gtk.Align.START)
    preview_label.set_ellipsize(Pango.EllipsizeMode.END)
    preview_label.set_max_width_chars(60)
    preview_label.get_style_context().add_class("session-preview")
    info_box.pack_start(preview_label, False, False, 0)

    # Full project path — dimmed, only if different from short_name
    if session["project"] != session["short_name"]:
        path_label = Gtk.Label(label=session["project"])
        path_label.set_halign(Gtk.Align.START)
        path_label.set_ellipsize(Pango.EllipsizeMode.END)
        path_label.set_max_width_chars(60)
        path_label.get_style_context().add_class("session-meta")
        path_attrs = Pango.AttrList()
        path_attrs.insert(Pango.attr_scale_new(0.82))
        path_label.set_attributes(path_attrs)
        info_box.pack_start(path_label, False, False, 0)

    # Right: meta info + resume button
    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    meta_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(meta_box, False, False, 0)

    # "AKTIV" badge for running sessions
    if is_active:
        active_label = Gtk.Label(label="AKTIV")
        active_label.get_style_context().add_class("session-active-badge")
        active_label.set_halign(Gtk.Align.END)
        meta_box.pack_start(active_label, False, False, 0)

    # Date/time + size — dimmed metadata
    meta_line = Gtk.Label(label=f"{session['time_str']}  {_format_size(session['size'])}")
    meta_line.set_halign(Gtk.Align.END)
    meta_line.get_style_context().add_class("session-meta")
    meta_box.pack_start(meta_line, False, False, 0)

    # Resume button — custom styled
    resume_btn = Gtk.Button(label="Resume")
    resume_btn.set_tooltip_text(f"claude -r {session['session_id']}")
    resume_btn.get_style_context().add_class("session-resume")
    session_id = session["session_id"]
    resume_btn.connect("clicked", _on_resume_clicked, session_id)
    meta_box.pack_start(resume_btn, False, False, 0)

    # Store session data on row for filtering
    row._session_data = session  # type: ignore[attr-defined]

    row.show_all()
    return row


def _on_resume_clicked(_btn: Gtk.Button, session_id: str) -> None:
    """Launch kitty terminal with claude -r SESSION_ID."""
    try:
        # List-form Popen (not shell=True) is safe against injection regardless of
        # special characters in session_id — each element is passed as a literal argument.
        subprocess.Popen(
            ["kitty", "-e", "claude", "-r", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # kitty not found — fallback terminal uses shell=False list form too.
        # shlex.quote is applied to session_id only for the shell=True bash -c string.
        try:
            subprocess.Popen(
                ["x-terminal-emulator", "-e", "bash", "-c", f"claude -r {shlex.quote(session_id)}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Filter function for Gtk.ListBox
# ---------------------------------------------------------------------------

def _filter_func(row: Gtk.ListBoxRow) -> bool:
    """Return True if row matches current search query."""
    if _state["search_entry"] is None:
        return True
    query = _state["search_entry"].get_text().strip().lower()
    if not query:
        return True
    session = getattr(row, "_session_data", None)
    if session is None:
        return True
    return (
        query in session.get("short_name", "").lower()
        or query in session["project"].lower()
        or query in session["preview"].lower()
        or query in session["session_id"].lower()
    )


# ---------------------------------------------------------------------------
# Populate / refresh the list
# ---------------------------------------------------------------------------

def _populate_list_box(sessions: list[dict]) -> None:
    """Clear and re-populate the ListBox with new session rows."""
    _state["all_sessions"] = sessions

    if _state["list_box"] is None:
        return

    # Remove all existing children
    for child in _state["list_box"].get_children():
        _state["list_box"].remove(child)

    # Add rows (up to MAX_SESSIONS)
    now = time.time()
    for session in sessions[:MAX_SESSIONS]:
        row = _build_session_row(session, now)
        _state["list_box"].add(row)

    _state["list_box"].show_all()

    # Update stats bar
    if _state["stats_label"] is not None:
        _state["stats_label"].set_text(_compute_stats(sessions, now))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh_sessions() -> bool:
    """Re-scan sessions and update the UI. Returns True to keep GLib timer alive."""
    try:
        sessions = _scan_all_sessions()
        GLib.idle_add(_populate_list_box, sessions)
    except Exception:  # noqa: BLE001
        pass
    return True  # keep timer running


def build_sessions_tab() -> Gtk.ScrolledWindow:
    """Build and return the Sessions tab widget (Gtk.ScrolledWindow)."""

    # -----------------------------------------------------------------------
    # Root: ScrolledWindow → main VBox
    # -----------------------------------------------------------------------
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)

    main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    main_vbox.set_margin_top(8)
    main_vbox.set_margin_bottom(8)
    main_vbox.set_margin_start(8)
    main_vbox.set_margin_end(8)
    scrolled.add(main_vbox)

    # -----------------------------------------------------------------------
    # Toolbar: title + search + refresh button
    # -----------------------------------------------------------------------
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_bottom(6)
    main_vbox.pack_start(toolbar, False, False, 0)

    title_label = Gtk.Label(label="Sessions")
    title_label.get_style_context().add_class("section-title")
    title_attrs = Pango.AttrList()
    title_attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
    title_attrs.insert(Pango.attr_scale_new(1.1))
    title_label.set_attributes(title_attrs)
    title_label.set_halign(Gtk.Align.START)
    toolbar.pack_start(title_label, False, False, 0)

    # Spacer
    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    toolbar.pack_start(spacer, True, True, 0)

    # Search entry
    _state["search_entry"] = Gtk.SearchEntry()
    _state["search_entry"].set_placeholder_text("Projekt oder Inhalt suchen…")
    _state["search_entry"].set_size_request(220, -1)
    toolbar.pack_start(_state["search_entry"], False, False, 0)

    # Refresh button
    refresh_btn = Gtk.Button()
    refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
    refresh_btn.add(refresh_icon)
    refresh_btn.set_tooltip_text("Sessions neu einlesen")
    refresh_btn.connect("clicked", lambda _b: refresh_sessions())
    toolbar.pack_start(refresh_btn, False, False, 0)

    # -----------------------------------------------------------------------
    # Stats pill (rounded bar instead of frame)
    # -----------------------------------------------------------------------
    _state["stats_label"] = Gtk.Label(label="Lade Sessions…")
    _state["stats_label"].get_style_context().add_class("session-stats")
    _state["stats_label"].set_margin_top(2)
    _state["stats_label"].set_margin_bottom(8)
    _state["stats_label"].set_halign(Gtk.Align.START)
    main_vbox.pack_start(_state["stats_label"], False, False, 0)

    # -----------------------------------------------------------------------
    # ListBox for sessions
    # -----------------------------------------------------------------------
    _state["list_box"] = Gtk.ListBox()
    _state["list_box"].set_selection_mode(Gtk.SelectionMode.NONE)
    _state["list_box"].set_filter_func(_filter_func)
    _state["list_box"].get_style_context().add_class("view")
    main_vbox.pack_start(_state["list_box"], True, True, 0)

    # Connect search to filter
    _state["search_entry"].connect("search-changed", lambda _e: _state["list_box"].invalidate_filter())

    # -----------------------------------------------------------------------
    # Initial load (non-blocking via idle_add)
    # -----------------------------------------------------------------------
    idle_once(lambda: _populate_list_box(_scan_all_sessions()))

    scrolled.show_all()
    return scrolled
