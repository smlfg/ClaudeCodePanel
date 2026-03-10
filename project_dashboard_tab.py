#!/usr/bin/env python3
"""Project Dashboard Tab — WebKit2 WebView embedding the Project Dashboard.

Provides build_project_dashboard_tab() returning a Gtk.Box with:
- Toolbar: Reload, Open in Browser, Rescan, server status
- WebKit2 WebView loading http://localhost:5222
- Auto-starts uvicorn server if not running
"""

import subprocess
import os
from pathlib import Path

LOG_DIR = Path.home() / ".cache" / "project-dashboard"
LOG_FILE = LOG_DIR / "server.log"

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, WebKit2
from theme import get_palette

DASHBOARD_PORT = 5222
DASHBOARD_URL = f"http://localhost:{DASHBOARD_PORT}"
DASHBOARD_DIR = Path.home() / "Projekte" / "ProjectDashboard"
VENV_PYTHON = DASHBOARD_DIR / ".venv" / "bin" / "python3"

# Module-level refs for refresh
_webview = None
_status_label = None
_spinner = None
_server_proc = None
_retry_count = 0


def _server_healthy(port: int) -> bool:
    """Check if the dashboard server responds."""
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/stats", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_server():
    """Start the uvicorn dashboard server if not already running."""
    global _server_proc
    if _server_healthy(DASHBOARD_PORT):
        return True

    if not DASHBOARD_DIR.exists():
        return False

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
    main_py = DASHBOARD_DIR / "main.py"
    if not main_py.exists():
        return False

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fh = open(LOG_FILE, "a", encoding="utf-8")
        _server_proc = subprocess.Popen(
            [python, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(DASHBOARD_PORT)],
            cwd=str(DASHBOARD_DIR),
            stdout=log_fh,
            stderr=log_fh,
            preexec_fn=os.setpgrp,
        )
        return True
    except Exception:
        return False


def _update_status(connected: bool):
    """Update the status label and spinner."""
    if _status_label is None:
        return
    ctx = _status_label.get_style_context()
    ctx.remove_class("swarm-status-connected")
    ctx.remove_class("swarm-status-disconnected")
    if connected:
        ctx.add_class("swarm-status-connected")
        _status_label.set_text("● Verbunden")
        if _spinner is not None:
            _spinner.stop()
    else:
        ctx.add_class("swarm-status-disconnected")
        _status_label.set_text("● Getrennt")
        if _spinner is not None:
            _spinner.start()


def build_project_dashboard_tab() -> Gtk.Box:
    """Build and return the Project Dashboard tab widget."""
    global _webview, _status_label, _spinner

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_start(8)
    toolbar.set_margin_end(8)
    toolbar.set_margin_top(6)
    toolbar.set_margin_bottom(4)
    toolbar.get_style_context().add_class("swarm-toolbar")

    reload_btn = Gtk.Button(label="⟳ Reload")
    reload_btn.set_tooltip_text("Dashboard neu laden")
    reload_btn.connect("clicked", _on_reload)
    reload_btn.get_style_context().add_class("swarm-btn")
    toolbar.pack_start(reload_btn, False, False, 0)

    browser_btn = Gtk.Button(label="↗ Browser")
    browser_btn.set_tooltip_text("Im Browser oeffnen")
    browser_btn.connect("clicked", _on_open_browser)
    browser_btn.get_style_context().add_class("swarm-btn")
    toolbar.pack_start(browser_btn, False, False, 0)

    rescan_btn = Gtk.Button(label="⟳ Rescan")
    rescan_btn.set_tooltip_text("Alle Projekte neu scannen")
    rescan_btn.connect("clicked", _on_rescan)
    rescan_btn.get_style_context().add_class("swarm-btn")
    toolbar.pack_start(rescan_btn, False, False, 0)

    _spinner = Gtk.Spinner()
    toolbar.pack_end(_spinner, False, False, 0)

    _status_label = Gtk.Label()
    _status_label.set_xalign(1.0)
    toolbar.pack_end(_status_label, False, False, 4)

    url_label = Gtk.Label(label=DASHBOARD_URL)
    url_label.get_style_context().add_class("swarm-url")
    url_label.set_xalign(1.0)
    toolbar.pack_end(url_label, False, False, 8)

    box.pack_start(toolbar, False, False, 0)

    # --- Separator ---
    box.pack_start(Gtk.Separator(), False, False, 0)

    # --- WebView ---
    _webview = WebKit2.WebView()

    settings = _webview.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_developer_extras(False)
    settings.set_hardware_acceleration_policy(
        WebKit2.HardwareAccelerationPolicy.NEVER
    )

    # Start server and load
    _ensure_server()

    if _server_healthy(DASHBOARD_PORT):
        _webview.load_uri(DASHBOARD_URL)
        _update_status(True)
    else:
        _update_status(False)
        p = get_palette()
        _webview.load_html(
            f'<html><body style="background:{p["bg"]};color:{p["text"]};font-family:system-ui;'
            'display:flex;align-items:center;justify-content:center;height:100vh;'
            'flex-direction:column;gap:12px">'
            '<div style="font-size:24px;opacity:0.4">⟳</div>'
            '<div>Project Dashboard startet...</div>'
            f'<div style="font-size:12px;color:{p["overlay"]}">Automatischer Retry alle 2s</div>'
            '</body></html>',
            None
        )
        GLib.timeout_add_seconds(2, _retry_load)

    box.pack_start(_webview, True, True, 0)

    return box


def _retry_load() -> bool:
    """Retry loading the dashboard URL."""
    global _retry_count
    _retry_count += 1
    if _server_healthy(DASHBOARD_PORT):
        _retry_count = 0
        if _webview:
            _webview.load_uri(DASHBOARD_URL)
        _update_status(True)
        return False
    elif _retry_count >= 15:
        _retry_count = 0
        if _webview:
            log_hint = str(LOG_FILE)
            p = get_palette()
            _webview.load_html(
                f'<html><body style="background:{p["bg"]};color:{p["red"]};font-family:system-ui;'
                'display:flex;align-items:center;justify-content:center;height:100vh;'
                'flex-direction:column;gap:12px">'
                '<div style="font-size:24px">Server-Fehler</div>'
                f'<div style="font-size:12px;color:{p["overlay"]}">Log: {log_hint}</div>'
                f'<button onclick="location.reload()" style="background:{p["card"]};color:{p["text"]};'
                f'border:1px solid {p["border"]};border-radius:6px;padding:8px 16px;cursor:pointer;'
                'font-size:13px;margin-top:8px">Retry</button>'
                '</body></html>',
                None
            )
        _update_status(False)
        return False
    else:
        _ensure_server()
        return True


def _on_reload(_btn):
    """Reload the WebView."""
    if _webview:
        _webview.reload()


def _on_open_browser(_btn):
    """Open dashboard in default browser."""
    try:
        subprocess.Popen(
            ["xdg-open", DASHBOARD_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _on_rescan(_btn):
    """Trigger a rescan via the API and reload."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{DASHBOARD_PORT}/rescan", method="POST"
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
    if _webview:
        _webview.reload()


def refresh_project_dashboard() -> bool:
    """Check server health — called by GLib timer from panel.py."""
    connected = _server_healthy(DASHBOARD_PORT)
    _update_status(connected)
    if not connected:
        _ensure_server()
    return True  # keep timer alive
