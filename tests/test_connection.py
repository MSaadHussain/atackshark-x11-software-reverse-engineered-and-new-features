import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import device as D  # noqa: E402


def present(*pids):
    return lambda vid, pid: [{"pid": pid}] if pid in pids else []


class Connection(unittest.TestCase):
    def check(self, pids, last_status, want):
        with mock.patch.object(D.hid, "enumerate", present(*pids)):
            self.assertEqual(D.connection(last_status, now=100), want)

    def test_cable_wins_even_with_dongle_plugged(self):
        self.check((D.WIRED, D.WIRELESS), None, "wired")

    def test_wireless_needs_recent_status(self):
        self.check((D.WIRELESS,), 98, "wireless")

    def test_dongle_without_mouse(self):
        self.check((D.WIRELESS,), 80, "dongle_only")
        self.check((D.WIRELESS,), None, "dongle_only")

    def test_nothing(self):
        self.check((), None, "none")


if __name__ == "__main__":
    unittest.main()
