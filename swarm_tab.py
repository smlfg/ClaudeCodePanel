#!/usr/bin/env python3
"""Swarm Tab — WebKit2 WebView embedding the Agent Swarm Dashboard.

Provides build_swarm_tab() returning a Gtk.Box with:
- Toolbar: Reload, Open in Browser, server status
- WebKit2 WebView loading http://localhost:5111
- Auto-starts Flask server if not running

The dashboard itself handles SSE updates — no GTK polling needed for data.
"""

import subprocess
import socket
import os
import signal
from pathlib import Path

LOG_DIR = Path.home() / ".cache" / "swarm-dashboard"
LOG_FILE = LOG_DIR / "server.log"

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, WebKit2

DASHBOARD_PORT = 5111
DASHBOARD_URL = f"http://localhost:{DASHBOARD_PORT}"
DASHBOARD_DIR = Path.home() / "Projekte" / "AgentSwarmDashboard"
VENV_PYTHON = DASHBOARD_DIR / "venv" / "bin" / "python3"

# Module-level refs for refresh
_webview = None
_status_label = None
_server_proc = None
_retry_count = 0


def _server_healthy(port: int) -> bool:
    """Check if the dashboard server is healthy via /api/health."""
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_server():
    """Start the Flask dashboard server if not already running."""
    global _server_proc
    if _server_healthy(DASHBOARD_PORT):
        return True

    if not DASHBOARD_DIR.exists():
        return False

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else "python3"
    app_py = DASHBOARD_DIR / "app.py"
    if not app_py.exists():
        return False

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fh = open(LOG_FILE, "a", encoding="utf-8")
        _server_proc = subprocess.Popen(
            [python, str(app_py)],
            cwd=str(DASHBOARD_DIR),
            stdout=log_fh,
            stderr=log_fh,
            preexec_fn=os.setpgrp,
        )
        return True
    except Exception:
        return False


def _update_status(connected: bool):
    """Update the status label."""
    if _status_label is None:
        return
    if connected:
        _status_label.set_markup(
            '<span foreground="#a6e3a1">● Verbunden</span>'
        )
    else:
        _status_label.set_markup(
            '<span foreground="#f38ba8">● Getrennt</span>'
        )


def build_swarm_tab() -> Gtk.Box:
    """Build and return the Swarm tab widget."""
    global _webview, _status_label

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_start(8)
    toolbar.set_margin_end(8)
    toolbar.set_margin_top(6)
    toolbar.set_margin_bottom(4)

    reload_btn = Gtk.Button(label="⟳ Reload")
    reload_btn.set_tooltip_text("Dashboard neu laden")
    reload_btn.connect("clicked", _on_reload)
    toolbar.pack_start(reload_btn, False, False, 0)

    browser_btn = Gtk.Button(label="↗ Browser")
    browser_btn.set_tooltip_text("Im Browser öffnen")
    browser_btn.connect("clicked", _on_open_browser)
    toolbar.pack_start(browser_btn, False, False, 0)

    _status_label = Gtk.Label()
    _status_label.set_xalign(1.0)
    toolbar.pack_end(_status_label, False, False, 0)

    url_label = Gtk.Label(label=DASHBOARD_URL)
    url_label.set_opacity(0.5)
    url_label.set_xalign(1.0)
    toolbar.pack_end(url_label, False, False, 8)

    box.pack_start(toolbar, False, False, 0)

    # --- Separator ---
    box.pack_start(Gtk.Separator(), False, False, 0)

    # --- WebView ---
    _webview = WebKit2.WebView()

    # Transparent background to match panel theme
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
        # Server starting — retry after 2s
        _update_status(False)
        _webview.load_html(
            '<html><body style="background:#1e1e2e;color:#cdd6f4;font-family:system-ui;'
            'display:flex;align-items:center;justify-content:center;height:100vh;'
            'flex-direction:column;gap:12px">'
            '<div style="font-size:24px;opacity:0.4">⟳</div>'
            '<div>Server startet...</div>'
            '<div style="font-size:12px;color:#a6adc8">Automatischer Retry alle 2s</div>'
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
        return False  # stop retrying
    elif _retry_count >= 15:
        # Give up after 30s
        _retry_count = 0
        if _webview:
            log_hint = str(LOG_FILE)
            _webview.load_html(
                '<html><body style="background:#1e1e2e;color:#f38ba8;font-family:system-ui;'
                'display:flex;align-items:center;justify-content:center;height:100vh;'
                'flex-direction:column;gap:12px">'
                '<div style="font-size:24px">Server-Fehler</div>'
                f'<div style="font-size:12px;color:#a6adc8">Log: {log_hint}</div>'
                '<button onclick="location.reload()" style="background:#313244;color:#cdd6f4;'
                'border:1px solid #45475a;border-radius:6px;padding:8px 16px;cursor:pointer;'
                'font-size:13px;margin-top:8px">Retry</button>'
                '</body></html>',
                None
            )
        _update_status(False)
        return False  # stop retrying
    else:
        _ensure_server()
        return True  # keep retrying every 2s


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


def refresh_swarm() -> bool:
    """Check server health — called by GLib timer from panel.py."""
    connected = _server_healthy(DASHBOARD_PORT)
    _update_status(connected)
    if not connected:
        _ensure_server()
    return True  # keep timer alive
