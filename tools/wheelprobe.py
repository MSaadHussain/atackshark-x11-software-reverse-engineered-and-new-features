"""Count raw wheel/button/move events coming from the X11 for N seconds.

    python tools/wheelprobe.py [seconds]

Raw Input sits below any app hooks, so zero wheel events while scrolling means
the mouse itself is not sending them (firmware mapping or hardware).
"""
import ctypes
import os
import sys
import threading
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import health as H  # noqa: E402

u = H.user32
stats = {}   # device name -> counters
names = {}


def dev_name(h):
    k = int(h or 0)
    if k not in names:
        n = wintypes.UINT(0)
        u.GetRawInputDeviceInfoW(h, 0x20000007, None, ctypes.byref(n))
        b = ctypes.create_unicode_buffer(n.value + 1)
        u.GetRawInputDeviceInfoW(h, 0x20000007, b, ctypes.byref(n))
        names[k] = b.value
    return names[k]


def proc(hwnd, msg, wp, lp):
    if msg == 0xFF:
        ri = H.RAWINPUT()
        sz = wintypes.UINT(ctypes.sizeof(ri))
        if u.GetRawInputData(lp, 0x10000003, ctypes.byref(ri), ctypes.byref(sz),
                             ctypes.sizeof(H.RAWINPUTHEADER)) > 0 and ri.header.dwType == 0:
            name = dev_name(ri.header.hDevice) or f"<handle {int(ri.header.hDevice or 0)}>"
            d = stats.setdefault(name, {"wheel_up": 0, "wheel_down": 0, "buttons": 0, "move": 0})
            f = ri.mouse.usButtonFlags
            if f & 0x0400:
                delta = ctypes.c_short(ri.mouse.usButtonData).value
                d["wheel_up" if delta > 0 else "wheel_down"] += 1
            if f & 0x03FF:
                d["buttons"] += 1
            if ri.mouse.lLastX or ri.mouse.lLastY:
                d["move"] += 1
    return u.DefWindowProcW(hwnd, msg, wp, lp)


def main(seconds):
    cb = H.WNDPROC(proc)
    hi = ctypes.windll.kernel32.GetModuleHandleW(None)
    wc = H.WNDCLASSW(lpfnWndProc=cb, hInstance=hi, lpszClassName="WheelProbe")
    u.RegisterClassW(ctypes.byref(wc))
    hwnd = u.CreateWindowExW(0, "WheelProbe", "", 0, 0, 0, 0, 0, wintypes.HWND(-3), None, hi, None)
    rid = H.RAWINPUTDEVICE(1, 2, 0x100, hwnd)
    u.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid))
    threading.Timer(seconds, lambda: u.PostMessageW(hwnd, 0x12, 0, 0)).start()
    print(f"Scroll the wheel and click for {seconds} s…", flush=True)
    m = wintypes.MSG()
    while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
        u.TranslateMessage(ctypes.byref(m))
        u.DispatchMessageW(ctypes.byref(m))
    for name, d in stats.items():
        print(f"{d}  {name}", flush=True)
    if not stats:
        print("no mouse input at all", flush=True)


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 20)
