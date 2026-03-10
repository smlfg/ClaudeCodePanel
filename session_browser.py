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

import html as html_mod
import json
import logging
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("claude_panel.session_browser")

# Module-level metadata cache: path → (mtime, meta_dict)
_meta_cache: dict[str, tuple[float, dict]] = {}

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from utils import idle_once, short_name_from_path, HOME

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECTS_DIR = Path(HOME) / ".claude" / "projects"

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
    "search_mode": False,   # True when in grep search mode
    "debounce_id": None,    # GLib timeout source ID for debounce
    "batch_id": None,       # GLib idle source ID for batch insertion
}

# Cancellation token for the background grep thread.
# Replaced with a fresh Event each time a new search starts.
_search_cancel: threading.Event = threading.Event()


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
    "<local-command-caveat>",
    "<command-name>",
)


def _get_session_meta(path: Path, max_len: int = 120) -> dict:
    """Read metadata from JSONL file: preview text, cwd, slug.

    Uses a module-level mtime cache — unchanged files are never re-read.
    For files under 512KB: reads all lines ONCE, extracts first 30 + last 10.
    For large files (>=512KB): reads first 30 lines + subprocess tail for last 10.
    """
    path_str = str(path)
    try:
        stat = path.stat()
        mtime = stat.st_mtime
        file_size = stat.st_size
    except OSError:
        return {"preview": "(keine Vorschau)", "cwd": "", "slug": ""}

    # Cache hit — file unchanged since last read
    cached = _meta_cache.get(path_str)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    slug: str = ""
    cwd: str = ""
    preview: str = ""

    try:
        if file_size < 512 * 1024:
            # Single read — extract head and tail from the same list
            with path.open(encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            head_lines = all_lines[:30]
            tail_lines = all_lines[-10:] if len(all_lines) > 30 else []
        else:
            # Large file: read first 30 lines + subprocess tail
            head_lines = []
            with path.open(encoding="utf-8", errors="replace") as f:
                for _ in range(30):
                    line = f.readline()
                    if not line:
                        break
                    head_lines.append(line)
            try:
                result = subprocess.run(
                    ["tail", "-n", "10", str(path)],
                    capture_output=True, text=True, timeout=2
                )
                tail_lines = result.stdout.splitlines(keepends=True)
            except (subprocess.TimeoutExpired, OSError):
                tail_lines = []

        all_sample = head_lines + tail_lines

        for line in all_sample:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not slug and entry.get("slug"):
                slug = entry["slug"]
            if not cwd and entry.get("cwd"):
                cwd = entry["cwd"]

            if preview:
                if cwd and slug:
                    break
                continue

            if entry.get("type") != "user":
                continue

            msg = entry.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                text_blocks = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and "<local-command-caveat>" not in block.get("text", "")
                ]
                if text_blocks:
                    content = text_blocks[0]
                else:
                    continue
            if not isinstance(content, str):
                continue
            content = content.strip()
            if any(content.startswith(p) for p in _PREVIEW_SKIP_PREFIXES):
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
    except (OSError, PermissionError):
        pass

    if not preview:
        preview = slug if slug else "(keine Vorschau)"
    result = {"preview": preview, "cwd": cwd, "slug": slug}
    _meta_cache[path_str] = (mtime, result)
    return result


def _get_session_status(path: Path, mtime: float, now: float) -> str:
    """Determine session status from last JSONL entry.

    Returns:
        "working"  — Claude is actively processing (last entry is tool_use or user msg)
        "ready"    — Claude finished, waiting for user input
        "idle"     — session not recently active
    """
    age = now - mtime
    if age > 120:
        return "idle"

    # Read last non-empty line of JSONL (seek from end for speed)
    try:
        size = path.stat().st_size
        if size == 0:
            return "idle"
        # Read last 4KB — enough for the final JSONL line
        read_size = min(size, 4096)
        with path.open("rb") as f:
            f.seek(-read_size, 2)
            tail = f.read().decode("utf-8", errors="replace")
        lines = [l for l in tail.strip().splitlines() if l.strip()]
        if not lines:
            return "idle"
        last_line = lines[-1]
        try:
            entry = json.loads(last_line)
        except json.JSONDecodeError:
            # If last line is corrupt, check second-to-last
            if len(lines) >= 2:
                try:
                    entry = json.loads(lines[-2])
                except json.JSONDecodeError:
                    return "ready" if age < 120 else "idle"
            else:
                return "ready" if age < 120 else "idle"

        entry_type = entry.get("type", "")
        msg = entry.get("message", {})

        # User message → Claude is about to process
        if entry_type == "user":
            return "working"

        # Assistant message → check for tool_use (Claude still working)
        if entry_type == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        return "working"
            # Check stop_reason — tool_use means Claude wants to call a tool
            stop = msg.get("stop_reason", "")
            if stop == "tool_use":
                return "working"
            # end_turn or max_tokens → Claude is done
            return "ready"

        # Tool result → Claude is processing
        if entry_type == "tool_result":
            return "working"

        # Fallback: if very recent mtime, likely working
        return "working" if age < 5 else "ready"

    except (OSError, PermissionError):
        return "ready" if age < 120 else "idle"


def _build_project_info(path: Path, session_cwd: str) -> tuple[str, str]:
    """Derive (project_name, short_name) from a session's CWD or parent dir name.

    Shared by _scan_all_sessions() and _grep_sessions() to avoid duplicated logic.
    Returns (project_name, short_name).
    """
    short_name = short_name_from_path(session_cwd)
    if session_cwd and session_cwd != HOME:
        project_name = session_cwd.replace(HOME, "~", 1)
    else:
        # Fallback: derive project from JSONL parent dir name
        # e.g. "-home-smlflg-Projekte-ClaudeCodePanel" → "ClaudeCodePanel"
        dir_name = path.parent.name  # encoded path
        parts = dir_name.split("-")
        # Strip leading home-user prefix
        # "-home-smlflg-Projekte-Foo" → ["", "home", "smlflg", "Projekte", "Foo"]
        meaningful = [p for p in parts if p and p not in ("home", "smlflg")]
        if meaningful:
            short_name = meaningful[-1]
            project_name = "~/" + "/".join(meaningful)
        else:
            project_name = "Home"
    return project_name, short_name


def _scan_all_sessions() -> list[dict]:
    """Scan ~/.claude/projects/ and return session dicts sorted by mtime desc.

    Caps at 200 most-recently-modified JSONL files to bound scan time on
    large installations (2800+ files). Older sessions remain accessible
    via fulltext search (_grep_sessions).
    """
    sessions: list[dict] = []

    if not PROJECTS_DIR.exists():
        return sessions

    # Collect all JSONL paths first, then sort by mtime and cap at 200
    all_files: list[Path] = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            all_files.append(f)

    # Sort newest-first and limit to 200 — avoids scanning all 2800+ files
    all_files = sorted(all_files, key=lambda p: p.stat().st_mtime, reverse=True)[:200]

    now = time.time()
    for f in all_files:
        try:
            stat = f.stat()
            meta = _get_session_meta(f)
            project_name, short_name = _build_project_info(f, meta["cwd"])
            status = _get_session_status(f, stat.st_mtime, now)
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
                    "preview": meta["preview"],
                    "slug": meta["slug"],
                    "status": status,
                }
            )
        except (OSError, PermissionError):
            continue

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def _grep_sessions(query: str) -> list[dict]:
    """Fulltext search across all session files using grep -l -i.

    Returns matching session dicts with snippet context.
    Runs synchronously (~0.3s for 649 files).
    """
    if not PROJECTS_DIR.exists():
        return []

    # Collect all JSONL files
    all_files = []
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            all_files.append(str(f))

    if not all_files:
        return []

    # grep -l -i for matching files
    try:
        result = subprocess.run(
            ["grep", "-l", "-i", "--", query] + all_files,
            capture_output=True, text=True, timeout=10
        )
        matching_files = [f for f in result.stdout.splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, OSError):
        return []

    if not matching_files:
        return []

    # Batch grep -c for match counts (one subprocess for all files)
    counts: dict[str, int] = {}
    try:
        count_result = subprocess.run(
            ["grep", "-i", "-c", "--", query] + matching_files,
            capture_output=True, text=True, timeout=10
        )
        for line in count_result.stdout.splitlines():
            # Format: filepath:count
            if ":" in line:
                fpath, _, cnt = line.rpartition(":")
                try:
                    counts[fpath] = int(cnt)
                except ValueError:
                    pass
    except (subprocess.TimeoutExpired, OSError):
        pass

    # Build session dicts for matching files
    now = time.time()
    query_lower = query.lower()
    sessions = []
    for filepath in matching_files:
        path = Path(filepath)
        try:
            stat = path.stat()
            meta = _get_session_meta(path)
            project_name, short_name = _build_project_info(path, meta["cwd"])
            status = _get_session_status(path, stat.st_mtime, now)

            sessions.append({
                "path": filepath,
                "project": project_name,
                "short_name": short_name,
                "session_id": path.stem,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "time_str": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M"),
                "preview": meta["preview"],
                "status": status,
                "snippet": "",
                "match_count": counts.get(filepath, 0),
            })
        except (OSError, PermissionError):
            continue

    sessions.sort(key=lambda s: s["mtime"], reverse=True)

    # Extract snippets: one batched grep -m1 -H for top 50 results
    top_files = [s["path"] for s in sessions[:50]]
    if top_files:
        snippet_map: dict[str, str] = {}
        try:
            snip_result = subprocess.run(
                ["grep", "-i", "-m", "1", "-H", "--", query] + top_files,
                capture_output=True, text=True, timeout=10
            )
            for line in snip_result.stdout.splitlines():
                # Format: filepath:json_line (split on first : after filepath)
                # JSONL paths contain no colons, so first : is the separator
                sep = line.find(".jsonl:")
                if sep < 0:
                    continue
                fpath = line[:sep + 6]  # include ".jsonl"
                raw = line[sep + 7:]    # skip ":"
                if fpath not in snippet_map:
                    snippet_map[fpath] = raw.strip()
        except (subprocess.TimeoutExpired, OSError):
            snippet_map = {}

        for session in sessions[:50]:
            raw = snippet_map.get(session["path"], "")
            if not raw:
                continue
            try:
                obj = json.loads(raw)
                msg = obj.get("message", {})
                content = msg.get("content", "") if isinstance(msg, dict) else ""
                if isinstance(content, list):
                    texts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    content = " ".join(texts)
                if not isinstance(content, str):
                    content = ""
                if query_lower not in content.lower():
                    content = json.dumps(obj, ensure_ascii=False)
            except (json.JSONDecodeError, AttributeError):
                content = raw
            if query_lower in content.lower():
                idx = content.lower().find(query_lower)
                start = max(0, idx - 60)
                end = min(len(content), idx + len(query) + 60)
                snippet = content[start:end].replace("\n", " ").strip()
                for char in ('"', '{', '}', '\\'):
                    snippet = snippet.replace(char, ' ')
                snippet = " ".join(snippet.split())
                if start > 0:
                    snippet = "…" + snippet
                if end < len(content):
                    snippet = snippet + "…"
                session["snippet"] = snippet

    return sessions


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def _compute_stats(sessions: list[dict], now: float | None = None) -> str:
    total = len(sessions)
    if now is None:
        now = time.time()
    working = sum(1 for s in sessions if s.get("status") == "working")
    ready = sum(1 for s in sessions if s.get("status") == "ready")
    recent = sum(1 for s in sessions if now - s["mtime"] < 3600)
    projects = len({s["project"] for s in sessions})
    parts = [f"{total} Sessions"]
    if working:
        parts.append(f"{working} arbeitet")
    if ready:
        parts.append(f"{ready} bereit")
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

    status = session.get("status", "idle")
    is_active = status in ("working", "ready")
    if status == "working":
        row.get_style_context().add_class("session-row-working")
    elif status == "ready":
        row.get_style_context().add_class("session-row-ready")

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

    # Subtitle: slug (e.g. "Wild Crafting Sutton") or first 80 chars of preview
    raw_slug = session.get("slug", "")
    if raw_slug:
        subtitle_text = raw_slug.replace("-", " ").title()
    else:
        raw_preview = session.get("preview", "")
        subtitle_text = raw_preview[:80] + ("…" if len(raw_preview) > 80 else "")
    if subtitle_text:
        subtitle_label = Gtk.Label(label=subtitle_text)
        subtitle_label.set_halign(Gtk.Align.START)
        subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle_label.set_max_width_chars(70)
        subtitle_label.get_style_context().add_class("session-meta")
        subtitle_attrs = Pango.AttrList()
        subtitle_attrs.insert(Pango.attr_scale_new(0.88))
        subtitle_label.set_attributes(subtitle_attrs)
        info_box.pack_start(subtitle_label, False, False, 0)

    # Preview — Subtext1 for better contrast
    preview_label = Gtk.Label(label=session["preview"])
    preview_label.set_halign(Gtk.Align.START)
    preview_label.set_ellipsize(Pango.EllipsizeMode.END)
    preview_label.set_max_width_chars(90)
    preview_label.get_style_context().add_class("session-preview")
    info_box.pack_start(preview_label, False, False, 0)

    # Project path — always shown for context
    path_label = Gtk.Label(label=session["project"])
    path_label.set_halign(Gtk.Align.START)
    path_label.set_ellipsize(Pango.EllipsizeMode.END)
    path_label.set_max_width_chars(70)
    path_label.get_style_context().add_class("session-meta")
    path_attrs = Pango.AttrList()
    path_attrs.insert(Pango.attr_scale_new(0.82))
    path_label.set_attributes(path_attrs)
    info_box.pack_start(path_label, False, False, 0)

    # Right: meta info + resume button
    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    meta_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(meta_box, False, False, 0)

    # Status badge for active sessions
    if status == "working":
        badge = Gtk.Label(label="ARBEITET")
        badge.get_style_context().add_class("session-working-badge")
        badge.set_halign(Gtk.Align.END)
        meta_box.pack_start(badge, False, False, 0)
    elif status == "ready":
        badge = Gtk.Label(label="BEREIT")
        badge.get_style_context().add_class("session-ready-badge")
        badge.set_halign(Gtk.Align.END)
        meta_box.pack_start(badge, False, False, 0)

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


def _build_search_row(session: dict, query: str, now: float | None = None) -> Gtk.ListBoxRow:
    """Build a ListBoxRow for a search result with snippet + highlighting."""
    if now is None:
        now = time.time()
    row = Gtk.ListBoxRow()
    row.set_name("session-row")
    row.get_style_context().add_class("session-row")
    row.get_style_context().add_class("session-row-search")

    # Outer box
    outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    outer.set_margin_top(4)
    outer.set_margin_bottom(4)
    outer.set_margin_start(8)
    outer.set_margin_end(10)
    row.add(outer)

    # Left: short name + preview + snippet + path
    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    info_box.set_hexpand(True)
    outer.pack_start(info_box, True, True, 0)

    # Short project name
    name_label = Gtk.Label(label=session["short_name"])
    name_label.set_halign(Gtk.Align.START)
    name_label.set_ellipsize(Pango.EllipsizeMode.END)
    name_label.set_max_width_chars(45)
    name_label.get_style_context().add_class("session-project")
    info_box.pack_start(name_label, False, False, 0)

    # Preview
    preview_label = Gtk.Label(label=session["preview"])
    preview_label.set_halign(Gtk.Align.START)
    preview_label.set_ellipsize(Pango.EllipsizeMode.END)
    preview_label.set_max_width_chars(90)
    preview_label.get_style_context().add_class("session-preview")
    info_box.pack_start(preview_label, False, False, 0)

    # Snippet with Pango markup highlighting
    snippet = session.get("snippet", "")
    if snippet and query:
        # Escape markup characters first, then highlight
        safe_snippet = html_mod.escape(snippet)
        safe_query = html_mod.escape(query)
        # Case-insensitive highlight
        highlighted = re.sub(
            re.escape(safe_query),
            lambda m: f"<b>{m.group()}</b>",
            safe_snippet,
            flags=re.IGNORECASE
        )
        snippet_label = Gtk.Label()
        snippet_label.set_markup(highlighted)
        snippet_label.set_halign(Gtk.Align.START)
        snippet_label.set_ellipsize(Pango.EllipsizeMode.END)
        snippet_label.set_max_width_chars(100)
        snippet_label.get_style_context().add_class("session-snippet")
        info_box.pack_start(snippet_label, False, False, 0)

    # Project path
    path_label = Gtk.Label(label=session["project"])
    path_label.set_halign(Gtk.Align.START)
    path_label.set_ellipsize(Pango.EllipsizeMode.END)
    path_label.set_max_width_chars(70)
    path_label.get_style_context().add_class("session-meta")
    path_attrs = Pango.AttrList()
    path_attrs.insert(Pango.attr_scale_new(0.82))
    path_label.set_attributes(path_attrs)
    info_box.pack_start(path_label, False, False, 0)

    # Right: match badge + meta + resume button
    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    meta_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(meta_box, False, False, 0)

    # Match count badge
    match_count = session.get("match_count", 0)
    if match_count > 0:
        badge = Gtk.Label(label=f"{match_count} Treffer")
        badge.get_style_context().add_class("session-match-badge")
        badge.set_halign(Gtk.Align.END)
        meta_box.pack_start(badge, False, False, 0)

    # Date/time + size
    meta_line = Gtk.Label(label=f"{session['time_str']}  {_format_size(session['size'])}")
    meta_line.set_halign(Gtk.Align.END)
    meta_line.get_style_context().add_class("session-meta")
    meta_box.pack_start(meta_line, False, False, 0)

    # Resume button
    resume_btn = Gtk.Button(label="Resume")
    resume_btn.set_tooltip_text(f"claude -r {session['session_id']}")
    resume_btn.get_style_context().add_class("session-resume")
    session_id = session["session_id"]
    resume_btn.connect("clicked", _on_resume_clicked, session_id)
    meta_box.pack_start(resume_btn, False, False, 0)

    row._session_data = session
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
    if _state["search_mode"]:
        return True  # grep already filtered — show all rows
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
    # Cancel any running batch insertion from search mode
    if _state["batch_id"] is not None:
        GLib.source_remove(_state["batch_id"])
        _state["batch_id"] = None

    _state["all_sessions"] = sessions

    if _state["list_box"] is None:
        return

    # Remove all existing children
    for child in _state["list_box"].get_children():
        _state["list_box"].remove(child)

    # Add all rows
    now = time.time()
    for session in sessions:
        row = _build_session_row(session, now)
        _state["list_box"].add(row)

    _state["list_box"].show_all()

    # Update stats bar
    if _state["stats_label"] is not None:
        _state["stats_label"].set_text(_compute_stats(sessions, now))


# ---------------------------------------------------------------------------
# Search logic (debounced, two-mode)
# ---------------------------------------------------------------------------

def _on_search_changed(_entry: Gtk.SearchEntry) -> None:
    """Handle search text changes with 300ms debounce."""
    # Cancel previous debounce timer
    if _state["debounce_id"] is not None:
        GLib.source_remove(_state["debounce_id"])
        _state["debounce_id"] = None

    # Cancel any running batch insertion
    if _state["batch_id"] is not None:
        GLib.source_remove(_state["batch_id"])
        _state["batch_id"] = None

    query = _state["search_entry"].get_text().strip()

    if len(query) < 3:
        # Short query or empty: switch back to browse mode
        if _state["search_mode"]:
            _state["search_mode"] = False
            _populate_list_box(_state["all_sessions"])
        else:
            # Just filter existing rows
            if _state["list_box"] is not None:
                _state["list_box"].invalidate_filter()
        return

    # Debounce: schedule grep search after 300ms
    _state["debounce_id"] = GLib.timeout_add(300, _execute_search, query)


def _show_search_status(message: str) -> None:
    """Clear the ListBox and show a status message placeholder. GTK-thread only."""
    if _state["list_box"] is None:
        return

    # Cancel any running batch insertion first
    if _state["batch_id"] is not None:
        GLib.source_remove(_state["batch_id"])
        _state["batch_id"] = None

    for child in _state["list_box"].get_children():
        _state["list_box"].remove(child)

    placeholder = Gtk.Label(label=message)
    placeholder.get_style_context().add_class("session-meta")
    placeholder.set_margin_top(16)
    placeholder.set_margin_bottom(16)
    row = Gtk.ListBoxRow()
    row.set_activatable(False)
    row.add(placeholder)
    row.show_all()
    _state["list_box"].add(row)
    _state["list_box"].show_all()


def _display_search_results(results: list, query: str) -> bool:
    """Populate the ListBox with search results in batches. GTK-thread only.

    Called via GLib.idle_add from the background worker thread.
    Returns False (one-shot).
    """
    if _state["list_box"] is None:
        return False

    # Clear the "Suche..." placeholder (and any stale rows)
    for child in _state["list_box"].get_children():
        _state["list_box"].remove(child)

    # Update stats bar
    if _state["stats_label"] is not None:
        total_matches = sum(s.get("match_count", 0) for s in results)
        _state["stats_label"].set_text(
            f"{len(results)} Sessions gefunden  |  {total_matches} Treffer gesamt  |  Suche: \"{query}\""
        )

    if not results:
        _state["list_box"].show_all()
        return False

    # Cancel any previous batch insertion
    if _state["batch_id"] is not None:
        GLib.source_remove(_state["batch_id"])
        _state["batch_id"] = None

    # Batch-insert via idle_add
    now = time.time()
    it = iter(results)

    def _insert_batch() -> bool:
        BATCH = 30
        try:
            for _ in range(BATCH):
                session = next(it)
                row = _build_search_row(session, query, now)
                _state["list_box"].add(row)
            _state["list_box"].show_all()
            return True  # more rows pending
        except StopIteration:
            _state["list_box"].show_all()
            _state["batch_id"] = None
            return False  # done

    _state["batch_id"] = GLib.idle_add(_insert_batch)
    return False  # one-shot


def _execute_search(query: str) -> bool:
    """Kick off a non-blocking grep search. Returns False (one-shot GLib timer)."""
    global _search_cancel

    _state["debounce_id"] = None
    _state["search_mode"] = True

    # Signal any previous background search to abort early
    _search_cancel.set()
    cancel = threading.Event()
    _search_cancel = cancel

    # Immediate visual feedback — clears the list and shows "Suche..."
    _show_search_status("Suche…")

    def _worker() -> None:
        if cancel.is_set():
            return
        results = _grep_sessions(query)
        if cancel.is_set():
            return
        GLib.idle_add(_display_search_results, results, query)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    return False  # one-shot timer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def refresh_sessions() -> bool:
    """Re-scan sessions and update the UI. Returns True to keep GLib timer alive."""
    try:
        sessions = _scan_all_sessions()
        GLib.idle_add(_populate_list_box, sessions)
    except Exception:
        log.exception("refresh sessions")
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
    _state["search_entry"].connect("search-changed", _on_search_changed)

    # -----------------------------------------------------------------------
    # Initial load (non-blocking via idle_add)
    # -----------------------------------------------------------------------
    idle_once(lambda: _populate_list_box(_scan_all_sessions()))

    scrolled.show_all()
    return scrolled
