"""Send feature reports to the X11 exactly like the vendor's hiddriver DLL does.

The official app calls HidD_SetFeature(handle, report, len) with the exact length
of each report on the dongle's vendor collection (interface 2, usage page 0x0B),
sleeping 200 ms after each one and retrying up to 5 times.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import hid

VID = 0x1D57
WIRELESS, WIRED = 0xFA60, 0xFA55   # 2.4 GHz dongle, mouse on its USB cable
PIDS = (WIRED, WIRELESS)            # with the cable in, the mouse talks over the cable

_hid = ctypes.WinDLL("hid.dll")
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
_hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
_hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
INVALID = wintypes.HANDLE(-1).value
GENERIC_RW = 0xC0000000


class _Caps(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputLen", ctypes.c_ushort), ("OutputLen", ctypes.c_ushort),
                ("FeatureLen", ctypes.c_ushort), ("_pad", ctypes.c_ushort * 27)]


class DeviceNotFound(RuntimeError):
    pass


def _open(path: str, access: int):
    return _k32.CreateFileW(path, access, 3, None, 3, 0, None)


def _feature_len(path: str) -> int:
    h = _open(path, 0)
    if h in (None, INVALID):
        return 0
    try:
        pp, caps = ctypes.c_void_p(), _Caps()
        if not _hid.HidD_GetPreparsedData(h, ctypes.byref(pp)):
            return 0
        _hid.HidP_GetCaps(pp, ctypes.byref(caps))
        _hid.HidD_FreePreparsedData(pp)
        return caps.FeatureLen
    finally:
        _k32.CloseHandle(h)


def find() -> dict | None:
    """The vendor (feature-report) collection of the X11, cable preferred over dongle."""
    for pid in PIDS:
        for d in hid.enumerate(VID, pid):
            path = d["path"].decode()
            if _feature_len(path) > 0:
                return {"pid": pid, "path": path,
                        "mode": "wireless" if pid == WIRELESS else "wired"}
    return None


CONNECTION_LABELS = {
    "wired": "Wired (USB cable)",
    "wireless": "Wireless (2.4 GHz dongle)",
    "dongle_only": "Dongle plugged in, mouse not responding (off, asleep or out of range)",
    "none": "Not connected",
}


def connection(last_status: float | None = None, now: float | None = None) -> str:
    """How the X11 is connected right now.

    The mouse shows up as 1D57:FA55 while its cable is plugged into this PC; the
    dongle (1D57:FA60) is only a link to the mouse, so on its own it proves
    nothing. `last_status` is the time of the last dongle status report (they
    arrive every ~2 s while the mouse is awake and in range).
    """
    present = {pid for pid in PIDS if hid.enumerate(VID, pid)}
    if WIRED in present:
        return "wired"
    if WIRELESS in present:
        now = now or time.time()
        if last_status is None or now - last_status > 6:
            return "dongle_only"
        return "wireless"
    return "none"


class X11:
    def __init__(self, info: dict | None = None):
        self.info = info or find()
        if not self.info:
            raise DeviceNotFound("Attack Shark X11 not found (is the dongle plugged in?)")
        self.handle = _open(self.info["path"], GENERIC_RW)
        if self.handle in (None, INVALID):
            raise OSError(ctypes.get_last_error(), "cannot open the mouse (close the official app first)")

    def close(self):
        if self.handle not in (None, INVALID):
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def send(self, report: bytes, retries: int = 5, gap: float = 0.2) -> bool:
        report = bytearray(report)
        if self.info["mode"] == "wired" and len(report) == 64 and report[:2] == b"\x09\x40" and report[3] == 2:
            report[1] = 0x0C   # the cable path marks the last macro chunk differently (exe 0x4a1c3d)
        buf = ctypes.create_string_buffer(bytes(report), len(report))
        for _ in range(retries):
            ok = _hid.HidD_SetFeature(self.handle, buf, len(report))
            time.sleep(gap)
            if ok:
                return True
        return False

    def send_all(self, reports, progress=None) -> list[bool]:
        results = []
        for i, r in enumerate(reports):
            results.append(self.send(r))
            if progress:
                progress(i + 1, len(reports), r, results[-1])
        return results
