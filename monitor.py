"""Monitor data layer for Claude Code Control Panel.

Reads usage data, active sessions, missed skills, and recent sessions.
Gracefully falls back when data sources are unavailable.

Performance: TTL cache (30s) prevents redundant disk I/O.
Single combined scan for active + recent sessions.
Session previews cached permanently (content never changes).
Sidecar socket queries refresh asynchronously to avoid blocking GTK.
"""

import importlib.util
import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("claude_panel.monitor")


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
    log.warning("usage_reader not available"); _HAS_USAGE_READER = False

_skill_tracker_mod = None
try:
    _skill_tracker_mod = _load_skill_tracker()
    _HAS_SKILL_TRACKER = _skill_tracker_mod is not None
except Exception:
    log.warning("skill_tracker not available"); _HAS_SKILL_TRACKER = False


PROJECTS_DIR = Path.home() / ".claude" / "projects"
USAGE_DIR = Path.home() / ".claude" / "usage"

# ---------------------------------------------------------------------------
# TTL Cache — 30s for data that changes, permanent for session previews
# ---------------------------------------------------------------------------
_CACHE_TTL_SECONDS = 30
_cache = {}       # key -> value
_cache_ts = {}    # key -> timestamp
_preview_cache = {}  # path_str -> preview text (permanent, content never changes)
_sidecar_lock = threading.Lock()
_sidecar_refresh_in_flight = False
_jsonl_scan_lock = threading.Lock()
_jsonl_scan_in_flight = False


def _cache_get(key):
    """Return cached value if still within TTL, else None."""
    if key in _cache and (time.time() - _cache_ts.get(key, 0)) < _CACHE_TTL_SECONDS:
        return _cache[key]
    return None


def _cache_set(key, value, ttl=None):
    """Store value in TTL cache. Optional custom ttl in seconds."""
    _cache[key] = value
    if ttl is not None:
        _cache_ts[key] = time.time() - (_CACHE_TTL_SECONDS - ttl)
    else:
        _cache_ts[key] = time.time()


# ---------------------------------------------------------------------------
# Anthropic token pricing (per 1M tokens) — Claude 4.x / 4.5 generation
# model_id -> (input_$/1M, cache_write_$/1M, cache_read_$/1M, output_$/1M)
# Source: https://docs.anthropic.com/en/docs/about-claude/models (March 2026)
# ---------------------------------------------------------------------------
ANTHROPIC_PRICING = {
    "claude-opus-4-6":   (5.00, 6.25, 0.50, 25.0),
    "claude-sonnet-4-6": (3.00, 3.75, 0.30, 15.0),
    "claude-haiku-4-5-20251001":  (1.00, 1.25, 0.10,  5.0),
}
DEFAULT_PRICING = (5.00, 6.25, 0.50, 25.0)  # Opus as conservative fallback


# ---------------------------------------------------------------------------
# Combined JSONL scanner — single pass for tokens + skills (cached 30s)
# ---------------------------------------------------------------------------
def _default_jsonl_scan() -> dict:
    """Return empty result structure for JSONL scan."""
    return {"tokens_by_date": {}, "models_by_date": {}, "skills_by_date": {}}


def _do_jsonl_scan(days: int, cache_key: str) -> None:
    """Heavy JSONL scan — runs in background thread, stores result in cache."""
    global _jsonl_scan_in_flight
    try:
        result = _do_jsonl_scan_inner(days)
        _cache_set(cache_key, result)
    except Exception:
        log.exception("background JSONL scan failed")
    finally:
        with _jsonl_scan_lock:
            _jsonl_scan_in_flight = False


def _scan_session_jsonls(days: int = 1) -> dict:
    """Non-blocking JSONL scan. Returns cached/stale data immediately,
    kicks off background thread on cache miss (like get_sidecar_status).
    """
    global _jsonl_scan_in_flight

    cache_key = f"jsonl_scan_{days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    stale = _cache.get(cache_key, _default_jsonl_scan())

    with _jsonl_scan_lock:
        if not _jsonl_scan_in_flight:
            _jsonl_scan_in_flight = True
            threading.Thread(
                target=_do_jsonl_scan,
                args=(days, cache_key),
                daemon=True,
                name="claude-panel-jsonl-scan",
            ).start()

    return stale


def _do_jsonl_scan_inner(days: int = 1) -> dict:
    """Single-pass scan of session JSONLs for tokens AND skills.

    Returns: {
        "tokens_by_date": {"2026-03-03": {"input": N, "cache_create": N, "cache_read": N, "output": N, "cost": F}},
        "skills_by_date": {"2026-03-03": {"research": 3, "chef": 5}},
    }
    """
    from collections import defaultdict

    now = time.time()
    cutoff_ts = now - (days * 86400)

    tokens_by_date = defaultdict(lambda: {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "cost": 0.0})
    # Per-model breakdown: date -> model_tier -> {input, cache_create, cache_read, output, cost}
    tokens_by_date_model = defaultdict(lambda: defaultdict(
        lambda: {"input": 0, "cache_create": 0, "cache_read": 0, "output": 0, "cost": 0.0}
    ))
    skills_by_date = defaultdict(lambda: defaultdict(int))
    sessions_by_date = defaultdict(set)

    if not PROJECTS_DIR.exists():
        return _default_jsonl_scan()

    # Collect last usage per requestId to avoid streaming duplication.
    # Streaming responses emit multiple usage objects per request — only
    # the final one contains the cumulative (correct) token counts.
    # Key: requestId -> (date_str, model_id, usage_dict, file_path)
    request_usage = {}

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.glob("**/*.jsonl"):
            try:
                mtime = f.stat().st_mtime
            except (OSError, PermissionError):
                continue
            if mtime < cutoff_ts:
                continue

            try:
                with f.open() as fh:
                    for line in fh:
                        has_usage = '"usage"' in line
                        has_skill = '"name":"Skill"' in line or '"name": "Skill"' in line
                        if not has_usage and not has_skill:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Extract date from timestamp
                        ts = entry.get("timestamp", "")
                        if not ts:
                            continue
                        date_str = ts[:10]  # "2026-03-03T..." -> "2026-03-03"

                        msg = entry.get("message", {})

                        # --- Token usage (dedup by requestId) ---
                        if has_usage:
                            usage = msg.get("usage")
                            rid = entry.get("requestId", "")
                            if usage and rid:
                                model_id = msg.get("model", "")
                                # Always overwrite — last entry per requestId wins
                                request_usage[rid] = (date_str, model_id, usage, str(f))

                        # --- Skill usage ---
                        if has_skill:
                            content = msg.get("content", [])
                            if isinstance(content, list):
                                for block in content:
                                    if isinstance(block, dict) and block.get("name") == "Skill":
                                        skill_input = block.get("input", {})
                                        skill_name = skill_input.get("skill", "unknown")
                                        skills_by_date[date_str][skill_name] += 1
            except (OSError, PermissionError):
                continue

    # Aggregate deduplicated usage into date buckets (total + per-model)
    for date_str, model_id, usage, file_path in request_usage.values():
        rate_in, rate_write, rate_read, rate_out = ANTHROPIC_PRICING.get(model_id, DEFAULT_PRICING)

        inp = usage.get("input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        out = usage.get("output_tokens", 0)

        cost = (inp * rate_in
                + cache_create * rate_write
                + cache_read * rate_read
                + out * rate_out) / 1_000_000

        # Total bucket
        bucket = tokens_by_date[date_str]
        bucket["input"] += inp
        bucket["cache_create"] += cache_create
        bucket["cache_read"] += cache_read
        bucket["output"] += out
        bucket["cost"] += cost

        # Per-model bucket (derive tier from model_id)
        if "opus" in model_id:
            tier = "opus"
        elif "sonnet" in model_id:
            tier = "sonnet"
        elif "haiku" in model_id:
            tier = "haiku"
        else:
            tier = "opus"  # conservative fallback
        mb = tokens_by_date_model[date_str][tier]
        mb["input"] += inp
        mb["cache_create"] += cache_create
        mb["cache_read"] += cache_read
        mb["output"] += out
        mb["cost"] += cost

        sessions_by_date[date_str].add(file_path)

    # Convert defaultdicts to regular dicts and add session counts
    tokens_dict = {}
    for d, bucket in tokens_by_date.items():
        tokens_dict[d] = {
            "input": bucket["input"],
            "cache_create": bucket["cache_create"],
            "cache_read": bucket["cache_read"],
            "output": bucket["output"],
            "cost": round(bucket["cost"], 6),
            "sessions": len(sessions_by_date.get(d, set())),
        }

    # Convert per-model defaultdicts
    models_dict = {}
    for d, model_map in tokens_by_date_model.items():
        models_dict[d] = {tier: dict(b) for tier, b in model_map.items()}

    return {
        "tokens_by_date": tokens_dict,
        "models_by_date": models_dict,
        "skills_by_date": {k: dict(v) for k, v in skills_by_date.items()},
    }


# ---------------------------------------------------------------------------
# Cost & Tools
# ---------------------------------------------------------------------------
def get_anthropic_session_cost() -> dict:
    """Get real API token costs from session JSONL files for today (cached 30s).

    Parses ~/.claude/projects/*/*.jsonl for actual Anthropic API usage data,
    with model-specific pricing and cache token breakdown.
    """
    cached = _cache_get("anthropic_session_cost")
    if cached is not None:
        return cached

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    scan = _scan_session_jsonls(days=1)
    bucket = scan["tokens_by_date"].get(today, {})

    # Per-model breakdown for today
    models_today = scan["models_by_date"].get(today, {})
    models = {}
    for tier, mb in models_today.items():
        models[tier] = {
            "cost_usd": round(mb.get("cost", 0.0), 4),
            "input_tokens": mb.get("input", 0) + mb.get("cache_create", 0),
            "cache_read_tokens": mb.get("cache_read", 0),
            "output_tokens": mb.get("output", 0),
        }

    result = {
        "cost_usd": round(bucket.get("cost", 0.0), 4),
        "input_tokens": bucket.get("input", 0) + bucket.get("cache_create", 0),
        "cache_read_tokens": bucket.get("cache_read", 0),
        "output_tokens": bucket.get("output", 0),
        "sessions": bucket.get("sessions", 0),
        "models": models,
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
        log.exception("get_skill_usage")
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
        log.exception("get_top_tools")
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
        for f in project_dir.glob("**/*.jsonl"):
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
# Skill Usage (from session JSONLs)
# ---------------------------------------------------------------------------
def get_skill_usage(days: int = 1) -> dict[str, dict[str, int]]:
    """Get skill usage counts grouped by date.

    Returns: {"2026-03-03": {"research": 3, "chef": 5, ...}, ...}
    """
    cached = _cache_get(f"skill_usage_{days}")
    if cached is not None:
        return cached

    scan = _scan_session_jsonls(days=days)
    result = scan["skills_by_date"]
    _cache_set(f"skill_usage_{days}", result)
    return result


# ---------------------------------------------------------------------------
# Skills (missed skills from MyAIGame tracker)
# ---------------------------------------------------------------------------
def get_missed_skills() -> list[dict]:
    """Get today's missed skill suggestions."""
    if not _HAS_SKILL_TRACKER:
        return []
    try:
        return _skill_tracker_mod.get_missed_today()
    except Exception:
        log.exception("get_missed_skills_summary")
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
    real token costs from session JSONL files (model-specific pricing).
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

    # 2. Real token costs from session JSONLs (7-day scan)
    scan = _scan_session_jsonls(days=7)
    tokens_by_date = scan["tokens_by_date"]

    # 3. Merge: all dates from both sources, sorted desc, limit 7
    all_dates = sorted(set(calls_by_date) | set(tokens_by_date), reverse=True)[:7]
    timeline = []
    for date_str in all_dates:
        calls = calls_by_date.get(date_str, 0)
        bucket = tokens_by_date.get(date_str, {})
        cost = bucket.get("cost", 0.0)
        inp = bucket.get("input", 0) + bucket.get("cache_create", 0)
        cache_read = bucket.get("cache_read", 0)
        out = bucket.get("output", 0)
        timeline.append({
            "date": date_str,
            "calls": calls,
            "cost_est": round(cost, 4),
            "input_tokens": inp,
            "cache_read_tokens": cache_read,
            "output_tokens": out,
        })

    _cache_set("usage_timeline", timeline)
    return timeline


def get_provider_costs() -> dict[str, float]:
    """Get today's cost breakdown by provider (cached 30s).

    Anthropic: real API tokens from session JSONLs (model-specific pricing).
    Others: from ~/.claude/usage/providers.jsonl (minimax, codex, gemini).
    """
    cached = _cache_get("provider_costs")
    if cached is not None:
        return cached

    # 1. Anthropic: per-model costs from session JSONLs
    anthropic = get_anthropic_session_cost()
    costs: dict[str, float] = {}
    for tier in ("opus", "sonnet", "haiku"):
        tier_cost = anthropic.get("models", {}).get(tier, {}).get("cost_usd", 0.0)
        if tier_cost > 0:
            costs[tier] = tier_cost

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


# ---------------------------------------------------------------------------
# Sidecar Watcher — queries Sidecar V6 daemon via /tmp/sidecar-v6.sock
# ---------------------------------------------------------------------------


def _category_to_severity(category: str) -> str:
    """Map V6 mechanism category to UI severity level."""
    if category == "safety":
        return "critical"
    if category in ("adhs", "context"):
        return "warning"
    return "info"


def _default_sidecar_status() -> dict:
    return {
        "running": False,
        "sessions": 0,
        "active_findings": [],
        "detectors": {},
        "mechanisms_list": [],
        "mechanisms_total": 0,
        "mechanisms_active": 0,
        "phase": "",
    }


def _query_sidecar_status() -> dict:
    """Query Sidecar V6 daemon via Unix socket (blocking worker helper)."""
    result = _default_sidecar_status()
    sock_path = Path("/tmp/sidecar-v6.sock")
    if not sock_path.exists():
        return result

    try:
        import socket as sock_mod

        def _query(request: dict) -> dict:
            s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
            # Keep timeouts tight so a wedged sidecar cannot pile up worker
            # threads even though we no longer block GTK.
            s.settimeout(0.75)
            try:
                s.connect(str(sock_path))
                s.sendall(json.dumps(request).encode())
                s.shutdown(sock_mod.SHUT_WR)  # signal EOF so daemon responds
                data = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                return json.loads(data.decode().strip())
            finally:
                s.close()

        # Query status
        status_resp = _query({"type": "ctl", "cmd": "status"})
        result["running"] = True

        session_data = status_resp.get("session", {})
        result["phase"] = session_data.get("phase", "")

        # Count V6 session files
        v6_sessions = list(Path("/tmp").glob("sidecar-v6-ses-*.json"))
        result["sessions"] = len(v6_sessions)

        # Query ranking for mechanism info
        try:
            ranking_resp = _query({"type": "ctl", "cmd": "ranking"})
            result["mechanisms_total"] = ranking_resp.get("total", 0)
            result["mechanisms_active"] = ranking_resp.get("active", 0)

            # Build detectors dict for UI (panel.py, swarm_tab.py)
            mechanisms = ranking_resp.get("mechanisms", [])
            detectors = {}
            for m in mechanisms:
                name = m.get("name", "")
                if not name:
                    continue
                detectors[name] = {
                    "active": m.get("active", False),
                    "severity": _category_to_severity(m.get("category", "")),
                    "count": m.get("fire_count", 0),
                    "last_seen": m.get("last_seen", ""),
                }
            result["detectors"] = detectors
            result["mechanisms_list"] = mechanisms  # full data for Monitor tab
            result["active_findings"] = [
                n for n, d in detectors.items() if d["active"]
            ][:10]
        except Exception:
            pass  # ranking query is optional

    except Exception:
        log.exception("sidecar v6 query")

    return result


def _refresh_sidecar_status_async(cache_key: str) -> None:
    """Refresh sidecar status in a background thread."""
    global _sidecar_refresh_in_flight
    try:
        _cache_set(cache_key, _query_sidecar_status(), ttl=10)
    finally:
        with _sidecar_lock:
            _sidecar_refresh_in_flight = False


def get_sidecar_status() -> dict:
    """Return the latest known Sidecar V6 status without blocking GTK."""
    global _sidecar_refresh_in_flight

    cache_key = "sidecar_status"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    stale = _cache.get(cache_key, _default_sidecar_status())

    with _sidecar_lock:
        if not _sidecar_refresh_in_flight:
            _sidecar_refresh_in_flight = True
            threading.Thread(
                target=_refresh_sidecar_status_async,
                args=(cache_key,),
                daemon=True,
                name="claude-panel-sidecar",
            ).start()

    return stale
