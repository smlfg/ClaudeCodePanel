#!/usr/bin/env python3
"""Tests for shortcut_counter_tab.py

Run with: python3 -m pytest test_shortcut_counter.py -v
or:        python3 test_shortcut_counter.py
"""

import sqlite3
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Test 1: Import check (no GTK required)
# ---------------------------------------------------------------------------

def test_import():
    """Module must be importable without crashing.
    GTK may not be available in CI — mock gi before importing."""
    # Mock gi and GTK so the import doesn't need a display
    gi_mock = MagicMock()
    gtk_mock = MagicMock()
    glib_mock = MagicMock()
    pango_mock = MagicMock()

    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", MagicMock())
    sys.modules.setdefault("gi.repository.Gtk", gtk_mock)
    sys.modules.setdefault("gi.repository.GLib", glib_mock)
    sys.modules.setdefault("gi.repository.Pango", pango_mock)
    sys.modules.setdefault("theme", MagicMock(
        get_palette=lambda: {
            "text": "#cdd6f4", "overlay": "#6c7086", "dim": "#585b70",
            "green": "#a6e3a1", "yellow": "#f9e2af",
        },
        hex_to_pango_rgb=lambda h: (0, 0, 0),
    ))

    import importlib
    import shortcut_counter_tab as sct
    importlib.reload(sct)  # reload with mocks in place

    assert hasattr(sct, "build_shortcut_counter_tab"), "build_shortcut_counter_tab missing"
    assert hasattr(sct, "refresh_shortcut_counter"), "refresh_shortcut_counter missing"
    assert callable(sct.build_shortcut_counter_tab)
    assert callable(sct.refresh_shortcut_counter)
    print("PASS  test_import")


# ---------------------------------------------------------------------------
# Test 2: _load_rows — DB missing → returns []
# ---------------------------------------------------------------------------

def test_load_rows_missing_db():
    """_load_rows must return [] when the DB file does not exist."""
    import shortcut_counter_tab as sct

    with patch.object(sct, "_DB_PATH", Path("/nonexistent/path/shortcuts.db")):
        rows = sct._load_rows()

    assert rows == [], f"Expected [], got {rows}"
    print("PASS  test_load_rows_missing_db")


# ---------------------------------------------------------------------------
# Test 3: _load_rows — real SQLite DB
# ---------------------------------------------------------------------------

def test_load_rows_with_db():
    """_load_rows must return dicts with expected keys from a real SQLite file."""
    import shortcut_counter_tab as sct

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE shortcuts "
            "(combo TEXT, count INTEGER, last_used TEXT, category TEXT)"
        )
        conn.execute(
            "INSERT INTO shortcuts VALUES (?, ?, ?, ?)",
            ("Ctrl+C", 42, "2026-03-04T10:00:00", "Editing"),
        )
        conn.execute(
            "INSERT INTO shortcuts VALUES (?, ?, ?, ?)",
            ("Ctrl+V", 5, None, "Editing"),
        )
        conn.commit()
        conn.close()

        with patch.object(sct, "_DB_PATH", db_path):
            rows = sct._load_rows()

        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        # Should be sorted by count DESC
        assert rows[0]["combo"] == "Ctrl+C", f"First row should be Ctrl+C (count 42), got {rows[0]['combo']}"
        assert rows[0]["count"] == 42
        assert rows[1]["combo"] == "Ctrl+V"
        # Keys present
        for key in ("combo", "count", "last_used", "category"):
            assert key in rows[0], f"Key '{key}' missing from row"

        print("PASS  test_load_rows_with_db")
    finally:
        db_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 4: _load_config_combos — config missing → returns []
# ---------------------------------------------------------------------------

def test_load_config_combos_missing():
    """_load_config_combos must return [] when config.toml does not exist."""
    import shortcut_counter_tab as sct

    with patch.object(sct, "_CONFIG_PATH", Path("/nonexistent/config.toml")):
        combos = sct._load_config_combos()

    assert combos == [], f"Expected [], got {combos}"
    print("PASS  test_load_config_combos_missing")


# ---------------------------------------------------------------------------
# Test 5: _load_config_combos — real TOML file
# ---------------------------------------------------------------------------

def test_load_config_combos_with_file():
    """_load_config_combos must parse shortcuts from a TOML config."""
    import shortcut_counter_tab as sct

    toml_content = b"""
[shortcuts.Editing]
combos = ["Ctrl+C", "Ctrl+V", "Ctrl+Z"]

[shortcuts.Navigation]
combos = ["Ctrl+Tab", "Alt+F4"]
"""
    with tempfile.NamedTemporaryFile(suffix=".toml", delete=False) as f:
        f.write(toml_content)
        cfg_path = Path(f.name)

    try:
        with patch.object(sct, "_CONFIG_PATH", cfg_path):
            combos = sct._load_config_combos()

        assert len(combos) == 5, f"Expected 5 combos, got {len(combos)}: {combos}"
        assert "Ctrl+C" in combos
        assert "Alt+F4" in combos
        print("PASS  test_load_config_combos_with_file")
    finally:
        cfg_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Test 6: _status_label thresholds
# ---------------------------------------------------------------------------

def test_status_label():
    """_status_label must return correct labels for count thresholds."""
    import shortcut_counter_tab as sct

    assert sct._status_label(0) == "Not Used"
    assert sct._status_label(1) == "Learning"
    assert sct._status_label(49) == "Learning"
    assert sct._status_label(50) == "Mastered"
    assert sct._status_label(100) == "Mastered"
    print("PASS  test_status_label")


# ---------------------------------------------------------------------------
# Test 7: _fmt_last_used edge cases
# ---------------------------------------------------------------------------

def test_fmt_last_used():
    """_fmt_last_used must handle None, ISO strings, and datetime objects."""
    from datetime import datetime
    import shortcut_counter_tab as sct

    assert sct._fmt_last_used(None) == "—"
    assert sct._fmt_last_used("2026-03-04T10:30:00") == "04.03 10:30"

    dt = datetime(2026, 3, 4, 10, 30)
    assert sct._fmt_last_used(dt) == "04.03 10:30"
    print("PASS  test_fmt_last_used")


# ---------------------------------------------------------------------------
# Test 8: Widget creation (GTK required — skipped if no display)
# ---------------------------------------------------------------------------

def test_build_tab_gtk():
    """build_shortcut_counter_tab must return a Gtk.ScrolledWindow.

    Skipped if no GTK display is available (e.g. headless CI).
    Uses hasattr check to avoid isinstance() issues with mocked types.
    """
    # Clean up any mocks from test_import so real GTK is loaded
    for key in list(sys.modules.keys()):
        if key in ("gi.repository.Gtk", "gi.repository.GLib", "gi.repository.Pango",
                   "theme", "shortcut_counter_tab"):
            sys.modules.pop(key, None)

    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        ok, _ = Gtk.init_check(None)
        if not ok:
            print("SKIP  test_build_tab_gtk (Gtk.init_check failed — no display)")
            return
    except Exception as e:
        print(f"SKIP  test_build_tab_gtk (no GTK display: {e})")
        return

    import importlib
    import shortcut_counter_tab
    importlib.reload(shortcut_counter_tab)

    tab = shortcut_counter_tab.build_shortcut_counter_tab()
    # Check widget type by name to avoid isinstance() failures with stale mocks
    type_name = type(tab).__name__
    assert type_name == "ScrolledWindow", f"Expected ScrolledWindow, got {type_name}"
    print("PASS  test_build_tab_gtk")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_import,
        test_load_rows_missing_db,
        test_load_rows_with_db,
        test_load_config_combos_missing,
        test_load_config_combos_with_file,
        test_status_label,
        test_fmt_last_used,
        test_build_tab_gtk,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed.append(t.__name__)

    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests passed.")
