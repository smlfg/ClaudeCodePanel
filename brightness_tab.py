#!/usr/bin/env python3
"""Interactive Sonnenuhr control tab for ClaudeCodePanel."""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from theme import get_palette

STATE_PATH = Path("/tmp/sonnenuhr-state.json")
SOCKET_PATH = Path("/tmp/sonnenuhr.sock")
SOCKET_TIMEOUT_SECONDS = 2.0
SLIDER_DEBOUNCE_MS = 300
OFFSET_MIN = -50
OFFSET_MAX = 50

# ---------------------------------------------------------------------------
# Module-level widget refs
# ---------------------------------------------------------------------------
_header_label: Gtk.Label | None = None
_mode_badge: Gtk.Label | None = None
_refresh_button: Gtk.Button | None = None
_timeline_bar: Gtk.ProgressBar | None = None
_timeline_label: Gtk.Label | None = None
_brightness_value: Gtk.Label | None = None
_brightness_scale: Gtk.Scale | None = None
_temp_value: Gtk.Label | None = None
_temp_scale: Gtk.Scale | None = None
_temp_rgb: Gtk.Label | None = None
_displays_box: Gtk.Box | None = None
_footer_update: Gtk.Label | None = None
_footer_theme: Gtk.Label | None = None
_footer_status: Gtk.Label | None = None

_mode_buttons: dict[str, Gtk.RadioButton] = {}
_theme_buttons: dict[str, Gtk.RadioButton] = {}
_debounce_sources: dict[str, int] = {}
_last_state: dict | None = None
_ui_syncing = False
_last_user_interaction: float = 0.0
INTERACTION_COOLDOWN = 3.0  # seconds — skip periodic slider updates while user interacts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    try:
        with STATE_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _extract_state(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state", payload)
    return state if isinstance(state, dict) else None


def _send_socket_command(command: dict) -> dict:
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(SOCKET_TIMEOUT_SECONDS)
        try:
            sock.connect(str(SOCKET_PATH))
            sock.sendall(json.dumps(command).encode() + b"\n")
            sock.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()
    except OSError as exc:
        return {"ok": False, "error": f"daemon not reachable: {exc}"}

    if not response:
        return {"ok": False, "error": "daemon returned empty response"}

    try:
        return json.loads(response.decode())
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"invalid daemon response: {exc}"}


def _schedule_command(key: str, callback, delay_ms: int = SLIDER_DEBOUNCE_MS) -> None:
    source_id = _debounce_sources.pop(key, None)
    if source_id:
        GLib.source_remove(source_id)

    def _fire() -> bool:
        _debounce_sources.pop(key, None)
        callback()
        return False

    _debounce_sources[key] = GLib.timeout_add(delay_ms, _fire)


def _run_command_async(commands: list[dict] | dict, pending_text: str) -> None:
    _set_status_text(pending_text, "busy")
    command_list = commands if isinstance(commands, list) else [commands]

    def worker() -> None:
        last_result = {"ok": True}
        for command in command_list:
            last_result = _send_socket_command(command)
            if not last_result.get("ok", True):
                GLib.idle_add(_handle_command_result, None, last_result)
                return

        GLib.idle_add(_handle_command_result, _load_state(), last_result)

    threading.Thread(
        target=worker,
        daemon=True,
        name="claude-panel-sonnenuhr-command",
    ).start()


def _refresh_from_daemon_async(_button=None) -> None:
    _set_status_text("Status wird geladen…", "busy")

    def worker() -> None:
        result = _send_socket_command({"cmd": "status"})
        if result.get("ok", True) and "message" not in result:
            result = dict(result)
            result["message"] = "Status aktualisiert"
        state = _extract_state(result) if result.get("ok", True) else None
        if state is None:
            state = _load_state()
        GLib.idle_add(_handle_command_result, state, result)

    threading.Thread(
        target=worker,
        daemon=True,
        name="claude-panel-sonnenuhr-refresh",
    ).start()


def _handle_command_result(state: dict | None, result: dict) -> bool:
    global _last_user_interaction
    if result and not result.get("ok", True):
        _set_status_text(result.get("error", "Unbekannter Fehler"), "error")
        return False

    # Clear cooldown — command response has fresh state from daemon
    _last_user_interaction = 0.0
    if state is not None:
        _apply_state_to_ui(state, force=True)
    else:
        _apply_state_to_ui(_load_state(), force=True)

    message = result.get("message") or "Einstellung übernommen"
    _set_status_text(message, "ok")
    return False


def _set_status_text(text: str, tone: str = "dim") -> None:
    if _footer_status is None:
        return
    palette = get_palette()
    color = {
        "ok": palette["green"],
        "error": palette["red"],
        "busy": palette["yellow"],
        "dim": palette["dim"],
    }.get(tone, palette["dim"])
    escaped = GLib.markup_escape_text(text)
    _footer_status.set_markup(
        f'<span foreground="{color}" size="small">{escaped}</span>'
    )


def _fmt(iso: str | None, fmt: str = "%H:%M") -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime(fmt)
    except (TypeError, ValueError):
        return "—"


def _day_progress(state: dict) -> float:
    try:
        sun = state["sun_times"]
        sunrise = datetime.fromisoformat(sun["sunrise"])
        sunset = datetime.fromisoformat(sun["sunset"])
        now = datetime.now(tz=sunrise.tzinfo)
        if now <= sunrise:
            return 0.0
        if now >= sunset:
            return 1.0
        return (now - sunrise).total_seconds() / (sunset - sunrise).total_seconds()
    except Exception:
        return 0.0


def _section(title: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.get_style_context().add_class("section-frame")
    box.set_margin_bottom(10)
    label = Gtk.Label(label=title)
    label.get_style_context().add_class("section-title")
    label.set_halign(Gtk.Align.START)
    box.pack_start(label, False, False, 0)
    return box


def _lbl(markup: str, halign: Gtk.Align = Gtk.Align.START) -> Gtk.Label:
    label = Gtk.Label()
    label.set_markup(markup)
    label.set_halign(halign)
    label.set_xalign(0.0 if halign != Gtk.Align.END else 1.0)
    return label


def _build_radio_row(options: list[tuple[str, str]], callback) -> tuple[Gtk.Box, dict[str, Gtk.RadioButton]]:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    buttons: dict[str, Gtk.RadioButton] = {}
    group: Gtk.RadioButton | None = None

    for key, label in options:
        button = Gtk.RadioButton.new_with_label_from_widget(group, label)
        button.set_can_focus(False)
        button.connect("toggled", callback, key)
        row.pack_start(button, False, False, 0)
        buttons[key] = button
        if group is None:
            group = button

    return row, buttons


def _set_fixed_controls_sensitive(enabled: bool) -> None:
    if _refresh_button is not None:
        _refresh_button.set_sensitive(True)
    for button in _mode_buttons.values():
        button.set_sensitive(enabled)
    for button in _theme_buttons.values():
        button.set_sensitive(enabled)
    if _brightness_scale is not None:
        _brightness_scale.set_sensitive(enabled)
    if _temp_scale is not None:
        _temp_scale.set_sensitive(enabled)


def _set_brightness_value_markup(value: int) -> None:
    if _brightness_value is None:
        return
    palette = get_palette()
    _brightness_value.set_markup(
        f'<span foreground="{palette["peach"]}" font_size="22000" font_weight="bold">'
        f"{value}%</span>"
    )


def _set_temp_value_markup(value: int) -> None:
    if _temp_value is None:
        return
    palette = get_palette()
    _temp_value.set_markup(
        f'<span foreground="{palette["yellow"]}" font_size="22000" font_weight="bold">'
        f"{value} K</span>"
    )


def _on_mode_toggled(button: Gtk.RadioButton, mode: str) -> None:
    global _last_user_interaction
    if _ui_syncing or not button.get_active():
        return
    _last_user_interaction = time.monotonic()

    if mode == "auto":
        command = {"cmd": "auto"}
    elif mode == "disabled":
        command = {"cmd": "disable"}
    else:
        minutes = int((_last_state or {}).get("override_duration_minutes", 120))
        command = {"cmd": "override", "minutes": minutes}

    _run_command_async(command, "Modus wird gesetzt…")


def _on_theme_toggled(button: Gtk.RadioButton, mode: str) -> None:
    global _last_user_interaction
    if _ui_syncing or not button.get_active():
        return
    _last_user_interaction = time.monotonic()
    _run_command_async({"cmd": "theme", "mode": mode}, "Theme wird gesetzt…")


def _on_global_brightness_changed(scale: Gtk.Scale) -> None:
    global _last_user_interaction
    if _ui_syncing:
        return
    _last_user_interaction = time.monotonic()

    value = int(round(scale.get_value()))
    _set_brightness_value_markup(value)
    _schedule_command(
        "global_brightness",
        lambda: _run_command_async(
            {"cmd": "set", "brightness": value},
            "Globale Helligkeit wird gesetzt…",
        ),
    )


def _on_global_temp_changed(scale: Gtk.Scale) -> None:
    global _last_user_interaction
    if _ui_syncing:
        return
    _last_user_interaction = time.monotonic()

    value = int(round(scale.get_value() / 50.0) * 50)
    _set_temp_value_markup(value)
    _schedule_command(
        "global_temp",
        lambda: _run_command_async(
            {"cmd": "color", "kelvin": value},
            "Globale Farbtemperatur wird gesetzt…",
        ),
    )


def _on_display_brightness_changed(
    scale: Gtk.Scale,
    display_name: str,
    value_label: Gtk.Label,
) -> None:
    global _last_user_interaction
    _last_user_interaction = time.monotonic()
    value = int(round(scale.get_value()))
    palette = get_palette()
    value_label.set_markup(
        f'<tt><span foreground="{palette["peach"]}" size="small">{value}%</span></tt>'
    )
    _schedule_command(
        f"display-brightness:{display_name}",
        lambda: _run_command_async(
            {"cmd": "set_display", "display": display_name, "brightness": value},
            f"{display_name} wird angepasst…",
        ),
    )


def _on_display_offset_changed(
    scale: Gtk.Scale,
    display_name: str,
    value_label: Gtk.Label,
) -> None:
    global _last_user_interaction
    _last_user_interaction = time.monotonic()
    value = int(round(scale.get_value()))
    palette = get_palette()
    value_label.set_markup(
        f'<tt><span foreground="{palette["overlay"]}" size="small">{value:+d}</span></tt>'
    )
    _schedule_command(
        f"display-offset:{display_name}",
        lambda: _run_command_async(
            {"cmd": "set_display_offset", "display": display_name, "offset": value},
            f"{display_name} Korrektur wird gespeichert…",
        ),
        delay_ms=500,
    )


def _on_display_enabled_changed(
    switch: Gtk.Switch,
    _pspec,
    display_name: str,
) -> None:
    global _last_user_interaction
    _last_user_interaction = time.monotonic()
    _run_command_async(
        {
            "cmd": "set_display_enabled",
            "display": display_name,
            "enabled": switch.get_active(),
        },
        f"{display_name} wird aktualisiert…",
    )


def _build_display_row(display: dict, palette: dict, base_brightness: int) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.get_style_context().add_class("section-frame")
    card.set_margin_bottom(8)
    card.set_margin_top(2)
    card.set_margin_start(4)
    card.set_margin_end(4)

    display_name = display.get("name", "unknown")
    connected = bool(display.get("connected", False))
    enabled = bool(display.get("enabled", True))
    brightness = display.get("brightness")
    brightness = int(brightness) if isinstance(brightness, int) else base_brightness
    offset = int(display.get("brightness_offset", 0))
    rgb = display.get("rgb")

    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    dot_color = palette["green"] if (connected and enabled) else (
        palette["yellow"] if connected else palette["red"]
    )
    dot = "✓" if (connected and enabled) else ("○" if connected else "✗")
    top.pack_start(
        _lbl(f'<span foreground="{dot_color}">{dot}</span>'),
        False,
        False,
        0,
    )

    name = _lbl(
        f'<tt><span foreground="{palette["subtext1"]}" size="small">'
        f"{GLib.markup_escape_text(display_name)}</span></tt>"
    )
    name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
    name.set_max_width_chars(38)
    top.pack_start(name, True, True, 0)

    connected_text = "verbunden" if connected else "getrennt"
    top.pack_end(
        _lbl(
            f'<span foreground="{dot_color}" size="small">{connected_text}</span>',
            Gtk.Align.END,
        ),
        False,
        False,
        0,
    )
    card.pack_start(top, False, False, 0)

    brightness_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    brightness_header.pack_start(
        _lbl(f'<span foreground="{palette["overlay"]}" size="small">Monitor-Helligkeit</span>'),
        False,
        False,
        0,
    )
    brightness_value = _lbl(
        f'<tt><span foreground="{palette["peach"]}" size="small">{brightness}%</span></tt>',
        Gtk.Align.END,
    )
    brightness_header.pack_end(brightness_value, False, False, 0)
    card.pack_start(brightness_header, False, False, 0)

    brightness_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    brightness_scale.set_draw_value(False)
    brightness_scale.set_value(float(brightness))
    brightness_scale.connect(
        "value-changed",
        _on_display_brightness_changed,
        display_name,
        brightness_value,
    )
    card.pack_start(brightness_scale, False, False, 0)

    offset_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    offset_row.pack_start(
        _lbl(f'<span foreground="{palette["overlay"]}" size="small">Offset</span>'),
        False,
        False,
        0,
    )
    offset_value = _lbl(
        f'<tt><span foreground="{palette["overlay"]}" size="small">{offset:+d}</span></tt>'
    )
    offset_row.pack_end(offset_value, False, False, 0)
    card.pack_start(offset_row, False, False, 0)

    offset_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, OFFSET_MIN, OFFSET_MAX, 1)
    offset_scale.set_draw_value(False)
    offset_scale.set_value(float(offset))
    offset_scale.connect(
        "value-changed",
        _on_display_offset_changed,
        display_name,
        offset_value,
    )
    card.pack_start(offset_scale, False, False, 0)

    footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    enabled_label = _lbl(
        f'<span foreground="{palette["overlay"]}" size="small">aktiv</span>'
    )
    enabled_switch = Gtk.Switch()
    enabled_switch.set_active(enabled)
    enabled_switch.connect("notify::active", _on_display_enabled_changed, display_name)
    footer.pack_start(enabled_label, False, False, 0)
    footer.pack_start(enabled_switch, False, False, 0)

    if rgb is not None and len(rgb) == 3:
        footer.pack_end(
            _lbl(
                f'<tt><span foreground="{palette["dim"]}" size="small">'
                f"RGB {rgb[0]}/{rgb[1]}/{rgb[2]}</span></tt>",
                Gtk.Align.END,
            ),
            False,
            False,
            0,
        )

    card.pack_start(footer, False, False, 0)
    return card


def _apply_state_to_ui(state: dict | None, force: bool = False) -> None:
    global _last_state, _ui_syncing

    palette = get_palette()
    offline = state is None
    # Skip slider/display updates during periodic refresh if user recently interacted.
    # Command-response refreshes pass force=True and always update.
    skip_controls = not force and (time.monotonic() - _last_user_interaction) < INTERACTION_COOLDOWN
    if not offline:
        _last_state = state

    if _header_label is not None:
        _header_label.set_markup("<b>☀ Sonnenuhr</b>")

    if offline:
        if _mode_badge is not None:
            _mode_badge.set_markup(
                f'<span foreground="{palette["red"]}" size="small">● offline</span>'
            )
        if _timeline_label is not None:
            _timeline_label.set_markup(
                f'<span foreground="{palette["red"]}" size="small">Daemon nicht aktiv</span>'
            )
        if _timeline_bar is not None:
            _timeline_bar.set_fraction(0.0)
            _timeline_bar.set_text("—")
        _set_brightness_value_markup(0)
        _set_temp_value_markup(0)
        if _temp_rgb is not None:
            _temp_rgb.set_markup(f'<span foreground="{palette["dim"]}" size="small">—</span>')
        if _footer_update is not None:
            _footer_update.set_markup(
                f'<span foreground="{palette["dim"]}" size="small">Letzte Aktualisierung: —</span>'
            )
        if _footer_theme is not None:
            _footer_theme.set_markup(
                f'<span foreground="{palette["dim"]}" size="small">Theme: —</span>'
            )
        if _displays_box is not None:
            for child in _displays_box.get_children():
                _displays_box.remove(child)
            _displays_box.pack_start(
                _lbl(f'<span foreground="{palette["dim"]}">Keine Displays</span>'),
                False,
                False,
                0,
            )
            _displays_box.show_all()
        _set_fixed_controls_sensitive(False)
        return

    mode = state.get("mode", "unknown")
    brightness = int(state.get("brightness", 0))
    color_temp = int(state.get("color_temp", 6500))
    sun = state.get("sun_times", {})
    is_daytime = bool(state.get("is_daytime", False))
    displays = state.get("displays", [])
    theme_name = state.get("theme", "—")
    theme_locked = bool(state.get("theme_locked", False))
    theme_auto_switch = bool(state.get("theme_auto_switch", True))
    override_minutes = int(state.get("override_duration_minutes", 120))

    if _mode_badge is not None:
        badge_color = {
            "auto": palette["green"],
            "override": palette["yellow"],
            "disabled": palette["red"],
        }.get(mode, palette["red"])
        _mode_badge.set_markup(
            f'<span foreground="{badge_color}" size="small">● {mode}</span>'
        )

    if _timeline_label is not None:
        day_text = "☀ Tag" if is_daytime else "☾ Nacht"
        _timeline_label.set_markup(
            f'<span foreground="{palette["overlay"]}" size="small">'
            f"↑ {_fmt(sun.get('sunrise'))}  {day_text}  ↓ {_fmt(sun.get('sunset'))}</span>"
        )
    if _timeline_bar is not None:
        _timeline_bar.set_fraction(_day_progress(state))
        _timeline_bar.set_text(f"{_fmt(sun.get('sunrise'))} → {_fmt(sun.get('sunset'))}")

    _set_brightness_value_markup(brightness)
    _set_temp_value_markup(color_temp)

    rgb = next((d.get("rgb") for d in displays if d.get("rgb") is not None), None)
    if _temp_rgb is not None:
        rgb_text = f"RGB: {rgb[0]} / {rgb[1]} / {rgb[2]}" if rgb else "—"
        _temp_rgb.set_markup(
            f'<span foreground="{palette["overlay"]}" size="small">{rgb_text}</span>'
        )

    _ui_syncing = True
    try:
        _set_fixed_controls_sensitive(True)
        if not skip_controls:
            if _brightness_scale is not None:
                _brightness_scale.set_value(float(brightness))
            if _temp_scale is not None:
                _temp_scale.set_value(float(color_temp))

            for key, button in _mode_buttons.items():
                button.set_active(key == mode)

            selected_theme = "auto" if not theme_locked else theme_name
            for key, button in _theme_buttons.items():
                button.set_active(key == selected_theme)
    finally:
        _ui_syncing = False

    if _displays_box is not None and not skip_controls:
        for child in _displays_box.get_children():
            _displays_box.remove(child)
        if displays:
            for display in displays:
                _displays_box.pack_start(
                    _build_display_row(display, palette, brightness),
                    False,
                    False,
                    0,
                )
        else:
            _displays_box.pack_start(
                _lbl(f'<span foreground="{palette["dim"]}">Keine Displays gefunden</span>'),
                False,
                False,
                0,
            )
        _displays_box.show_all()

    if _footer_update is not None:
        _footer_update.set_markup(
            f'<span foreground="{palette["dim"]}" size="small">'
            f"Letzte Aktualisierung: {_fmt(state.get('last_update'), '%H:%M:%S')}</span>"
        )
    if _footer_theme is not None:
        theme_mode = "gesperrt" if theme_locked else "auto"
        suffix = "" if theme_locked or theme_auto_switch else ", Wechsel aus"
        _footer_theme.set_markup(
            f'<span foreground="{palette["dim"]}" size="small">'
            f"Theme: {theme_name} ({theme_mode}{suffix}) · Override: {override_minutes} min</span>"
        )


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _do_refresh() -> bool:
    _apply_state_to_ui(_load_state())
    return False


def refresh_brightness(_tab=None) -> bool:
    GLib.idle_add(_do_refresh)
    return True


def build_brightness_tab() -> Gtk.ScrolledWindow:
    global _header_label, _mode_badge, _refresh_button, _timeline_bar, _timeline_label
    global _brightness_value, _brightness_scale, _temp_value, _temp_scale, _temp_rgb
    global _displays_box, _footer_update, _footer_theme, _footer_status
    global _mode_buttons, _theme_buttons

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scrolled.set_hexpand(True)
    scrolled.set_vexpand(True)

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    root.set_margin_top(10)
    root.set_margin_bottom(10)
    root.set_margin_start(10)
    root.set_margin_end(10)
    scrolled.add(root)

    palette = get_palette()

    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    header.set_margin_bottom(8)
    _header_label = _lbl("<b>☀ Sonnenuhr</b>")
    attrs = Pango.AttrList()
    attrs.insert(Pango.attr_scale_new(1.1))
    _header_label.set_attributes(attrs)
    header.pack_start(_header_label, True, True, 0)

    _mode_badge = _lbl(
        f'<span foreground="{palette["dim"]}" size="small">● lade…</span>',
        Gtk.Align.END,
    )
    header.pack_end(_mode_badge, False, False, 0)

    _refresh_button = Gtk.Button(label="Aktualisieren")
    _refresh_button.set_can_focus(False)
    _refresh_button.connect("clicked", _refresh_from_daemon_async)
    header.pack_end(_refresh_button, False, False, 0)
    root.pack_start(header, False, False, 0)

    timeline = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    timeline.get_style_context().add_class("section-frame")
    timeline.set_margin_bottom(10)
    _timeline_label = _lbl(
        f'<span foreground="{palette["overlay"]}" size="small">Lade…</span>',
        Gtk.Align.CENTER,
    )
    timeline.pack_start(_timeline_label, False, False, 0)
    _timeline_bar = Gtk.ProgressBar()
    _timeline_bar.set_show_text(True)
    _timeline_bar.set_fraction(0.0)
    _timeline_bar.set_text("—")
    timeline.pack_start(_timeline_bar, False, False, 0)
    root.pack_start(timeline, False, False, 0)

    mode_section = _section("Modus")
    mode_row, _mode_buttons = _build_radio_row(
        [("auto", "Auto"), ("override", "Override"), ("disabled", "Disabled")],
        _on_mode_toggled,
    )
    mode_section.pack_start(mode_row, False, False, 0)
    root.pack_start(mode_section, False, False, 0)

    theme_section = _section("Theme")
    theme_row, _theme_buttons = _build_radio_row(
        [("dark", "Dark"), ("light", "Light"), ("auto", "Auto")],
        _on_theme_toggled,
    )
    theme_section.pack_start(theme_row, False, False, 0)
    root.pack_start(theme_section, False, False, 0)

    brightness_section = _section("Globale Helligkeit")
    _brightness_value = _lbl(
        f'<span foreground="{palette["peach"]}" font_size="22000" font_weight="bold">—</span>'
    )
    brightness_section.pack_start(_brightness_value, False, False, 0)
    _brightness_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    _brightness_scale.set_draw_value(False)
    _brightness_scale.set_value(0)
    _brightness_scale.connect("value-changed", _on_global_brightness_changed)
    brightness_section.pack_start(_brightness_scale, False, False, 0)
    root.pack_start(brightness_section, False, False, 0)

    temp_section = _section("Globale Farbtemperatur")
    _temp_value = _lbl(
        f'<span foreground="{palette["yellow"]}" font_size="22000" font_weight="bold">—</span>'
    )
    temp_section.pack_start(_temp_value, False, False, 0)
    _temp_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 2700, 6500, 50)
    _temp_scale.set_draw_value(False)
    _temp_scale.set_value(6500)
    _temp_scale.connect("value-changed", _on_global_temp_changed)
    temp_section.pack_start(_temp_scale, False, False, 0)
    _temp_rgb = _lbl(f'<span foreground="{palette["overlay"]}" size="small">—</span>')
    temp_section.pack_start(_temp_rgb, False, False, 0)
    root.pack_start(temp_section, False, False, 0)

    displays_section = _section("Displays")
    _displays_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    displays_section.pack_start(_displays_box, False, False, 0)
    root.pack_start(displays_section, False, False, 0)

    separator = Gtk.Separator()
    separator.set_margin_top(4)
    separator.set_margin_bottom(6)
    root.pack_start(separator, False, False, 0)

    _footer_update = _lbl(
        f'<span foreground="{palette["dim"]}" size="small">Letzte Aktualisierung: —</span>'
    )
    root.pack_start(_footer_update, False, False, 0)
    _footer_theme = _lbl(
        f'<span foreground="{palette["dim"]}" size="small">Theme: —</span>'
    )
    root.pack_start(_footer_theme, False, False, 0)
    _footer_status = _lbl(
        f'<span foreground="{palette["dim"]}" size="small">Bereit</span>'
    )
    root.pack_start(_footer_status, False, False, 0)

    GLib.idle_add(_do_refresh)
    scrolled.show_all()
    return scrolled
