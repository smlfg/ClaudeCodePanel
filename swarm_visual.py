#!/usr/bin/env python3
"""Swarm Visual — HTML generator for live agent communication graphs.

Generates a self-contained HTML page with hub-and-spoke layout showing
real team members, their connections via bezier curves, animated particles
with trail effects, and live-updating status.
"""

import json
import math
from datetime import datetime, timezone
from html import escape as _esc
from pathlib import Path

TEAMS_DIR = Path.home() / ".claude" / "teams"
TASKS_DIR = Path.home() / ".claude" / "tasks"

# Color mapping for member.color -> CSS hex
COLOR_MAP = {
    "pink": "#f5a0c0",
    "green": "#98c379",
    "cyan": "#56b6c2",
    "blue": "#61afef",
    "purple": "#c678dd",
    "yellow": "#e5c07b",
    "red": "#e06c75",
    "orange": "#d19a66",
}

MODEL_BADGE = {
    "claude-opus-4-6": ("opus", "#c678dd"),
    "opus": ("opus", "#c678dd"),
    "claude-sonnet-4-6": ("sonnet", "#61afef"),
    "sonnet": ("sonnet", "#61afef"),
    "claude-haiku-4-5": ("haiku", "#98c379"),
    "haiku": ("haiku", "#98c379"),
}


def _is_recent(ts_str: str, seconds: int = 120) -> bool:
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() < seconds
    except (ValueError, AttributeError):
        return False


def _load_inbox(team_dir: Path, member_name: str) -> list[dict]:
    inbox = team_dir / "inboxes" / f"{member_name}.json"
    if not inbox.exists():
        return []
    try:
        data = json.loads(inbox.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [
            m for m in data
            if not any(k in m.get("text", "") for k in
                       ['"type":"task_assignment"', '"type":"shutdown_request"',
                        '"type":"idle_notification"'])
        ]
    except (json.JSONDecodeError, OSError):
        return []


def _load_tasks(team_name: str) -> list[dict]:
    tasks = []
    task_dir = TASKS_DIR / team_name
    if not task_dir.exists():
        return tasks
    for tf in sorted(task_dir.glob("*.json"), key=lambda f: f.stem.zfill(10)):
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            if data.get("metadata", {}).get("_internal"):
                continue
            tasks.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return tasks


def generate_comm_graph(team_name: str) -> str:
    """Generate a complete HTML comm graph for a team."""
    team_dir = TEAMS_DIR / team_name
    config_path = team_dir / "config.json"
    if not config_path.exists():
        return f"<html><body style='background:#080c10;color:#e6edf3;padding:2em'>Team '{team_name}' nicht gefunden.</body></html>"

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return f"<html><body style='background:#080c10;color:#e6edf3;padding:2em'>Team '{_esc(team_name)}' config fehlerhaft.</body></html>"
    members = config.get("members", [])
    if not members:
        return f"<html><body style='background:#080c10;color:#e6edf3;padding:2em'>Team '{team_name}' hat keine Members.</body></html>"

    # Separate lead from other members
    lead = None
    others = []
    for m in members:
        if m.get("agentType") == "team-lead" or m.get("name") == "team-lead":
            lead = m
        else:
            others.append(m)
    if not lead and members:
        lead = members[0]
        others = members[1:]

    # Collect inbox data per member
    member_data = []
    for m in [lead] + others:
        name = m.get("name", "?")
        msgs = _load_inbox(team_dir, name)
        model_key = m.get("model", "")
        model_info = MODEL_BADGE.get(model_key, (model_key, "#8b949e"))
        color = COLOR_MAP.get(m.get("color", ""), "#8b949e")
        last_ts = msgs[-1].get("timestamp", "") if msgs else ""
        member_data.append({
            "name": name,
            "model": model_info[0],
            "model_color": model_info[1],
            "color": color,
            "agent_type": m.get("agentType", ""),
            "msg_count": len(msgs),
            "status": "working" if _is_recent(last_ts, 120) else "idle",
            "last_msgs": msgs[-3:] if msgs else [],
            "is_lead": m == lead,
            "has_recent": _is_recent(last_ts, 300),
        })

    # Load tasks
    tasks = _load_tasks(team_name)

    # Collect all messages chronologically
    all_msgs = []
    for m in members:
        name = m.get("name", "?")
        for msg in _load_inbox(team_dir, name):
            msg["_to"] = name
            all_msgs.append(msg)
    all_msgs.sort(key=lambda x: x.get("timestamp", ""))
    recent_msgs = all_msgs[-10:]

    # Layout: lead in center, others in circle
    cx, cy = 400, 270
    radius = 190
    n = len(others)
    positions = {}
    positions[lead.get("name", "?")] = (cx, cy)
    for i, m in enumerate(others):
        angle = -math.pi / 2 + (2 * math.pi * i / max(n, 1))
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        positions[m.get("name", "?")] = (x, y)

    lead_name = lead.get("name", "?")

    # Build SVG bezier connections
    svg_paths = []
    for md in member_data:
        if md["is_lead"]:
            continue
        lx, ly = positions[lead_name]
        mx, my = positions[md["name"]]
        # Control point: offset perpendicular to midpoint
        midx, midy = (lx + mx) / 2, (ly + my) / 2
        dx, dy = mx - lx, my - ly
        length = math.sqrt(dx * dx + dy * dy) or 1
        # Perpendicular offset (30px)
        px, py = -dy / length * 30, dx / length * 30
        cpx, cpy = midx + px, midy + py
        active_class = "conn-active" if md["has_recent"] else "conn"
        svg_paths.append(
            f'<path class="{active_class}" id="path-{md["name"]}" '
            f'data-from="{lead_name}" data-to="{md["name"]}" '
            f'd="M {lx} {ly} Q {cpx:.1f} {cpy:.1f} {mx:.1f} {my:.1f}" '
            f'stroke="{md["color"]}" stroke-width="2" fill="none" stroke-opacity="0.3"/>'
        )

    # Build node HTML
    node_html_parts = []
    for md in member_data:
        x, y = positions[md["name"]]
        assigned_task = next((t for t in tasks if t.get("owner") == md["name"]), None)
        task_html = ""
        if assigned_task:
            st = assigned_task.get("status", "pending")
            st_color = {"completed": "#98c379", "in_progress": "#e5c07b"}.get(st, "#8b949e")
            desc_preview = _esc(assigned_task.get("description", "")[:100].replace('\n', ' '))
            subj = _esc(assigned_task.get("subject", "")[:60])
            task_html = f'''
            <div class="node-task">
              <span class="task-status" style="color:{st_color}">{st.upper().replace("_"," ")}</span>
              {subj}
              <div class="task-desc">{desc_preview}</div>
            </div>'''

        msg_html = ""
        if md["last_msgs"]:
            msg_items = ""
            for msg in md["last_msgs"]:
                sender = _esc(msg.get("from", "?"))
                text = _esc(msg.get("text", "")[:80].replace("\n", " "))
                msg_items += f'<div class="detail-msg"><b>{sender}:</b> {text}</div>'
            msg_html = f'<div class="node-messages">{msg_items}</div>'

        status_class = "status-working" if md["status"] == "working" else "status-idle"

        safe_name = _esc(md['name'])
        # Use data attribute for JS to avoid quote-breaking in onclick
        node_html_parts.append(f'''
        <div class="node" id="node-{safe_name}" data-name="{safe_name}"
             style="left:{x-70}px;top:{y-40}px;--node-color:{md['color']}"
             onclick="toggleDetail(this.dataset.name)"
             onmouseenter="highlightConn(this.dataset.name,true)"
             onmouseleave="highlightConn(this.dataset.name,false)">
          <div class="node-header">
            <span class="status-dot {status_class}" id="dot-{safe_name}"></span>
            <span class="node-name">{safe_name}</span>
            <span class="model-badge" style="background:{md['model_color']}20;color:{md['model_color']}">{md['model']}</span>
          </div>
          <div class="node-meta">
            <span class="agent-type">{md['agent_type']}</span>
            <span class="msg-badge" id="badge-{safe_name}">{md['msg_count']}</span>
          </div>
          <div class="node-detail" id="detail-{safe_name}">
            <div class="detail-inner">
            {task_html}
            {msg_html}
            </div>
          </div>
        </div>''')

    # Task stream
    order = {"in_progress": 0, "pending": 1, "completed": 2}
    tasks.sort(key=lambda t: order.get(t.get("status", ""), 9))
    task_stream_items = ""
    for t in tasks[:8]:
        st = t.get("status", "pending")
        st_icon = {"completed": "&#10003;", "in_progress": "&#9679;"}.get(st, "&#9675;")
        st_color = {"completed": "#98c379", "in_progress": "#e5c07b"}.get(st, "#8b949e")
        owner = _esc(t.get("owner", ""))
        task_stream_items += f'''
        <div class="stream-item">
          <span style="color:{st_color}">{st_icon}</span>
          <span class="stream-subject">{_esc(t.get("subject","")[:50])}</span>
          <span class="stream-owner">{owner}</span>
        </div>'''

    # Message log
    msg_log_items = ""
    for msg in reversed(recent_msgs):
        sender = _esc(msg.get("from", "?"))
        recipient = _esc(msg.get("_to", "?"))
        text = _esc(msg.get("text", "")[:90].replace("\n", " "))
        ts = msg.get("timestamp", "")
        try:
            time_fmt = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%H:%M")
        except (ValueError, AttributeError):
            time_fmt = "??:??"
        msg_log_items += f'''
        <div class="log-item">
          <span class="log-time">{time_fmt}</span>
          <span class="log-route">{sender} &rarr; {recipient}</span>
          <span class="log-text">{text}</span>
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<title>Comm Graph &mdash; {_esc(team_name)}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
:root {{
  --bg: #080c10;
  --surface: #0d1117;
  --surface-2: #161b22;
  --surface-3: #1c2333;
  --border: rgba(255,255,255,0.06);
  --border-bright: rgba(255,255,255,0.14);
  --text: #e6edf3;
  --text-dim: #8b949e;
  --text-muted: #484f58;
  --mono: 'Space Mono', 'SF Mono', 'Fira Code', monospace;
  --sans: system-ui, -apple-system, sans-serif;
}}
body {{ font-family: var(--sans); background: var(--bg); color: var(--text); overflow-x: hidden; }}

.header {{ text-align: center; padding: 14px 0 6px; }}
.header h2 {{ font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }}
.header .sub {{ font-size: 11px; color: var(--text-dim); margin-top: 2px; }}

/* ---- GRAPH AREA with dot grid ---- */
.graph-area {{
  position: relative; width: 800px; height: 540px; margin: 0 auto;
  background-image: radial-gradient(circle, rgba(255,255,255,0.025) 1px, transparent 1px);
  background-size: 24px 24px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
}}
svg.connections {{
  position: absolute; top: 0; left: 0; width: 800px; height: 540px;
  pointer-events: none; z-index: 1;
}}

/* ---- CONNECTIONS ---- */
.conn, .conn-active {{
  transition: stroke-opacity 0.3s, filter 0.3s;
}}
.conn-active {{
  stroke-opacity: 0.5;
  stroke-dasharray: 6 4;
  animation: dash 1.5s linear infinite;
}}
.conn-highlight {{
  stroke-opacity: 0.85 !important;
  stroke-width: 3 !important;
  filter: url(#glow);
}}
@keyframes dash {{
  to {{ stroke-dashoffset: -20; }}
}}

/* ---- NODES ---- */
.node {{
  position: absolute; width: 140px; padding: 10px 12px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; cursor: pointer; z-index: 2;
  border-left: 3px solid var(--node-color);
  transition: box-shadow 0.25s ease, transform 0.2s ease, border-color 0.3s;
}}
.node:hover {{
  transform: translateY(-4px) scale(1.03);
  border-color: var(--node-color);
  box-shadow:
    0 8px 25px color-mix(in srgb, var(--node-color) 20%, transparent),
    0 0 0 1px color-mix(in srgb, var(--node-color) 15%, transparent);
}}
.node-header {{ display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }}
.node-name {{
  font-weight: 600; font-size: 12px; flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.model-badge {{
  font-size: 9px; padding: 1px 5px; border-radius: 4px;
  font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
}}
.status-dot {{
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
  transition: background-color 0.4s, box-shadow 0.4s;
}}
.status-working {{
  background: #98c379;
  box-shadow: 0 0 8px #98c37988;
  animation: pulse 2s ease-in-out infinite;
}}
.status-idle {{ background: #484f58; }}
@keyframes pulse {{ 0%,100% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} }}

.node-meta {{ display: flex; justify-content: space-between; align-items: center; }}
.agent-type {{ font-size: 10px; color: var(--text-dim); }}
.msg-badge {{
  font-size: 10px; background: var(--surface-2); padding: 1px 6px;
  border-radius: 8px; color: var(--text-dim); min-width: 18px; text-align: center;
}}

/* ---- NODE DETAIL (expandable) ---- */
.node-detail {{
  display: grid; grid-template-rows: 0fr;
  transition: grid-template-rows 0.3s ease-out, padding 0.3s;
  overflow: hidden; margin-top: 0; padding-top: 0;
  border-top: 1px solid transparent;
}}
.node-detail > .detail-inner {{ min-height: 0; }}
.node-detail.open {{
  grid-template-rows: 1fr;
  margin-top: 8px; padding-top: 8px;
  border-top-color: var(--border);
}}
.node-task {{ font-size: 11px; margin-bottom: 6px; line-height: 1.4; }}
.task-status {{ font-size: 9px; font-weight: 700; margin-right: 4px; }}
.task-desc {{ color: var(--text-dim); font-size: 10px; margin-top: 2px; }}
.node-messages {{ font-size: 10px; }}
.detail-msg {{ margin-bottom: 4px; color: var(--text-dim); line-height: 1.3; }}
.detail-msg b {{ color: var(--text); font-weight: 500; }}

/* ---- BOTTOM PANELS ---- */
.bottom {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
  padding: 14px 20px; max-width: 800px; margin: 0 auto;
}}
.section-title {{
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 1.2px; color: var(--text-muted); margin-bottom: 8px;
}}
.stream-item {{
  display: flex; gap: 8px; align-items: baseline; font-size: 12px;
  padding: 4px 0; border-bottom: 1px solid var(--border);
}}
.stream-subject {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.stream-owner {{ color: var(--text-dim); font-size: 11px; font-family: var(--mono); }}
.log-item {{
  display: flex; gap: 8px; font-size: 11px; padding: 3px 0;
  border-bottom: 1px solid var(--border);
}}
.log-time {{ color: var(--text-muted); flex-shrink: 0; font-family: var(--mono); font-size: 10px; }}
.log-route {{
  color: var(--text-dim); flex-shrink: 0; width: 140px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--mono); font-size: 10px;
}}
.log-text {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
</style>
</head>
<body>
<div class="header">
  <h2>{_esc(team_name)}</h2>
  <div class="sub">{len(members)} Agents &middot; {len(tasks)} Tasks &middot; {sum(1 for md in member_data if md["status"]=="working")} active</div>
</div>

<div class="graph-area" id="graph">
  <svg class="connections" id="svg-conn">
    <defs>
      <filter id="glow">
        <feGaussianBlur stdDeviation="4" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
      <filter id="particle-glow">
        <feGaussianBlur stdDeviation="2.5" result="blur"/>
        <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>
    </defs>
    {"".join(svg_paths)}
  </svg>
  {"".join(node_html_parts)}
</div>

<div class="bottom">
  <div>
    <div class="section-title">Tasks</div>
    <div id="task-stream">{task_stream_items}</div>
  </div>
  <div>
    <div class="section-title">Messages</div>
    <div id="msg-log">{msg_log_items}</div>
  </div>
</div>

<script>
function toggleDetail(name) {{
  var el = document.getElementById('detail-' + name);
  if (el) el.classList.toggle('open');
}}

// Connection highlight on node hover — also highlights ALL paths for team-lead (W1 fix)
function highlightConn(name, on) {{
  var path = document.getElementById('path-' + name);
  if (path) {{
    if (on) path.classList.add('conn-highlight');
    else path.classList.remove('conn-highlight');
  }} else {{
    // Team-lead has no single path — highlight all connections
    document.querySelectorAll('[id^="path-"]').forEach(function(p) {{
      if (on) p.classList.add('conn-highlight');
      else p.classList.remove('conn-highlight');
    }});
  }}
}}

// Escape HTML to prevent XSS in dynamic updates (C1 fix)
function esc(s) {{ var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }}

// Bezier point interpolation
function bezierPoint(t, x0, y0, cpx, cpy, x1, y1) {{
  var u = 1 - t;
  return {{
    x: u*u*x0 + 2*u*t*cpx + t*t*x1,
    y: u*u*y0 + 2*u*t*cpy + t*t*y1
  }};
}}

// Parse path "M x0 y0 Q cpx cpy x1 y1" — with null guard (C2 fix)
function parsePath(pathEl) {{
  var d = pathEl.getAttribute('d');
  if (!d) return null;
  var nums = d.match(/-?[\\d.]+/g);
  if (!nums || nums.length < 6) return null;
  nums = nums.map(Number);
  return {{ x0: nums[0], y0: nums[1], cpx: nums[2], cpy: nums[3], x1: nums[4], y1: nums[5] }};
}}

// Particle with trail effect — cleanup on abort (W4 fix)
function animateParticle(memberName, reverse) {{
  var pathEl = document.getElementById('path-' + memberName);
  if (!pathEl) return;
  var pp = parsePath(pathEl);
  if (!pp) return;
  var color = pathEl.getAttribute('stroke');
  var svg = document.getElementById('svg-conn');
  if (!svg) return;

  var circles = [];
  var sizes = [5, 3.5, 2.5, 1.5];
  var opacities = [0.95, 0.6, 0.35, 0.15];
  for (var i = 0; i < 4; i++) {{
    var c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('r', sizes[i]);
    c.setAttribute('fill', color);
    c.setAttribute('opacity', '0');
    if (i === 0) c.setAttribute('filter', 'url(#particle-glow)');
    svg.appendChild(c);
    circles.push(c);
  }}

  var start = null, dur = 1400;
  var trailOffsets = [0, 0.04, 0.09, 0.15];
  var cancelled = false;

  function cleanup() {{ circles.forEach(function(c) {{ if (c.parentNode) c.remove(); }}); }}

  function step(ts) {{
    if (cancelled) {{ cleanup(); return; }}
    if (!start) start = ts;
    var progress = Math.min((ts - start) / dur, 1);

    for (var i = 0; i < circles.length; i++) {{
      var t = progress - trailOffsets[i];
      if (t < 0 || t > 1) {{
        circles[i].setAttribute('opacity', '0');
        continue;
      }}
      var actualT = reverse ? 1 - t : t;
      var pt = bezierPoint(actualT, pp.x0, pp.y0, pp.cpx, pp.cpy, pp.x1, pp.y1);
      circles[i].setAttribute('cx', pt.x);
      circles[i].setAttribute('cy', pt.y);
      var fadeOut = 1 - progress * 0.5;
      circles[i].setAttribute('opacity', opacities[i] * fadeOut);
    }}

    if (progress < 1) requestAnimationFrame(step);
    else cleanup();
  }}
  requestAnimationFrame(step);

  // Cancel on page unload to prevent orphaned circles (W4)
  window.addEventListener('beforeunload', function() {{ cancelled = true; cleanup(); }}, {{ once: true }});
}}

// Initial demo particles
setTimeout(function() {{
  var names = {json.dumps([md["name"] for md in member_data if not md["is_lead"]])};
  names.forEach(function(n, i) {{
    setTimeout(function() {{ animateParticle(n, Math.random() > 0.5); }}, i * 600);
  }});
}}, 800);

// Track message counts for particle triggers
var lastMsgCounts = {json.dumps({md["name"]: md["msg_count"] for md in member_data})};

function updateGraph(data) {{
  if (!data || !data.members) return;
  data.members.forEach(function(m) {{
    var dot = document.getElementById('dot-' + m.name);
    if (dot) {{
      dot.className = 'status-dot ' + (m.status === 'working' ? 'status-working' : 'status-idle');
    }}
    var badge = document.getElementById('badge-' + m.name);
    if (badge) badge.textContent = m.msg_count;

    // Fire particles for new messages
    var prev = lastMsgCounts[m.name] || 0;
    if (m.msg_count > prev) {{
      var count = Math.min(m.msg_count - prev, 3);
      for (var i = 0; i < count; i++) {{
        (function(idx) {{
          setTimeout(function() {{ animateParticle(m.name, true); }}, idx * 500);
        }})(i);
      }}
    }}
    lastMsgCounts[m.name] = m.msg_count;
  }});

  if (data.tasks) {{
    var html = '';
    var order = {{"in_progress":0,"pending":1,"completed":2}};
    data.tasks.sort(function(a,b) {{ return (order[a.status]||9) - (order[b.status]||9); }});
    data.tasks.slice(0,8).forEach(function(t) {{
      var subj = (t.subject || '').substring(0,50);
      var icon = t.status==='completed'?'&#10003;':t.status==='in_progress'?'&#9679;':'&#9675;';
      var col = t.status==='completed'?'#98c379':t.status==='in_progress'?'#e5c07b':'#8b949e';
      html += '<div class="stream-item"><span style="color:'+col+'">'+icon+'</span>'
        + '<span class="stream-subject">'+esc(subj)+'</span>'
        + '<span class="stream-owner">'+esc(t.owner||'')+'</span></div>';
    }});
    document.getElementById('task-stream').innerHTML = html;
  }}

  if (data.new_messages && data.new_messages.length) {{
    var log = document.getElementById('msg-log');
    data.new_messages.forEach(function(msg) {{
      var div = document.createElement('div');
      div.className = 'log-item';
      div.innerHTML = '<span class="log-time">'+esc(msg.time||'')+'</span>'
        + '<span class="log-route">'+esc(msg.sender||'')+' &rarr; '+esc(msg.recipient||'')+'</span>'
        + '<span class="log-text">'+esc((msg.text||'').substring(0,90))+'</span>';
      log.prepend(div);
    }});
    while (log.children.length > 10) log.removeChild(log.lastChild);
  }}
}}
</script>
</body>
</html>'''
    return html
