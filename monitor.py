"""Monitor data layer for Claude Code Control Panel.

Reads usage data, active sessions, missed skills, and recent sessions.
Gracefully falls back when data sources are unavailable.

Performance: TTL cache (30s) prevents redundant disk I/O.
Single combined scan for active + recent sessions.
Session previews cached permanently (content never changes).
"""

import importlib.util
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _load_usage_reader():
    """Load usage_reader from MyAIGame if available, without sys.path manipulation."""
    module_path = Path.home() / "Projekte" / "MyAIGame" / "tui" / "data" / "usage_reader.py"
    if not module_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("usage_reader", module_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_skill_tracker():
    """Load skill_tracker from MyAIGame if available, without sys.path manipulation."""
    module_path = Path.home() / "Projekte" / "MyAIGame" / "tui" / "data" / "skill_tracker.py"
    if not module_path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("skill_tracker", module_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Try loading TUI data modules via explicit importlib (avoids sys.path DLL-hijacking)
_usage_reader_mod = None
try:
    _usage_reader_mod = _load_usage_reader()
    _HAS_USAGE_READER = _usage_reader_mod is not None
except Exception:
    _HAS_USAGE_READER = False

_skill_tracker_mod = None
try:
    _skill_tracker_mod = _load_skill_tracker()
    _HAS_SKILL_TRACKER = _skill_tracker_mod is not None
except Exception:
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
# Anthropic token pricing (per 1M tokens)
# ---------------------------------------------------------------------------
ANTHROPIC_PRICING = {
    "opus":   (15.0, 75.0),   # (input_$/1M, output_$/1M)
    "sonnet": (3.0,  15.0),
    "haiku":  (0.80,  4.0),
}

SESSION_META_DIR = Path.home() / ".claude" / "usage-data" / "session-meta"


# ---------------------------------------------------------------------------
# Cost & Tools
# ---------------------------------------------------------------------------
def get_anthropic_session_cost() -> dict:
    """Read real token counts from Claude Code session metadata (cached 30s).

    Scans ~/.claude/usage-data/session-meta/*.json for today's sessions,
    sums input/output tokens, and calculates cost at Opus rates.
    """
    cached = _cache_get("anthropic_session_cost")
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_input = 0
    total_output = 0
    session_count = 0

    if SESSION_META_DIR.is_dir():
        for f in SESSION_META_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                # Prefer start_time field, fallback to file mtime
                start = data.get("start_time", "")
                if start:
                    date_str = start[:10]  # "2026-03-03T..." -> "2026-03-03"
                else:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    date_str = mtime.strftime("%Y-%m-%d")
                if date_str != today:
                    continue
                total_input += data.get("input_tokens", 0)
                total_output += data.get("output_tokens", 0)
                session_count += 1
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    # Default: Opus pricing (conservative — better too high than too low)
    rate_in, rate_out = ANTHROPIC_PRICING["opus"]
    cost = (total_input * rate_in + total_output * rate_out) / 1_000_000

    result = {
        "cost_usd": round(cost, 4),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "sessions": session_count,
    }
    _cache_set("anthropic_session_cost", result)
    return result


def _get_tool_call_stats() -> dict:
    """Get tool call count and unique tool count from usage-tracker data."""
    if not _HAS_USAGE_READER:
        return {"total_calls": 0, "unique_tools": 0}
    try:
        summary = _usage_reader_mod.get_daily_summary()
        return {
            "total_calls": summary.get("total_calls", 0),
            "unique_tools": summary.get("unique_tools", 0),
        }
    except Exception:
        return {"total_calls": 0, "unique_tools": 0}


def get_daily_cost() -> dict:
    """Get today's cost summary based on real token data (cached 30s)."""
    cached = _cache_get("daily_cost")
    if cached is not None:
        return cached

    provider_costs = get_provider_costs()
    total = sum(provider_costs.values())

    # Tool-call count for statistics (not for cost calculation)
    tool_stats = _get_tool_call_stats()

    result = {
        "cost_estimate_usd": round(total, 4),
        "total_calls": tool_stats["total_calls"],
        "unique_tools": tool_stats["unique_tools"],
        "source": "token-based",
    }
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
        result = _usage_reader_mod.get_top_n(n)
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
        return _skill_tracker_mod.get_missed_today()
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
    """Get usage data by date (last 7 days, cached 30s).

    Combines tool-call counts from usage-tracker JSONL files with
    real token costs from session-meta (grouped by mtime date).
    """
    cached = _cache_get("usage_timeline")
    if cached is not None:
        return cached

    # 1. Collect call counts per day from usage-tracker JSONL
    calls_by_date: dict[str, int] = {}
    if USAGE_DIR.exists():
        for f in sorted(USAGE_DIR.glob("????-??-??.jsonl"), reverse=True)[:7]:
            count = 0
            try:
                with f.open() as fh:
                    for line in fh:
                        if line.strip():
                            count += 1
            except OSError:
                pass
            calls_by_date[f.stem] = count

    # 2. Collect token costs per day from session-meta
    costs_by_date: dict[str, float] = {}
    tokens_by_date: dict[str, tuple[int, int]] = {}  # date -> (in, out)
    if SESSION_META_DIR.is_dir():
        for f in SESSION_META_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                # Prefer start_time field, fallback to file mtime
                start = data.get("start_time", "")
                if start:
                    date_str = start[:10]
                else:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    date_str = mtime.strftime("%Y-%m-%d")
                inp = data.get("input_tokens", 0)
                out = data.get("output_tokens", 0)
                rate_in, rate_out = ANTHROPIC_PRICING["opus"]
                cost = (inp * rate_in + out * rate_out) / 1_000_000
                costs_by_date[date_str] = costs_by_date.get(date_str, 0.0) + cost
                prev_in, prev_out = tokens_by_date.get(date_str, (0, 0))
                tokens_by_date[date_str] = (prev_in + inp, prev_out + out)
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    # 3. Merge: all dates from both sources, sorted desc, limit 7
    all_dates = sorted(set(calls_by_date) | set(costs_by_date), reverse=True)[:7]
    timeline = []
    for date_str in all_dates:
        calls = calls_by_date.get(date_str, 0)
        cost = costs_by_date.get(date_str, 0.0)
        inp, out = tokens_by_date.get(date_str, (0, 0))
        timeline.append({
            "date": date_str,
            "calls": calls,
            "cost_est": round(cost, 4),
            "input_tokens": inp,
            "output_tokens": out,
        })

    _cache_set("usage_timeline", timeline)
    return timeline


def get_provider_costs() -> dict[str, float]:
    """Get today's cost breakdown by provider (cached 30s).

    Anthropic: real tokens from session-meta (no more flat-rate fiction).
    Others: from ~/.claude/usage/providers.jsonl (minimax, codex, gemini).
    """
    cached = _cache_get("provider_costs")
    if cached is not None:
        return cached

    # 1. Anthropic: real token cost from session-meta
    anthropic = get_anthropic_session_cost()
    costs: dict[str, float] = {"anthropic": anthropic["cost_usd"]}

    # 2. Other providers: from providers.jsonl
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    providers_file = USAGE_DIR / "providers.jsonl"
    if providers_file.exists():
        try:
            with providers_file.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entry_date = entry.get("time", "")[:10]
                        if entry_date == today:
                            provider = entry.get("provider", "unknown")
                            cost = entry.get("cost_usd", 0.0)
                            costs[provider] = costs.get(provider, 0.0) + cost
                    except (json.JSONDecodeError, KeyError):
                        continue
        except OSError:
            pass

    # Round all values
    costs = {k: round(v, 6) for k, v in costs.items()}

    _cache_set("provider_costs", costs)
    return costs


def format_cost(usd: float) -> str:
    """Format USD cost for display."""
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"
