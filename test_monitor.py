import time
import unittest
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        monitor._cache.clear()
        monitor._cache_ts.clear()
        monitor._sidecar_refresh_in_flight = False

    def tearDown(self) -> None:
        monitor._cache.clear()
        monitor._cache_ts.clear()
        monitor._sidecar_refresh_in_flight = False

    def test_get_sidecar_status_returns_fresh_cache_without_thread(self) -> None:
        expected = {"running": True, "phase": "testing"}
        monitor._cache["sidecar_status"] = expected
        monitor._cache_ts["sidecar_status"] = time.time()

        with patch("monitor.threading.Thread") as thread_cls:
            result = monitor.get_sidecar_status()

        self.assertEqual(result, expected)
        thread_cls.assert_not_called()

    def test_get_sidecar_status_returns_stale_value_and_starts_background_refresh(self) -> None:
        stale = {"running": True, "phase": "implementation"}
        monitor._cache["sidecar_status"] = stale
        monitor._cache_ts["sidecar_status"] = 0

        started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None, name=None):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.name = name

            def start(self) -> None:
                started.append((self.target, self.args, self.daemon, self.name))

        with patch("monitor.threading.Thread", FakeThread):
            result = monitor.get_sidecar_status()

        self.assertEqual(result, stale)
        self.assertEqual(len(started), 1)
        self.assertTrue(monitor._sidecar_refresh_in_flight)

    def test_get_sidecar_status_only_starts_one_refresh_thread(self) -> None:
        monitor._cache["sidecar_status"] = {"running": False}
        monitor._cache_ts["sidecar_status"] = 0
        monitor._sidecar_refresh_in_flight = True

        with patch("monitor.threading.Thread") as thread_cls:
            result = monitor.get_sidecar_status()

        self.assertEqual(result, {"running": False})
        thread_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
