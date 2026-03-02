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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECTS_DIR = Path.home() / ".claude" / "projects"
MAX_SESSIONS = 20

# Colors are handled by CSS classes defined in theme.py

# ---------------------------------------------------------------------------
# Module-level state (kept between refreshes)
# ---------------------------------------------------------------------------
_list_box: Gtk.ListBox | None = None
_stats_label: Gtk.Label | None = None
_search_entry: Gtk.SearchEntry | None = None
_all_sessions: list[dict] = []


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


def _get_preview(path: Path, max_len: int = 60) -> str:
    """Read first user message from JSONL file."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "user":
                        msg = entry.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    content = block.get("text", "")
                                    break
                            else:
                                content = str(content)
                        if isinstance(content, str):
                            content = content.strip().split("\n")[0]
                            if len(content) > max_len:
                                content = content[:max_len] + "..."
                            return content
                except json.JSONDecodeError:
                    continue
    except (OSError, PermissionError):
        pass
    return "(keine Vorschau)"


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
                preview = _get_preview(f)
                raw_name = project_dir.name
                # Normalise project name: strip common home prefix fragments
                project_name = raw_name
                _home_frag = "-home-" + Path.home().name
                project_name = project_name.replace(_home_frag + "-", "~/")
                project_name = project_name.replace(_home_frag, "~")
                # Strip leading dashes left over
                project_name = project_name.lstrip("-")
                if not project_name or project_name in ("~", "~/"):
                    project_name = "Home"
                sessions.append(
                    {
                        "path": str(f),
                        "project": project_name,
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

def _compute_stats(sessions: list[dict]) -> str:
    total = len(sessions)
    now = time.time()
    recent = sum(1 for s in sessions if now - s["mtime"] < 3600)
    projects = len({s["project"] for s in sessions})
    return f"{total} Sessions  |  {recent} in letzter Stunde  |  {projects} Projekte"


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _build_session_row(session: dict) -> Gtk.ListBoxRow:
    """Build a single styled ListBoxRow for one session."""
    row = Gtk.ListBoxRow()
    row.set_name("session-row")
    row.get_style_context().add_class("session-row")

    # Outer box
    outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    outer.set_margin_top(4)
    outer.set_margin_bottom(4)
    outer.set_margin_start(8)
    outer.set_margin_end(10)
    row.add(outer)

    # Left: project name + preview stacked vertically
    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    info_box.set_hexpand(True)
    outer.pack_start(info_box, True, True, 0)

    # Project name — bold, dedicated class
    name_label = Gtk.Label(label=session["project"])
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

    # Right: meta info + resume button
    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    meta_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(meta_box, False, False, 0)

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
        subprocess.Popen(
            ["kitty", "-e", "claude", "-r", session_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        # kitty not found — try fallback terminal
        try:
            subprocess.Popen(
                ["x-terminal-emulator", "-e", f"claude -r {shlex.quote(session_id)}"],
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
    if _search_entry is None:
        return True
    query = _search_entry.get_text().strip().lower()
    if not query:
        return True
    session = getattr(row, "_session_data", None)
    if session is None:
        return True
    return (
        query in session["project"].lower()
        or query in session["preview"].lower()
        or query in session["session_id"].lower()
    )


# ---------------------------------------------------------------------------
# Populate / refresh the list
# ---------------------------------------------------------------------------

def _populate_list_box(sessions: list[dict]) -> None:
    """Clear and re-populate the ListBox with new session rows."""
    global _all_sessions
    _all_sessions = sessions

    if _list_box is None:
        return

    # Remove all existing children
    for child in _list_box.get_children():
        _list_box.remove(child)

    # Add rows (up to MAX_SESSIONS)
    for session in sessions[:MAX_SESSIONS]:
        row = _build_session_row(session)
        _list_box.add(row)

    _list_box.show_all()

    # Update stats bar
    if _stats_label is not None:
        _stats_label.set_text(_compute_stats(sessions))


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
    global _list_box, _stats_label, _search_entry

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
    _search_entry = Gtk.SearchEntry()
    _search_entry.set_placeholder_text("Projekt oder Inhalt suchen…")
    _search_entry.set_size_request(220, -1)
    toolbar.pack_start(_search_entry, False, False, 0)

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
    _stats_label = Gtk.Label(label="Lade Sessions…")
    _stats_label.get_style_context().add_class("session-stats")
    _stats_label.set_margin_top(2)
    _stats_label.set_margin_bottom(8)
    _stats_label.set_halign(Gtk.Align.START)
    main_vbox.pack_start(_stats_label, False, False, 0)

    # -----------------------------------------------------------------------
    # ListBox for sessions
    # -----------------------------------------------------------------------
    _list_box = Gtk.ListBox()
    _list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    _list_box.set_filter_func(_filter_func)
    _list_box.get_style_context().add_class("view")
    main_vbox.pack_start(_list_box, True, True, 0)

    # Connect search to filter
    _search_entry.connect("search-changed", lambda _e: _list_box.invalidate_filter())

    # -----------------------------------------------------------------------
    # Initial load (non-blocking via idle_add)
    # -----------------------------------------------------------------------
    def _initial_load() -> bool:
        sessions = _scan_all_sessions()
        _populate_list_box(sessions)
        return False  # run once

    GLib.idle_add(_initial_load)

    scrolled.show_all()
    return scrolled
