#!/usr/bin/env python3
"""Swarm Tab — Native GTK3 view for Agent Teams.

Reads directly from ~/.claude/teams/ and ~/.claude/tasks/ —
no WebKit, no Flask server needed.

Provides build_swarm_tab() and refresh_swarm() with the same API
as the old WebKit-based version.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, Pango, WebKit2

from theme import get_palette
from monitor import (
    get_anthropic_session_cost,
    get_provider_costs,
    get_active_sessions,
    get_sidecar_status,
)
from swarm_visual import generate_comm_graph

SWARM_HTML = Path.home() / ".agent" / "diagrams" / "agent-swarm-live.html"

TEAMS_DIR = Path.home() / ".claude" / "teams"
TASKS_DIR = Path.home() / ".claude" / "tasks"

# Module-level refs for refresh
_webview_ready: bool = False
_team_flow: Gtk.FlowBox | None = None
_task_list: Gtk.ListBox | None = None
_msg_list: Gtk.ListBox | None = None
_team_selector: Gtk.ComboBoxText | None = None
_status_label: Gtk.Label | None = None
_selected_team: str | None = None
_refreshing: bool = False
_stack: Gtk.Stack | None = None
_webview: WebKit2.WebView | None = None


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def _load_teams() -> list[dict]:
    """Load all team configs from ~/.claude/teams/*/config.json."""
    teams = []
    if not TEAMS_DIR.exists():
        return teams
    for team_dir in sorted(TEAMS_DIR.iterdir()):
        if not team_dir.is_dir():
            continue
        config = team_dir / "config.json"
        if not config.exists():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            name = data.get("name", team_dir.name)
            members = data.get("members", [])
            # Count tasks
            task_dir = TASKS_DIR / team_dir.name
            task_count = 0
            active_tasks = 0
            if task_dir.exists():
                for tf in task_dir.glob("*.json"):
                    try:
                        td = json.loads(tf.read_text(encoding="utf-8"))
                        task_count += 1
                        if td.get("status") == "in_progress":
                            active_tasks += 1
                    except (json.JSONDecodeError, OSError):
                        pass
            teams.append({
                "name": name,
                "dir_name": team_dir.name,
                "members": len(members),
                "member_names": [m.get("name", "?") for m in members],
                "tasks": task_count,
                "active_tasks": active_tasks,
                "active": active_tasks > 0,
                "created": data.get("createdAt", 0),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return teams


def _load_tasks(team_name: str) -> list[dict]:
    """Load tasks for a specific team."""
    tasks = []
    task_dir = TASKS_DIR / team_name
    if not task_dir.exists():
        return tasks
    for tf in sorted(task_dir.glob("*.json"), key=lambda f: f.stem.zfill(10)):
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            # Skip internal task-assignment tasks
            if data.get("metadata", {}).get("_internal"):
                continue
            tasks.append({
                "id": data.get("id", tf.stem),
                "subject": data.get("subject", "(kein Titel)"),
                "description": data.get("description", "")[:120],
                "status": data.get("status", "pending"),
                "owner": data.get("owner", data.get("subject", "")),
            })
        except (json.JSONDecodeError, OSError):
            continue
    # Sort: in_progress first, then pending, then completed
    order = {"in_progress": 0, "pending": 1, "completed": 2}
    tasks.sort(key=lambda t: order.get(t["status"], 9))
    return tasks


def _collect_comm_data(team_name: str) -> dict:
    """Collect per-member status and new messages for live updates."""
    team_dir = TEAMS_DIR / team_name
    config_path = team_dir / "config.json"
    if not config_path.exists():
        return {"members": [], "tasks": []}
    config = json.loads(config_path.read_text(encoding="utf-8"))
    members = []
    for m in config.get("members", []):
        name = m.get("name", "?")
        inbox = team_dir / "inboxes" / f"{name}.json"
        msg_count = 0
        last_msg_time = ""
        if inbox.exists():
            try:
                msgs = json.loads(inbox.read_text(encoding="utf-8"))
                if isinstance(msgs, list):
                    # Filter internal messages
                    real = [x for x in msgs if not any(
                        k in x.get("text", "") for k in
                        ['"type":"task_assignment"', '"type":"shutdown_request"',
                         '"type":"idle_notification"'])]
                    msg_count = len(real)
                    if real:
                        last_msg_time = real[-1].get("timestamp", "")
            except (json.JSONDecodeError, OSError):
                pass
        is_recent = False
        if last_msg_time:
            try:
                ts = datetime.fromisoformat(last_msg_time.replace("Z", "+00:00"))
                is_recent = (datetime.now(ts.tzinfo) - ts).total_seconds() < 120
            except (ValueError, AttributeError):
                pass
        members.append({
            "name": name,
            "status": "working" if is_recent else "idle",
            "msg_count": msg_count,
            "last_msg": last_msg_time,
        })

    tasks = []
    for t in _load_tasks(team_name):
        tasks.append({
            "id": t["id"],
            "subject": t["subject"],
            "status": t["status"],
            "owner": t.get("owner", ""),
        })

    # Collect new messages for the log
    all_msgs = []
    inbox_dir = team_dir / "inboxes"
    if inbox_dir.exists():
        for mf in inbox_dir.glob("*.json"):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    continue
                recipient = mf.stem
                for msg in data:
                    text = msg.get("text", "")
                    if any(k in text for k in ['"type":"task_assignment"',
                                                '"type":"shutdown_request"',
                                                '"type":"idle_notification"']):
                        continue
                    ts_str = msg.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        time_fmt = ts.strftime("%H:%M")
                    except (ValueError, AttributeError):
                        time_fmt = "??:??"
                    all_msgs.append({
                        "time": time_fmt,
                        "sender": msg.get("from", "?"),
                        "recipient": recipient,
                        "text": text[:90].replace("\n", " "),
                        "timestamp": ts_str,
                    })
            except (json.JSONDecodeError, OSError):
                continue
    all_msgs.sort(key=lambda m: m.get("timestamp", ""), reverse=True)

    return {"members": members, "tasks": tasks, "new_messages": all_msgs[:5]}


def _load_messages(team_name: str, limit: int = 10) -> list[dict]:
    """Load recent messages from team inboxes."""
    messages = []
    inbox_dir = TEAMS_DIR / team_name / "inboxes"
    if not inbox_dir.exists():
        return messages
    for mf in inbox_dir.glob("*.json"):
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            recipient = mf.stem
            for msg in data:
                # Skip internal task assignments and shutdown requests
                text = msg.get("text", "")
                if '"type":"task_assignment"' in text or '"type":"shutdown_request"' in text:
                    continue
                ts_str = msg.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    time_fmt = ts.strftime("%H:%M")
                    sort_key = ts.timestamp()
                except (ValueError, AttributeError):
                    time_fmt = "??:??"
                    sort_key = 0
                sender = msg.get("from", "?")
                # Truncate text for preview
                preview = text[:100].replace("\n", " ")
                if len(text) > 100:
                    preview += "..."
                messages.append({
                    "time": time_fmt,
                    "sort_key": sort_key,
                    "sender": sender,
                    "recipient": recipient,
                    "preview": preview,
                })
        except (json.JSONDecodeError, OSError):
            continue
    messages.sort(key=lambda m: m["sort_key"], reverse=True)
    return messages[:limit]


# ---------------------------------------------------------------------------
# Live data collection for WebKit visual view
# ---------------------------------------------------------------------------

def _collect_live_data() -> dict:
    """Collect live data from monitor.py + local team/task files.

    Returns a dict matching the JSON structure that updateSwarm() expects.
    """
    cost_data = get_anthropic_session_cost()
    provider_costs = get_provider_costs()
    active_sessions = get_active_sessions()
    sidecar = get_sidecar_status()
    teams = _load_teams()

    # Cost bars (percentage relative to reasonable daily max)
    OPUS_MAX = 5.0
    SONNET_MAX = 10.0
    HAIKU_MAX = 2.0
    GEMINI_MAX = 1.0

    models = cost_data.get("models", {})
    opus_cost = models.get("opus", {}).get("cost_usd", 0.0)
    sonnet_cost = models.get("sonnet", {}).get("cost_usd", 0.0)
    haiku_cost = models.get("haiku", {}).get("cost_usd", 0.0)
    gemini_cost = provider_costs.get("gemini", 0.0)

    # Node statuses
    active_task_count = sum(t["active_tasks"] for t in teams)
    sidecar_firing = any(
        d.get("active") for d in sidecar.get("detectors", {}).values()
    )

    # Build tasks list from all teams
    all_tasks = []
    for team in teams:
        for task in _load_tasks(team["dir_name"]):
            task["team"] = team["dir_name"]
            all_tasks.append(task)

    return {
        "agents": {
            "opus": {
                "status": "working" if any(
                    s.get("model", "").startswith("claude-opus") for s in active_sessions
                ) else "idle",
                "cost_usd": opus_cost,
                "cost_pct": min(100, round(opus_cost / OPUS_MAX * 100, 1)),
            },
            "sonnet": {
                "status": "working" if any(
                    s.get("model", "").startswith("claude-sonnet") for s in active_sessions
                ) else "idle",
                "cost_usd": sonnet_cost,
                "cost_pct": min(100, round(sonnet_cost / SONNET_MAX * 100, 1)),
            },
            "haiku": {
                "status": "working" if any(
                    s.get("model", "").startswith("claude-haiku") for s in active_sessions
                ) else "idle",
                "cost_usd": haiku_cost,
                "cost_pct": min(100, round(haiku_cost / HAIKU_MAX * 100, 1)),
            },
        },
        "gemini": {
            "status": "working" if gemini_cost > 0 else "idle",
            "cost_usd": gemini_cost,
            "cost_pct": min(100, round(gemini_cost / GEMINI_MAX * 100, 1)),
        },
        "sidecar": {
            "status": "working" if sidecar_firing else "idle",
            "overall_severity": sidecar.get("overall_severity", "none"),
            "active_detectors": [
                name for name, d in sidecar.get("detectors", {}).items()
                if d.get("active")
            ],
            "hook_events": sidecar.get("hook_events", 0),
        },
        "teams": [
            {
                "name": t["name"],
                "dir_name": t["dir_name"],
                "members": t["member_names"],
                "status": "working" if t["active_tasks"] > 0 else "idle",
                "tasks_total": t["tasks"],
                "tasks_active": t["active_tasks"],
            }
            for t in teams
        ],
        "tasks": [
            {
                "id": task["id"],
                "subject": task["subject"],
                "status": task["status"],
                "owner": task.get("owner", ""),
                "team": task.get("team", ""),
            }
            for task in all_tasks
        ],
        "total_cost_usd": cost_data.get("cost_usd", 0.0),
        "active_agent_count": len(active_sessions),
    }


# ---------------------------------------------------------------------------
# UI builders
# ---------------------------------------------------------------------------

def _build_team_card(team: dict) -> Gtk.Box:
    """Build a single team card widget."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    card.get_style_context().add_class("sc-card")
    card.set_size_request(180, -1)

    # Team name
    name_label = Gtk.Label(label=team["name"])
    name_label.set_halign(Gtk.Align.START)
    name_label.get_style_context().add_class("session-project")
    card.pack_start(name_label, False, False, 0)

    # Stats line
    stats = f"{team['members']} Agents  |  {team['tasks']} Tasks"
    stats_label = Gtk.Label(label=stats)
    stats_label.set_halign(Gtk.Align.START)
    stats_label.get_style_context().add_class("session-meta")
    card.pack_start(stats_label, False, False, 0)

    # Active indicator
    if team["active"]:
        dot = Gtk.Label(label=f"\u25cf Aktiv ({team['active_tasks']} laufen)")
        dot.get_style_context().add_class("swarm-status-connected")
    else:
        dot = Gtk.Label(label="\u25cb Idle")
        dot.get_style_context().add_class("swarm-status-disconnected")
    dot.set_halign(Gtk.Align.START)
    card.pack_start(dot, False, False, 0)

    return card


def _build_task_row(task: dict) -> Gtk.ListBoxRow:
    """Build a single task row."""
    row = Gtk.ListBoxRow()
    row.get_style_context().add_class("swarm-task-row")

    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    hbox.set_margin_top(4)
    hbox.set_margin_bottom(4)
    hbox.set_margin_start(8)
    hbox.set_margin_end(8)
    row.add(hbox)

    # Status icon
    status = task["status"]
    if status == "in_progress":
        icon_text = "\u25cf"
        badge_class = "swarm-badge-progress"
    elif status == "completed":
        icon_text = "\u2713"
        badge_class = "swarm-badge-completed"
    else:
        icon_text = "\u25cb"
        badge_class = "swarm-badge-pending"

    icon = Gtk.Label(label=icon_text)
    icon.get_style_context().add_class(badge_class)
    icon.set_size_request(20, -1)
    hbox.pack_start(icon, False, False, 0)

    # Task subject
    subject = Gtk.Label(label=task["subject"])
    subject.set_halign(Gtk.Align.START)
    subject.set_ellipsize(Pango.EllipsizeMode.END)
    subject.set_max_width_chars(50)
    subject.set_hexpand(True)
    subject.get_style_context().add_class("session-preview")
    hbox.pack_start(subject, True, True, 0)

    # Status badge text
    status_text = status.upper().replace("_", " ")
    badge = Gtk.Label(label=status_text)
    badge.get_style_context().add_class(badge_class)
    badge.set_halign(Gtk.Align.END)
    hbox.pack_start(badge, False, False, 0)

    row.show_all()
    return row


def _build_message_row(msg: dict) -> Gtk.ListBoxRow:
    """Build a single message row."""
    row = Gtk.ListBoxRow()
    row.get_style_context().add_class("swarm-message")

    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    hbox.set_margin_top(2)
    hbox.set_margin_bottom(2)
    hbox.set_margin_start(8)
    hbox.set_margin_end(8)
    row.add(hbox)

    # Timestamp
    time_label = Gtk.Label(label=msg["time"])
    time_label.get_style_context().add_class("log-time")
    time_label.set_size_request(40, -1)
    hbox.pack_start(time_label, False, False, 0)

    # Sender -> Recipient
    route = Gtk.Label(label=f"{msg['sender']} \u2192 {msg['recipient']}")
    route.get_style_context().add_class("session-meta")
    route.set_size_request(160, -1)
    route.set_halign(Gtk.Align.START)
    route.set_ellipsize(Pango.EllipsizeMode.END)
    hbox.pack_start(route, False, False, 0)

    # Preview
    preview = Gtk.Label(label=msg["preview"])
    preview.set_halign(Gtk.Align.START)
    preview.set_ellipsize(Pango.EllipsizeMode.END)
    preview.set_hexpand(True)
    preview.get_style_context().add_class("session-preview")
    hbox.pack_start(preview, True, True, 0)

    row.show_all()
    return row


# ---------------------------------------------------------------------------
# Refresh logic
# ---------------------------------------------------------------------------

def _refresh_content() -> None:
    """Refresh all swarm content based on selected team."""
    global _selected_team, _refreshing
    if _refreshing:
        return
    _refreshing = True
    try:
        _refresh_content_inner()
    finally:
        _refreshing = False


def _refresh_content_inner() -> None:
    """Actual refresh logic — guarded by _refreshing flag."""
    global _selected_team

    teams = _load_teams()

    # Update status
    if _status_label:
        total = len(teams)
        active = sum(1 for t in teams if t["active"])
        _status_label.set_text(f"{total} Teams, {active} aktiv")

    # Update team cards
    if _team_flow:
        for child in _team_flow.get_children():
            _team_flow.remove(child)
        for team in teams:
            card = _build_team_card(team)
            flow_child = Gtk.FlowBoxChild()
            flow_child.add(card)
            flow_child.show_all()
            _team_flow.add(flow_child)
        _team_flow.show_all()

    # Update selector
    if _team_selector:
        current = _team_selector.get_active_text()
        _team_selector.remove_all()
        _team_selector.append_text("Alle Teams")
        for team in teams:
            _team_selector.append_text(team["dir_name"])
        # Restore selection
        if current:
            model = _team_selector.get_model()
            for i, row in enumerate(model):
                if row[0] == current:
                    _team_selector.set_active(i)
                    break
            else:
                _team_selector.set_active(0)
        else:
            _team_selector.set_active(0)

    active_text = _team_selector.get_active_text() if _team_selector else None
    if active_text and active_text != "Alle Teams":
        _selected_team = active_text
    else:
        _selected_team = None

    # Update tasks
    if _task_list:
        for child in _task_list.get_children():
            _task_list.remove(child)
        if _selected_team:
            tasks = _load_tasks(_selected_team)
        else:
            # All teams
            tasks = []
            for team in teams:
                for t in _load_tasks(team["dir_name"]):
                    t["_team"] = team["dir_name"]
                    tasks.append(t)
            order = {"in_progress": 0, "pending": 1, "completed": 2}
            tasks.sort(key=lambda t: order.get(t["status"], 9))
        for task in tasks:
            _task_list.add(_build_task_row(task))
        _task_list.show_all()

    # Update messages
    if _msg_list:
        for child in _msg_list.get_children():
            _msg_list.remove(child)
        if _selected_team:
            messages = _load_messages(_selected_team)
        else:
            messages = []
            for team in teams:
                messages.extend(_load_messages(team["dir_name"], limit=5))
            messages.sort(key=lambda m: m["sort_key"], reverse=True)
            messages = messages[:10]
        for msg in messages:
            _msg_list.add(_build_message_row(msg))
        _msg_list.show_all()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _update_visual_for_team() -> None:
    """Load comm graph HTML for the selected team, or fallback HTML for all."""
    if not _stack or _stack.get_visible_child_name() != "visual" or not _webview:
        return
    active_text = _team_selector.get_active_text() if _team_selector else None
    if active_text and active_text != "Alle Teams":
        html = generate_comm_graph(active_text)
        _webview.load_html(html, None)
    elif SWARM_HTML.exists():
        _webview.load_uri(SWARM_HTML.as_uri())


def _on_load_changed(webview, event):
    """Track when WebKit page has finished loading."""
    global _webview_ready
    if event == WebKit2.LoadEvent.FINISHED:
        _webview_ready = True


def _build_visual_view() -> Gtk.Widget:
    """Build the WebKit visual view loading agent-swarm-live.html."""
    global _webview

    _webview = WebKit2.WebView()
    settings = _webview.get_settings()
    settings.set_enable_javascript(True)
    settings.set_enable_developer_extras(False)
    settings.set_hardware_acceleration_policy(
        WebKit2.HardwareAccelerationPolicy.NEVER
    )
    _webview.connect("load-changed", _on_load_changed)

    if SWARM_HTML.exists():
        _webview.load_uri(SWARM_HTML.as_uri())
    else:
        p = get_palette()
        _webview.load_html(
            f'<html><body style="background:{p["bg"]};color:{p["text"]};font-family:system-ui;'
            'display:flex;align-items:center;justify-content:center;height:100vh">'
            f'<div>agent-swarm-live.html nicht gefunden<br>'
            f'<small style="color:{p["overlay"]}">{SWARM_HTML}</small></div>'
            '</body></html>',
            None,
        )

    return _webview


def build_swarm_tab() -> Gtk.Box:
    """Build and return the Swarm tab widget."""
    global _team_flow, _task_list, _msg_list, _team_selector, _status_label, _stack

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    # --- Toolbar ---
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_start(8)
    toolbar.set_margin_end(8)
    toolbar.set_margin_top(6)
    toolbar.set_margin_bottom(4)
    toolbar.get_style_context().add_class("swarm-toolbar")

    title = Gtk.Label(label="Swarm")
    title.get_style_context().add_class("section-title")
    title_attrs = Pango.AttrList()
    title_attrs.insert(Pango.attr_weight_new(Pango.Weight.BOLD))
    title_attrs.insert(Pango.attr_scale_new(1.1))
    title.set_attributes(title_attrs)
    toolbar.pack_start(title, False, False, 0)

    # View toggle: Daten / Visual
    view_toggle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    view_toggle.get_style_context().add_class("linked")

    btn_data = Gtk.ToggleButton(label="Daten")
    btn_visual = Gtk.ToggleButton(label="Visual")
    btn_data.set_active(True)
    btn_data.get_style_context().add_class("swarm-btn")
    btn_visual.get_style_context().add_class("swarm-btn")

    def _on_toggle_data(btn):
        if btn.get_active():
            btn_visual.set_active(False)
            if _stack:
                _stack.set_visible_child_name("data")
        elif not btn_visual.get_active():
            btn.set_active(True)

    def _on_toggle_visual(btn):
        if btn.get_active():
            btn_data.set_active(False)
            if _stack:
                _stack.set_visible_child_name("visual")
                _update_visual_for_team()
        elif not btn_data.get_active():
            btn.set_active(True)

    btn_data.connect("toggled", _on_toggle_data)
    btn_visual.connect("toggled", _on_toggle_visual)
    view_toggle.pack_start(btn_data, False, False, 0)
    view_toggle.pack_start(btn_visual, False, False, 0)
    toolbar.pack_start(view_toggle, False, False, 4)

    # Team selector
    _team_selector = Gtk.ComboBoxText()
    _team_selector.append_text("Alle Teams")
    _team_selector.set_active(0)
    def _on_team_changed(_selector):
        _refresh_content()
        _update_visual_for_team()
    _team_selector.connect("changed", _on_team_changed)
    toolbar.pack_start(_team_selector, False, False, 4)

    # Spacer
    spacer = Gtk.Box()
    spacer.set_hexpand(True)
    toolbar.pack_start(spacer, True, True, 0)

    _status_label = Gtk.Label(label="")
    _status_label.get_style_context().add_class("session-stats")
    toolbar.pack_end(_status_label, False, False, 4)

    reload_btn = Gtk.Button(label="\u27f3 Refresh")
    reload_btn.get_style_context().add_class("swarm-btn")
    def _on_refresh(_):
        if _stack and _stack.get_visible_child_name() == "visual" and _webview:
            _webview.reload()
        else:
            _refresh_content()
    reload_btn.connect("clicked", _on_refresh)
    toolbar.pack_end(reload_btn, False, False, 0)

    box.pack_start(toolbar, False, False, 0)
    box.pack_start(Gtk.Separator(), False, False, 0)

    # --- Stack: Data view + Visual view ---
    _stack = Gtk.Stack()
    _stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    _stack.set_transition_duration(200)

    # Data view (native GTK)
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    content.set_margin_top(8)
    content.set_margin_bottom(8)
    content.set_margin_start(8)
    content.set_margin_end(8)

    # --- Team overview (FlowBox) ---
    team_label = Gtk.Label(label="Teams")
    team_label.set_halign(Gtk.Align.START)
    team_label.get_style_context().add_class("section-title")
    content.pack_start(team_label, False, False, 0)

    _team_flow = Gtk.FlowBox()
    _team_flow.set_min_children_per_line(1)
    _team_flow.set_max_children_per_line(5)
    _team_flow.set_selection_mode(Gtk.SelectionMode.NONE)
    _team_flow.set_homogeneous(True)
    content.pack_start(_team_flow, False, False, 0)

    # --- Tasks ---
    task_label = Gtk.Label(label="Tasks")
    task_label.set_halign(Gtk.Align.START)
    task_label.set_margin_top(8)
    task_label.get_style_context().add_class("section-title")
    content.pack_start(task_label, False, False, 0)

    _task_list = Gtk.ListBox()
    _task_list.set_selection_mode(Gtk.SelectionMode.NONE)
    content.pack_start(_task_list, False, False, 0)

    # --- Messages ---
    msg_label = Gtk.Label(label="Messages (letzte 10)")
    msg_label.set_halign(Gtk.Align.START)
    msg_label.set_margin_top(8)
    msg_label.get_style_context().add_class("section-title")
    content.pack_start(msg_label, False, False, 0)

    _msg_list = Gtk.ListBox()
    _msg_list.set_selection_mode(Gtk.SelectionMode.NONE)
    content.pack_start(_msg_list, False, False, 0)

    scrolled.add(content)
    _stack.add_named(scrolled, "data")

    # Visual view (WebKit)
    _stack.add_named(_build_visual_view(), "visual")

    box.pack_start(_stack, True, True, 0)

    # Initial load
    GLib.idle_add(_refresh_content)

    return box


def refresh_swarm() -> bool:
    """Called by GLib timer from panel.py every 30s."""
    try:
        _refresh_content()
        # Also update visual view if visible and ready
        if (
            _stack
            and _stack.get_visible_child_name() == "visual"
            and _webview
            and _webview_ready
        ):
            active_text = _team_selector.get_active_text() if _team_selector else None
            if active_text and active_text != "Alle Teams":
                # Comm graph: inject updateGraph() with live data
                data = _collect_comm_data(active_text)
                js = f"if(typeof updateGraph==='function')updateGraph({json.dumps(data, ensure_ascii=False)})"
                _webview.run_javascript(js, None, None, None)
            else:
                # Generic swarm view: inject updateSwarm()
                data = _collect_live_data()
                js = f"if(typeof updateSwarm==='function')updateSwarm({json.dumps(data, ensure_ascii=False)})"
                _webview.run_javascript(js, None, None, None)
    except Exception:
        pass
    return True  # keep timer alive
