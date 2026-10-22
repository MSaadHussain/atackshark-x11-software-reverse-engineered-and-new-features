import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import battery as B  # noqa: E402


def run(start, hours, pct0, rate, state, step=600):
    """Readings every `step` seconds changing by `rate` %/hour."""
    return [B.Reading(start + k * step, round(pct0 + rate * k * step / 3600), state)
            for k in range(int(hours * 3600 / step) + 1)]


class Battery(unittest.TestCase):
    def test_parse(self):
        r = B.parse([0x03, 0x55, 0x40, 0x01, 0x4E])
        self.assertEqual((r.percent, r.state, r.state_name), (78, 1, "On battery"))
        self.assertIsNone(B.parse([0x03, 0x55, 0x10, 0x02, 0x00]))   # DPI-stage event

    def test_discharge_and_current(self):
        rows = run(0, 5, 90, -4, 1)
        e = B.estimates(rows, capacity_mah=500)
        self.assertAlmostEqual(e["discharge_pct_per_h"], 4, delta=0.2)
        self.assertAlmostEqual(e["avg_current_ma"], 20, delta=1)       # 500 mAh * 4 %/h
        self.assertAlmostEqual(e["hours_left"], 70 / 4, delta=1)
        self.assertAlmostEqual(e["mah_left"], 350)
        self.assertAlmostEqual(e["cycles"], 0.2, delta=0.01)

    def test_charging(self):
        rows = run(0, 1, 40, 50, 3)
        e = B.estimates(rows, capacity_mah=500)
        self.assertAlmostEqual(e["charge_pct_per_h"], 50, delta=2)
        self.assertAlmostEqual(e["minutes_to_full"], 10 / 50 * 60, delta=2)
        self.assertIsNone(e["hours_left"])

    def test_health_needs_two_weeks(self):
        self.assertIsNone(B.estimates(run(0, 5, 90, -4, 1), None)["health_pct"])
        week = 7 * 86400
        rows = run(0, 10, 100, -4, 1) + run(2 * week, 10, 100, -5, 1)
        self.assertAlmostEqual(B.estimates(rows, None)["health_pct"], 80, delta=3)

    def test_too_little_data(self):
        e = B.estimates([B.Reading(0, 50, 1)], 500)
        self.assertIsNone(e["discharge_pct_per_h"])
        self.assertEqual(e["mah_left"], 250)


if __name__ == "__main__":
    unittest.main()
