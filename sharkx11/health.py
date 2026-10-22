"""Track stutters, freezes and dropouts of the X11 and fix what can be fixed.

How problems are seen:
  * Raw Input: every movement report Windows gets from the X11's dongle, with its
    arrival time. During continuous movement at 1000 Hz reports arrive ~1 ms apart,
    so a long gap in the middle of a fast movement is a hitch (stutter), and a
    pause of 0.1-1.5 s with full-speed motion before and after is a likely freeze.
  * The dongle's status report (every ~2 s): when it stops, the wireless link dropped.
  * System settings known to cause stutter: USB selective suspend and USB power
    management on the dongle, a low polling rate, low battery.

Fixes:
  * Re-sending the mouse settings (polling rate etc.) needs no admin rights; the
    auto-fixer does it by itself when stutter shows up (at most every 5 minutes).
  * USB power settings are Windows settings: fix_usb_power() asks for admin (UAC)
    and is never run silently.
"""
from __future__ import annotations

import base64
import collections
import ctypes
import subprocess
import threading
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass

from .device import PIDS, VID

USB_SUBGROUP = "2a737441-1930-4402-8d77-b2bebba308a3"
USB_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
DEV_KEY = f"VID_{VID:04X}&PID_{PIDS[0]:04X}"

HITCH_MIN_MS = 25        # gap inside fast motion that a human would feel
FREEZE_MIN_MS = 100
FREEZE_MAX_MS = 1500
FAST = 3                 # counts per report that mean "clearly moving"


@dataclass
class Event:
    t: float
    kind: str            # hitch | freeze | link_lost | link_back | fix
    detail: str


# --------------------------------------------------------------- raw input

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                   ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                          ctypes.POINTER(wintypes.UINT)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    # usButtonFlags/usButtonData live in a 4-byte-aligned union, hence the padding.
    _fields_ = [("usFlags", wintypes.USHORT), ("_pad", wintypes.USHORT), ("usButtonFlags", wintypes.USHORT),
                ("usButtonData", wintypes.USHORT), ("ulRawButtons", wintypes.ULONG),
                ("lLastX", wintypes.LONG), ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class MotionAnalyzer:
    """Pure logic: feed (time_s, dx, dy) reports, get hitches/freezes and rates."""

    def __init__(self):
        self.prev = None                      # (t, speed)
        self.second = int(time.time())
        self.count = 0
        self.fast = 0
        self.rates = collections.deque(maxlen=120)   # reports/s of seconds with fast motion
        self.events: list[Event] = []

    def feed(self, t: float, dx: int, dy: int):
        speed = abs(dx) + abs(dy)
        sec = int(t)
        if sec != self.second:
            # A mouse only reports when it moves, so slow movement legitimately gives
            # fewer reports. Judge the rate only on seconds of mostly fast movement.
            if self.count >= 100 and self.fast >= 0.8 * self.count:
                self.rates.append(self.count)
            self.second, self.count, self.fast = sec, 0, 0
        self.count += 1
        self.fast += speed >= FAST
        if self.prev:
            pt, ps = self.prev
            gap = (t - pt) * 1000
            if ps >= FAST and speed >= FAST:
                if FREEZE_MIN_MS <= gap <= FREEZE_MAX_MS:
                    self.events.append(Event(t, "freeze", f"cursor stopped {gap:.0f} ms mid-movement"))
                elif HITCH_MIN_MS <= gap < FREEZE_MIN_MS:
                    self.events.append(Event(t, "hitch", f"{gap:.0f} ms gap while moving"))
        self.prev = (t, speed)

    def report_rate(self):
        return round(sum(self.rates) / len(self.rates)) if self.rates else None


class RawInputMonitor(threading.Thread):
    """Feeds the X11's raw movement reports into a MotionAnalyzer."""

    def __init__(self, analyzer: MotionAnalyzer, on_event=None):
        super().__init__(daemon=True)
        self.an, self.on_event = analyzer, on_event
        self._mine: dict[int, bool] = {}
        self._hwnd = None
        self._proc = WNDPROC(self._wndproc)

    def _is_x11(self, hdev) -> bool:
        key = int(hdev or 0)
        if key not in self._mine:
            n = wintypes.UINT(0)
            user32.GetRawInputDeviceInfoW(hdev, 0x20000007, None, ctypes.byref(n))  # RIDI_DEVICENAME
            buf = ctypes.create_unicode_buffer(n.value + 1)
            user32.GetRawInputDeviceInfoW(hdev, 0x20000007, buf, ctypes.byref(n))
            name = buf.value.upper()
            self._mine[key] = f"VID_{VID:04X}" in name and any(f"PID_{p:04X}" in name for p in PIDS)
        return self._mine[key]

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == 0x00FF:  # WM_INPUT
            t = time.perf_counter()
            ri = RAWINPUT()
            size = wintypes.UINT(ctypes.sizeof(ri))
            if user32.GetRawInputData(lparam, 0x10000003, ctypes.byref(ri), ctypes.byref(size),
                                      ctypes.sizeof(RAWINPUTHEADER)) > 0 and ri.header.dwType == 0:
                if self._is_x11(ri.header.hDevice) and not ri.mouse.usFlags & 1:
                    n = len(self.an.events)
                    self.an.feed(self._wall(t), ri.mouse.lLastX, ri.mouse.lLastY)
                    if self.on_event:
                        for e in self.an.events[n:]:
                            self.on_event(e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _wall(self, perf):
        return self._t0 + (perf - self._p0)

    def run(self):
        self._t0, self._p0 = time.time(), time.perf_counter()
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst, lpszClassName="SharkX11RawInput")
        user32.RegisterClassW(ctypes.byref(wc))
        self._hwnd = user32.CreateWindowExW(0, "SharkX11RawInput", "", 0, 0, 0, 0, 0,
                                            wintypes.HWND(-3), None, hinst, None)  # message-only
        rid = RAWINPUTDEVICE(1, 2, 0x00000100, self._hwnd)  # mouse, RIDEV_INPUTSINK
        user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid))
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT


# --------------------------------------------------------------- system checks

@dataclass
class Check:
    key: str
    title: str
    ok: bool
    detail: str
    fix: str = ""        # "", "settings" (no admin) or "admin"


def usb_suspend_enabled() -> bool | None:
    try:
        out = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", USB_SUBGROUP, USB_SUSPEND],
                             capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    vals = [line.rsplit(":", 1)[1].strip() for line in out.splitlines() if "Current" in line and ":" in line]
    return any(int(v, 16) != 0 for v in vals) if vals else None


def dongle_power_managed() -> bool | None:
    """True if Windows may power down any of the dongle's USB interfaces."""
    found = None
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USB")
    except OSError:
        return None
    i = 0
    while True:
        try:
            dev = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        if not dev.upper().startswith(DEV_KEY):
            continue
        with winreg.OpenKey(root, dev) as dk:
            j = 0
            while True:
                try:
                    inst = winreg.EnumKey(dk, j)
                except OSError:
                    break
                j += 1
                try:
                    with winreg.OpenKey(dk, inst + r"\Device Parameters") as pk:
                        v, _ = winreg.QueryValueEx(pk, "EnhancedPowerManagementEnabled")
                        found = bool(found) or v == 1
                except OSError:
                    pass
    return found


def run_checks(cfg=None, battery_pct=None, report_rate=None, link_ok=None) -> list[Check]:
    checks = []
    s = usb_suspend_enabled()
    checks.append(Check("usb_suspend", "USB selective suspend", s is False,
                        {True: "ON: Windows can suspend the dongle, which causes hitches",
                         False: "off", None: "could not read"}[s], "admin" if s else ""))
    p = dongle_power_managed()
    checks.append(Check("usb_pm", "Dongle power management", p is False,
                        {True: "ON: 'Allow the computer to turn off this device' is set",
                         False: "off", None: "could not read"}[p], "admin" if p else ""))
    if cfg is not None:
        checks.append(Check("polling", "Polling rate", cfg.polling_hz >= 1000,
                            f"{cfg.polling_hz} Hz" + ("" if cfg.polling_hz >= 1000 else ": 1000 Hz is smoother"),
                            "settings" if cfg.polling_hz < 1000 else ""))
    if battery_pct is not None:
        checks.append(Check("battery", "Battery", battery_pct > 15, f"{battery_pct}%"
                            + ("" if battery_pct > 15 else ": low battery weakens the wireless link, charge it")))
    if report_rate is not None:
        want = cfg.polling_hz if cfg else 1000
        good = report_rate >= want * 0.6
        checks.append(Check("rate", "Measured report rate", good,
                            f"{report_rate}/s during fast movement (set {want} Hz)"
                            + ("" if good else ": interference likely, move the dongle closer / use a USB extension")))
    if link_ok is not None:
        checks.append(Check("link", "Wireless link", link_ok, "stable" if link_ok else "dropouts seen recently"))
    near = hub_neighbours()
    checks.append(Check("hub", "Devices next to the dongle", not near,
                        "none" if not near else "same USB hub: " + "; ".join(near)
                        + " - move the dongle to a separate port/extension cable"))
    return checks


def hub_neighbours() -> list[str]:
    """Other USB devices on the same hub as the dongle (possible 2.4 GHz neighbours)."""
    ps = (f"$d = Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -like 'USB\\{DEV_KEY}\\*' }} | "
          "Select-Object -First 1; if (-not $d) { exit }; "
          "$hub = (Get-PnpDeviceProperty -InstanceId $d.InstanceId DEVPKEY_Device_Parent).Data; "
          "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match '^USB\\\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\\\' "
          "-and $_.InstanceId -ne $d.InstanceId } | ForEach-Object { "
          "if ((Get-PnpDeviceProperty -InstanceId $_.InstanceId DEVPKEY_Device_Parent).Data -eq $hub) { "
          "$_.InstanceId.Split('\\')[1] + ' ' + $_.FriendlyName } }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=0x08000000).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


FIX_SCRIPT = rf"""
powercfg /setacvalueindex SCHEME_CURRENT {USB_SUBGROUP} {USB_SUSPEND} 0
powercfg /setdcvalueindex SCHEME_CURRENT {USB_SUBGROUP} {USB_SUSPEND} 0
powercfg /setactive SCHEME_CURRENT
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\{DEV_KEY}*' -Recurse |
  Where-Object {{ $_.PSChildName -eq 'Device Parameters' }} | ForEach-Object {{
    Set-ItemProperty -Path $_.PSPath -Name EnhancedPowerManagementEnabled -Value 0 -Type DWord
    Set-ItemProperty -Path $_.PSPath -Name SelectiveSuspendEnabled -Value ([byte[]](0)) -Type Binary
  }}
Get-PnpDevice -PresentOnly | Where-Object {{ $_.InstanceId -like 'USB\{DEV_KEY}\*' }} |
  ForEach-Object {{ pnputil /restart-device $_.InstanceId | Out-Null }}
"""


def fix_usb_power() -> bool:
    """Turn off USB suspend for the dongle. Shows a UAC prompt; returns False if declined."""
    enc = base64.b64encode(FIX_SCRIPT.encode("utf-16le")).decode()
    ps = ("$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
          f"-ArgumentList '-NoProfile','-EncodedCommand','{enc}'; exit $p.ExitCode")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                       creationflags=0x08000000)
    return r.returncode == 0


# --------------------------------------------------------------- watchdog

class Watchdog:
    """Combines the trackers, keeps an event log and runs the automatic fixes.

    reapply(): callback that re-sends the current settings to the mouse.
    """

    REAPPLY_EVERY = 300

    def __init__(self, reapply=None, on_event=None, auto_fix=True, log_path=None):
        self.an = MotionAnalyzer()
        self.events: list[Event] = []
        self.reapply, self.on_event = reapply, on_event
        self.auto_fix = auto_fix
        self.log_path = log_path
        self.last_status = None
        self.link_down_since = None
        self._last_fix = 0.0
        self.raw = RawInputMonitor(self.an, on_event=self._motion_event)

    def start(self):
        self.raw.start()

    def stop(self):
        self.raw.stop()

    def _emit(self, e: Event):
        self.events.append(e)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e.t))},{e.kind},{e.detail}\n")
        if self.on_event:
            self.on_event(e)

    def _motion_event(self, e: Event):
        # No action here on purpose: re-sending settings over a struggling radio
        # link was measured to cause ~500 ms freezes of its own.
        self._emit(e)

    def status_seen(self, t=None):
        """Call whenever a dongle status (battery) report arrives."""
        t = t or time.time()
        if self.link_down_since:
            self._emit(Event(t, "link_back", f"wireless link back after {t - self.link_down_since:.0f} s"))
            down = t - self.link_down_since
            self.link_down_since = None
            if down >= 30:   # mouse slept or was switched off, not a radio blip
                self._auto_fix("mouse reconnected")
        self.last_status = t

    def tick(self, t=None):
        """Call about once a second to notice link loss."""
        t = t or time.time()
        if self.last_status and not self.link_down_since and t - self.last_status > 6:
            self.link_down_since = self.last_status
            self._emit(Event(t, "link_lost", "no status from the mouse for 6 s (sleep, off or out of range)"))

    def _auto_fix(self, why):
        if not self.auto_fix or not self.reapply or time.time() - self._last_fix < self.REAPPLY_EVERY:
            return
        self._last_fix = time.time()
        threading.Thread(target=self._do_reapply, args=(why,), daemon=True).start()

    def _do_reapply(self, why):
        # Wait until the mouse has been still for 3 s so the packets can't interrupt movement.
        end = time.time() + 120
        while time.time() < end and self.an.prev and time.time() - self.an.prev[0] < 3:
            time.sleep(0.5)
        try:
            ok = self.reapply()
        except Exception as e:  # device busy/unplugged: report, don't crash the watcher
            ok, why = False, f"{why}: {e}"
        self._emit(Event(time.time(), "fix", f"{why} → re-sent mouse settings ({'ok' if ok else 'failed'})"))

    def diagnose(self, window=3600) -> list[str]:
        """Plain-language reading of the recent events."""
        now = time.time()
        ev = [e for e in self.events if e.t > now - window]
        stutter = [e for e in ev if e.kind in ("hitch", "freeze")]
        drops = [e for e in ev if e.kind == "link_lost"]
        if not stutter and not drops:
            return ["No problems seen in the last hour."]
        out = []
        if drops:
            near = [e for e in stutter if any(d.t - 30 <= e.t <= d.t + 30 for d in drops)]
            out.append(f"{len(drops)} wireless dropout(s); {len(near)} of {len(stutter)} stutters happened "
                       "around them. Stutter together with lost status reports means the 2.4 GHz radio "
                       "link is losing packets (interference or distance), not a Windows problem.")
        if len(stutter) >= 5:
            out.append(f"{len(stutter)} stutters in the last hour. What helps most: put the dongle on the "
                       "desk 20-30 cm from the mouse with a USB extension cable, away from other wireless "
                       "dongles, USB 3 devices and the PC case.")
        rate = self.an.report_rate()
        if rate and rate < 600:
            out.append(f"Only {rate} reports/s during fast movement (expected ~1000): packets are being dropped.")
        return out

    def summary(self, window=3600):
        now = time.time()
        recent = [e for e in self.events if e.t > now - window]
        c = collections.Counter(e.kind for e in recent)
        return {"hitches": c["hitch"], "freezes": c["freeze"], "dropouts": c["link_lost"],
                "fixes": c["fix"], "report_rate": self.an.report_rate(),
                "link_ok": self.link_down_since is None and c["link_lost"] == 0}
