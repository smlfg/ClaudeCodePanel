#!/usr/bin/env python3
"""Theme detection + CSS generation for Claude Code Control Panel.

Supports COSMIC Desktop light/dark switching.
Detection: COSMIC is_dark config → GTK Settings fallback.
Palettes: Catppuccin Mocha (dark) / Catppuccin Latte (light).
"""

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

# ---------------------------------------------------------------------------
# COSMIC config path
# ---------------------------------------------------------------------------
_COSMIC_IS_DARK = Path.home() / ".config/cosmic/com.system76.CosmicTheme.Mode/v1/is_dark"

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
DARK = {
    "name": "dark",
    "bg": "#1e1e2e",
    "mantle": "#181825",
    "card": "#313244",
    "border": "#45475a",
    "text": "#cdd6f4",
    "subtext1": "#bac2de",
    "dim": "#6c7086",
    "overlay": "#a6adc8",
    "accent": "#89b4fa",
    "mauve": "#cba6f7",
    "lavender": "#b4befe",
    "green": "#a6e3a1",
    "yellow": "#f9e2af",
    "red": "#f38ba8",
    "teal": "#94e2d5",
    "peach": "#fab387",
    # Derived
    "accent_hover_bg": "rgba(137,180,250,0.2)",
    "mauve_hover_bg": "rgba(203,166,247,0.2)",
    "green_hover_bg": "rgba(166,227,161,0.2)",
    "yellow_hover_bg": "rgba(249,226,175,0.2)",
    "resume_text": "#1e1e2e",
}

LIGHT = {
    "name": "light",
    "bg": "#eff1f5",
    "mantle": "#e6e9ef",
    "card": "#dce0e8",
    "border": "#ccd0da",
    "text": "#4c4f69",
    "subtext1": "#5c5f77",
    "dim": "#7c7f93",
    "overlay": "#8c8fa1",
    "accent": "#1e66f5",
    "mauve": "#8839ef",
    "lavender": "#7287fd",
    "green": "#1a7f12",
    "yellow": "#c65d06",
    "red": "#d20f39",
    "teal": "#127a80",
    "peach": "#e64d00",
    # Derived
    "accent_hover_bg": "rgba(30,102,245,0.12)",
    "mauve_hover_bg": "rgba(136,57,239,0.12)",
    "green_hover_bg": "rgba(64,160,43,0.12)",
    "yellow_hover_bg": "rgba(223,142,29,0.12)",
    "resume_text": "#eff1f5",
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_dark_mode() -> bool:
    """Detect dark mode: COSMIC config first, GTK Settings fallback."""
    try:
        content = _COSMIC_IS_DARK.read_text().strip().lower()
        return content == "true"
    except (OSError, ValueError):
        pass
    settings = Gtk.Settings.get_default()
    if settings is None:
        return True  # safe fallback
    return settings.get_property("gtk-application-prefer-dark-theme")


def get_palette() -> dict[str, str]:
    """Return the active color palette."""
    return DARK if is_dark_mode() else LIGHT


def hex_to_pango_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' to Pango 16-bit (r, g, b) tuple."""
    return (
        int(hex_color[1:3], 16) * 257,
        int(hex_color[3:5], 16) * 257,
        int(hex_color[5:7], 16) * 257,
    )


# ---------------------------------------------------------------------------
# CSS generation
# ---------------------------------------------------------------------------

def build_css(p: dict[str, str] | None = None) -> bytes:
    """Generate panel CSS from a palette dict. Auto-detects if p is None."""
    if p is None:
        p = get_palette()
    return f"""
window {{
    background-color: {p["bg"]};
}}

.panel-header {{
    font-size: 18px;
    font-weight: bold;
    color: {p["text"]};
}}

.stat-value {{
    font-size: 22px;
    font-weight: bold;
    color: {p["accent"]};
}}

.stat-label {{
    font-size: 11px;
    color: {p["overlay"]};
}}

.shortcut-btn {{
    padding: 8px 12px;
    border-radius: 8px;
    background: {p["card"]};
    color: {p["text"]};
    border: 1px solid {p["border"]};
    min-width: 90px;
}}

.shortcut-btn:hover {{
    background: {p["border"]};
}}

.shortcut-service {{ border-color: {p["accent"]}; }}
.shortcut-service:hover {{ border-color: {p["accent"]}; background: {p["accent_hover_bg"]}; }}
.shortcut-docs {{ border-color: {p["yellow"]}; }}
.shortcut-docs:hover {{ border-color: {p["yellow"]}; background: {p["yellow_hover_bg"]}; }}
.shortcut-folder {{ border-color: {p["green"]}; }}
.shortcut-folder:hover {{ border-color: {p["green"]}; background: {p["green_hover_bg"]}; }}
.shortcut-config {{ border-color: {p["mauve"]}; }}
.shortcut-config:hover {{ border-color: {p["mauve"]}; background: {p["mauve_hover_bg"]}; }}

.session-row {{
    padding: 8px 10px;
    border-radius: 8px;
    background: transparent;
    border-left: 3px solid {p["mauve"]};
    margin: 3px 0;
}}

.session-row:hover {{
    background: {p["card"]};
}}

.session-project {{
    font-size: 13px;
    font-weight: bold;
    color: {p["text"]};
}}

.session-preview {{
    font-size: 11px;
    color: {p["subtext1"]};
}}

.session-meta {{
    font-size: 10px;
    color: {p["dim"]};
    letter-spacing: 0.02em;
}}

.session-resume {{
    padding: 4px 14px;
    border-radius: 6px;
    background: {p["accent"]};
    color: {p["resume_text"]};
    font-weight: bold;
    font-size: 11px;
    border: none;
}}

.session-resume:hover {{
    background: {p["lavender"]};
}}

.session-stats {{
    border-radius: 20px;
    background: {p["card"]};
    padding: 6px 14px;
    color: {p["teal"]};
    font-size: 11px;
    letter-spacing: 0.02em;
}}

.section-frame {{
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 10px;
}}

.section-title {{
    font-weight: bold;
    color: {p["text"]};
    font-size: 13px;
}}

.monitor-value {{
    font-family: monospace;
    color: {p["green"]};
}}

.cost-value {{
    font-family: monospace;
    font-size: 20px;
    font-weight: bold;
    color: {p["yellow"]};
}}

.status-bar {{
    background: {p["mantle"]};
    padding: 6px 12px;
}}

.status-saved {{
    color: {p["green"]};
    font-weight: bold;
}}

.status-error {{
    color: {p["red"]};
    font-weight: bold;
}}

notebook header {{
    background: {p["mantle"]};
}}

notebook tab {{
    padding: 8px 16px;
    color: {p["overlay"]};
}}

notebook tab:checked {{
    color: {p["accent"]};
    border-bottom: 2px solid {p["accent"]};
}}

.watcher-dot-critical {{ color: {p["red"]}; font-size: 16px; }}
.watcher-dot-warning {{ color: {p["yellow"]}; font-size: 16px; }}
.watcher-dot-info {{ color: {p["accent"]}; font-size: 16px; }}
.watcher-dot-inactive {{ color: {p["dim"]}; font-size: 16px; }}
.watcher-name {{ font-family: monospace; font-size: 11px; color: {p["subtext1"]}; }}
""".encode()


# ---------------------------------------------------------------------------
# Theme watcher
# ---------------------------------------------------------------------------

_css_provider: Gtk.CssProvider | None = None
_file_monitor = None  # prevent GC


def apply_theme(provider: Gtk.CssProvider | None = None) -> None:
    """(Re-)apply CSS based on current theme. Stores provider for reuse."""
    global _css_provider
    if provider is not None:
        _css_provider = provider
    if _css_provider is None:
        return
    _css_provider.load_from_data(build_css())


def setup_theme_watcher(provider: Gtk.CssProvider) -> None:
    """Watch for COSMIC dark/light changes and re-apply CSS automatically."""
    global _css_provider, _file_monitor
    _css_provider = provider

    # Watch COSMIC config file (or parent dir if file doesn't exist yet)
    if _COSMIC_IS_DARK.exists():
        gio_file = Gio.File.new_for_path(str(_COSMIC_IS_DARK))
        try:
            _file_monitor = gio_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            _file_monitor.connect("changed", _on_cosmic_theme_changed)
        except Exception:
            pass
    elif _COSMIC_IS_DARK.parent.exists():
        # Watch parent dir so we detect when the file is created
        gio_dir = Gio.File.new_for_path(str(_COSMIC_IS_DARK.parent))
        try:
            _file_monitor = gio_dir.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            _file_monitor.connect("changed", _on_cosmic_theme_changed)
        except Exception:
            pass

    # Also watch GTK settings as fallback
    settings = Gtk.Settings.get_default()
    if settings:
        settings.connect("notify::gtk-application-prefer-dark-theme", _on_gtk_theme_changed)
        settings.connect("notify::gtk-theme-name", _on_gtk_theme_changed)


def _on_cosmic_theme_changed(_monitor, _file, _other, event_type) -> None:
    if event_type in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED):
        GLib.idle_add(apply_theme)


def _on_gtk_theme_changed(*_args) -> None:
    GLib.idle_add(apply_theme)
