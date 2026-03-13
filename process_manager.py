#!/usr/bin/env python3
"""Process Manager — GTK3 widget module for Claude Code Control Panel.

Provides build_processes_tab() returning a Gtk.ScrolledWindow with:
- List of all Claude-relevant processes (opencode-mcp, gemini-mcp, claude CLI, etc.)
- Per-process: PID, name, RAM (MB), CPU%, uptime, start date
- Kill button per process row with confirmation dialog
- "Kill All Ghosts" button (kills processes older than 24h)
- Summary stats at top: total processes, total RAM, ghost count
- Auto-refresh every 30s via GLib timer

Theme: Catppuccin Mocha (dark) / Latte (light) via theme.py
"""

import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("claude_panel.process_manager")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

from theme import get_palette, hex_to_pango_rgb
from utils import idle_once, short_name_from_path, HOME, CLK_TCK

GHOST_HOURS = 24  # processes older than this are "ghosts"

# Process name keywords to detect Claude-relevant processes
PROCESS_KEYWORDS = [
    "opencode-mcp",
    "opencode",
    "gemini-mcp",
    "server-filesystem",
    "server-memory",
    "server-github",
    "server-postgres",
    "claude",
    "codex",
    "mcp-server",
]

# ---------------------------------------------------------------------------
# Module-level state (kept between refreshes)
# ---------------------------------------------------------------------------
_list_box: Gtk.ListBox | None = None
_stats_label: Gtk.Label | None = None
_all_processes: list[dict] = []


# ---------------------------------------------------------------------------
# Process scanning
# ---------------------------------------------------------------------------

def _scan_processes() -> list[dict]:
    """Run ps aux and filter for Claude-relevant processes.

    Returns list of dicts with: pid, name, cpu, ram_mb, start_str, uptime_h, is_ghost
    """
    processes = []
    try:
        result = subprocess.run(
            ["ps", "aux", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return processes

        now = time.time()

        for line in result.stdout.splitlines():
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue

            # ps aux columns: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
            try:
                pid = int(parts[1])
                cpu = float(parts[2])
                rss_kb = int(parts[5])
                tty = parts[6]         # e.g. "pts/33" or "?"
                start_str = parts[8]   # e.g. "10:30" or "Feb12"
                time_str = parts[9]    # cumulative CPU time e.g. "0:00"
                command = parts[10]
            except (ValueError, IndexError):
                continue

            # Check if this process matches any keyword
            cmd_lower = command.lower()
            matched_keyword = None
            for kw in PROCESS_KEYWORDS:
                if kw in cmd_lower:
                    matched_keyword = kw
                    break
            if matched_keyword is None:
                continue

            # Skip self (the ps command or this Python process)
            if pid == os.getpid():
                continue

            # Derive display name from command
            name = _derive_name(command, matched_keyword, pid)

            # RAM in MB
            ram_mb = rss_kb / 1024

            # Estimate process age via /proc/<pid>/stat if available
            uptime_h = _get_uptime_hours(pid, now)

            is_ghost = uptime_h >= GHOST_HOURS

            processes.append({
                "pid": pid,
                "name": name,
                "command": command[:80],
                "cpu": cpu,
                "ram_mb": ram_mb,
                "tty": tty if tty != "?" else "",
                "start_str": start_str,
                "uptime_h": uptime_h,
                "is_ghost": is_ghost,
            })

    except (subprocess.TimeoutExpired, OSError):
        pass

    # Sort: ghosts first, then by RAM desc
    processes.sort(key=lambda p: (-p["is_ghost"], -p["ram_mb"]))
    return processes


_FRIENDLY_NAMES = {
    "opencode-mcp": "OpenCode MCP",
    "opencode": "OpenCode Server",
    "gemini-mcp": "Gemini MCP",
    "server-filesystem": "Filesystem MCP",
    "server-memory": "Memory MCP",
    "server-github": "GitHub MCP",
    "codex": "Codex CLI",
    "mcp-server": "MCP Server",
}


def _get_cwd_project(pid: int) -> str:
    """Try to read the CWD of a process and extract a project name."""
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        name = short_name_from_path(cwd)
        return "" if name == "Home" else name
    except OSError:
        return ""


def _derive_name(command: str, keyword: str, pid: int = 0) -> str:
    """Derive a friendly display name from the full command string."""
    # Special handling for claude CLI — try to show project context
    if keyword == "claude":
        # Check if resumed session (-r flag)
        resumed = " -r" in command or "\x00-r" in command
        project = _get_cwd_project(pid) if pid else ""
        if project:
            return f"Claude CLI — {project}"
        return "Claude CLI (resumed)" if resumed else "Claude CLI"

    # Check friendly name table (normalize mcp- prefix for lookup)
    lookup = keyword.removeprefix("mcp-") if keyword not in _FRIENDLY_NAMES else keyword
    if lookup in _FRIENDLY_NAMES:
        return _FRIENDLY_NAMES[lookup]

    # Fallback: executable basename
    parts = command.split()
    if not parts:
        return keyword

    exe_base = Path(parts[0]).name

    name_map = {
        "node": f"node/{keyword}",
        "python3": f"python/{keyword}",
        "python": f"python/{keyword}",
    }

    if exe_base in name_map:
        return name_map[exe_base]

    return exe_base if exe_base else keyword


def _get_uptime_hours(pid: int, now: float) -> float:
    """Estimate process uptime in hours via /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            stat_data = f.read().split()
        # Field 22 (index 21) is starttime in clock ticks since boot
        starttime_ticks = int(stat_data[21])
        clk_tck = CLK_TCK

        with open("/proc/uptime") as f:
            boot_seconds_ago = float(f.read().split()[0])

        # Process start time as seconds since epoch
        process_start = now - boot_seconds_ago + (starttime_ticks / clk_tck)
        uptime_seconds = now - process_start
        return max(0.0, uptime_seconds / 3600)
    except (OSError, ValueError, IndexError):
        return 0.0


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def _compute_stats(processes: list[dict]) -> str:
    total = len(processes)
    total_ram = sum(p["ram_mb"] for p in processes)
    ghosts = sum(1 for p in processes if p["is_ghost"])
    return (
        f"{total} Prozesse  |  RAM: {total_ram:.0f} MB  |  "
        f"Ghosts (>{GHOST_HOURS}h): {ghosts}"
    )


# ---------------------------------------------------------------------------
# Kill helpers
# ---------------------------------------------------------------------------

def _kill_process(pid: int, parent_widget: Gtk.Widget) -> bool:
    """Send SIGTERM to pid. Returns True if successful."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        _show_error(parent_widget, f"Prozess {pid} nicht gefunden (bereits beendet?)")
        return False
    except PermissionError:
        _show_error(parent_widget, f"Keine Berechtigung, Prozess {pid} zu beenden.")
        return False
    except OSError as e:
        _show_error(parent_widget, f"Fehler beim Beenden von {pid}: {e}")
        return False


def _show_error(parent: Gtk.Widget, msg: str) -> None:
    """Show a simple error dialog."""
    toplevel = parent.get_toplevel()
    dialog = Gtk.MessageDialog(
        transient_for=toplevel if isinstance(toplevel, Gtk.Window) else None,
        flags=0,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=msg,
    )
    dialog.run()
    dialog.destroy()


def _confirm_kill(parent: Gtk.Widget, pid: int, name: str) -> bool:
    """Show confirmation dialog. Returns True if user confirmed."""
    toplevel = parent.get_toplevel()
    dialog = Gtk.MessageDialog(
        transient_for=toplevel if isinstance(toplevel, Gtk.Window) else None,
        flags=0,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO,
        text=f"Prozess beenden?",
    )
    dialog.format_secondary_text(f"PID {pid} — {name}\n\nSignal: SIGTERM")
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


def _confirm_kill_ghosts(parent: Gtk.Widget, count: int) -> bool:
    """Show confirmation dialog for killing all ghost processes."""
    toplevel = parent.get_toplevel()
    dialog = Gtk.MessageDialog(
        transient_for=toplevel if isinstance(toplevel, Gtk.Window) else None,
        flags=0,
        message_type=Gtk.MessageType.WARNING,
        buttons=Gtk.ButtonsType.YES_NO,
        text=f"Alle Ghosts beenden?",
    )
    dialog.format_secondary_text(
        f"{count} Prozesse aelter als {GHOST_HOURS}h werden per SIGTERM beendet."
    )
    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.YES


# ---------------------------------------------------------------------------
# Row builder
# ---------------------------------------------------------------------------

def _ram_color(ram_mb: float) -> str:
    """Return theme-aware color based on RAM usage."""
    p = get_palette()
    if ram_mb < 100:
        return p["green"]
    elif ram_mb < 300:
        return p["peach"]
    else:
        return p["red"]


def _format_uptime(hours: float) -> str:
    if hours < 1:
        return f"{int(hours * 60)}m"
    elif hours < 24:
        return f"{hours:.1f}h"
    else:
        days = int(hours // 24)
        remaining_h = int(hours % 24)
        return f"{days}d {remaining_h}h"


def _build_process_row(proc: dict, parent_widget: Gtk.Widget) -> Gtk.ListBoxRow:
    """Build a single styled ListBoxRow for one process."""
    row = Gtk.ListBoxRow()
    row.set_name("process-row")

    # Outer box with padding
    outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    outer.set_margin_top(6)
    outer.set_margin_bottom(6)
    outer.set_margin_start(10)
    outer.set_margin_end(10)
    row.add(outer)

    # Left: name + command stacked vertically
    info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    info_box.set_hexpand(True)
    outer.pack_start(info_box, True, True, 0)

    # Process name — bold, ghost shown in red
    name_label = Gtk.Label(label=proc["name"])
    name_label.set_halign(Gtk.Align.START)
    name_label.set_ellipsize(Pango.EllipsizeMode.END)
    name_label.set_max_width_chars(35)
    name_attrs = Pango.AttrList()
    name_attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
    if proc["is_ghost"]:
        # Red tint for ghost processes (theme-aware)
        r, g, b = hex_to_pango_rgb(get_palette()["red"])
        name_attrs.insert(Pango.attr_foreground_new(r, g, b))
    name_label.set_attributes(name_attrs)
    info_box.pack_start(name_label, False, False, 0)

    # Command preview — dimmed, smaller
    cmd_label = Gtk.Label(label=proc["command"])
    cmd_label.set_halign(Gtk.Align.START)
    cmd_label.set_ellipsize(Pango.EllipsizeMode.END)
    cmd_label.set_max_width_chars(50)
    cmd_label.get_style_context().add_class("stat-label")
    cmd_attrs = Pango.AttrList()
    cmd_attrs.insert(Pango.attr_scale_new(0.82))
    cmd_label.set_attributes(cmd_attrs)
    info_box.pack_start(cmd_label, False, False, 0)

    # Middle: PID + uptime
    pid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    pid_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(pid_box, False, False, 0)

    pid_text = f"PID {proc['pid']}"
    if proc.get("tty"):
        pid_text += f"  {proc['tty']}"
    pid_label = Gtk.Label(label=pid_text)
    pid_label.get_style_context().add_class("stat-label")
    pid_attrs = Pango.AttrList()
    pid_attrs.insert(Pango.attr_scale_new(0.85))
    pid_label.set_attributes(pid_attrs)
    pid_box.pack_start(pid_label, False, False, 0)

    uptime_label = Gtk.Label(label=_format_uptime(proc["uptime_h"]))
    uptime_label.get_style_context().add_class("stat-label")
    uptime_attrs = Pango.AttrList()
    uptime_attrs.insert(Pango.attr_scale_new(0.82))
    uptime_label.set_attributes(uptime_attrs)
    pid_box.pack_start(uptime_label, False, False, 0)

    # Right: RAM + CPU + Kill button
    meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    meta_box.set_valign(Gtk.Align.CENTER)
    outer.pack_start(meta_box, False, False, 0)

    # RAM + CPU on one line
    stats_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    meta_box.pack_start(stats_line, False, False, 0)

    ram_label = Gtk.Label(label=f"{proc['ram_mb']:.0f} MB")
    ram_attrs = Pango.AttrList()
    ram_attrs.insert(Pango.attr_scale_new(0.85))
    # Color code RAM
    r, g, b = hex_to_pango_rgb(_ram_color(proc["ram_mb"]))
    ram_attrs.insert(Pango.attr_foreground_new(r, g, b))
    ram_label.set_attributes(ram_attrs)
    stats_line.pack_start(ram_label, False, False, 0)

    cpu_label = Gtk.Label(label=f"CPU {proc['cpu']:.1f}%")
    cpu_label.get_style_context().add_class("stat-label")
    cpu_attrs = Pango.AttrList()
    cpu_attrs.insert(Pango.attr_scale_new(0.85))
    cpu_label.set_attributes(cpu_attrs)
    stats_line.pack_start(cpu_label, False, False, 0)

    # Kill button
    kill_btn = Gtk.Button(label="Kill")
    kill_btn.get_style_context().add_class("destructive-action")
    kill_btn.set_tooltip_text(f"SIGTERM an PID {proc['pid']} senden")
    pid = proc["pid"]
    name = proc["name"]

    def on_kill_clicked(_btn: Gtk.Button, _pid: int = pid, _name: str = name) -> None:
        if _confirm_kill(_btn, _pid, _name):
            _kill_process(_pid, _btn)
            idle_once(refresh_processes)

    kill_btn.connect("clicked", on_kill_clicked)
    meta_box.pack_start(kill_btn, False, False, 0)

    row._process_data = proc  # type: ignore[attr-defined]
    row.show_all()
    return row


# ---------------------------------------------------------------------------
# Populate / refresh the list
# ---------------------------------------------------------------------------

def _populate_list_box(processes: list[dict]) -> bool:
    """Clear and re-populate the ListBox. Returns False (idle_add one-shot)."""
    global _all_processes
    _all_processes = processes

    if _list_box is None:
        return False

    for child in _list_box.get_children():
        _list_box.remove(child)

    if not processes:
        empty_label = Gtk.Label(label="Keine Claude-relevanten Prozesse gefunden.")
        empty_label.get_style_context().add_class("stat-label")
        empty_label.set_margin_top(20)
        _list_box.add(empty_label)
    else:
        for proc in processes:
            row = _build_process_row(proc, _list_box)
            _list_box.add(row)

    _list_box.show_all()

    if _stats_label is not None:
        _stats_label.set_text(_compute_stats(processes))

    return False  # one-shot for idle_add


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_refresh_in_flight = False
_refresh_lock = threading.Lock()


def _refresh_processes_thread() -> None:
    """Scan processes in background thread, push result to GTK via idle_add."""
    global _refresh_in_flight
    try:
        processes = _scan_processes()
        GLib.idle_add(_populate_list_box, processes)
    except Exception:
        log.exception("refresh processes (background)")
    finally:
        with _refresh_lock:
            _refresh_in_flight = False


def refresh_processes() -> bool:
    """Re-scan processes in background thread. Returns True to keep GLib timer alive."""
    global _refresh_in_flight
    with _refresh_lock:
        if _refresh_in_flight:
            return True
        _refresh_in_flight = True
    threading.Thread(
        target=_refresh_processes_thread,
        daemon=True,
        name="claude-panel-process-scan",
    ).start()
    return True  # keep timer running


def build_processes_tab() -> Gtk.ScrolledWindow:
    """Build and return the Processes tab widget (Gtk.ScrolledWindow)."""
    global _list_box, _stats_label

    # Root: ScrolledWindow -> main VBox
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

    # Toolbar: title + Kill All Ghosts + refresh button
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_bottom(6)
    main_vbox.pack_start(toolbar, False, False, 0)

    title_label = Gtk.Label(label="Prozesse")
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

    # Kill All Ghosts button
    kill_ghosts_btn = Gtk.Button(label=f"Kill All Ghosts (>{GHOST_HOURS}h)")
    kill_ghosts_btn.get_style_context().add_class("destructive-action")
    kill_ghosts_btn.set_tooltip_text(f"Alle Prozesse aelter als {GHOST_HOURS}h per SIGTERM beenden")

    def on_kill_ghosts_clicked(_btn: Gtk.Button) -> None:
        ghosts = [p for p in _all_processes if p["is_ghost"]]
        if not ghosts:
            return
        if _confirm_kill_ghosts(_btn, len(ghosts)):
            for p in ghosts:
                _kill_process(p["pid"], _btn)
            idle_once(refresh_processes)

    kill_ghosts_btn.connect("clicked", on_kill_ghosts_clicked)
    toolbar.pack_start(kill_ghosts_btn, False, False, 0)

    # Refresh button
    refresh_btn = Gtk.Button()
    refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
    refresh_btn.add(refresh_icon)
    refresh_btn.set_tooltip_text("Prozessliste aktualisieren")
    refresh_btn.connect("clicked", lambda _b: refresh_processes())
    toolbar.pack_start(refresh_btn, False, False, 0)

    # Stats bar
    stats_frame = Gtk.Frame()
    stats_frame.set_margin_bottom(8)
    main_vbox.pack_start(stats_frame, False, False, 0)

    _stats_label = Gtk.Label(label="Lade Prozesse...")
    _stats_label.get_style_context().add_class("stat-label")
    _stats_label.set_margin_top(6)
    _stats_label.set_margin_bottom(6)
    _stats_label.set_margin_start(10)
    _stats_label.set_margin_end(10)
    _stats_label.set_halign(Gtk.Align.START)
    stats_frame.add(_stats_label)

    # Column headers
    header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    header_box.set_margin_start(10)
    header_box.set_margin_end(10)
    header_box.set_margin_bottom(4)
    main_vbox.pack_start(header_box, False, False, 0)

    for col_label, expand in [("Name / Befehl", True), ("PID / Uptime", False), ("RAM / CPU / Kill", False)]:
        lbl = Gtk.Label(label=col_label)
        lbl.get_style_context().add_class("stat-label")
        col_attrs = Pango.AttrList()
        col_attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
        col_attrs.insert(Pango.attr_scale_new(0.85))
        lbl.set_attributes(col_attrs)
        lbl.set_halign(Gtk.Align.START)
        header_box.pack_start(lbl, expand, expand, 0)

    # ListBox for processes
    _list_box = Gtk.ListBox()
    _list_box.set_selection_mode(Gtk.SelectionMode.NONE)
    _list_box.get_style_context().add_class("view")
    main_vbox.pack_start(_list_box, True, True, 0)

    idle_once(lambda: _populate_list_box(_scan_processes()))

    scrolled.show_all()
    return scrolled
