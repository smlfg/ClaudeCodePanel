"""Shared utilities for Claude Code Control Panel."""

import os
from pathlib import Path

from gi.repository import GLib

# Cached constants — avoid per-call computation
HOME = str(Path.home())
CLK_TCK: int = os.sysconf("SC_CLK_TCK")


def idle_once(fn, *args):
    """Schedule fn(*args) as a one-shot GLib.idle_add (won't re-schedule)."""
    def _wrapper():
        fn(*args)
        return False
    GLib.idle_add(_wrapper)


def short_name_from_path(cwd: str) -> str:
    """Extract short project name from a path.

    Examples:
        "/home/smlflg"                          → "Home"
        "/home/smlflg/Projekte/ClaudeCodePanel" → "ClaudeCodePanel"
        "/home/smlflg/Erdhügel"                 → "Erdhügel"
        "/media/smlflg/writable"                → "writable"
    """
    if cwd == HOME or not cwd:
        return "Home"
    last = cwd.rstrip("/").rsplit("/", 1)[-1]
    return last if last else "Home"
