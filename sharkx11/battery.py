"""Battery status from the X11, plus a history log and estimates built on it.

What the mouse reports (decoded from hiddriver_4.dll 0x10001b37 and
AttackShark.exe 0x4a1690): on the dongle's status collection (interface 2,
usage page 0x0A) it sends a 5-byte input report about every 2 seconds:

    03 55 40 <state> <percent>
    state: 1 = on battery, 2 = fully charged, 3 = charging

That is all the firmware exposes. Capacity in mAh, current, voltage and health
are NOT reported; the estimates below come from how fast the percentage moves,
combined with the rated capacity you enter.
"""
from __future__ import annotations

import csv
import os
import threading
import time
from dataclasses import dataclass

import hid

from .device import PIDS, VID

STATES = {1: "On battery", 2: "Fully charged", 3: "Charging"}
EVENT_BATTERY = 0x40
LOG_EVERY = 300          # seconds between log rows when nothing changes


@dataclass
class Reading:
    t: float
    percent: int
    state: int

    @property
    def state_name(self):
        return STATES.get(self.state, f"Unknown ({self.state})")


def _status_path():
    for pid in PIDS:
        for d in hid.enumerate(VID, pid):
            if d["usage_page"] == 0x0A:
                return d["path"]
    return None


def parse(report) -> Reading | None:
    r = bytes(report)
    if len(r) >= 5 and r[0] == 0x03 and r[1] == 0x55 and r[2] == EVENT_BATTERY and r[4] <= 100:
        return Reading(time.time(), r[4], r[3])
    return None


def read_once(timeout: float = 5.0) -> Reading | None:
    """Wait for the next battery report (the mouse sends one every ~2 s)."""
    path = _status_path()
    if not path:
        return None
    h = hid.device()
    h.open_path(path)
    try:
        end = time.time() + timeout
        while time.time() < end:
            r = h.read(16, 250)
            reading = parse(r) if r else None
            if reading:
                return reading
    finally:
        h.close()
    return None


class Log:
    """CSV history: time,percent,state."""

    def __init__(self, path: str):
        self.path = path
        self.rows: list[Reading] = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.reader(f):
                    try:
                        self.rows.append(Reading(float(row[0]), int(row[1]), int(row[2])))
                    except (ValueError, IndexError):
                        pass

    def add(self, r: Reading) -> bool:
        last = self.rows[-1] if self.rows else None
        if last and last.percent == r.percent and last.state == r.state and r.t - last.t < LOG_EVERY:
            return False
        self.rows.append(r)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([round(r.t), r.percent, r.state])
        return True


def _rate(rows: list[Reading]) -> float | None:
    """Least-squares slope in %/hour over a run of readings (None if too little data)."""
    if len(rows) < 2 or rows[-1].t - rows[0].t < 15 * 60:
        return None
    ts = [(r.t - rows[0].t) / 3600 for r in rows]
    ps = [r.percent for r in rows]
    mt, mp = sum(ts) / len(ts), sum(ps) / len(ps)
    var = sum((t - mt) ** 2 for t in ts)
    return sum((t - mt) * (p - mp) for t, p in zip(ts, ps)) / var if var else None


def _last_run(rows, state, max_gap=3600):
    """The most recent uninterrupted stretch of readings in `state`."""
    run = []
    for r in reversed(rows):
        if r.state != state or (run and run[-1].t - r.t > max_gap):
            break
        run.append(r)
    return run[::-1]


def estimates(rows: list[Reading], capacity_mah: float | None) -> dict:
    """Numbers derived from the log. Everything here is an estimate."""
    out = {"discharge_pct_per_h": None, "hours_left": None, "charge_pct_per_h": None,
           "minutes_to_full": None, "mah_left": None, "avg_current_ma": None,
           "charge_current_ma": None, "cycles": 0.0, "health_pct": None}
    if not rows:
        return out
    now = rows[-1]
    d = _rate(_last_run(rows, 1))
    if d is not None and d < 0:
        out["discharge_pct_per_h"] = -d
        out["hours_left"] = now.percent / -d if now.state == 1 else None
    c = _rate(_last_run(rows, 3))
    if c is not None and c > 0:
        out["charge_pct_per_h"] = c
        if now.state == 3:
            out["minutes_to_full"] = (100 - now.percent) / c * 60
    # Equivalent full cycles: total percentage points used, / 100.
    used = sum(max(0, a.percent - b.percent) for a, b in zip(rows, rows[1:]) if b.state == 1)
    out["cycles"] = used / 100
    if capacity_mah:
        out["mah_left"] = capacity_mah * now.percent / 100
        if out["discharge_pct_per_h"]:
            out["avg_current_ma"] = capacity_mah * out["discharge_pct_per_h"] / 100
        if out["charge_pct_per_h"]:
            out["charge_current_ma"] = capacity_mah * out["charge_pct_per_h"] / 100
    out["health_pct"] = _health(rows)
    return out


def _health(rows):
    """Recent battery life vs the earliest logged, as a rough wear indicator.

    Compares the discharge rate of the first logged week with the last week.
    A battery that drains faster under the same use has less usable capacity.
    Needs at least two weeks of log.
    """
    if not rows or rows[-1].t - rows[0].t < 14 * 86400:
        return None
    first = [r for r in rows if r.t < rows[0].t + 7 * 86400 and r.state == 1]
    last = [r for r in rows if r.t > rows[-1].t - 7 * 86400 and r.state == 1]
    a, b = _rate(first), _rate(last)
    if not a or not b or a >= 0 or b >= 0:
        return None
    return max(0.0, min(100.0, 100 * a / b))


class Monitor(threading.Thread):
    """Reads battery reports in the background, logs them and calls on_reading."""

    def __init__(self, log: Log, on_reading=None, on_status=None):
        super().__init__(daemon=True)
        self.log, self.on_reading, self.on_status = log, on_reading, on_status
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            path = _status_path()
            if not path:
                if self.on_status:
                    self.on_status("Mouse not found")
                self._stop.wait(3)
                continue
            h = hid.device()
            try:
                h.open_path(path)
                while not self._stop.is_set():
                    r = h.read(16, 500)
                    reading = parse(r) if r else None
                    if reading:
                        self.log.add(reading)
                        if self.on_reading:
                            self.on_reading(reading)
            except OSError:
                if self.on_status:
                    self.on_status("Lost connection, retrying…")
                self._stop.wait(2)
            finally:
                h.close()
