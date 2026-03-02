"""Monitor data layer for Claude Code Control Panel.

Reads usage data, active sessions, missed skills, and recent sessions.
Gracefully falls back when data sources are unavailable.

Performance: TTL cache (30s) prevents redundant disk I/O.
Single combined scan for active + recent sessions.
Session previews cached permanently (content never changes).
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Add MyAIGame root to path so `tui.data.*` imports resolve
_MYAIGAME_ROOT = Path.home() / "Projekte" / "MyAIGame"
if _MYAIGAME_ROOT.exists() and str(_MYAIGAME_ROOT) not in sys.path:
    sys.path.insert(0, str(_MYAIGAME_ROOT))

# Try importing from TUI data layer
try:
    from tui.data.usage_reader import get_daily_summary, get_top_n
    _HAS_USAGE_READER = True
except ImportError:
    _HAS_USAGE_READER = False

try:
    from tui.data.skill_tracker import get_missed_today
    _HAS_SKILL_TRACKER = True
except ImportError:
    _HAS_SKILL_TRACKER = False


PROJECTS_DIR = Path.home() / ".claude" / "projects"
USAGE_DIR = Path.home() / ".claude" / "usage"

# ---------------------------------------------------------------------------
# TTL Cache — 30s for data that changes, permanent for session previews
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 30
_cache = {}       # key -> value
_cache_ts = {}    # key -> timestamp
_preview_cache = {}  # path_str -> preview text (permanent, content never changes)


def _cache_get(key):
    """Return cached value if still within TTL, else None."""
    if key in _cache and (time.time() - _cache_ts.get(key, 0)) < _CACHE_TTL_SECONDS:
        return _cache[key]
    return None


def _cache_set(key, value):
    """Store value in TTL cache."""
    _cache[key] = value
    _cache_ts[key] = time.time()


# ---------------------------------------------------------------------------
# Cost & Tools
# ---------------------------------------------------------------------------
def get_daily_cost() -> dict:
    """Get today's cost summary (cached 30s)."""
    cached = _cache_get("daily_cost")
    if cached is not None:
        return cached
    if not _HAS_USAGE_READER:
        return {"error": "usage_reader nicht verfuegbar", "cost_estimate_usd": 0.0}
    try:
        result = get_daily_summary()
    except Exception as e:
        result = {"error": str(e), "cost_estimate_usd": 0.0}
    _cache_set("daily_cost", result)
    return result


def get_top_tools(n: int = 5) -> list[tuple[str, int]]:
    """Get top N tools by usage count today (cached 30s)."""
    cached = _cache_get(f"top_tools_{n}")
    if cached is not None:
        return cached
    if not _HAS_USAGE_READER:
        return []
    try:
        result = get_top_n(n)
    except Exception:
        result = []
    _cache_set(f"top_tools_{n}", result)
    return result


# ---------------------------------------------------------------------------
# Sessions — single combined scan, shared cache
# ---------------------------------------------------------------------------
def _scan_sessions():
    """Single scan of all session files. Returns (active_list, all_files_sorted).

    active_list: sessions modified in last hour, sorted by age.
    all_files_sorted: (Path, os.stat_result, Path) tuples sorted by mtime desc.
    """
    cached = _cache_get("session_scan")
    if cached is not None:
        return cached

    active = []
    all_files = []
    if not PROJECTS_DIR.exists():
        result = (active, all_files)
        _cache_set("session_scan", result)
        return result

    now = time.time()
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("*.jsonl"):
            try:
                stat = f.stat()
            except (OSError, PermissionError):
                continue

            age_hours = (now - stat.st_mtime) / 3600
            if age_hours < 1:
                active.append({
                    "project": project_dir.name[:40],
                    "file": f.name,
                    "session_id": f.stem,
                    "age_min": int(age_hours * 60),
                })

            all_files.append((f, stat, project_dir))

    active.sort(key=lambda s: s["age_min"])
    all_files.sort(key=lambda x: x[1].st_mtime, reverse=True)

    result = (active, all_files)
    _cache_set("session_scan", result)
    return result


def get_active_sessions() -> list[dict]:
    """Scan ~/.claude/projects/ for recently active sessions (last hour)."""
    active, _ = _scan_sessions()
    return active


def get_recent_sessions(n: int = 5) -> list[dict]:
    """Get n most recent session files across all projects for resume dropdown."""
    _, all_files = _scan_sessions()
    sessions = []
    for f, stat, project_dir in all_files[:n]:
        preview = _get_session_preview(f)
        project_name = project_dir.name.replace("-home-smlflg-", "").replace("-home-smlflg", "~")
        if not project_name or project_name == "~":
            project_name = "Home"

        sessions.append({
            "path": str(f),
            "project": project_name,
            "session_id": f.stem,
            "mtime": stat.st_mtime,
            "time_str": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M"),
            "preview": preview,
        })
    return sessions


def _get_session_preview(path: Path, max_len: int = 60) -> str:
    """Extract first user message from session JSONL as preview text.

    Cached permanently — session content doesn't change after creation.
    """
    key = str(path)
    if key in _preview_cache:
        return _preview_cache[key]

    result = "(keine Vorschau)"
    try:
        with path.open() as f:
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
                            result = content
                            break
                except json.JSONDecodeError:
                    continue
    except (OSError, PermissionError):
        pass

    _preview_cache[key] = result
    return result


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------
def get_missed_skills() -> list[dict]:
    """Get today's missed skill suggestions."""
    if not _HAS_SKILL_TRACKER:
        return []
    try:
        return get_missed_today()
    except Exception:
        return []


def get_missed_skills_summary() -> list[tuple[str, int]]:
    """Get missed skills grouped by skill name with counts (cached 30s)."""
    cached = _cache_get("missed_skills")
    if cached is not None:
        return cached
    missed = get_missed_skills()
    if not missed:
        result = []
    else:
        counts = Counter(m.get("suggested_skill", "?") for m in missed)
        result = counts.most_common(10)
    _cache_set("missed_skills", result)
    return result


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
def get_usage_timeline() -> list[dict]:
    """Get usage data files sorted by date (last 7 days, cached 30s)."""
    cached = _cache_get("usage_timeline")
    if cached is not None:
        return cached

    if not USAGE_DIR.exists():
        _cache_set("usage_timeline", [])
        return []

    files = sorted(USAGE_DIR.glob("????-??-??.jsonl"), reverse=True)[:7]
    timeline = []
    for f in files:
        count = 0
        try:
            with f.open() as fh:
                for line in fh:
                    if line.strip():
                        count += 1
        except (OSError, json.JSONDecodeError):
            pass
        timeline.append({
            "date": f.stem,
            "calls": count,
            "cost_est": round(count * 0.003, 4),
        })

    _cache_set("usage_timeline", timeline)
    return timeline


def format_cost(usd: float) -> str:
    """Format USD cost for display."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"
