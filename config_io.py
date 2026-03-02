"""Config I/O for Claude Code Control Panel.

Reads and writes ~/.claude/settings.json with atomic operations (backup + tmp + rename).
Also handles hook configuration and coaching script parameters.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
COACH_SCRIPT = Path.home() / ".claude" / "hooks" / "coaching" / "coach-git-test.sh"


def read_settings() -> dict:
    """Read and parse ~/.claude/settings.json."""
    if not SETTINGS_PATH.exists():
        return {}
    with SETTINGS_PATH.open() as f:
        return json.load(f)


def write_settings(settings: dict) -> None:
    """Atomically write settings.json (backup + tmp + rename)."""
    # Create backup
    if SETTINGS_PATH.exists():
        backup = SETTINGS_PATH.with_suffix(
            f".json.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(SETTINGS_PATH, backup)

    # Write to temp file, then atomic rename
    fd, tmp_path = tempfile.mkstemp(
        dir=SETTINGS_PATH.parent, suffix=".tmp", prefix="settings-"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, SETTINGS_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def update_setting(key_path: str, value) -> dict:
    """Update a nested key in settings.json using dot notation.

    Example: update_setting("env.SLASH_COMMAND_TOOL_CHAR_BUDGET", "15000")
    """
    settings = read_settings()
    keys = key_path.split(".")
    obj = settings
    for key in keys[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    obj[keys[-1]] = value
    write_settings(settings)
    return settings


def read_hook_list() -> list[dict]:
    """Extract all hooks from settings.json as a flat list.

    Returns list of dicts with:
        event, group_index, matcher, command, timeout, async_, short_name
    """
    settings = read_settings()
    hooks_config = settings.get("hooks", {})
    result = []

    for event_name, hook_groups in hooks_config.items():
        for group_idx, group in enumerate(hook_groups):
            matcher = group.get("matcher", "")
            for hook in group.get("hooks", []):
                command = hook.get("command", "")
                short_name = _derive_hook_name(command)
                result.append({
                    "event": event_name,
                    "group_index": group_idx,
                    "matcher": matcher,
                    "command": command,
                    "timeout": hook.get("timeout", 10),
                    "async_": hook.get("async", False),
                    "short_name": short_name,
                })
    return result


def _derive_hook_name(command: str) -> str:
    """Extract a readable name from a hook command path."""
    parts = command.strip().split()
    for part in reversed(parts):
        if "/" in part:
            name = Path(part).stem
            return name.replace("_", " ").replace("-", " ").title()
    return command[:30]


def update_hook(event: str, group_index: int, field: str, value) -> dict:
    """Update a specific hook parameter in settings.json.

    field can be: 'timeout', 'async'
    """
    settings = read_settings()
    hooks_config = settings.get("hooks", {})

    if event not in hooks_config:
        raise KeyError(f"Event '{event}' not found in hooks config")
    if group_index >= len(hooks_config[event]):
        raise IndexError(f"Group index {group_index} out of range")

    group = hooks_config[event][group_index]
    for hook in group.get("hooks", []):
        if field == "timeout":
            hook["timeout"] = value
        elif field == "async":
            if value:
                hook["async"] = True
            elif "async" in hook:
                del hook["async"]

    write_settings(settings)
    return settings


def read_coaching_rate_limit() -> int:
    """Read RATE_LIMIT_SECONDS from coach-git-test.sh."""
    if not COACH_SCRIPT.exists():
        return 300
    with COACH_SCRIPT.open() as f:
        for line in f:
            if line.strip().startswith("RATE_LIMIT_SECONDS="):
                try:
                    return int(line.split("=", 1)[1].strip().split("#")[0].strip())
                except ValueError:
                    return 300
    return 300


def write_coaching_rate_limit(seconds: int) -> None:
    """Update RATE_LIMIT_SECONDS in coach-git-test.sh."""
    if not COACH_SCRIPT.exists():
        return

    lines = COACH_SCRIPT.read_text().splitlines(keepends=True)
    new_lines = []
    for line in lines:
        if line.strip().startswith("RATE_LIMIT_SECONDS="):
            new_lines.append(f"RATE_LIMIT_SECONDS={seconds}   # {seconds // 60} minutes\n")
        else:
            new_lines.append(line)

    # Atomic write
    fd, tmp = tempfile.mkstemp(dir=COACH_SCRIPT.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(new_lines)
        os.replace(tmp, COACH_SCRIPT)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
