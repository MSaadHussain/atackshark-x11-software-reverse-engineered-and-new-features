import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import health as H  # noqa: E402


def stream(an, t, n, dt, speed):
    for _ in range(n):
        an.feed(t, speed, 0)
        t += dt
    return t


class Health(unittest.TestCase):
    def test_hitch_and_freeze(self):
        an = H.MotionAnalyzer()
        t = stream(an, 100.0, 300, 0.001, 5)
        t = stream(an, t + 0.040, 100, 0.001, 5)   # 40 ms gap mid-motion
        stream(an, t + 0.400, 10, 0.001, 6)        # 400 ms gap mid-motion
        self.assertEqual([e.kind for e in an.events], ["hitch", "freeze"])

    def test_slow_motion_and_natural_stop_are_ignored(self):
        an = H.MotionAnalyzer()
        t = stream(an, 100.0, 50, 0.030, 1)        # slow hand: sparse reports
        stream(an, t + 2.0, 10, 0.001, 5)          # stop, then move again later
        self.assertEqual(an.events, [])

    def test_rate_only_from_fast_seconds(self):
        an = H.MotionAnalyzer()
        t = stream(an, 100.0, 1000, 0.001, 5)      # one fast second at 1000 Hz
        t = stream(an, t, 40, 0.025, 1)            # one slow second
        stream(an, t, 2, 0.001, 5)
        self.assertEqual(an.report_rate(), 1000)

    def test_link_loss_and_return(self):
        events = []
        w = H.Watchdog(on_event=events.append, auto_fix=False)
        w.status_seen(1000)
        w.tick(1003)
        w.tick(1007)
        w.status_seen(1020)
        self.assertEqual([e.kind for e in events], ["link_lost", "link_back"])


if __name__ == "__main__":
    unittest.main()
