import ctypes
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import softmacro as S  # noqa: E402

DOWN, UP = 0x0201, 0x0202


class HoldTrigger(unittest.TestCase):
    def setUp(self):
        self.started = []
        self.L = S.Listener([{"trigger": "hold_left", "hold_ms": 150, "steps": [{"wait": 10}]}], armed=True)
        self.L._start = lambda key, macro: (self.started.append(key),
                                            self.L._running.__setitem__(key, S.Player()))
        self.info = S._MSLL()
        self.p = ctypes.addressof(self.info)

    def press(self, held_s):
        self.L._mouse_proc(0, DOWN, self.p)
        time.sleep(held_s)
        self.L._mouse_proc(0, UP, self.p)

    def test_quick_click_does_not_start(self):
        self.press(0.05)
        time.sleep(0.25)
        self.assertEqual(self.started, [])

    def test_long_hold_starts_and_release_stops(self):
        self.press(0.3)
        self.assertEqual(self.started, ["hold:left"])
        self.assertEqual(self.L._running, {})

    def test_disarmed_ignores_hold(self):
        self.L.armed = False
        self.press(0.3)
        self.assertEqual(self.started, [])


class LoopPlayback(unittest.TestCase):
    def test_loop_until_stopped(self):
        import threading
        p = S.Player()
        t = threading.Thread(target=p.play, args=([{"wait": 10}], 0))
        t.start()
        time.sleep(0.1)
        p.stop()
        t.join(1)
        self.assertFalse(t.is_alive())


if __name__ == "__main__":
    unittest.main()
