"""Movement macros: things the mouse firmware cannot store (cursor moves, paths, scrolls).

The firmware only replays key presses, so movement macros run on the PC.
Bind a mouse button to a hotkey (F13-F24) in the firmware, and the Listener
plays the matching macro whenever that button is pressed.

A macro is a list of steps (JSON-friendly dicts):
    {"move": [dx, dy], "ms": 200}     relative move, spread smoothly over ms
    {"move_to": [x, y], "ms": 0}      absolute screen position
    {"click": "left"}                 left/right/middle/x1/x2
    {"down": "left"} / {"up": "left"}
    {"key": "F5"} / {"key_down": "ctrl"} / {"key_up": "ctrl"}
    {"scroll": -3}                    wheel notches (+ up, - down)
    {"wait": 50}                      milliseconds

Use from your own scripts:
    from sharkx11.softmacro import play
    play([{"move": [300, 0], "ms": 250}, {"click": "left"}])
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOVE, ABSOLUTE, VIRTUALDESK, WHEEL = 0x0001, 0x8000, 0x4000, 0x0800
KEYUP = 0x0002
BUTTONS = {  # name -> (down flag, up flag, mouseData)
    "left": (0x0002, 0x0004, 0), "right": (0x0008, 0x0010, 0), "middle": (0x0020, 0x0040, 0),
    "x1": (0x0080, 0x0100, 1), "x2": (0x0080, 0x0100, 2),
}
VK_NAMES = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "esc": 0x1B,
            "space": 0x20, "scrolllock": 0x91, "pause": 0x13, "tab": 0x09, "backspace": 0x08, "up": 0x26, "down": 0x28,
            "left": 0x25, "right": 0x27, **{f"f{i}": 0x6F + i for i in range(1, 25)}}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, dx=0, dy=0, data=0):
    i = INPUT(type=INPUT_MOUSE)
    i.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return i


def _key(vk, up=False):
    i = INPUT(type=INPUT_KEYBOARD)
    i.u.ki = KEYBDINPUT(vk, 0, KEYUP if up else 0, 0, 0)
    return i


def vk_of(name) -> int:
    if isinstance(name, int):
        return name
    n = str(name).lower()
    if n in VK_NAMES:
        return VK_NAMES[n]
    if len(n) == 1:
        return user32.VkKeyScanW(ord(n)) & 0xFF
    raise ValueError(f"unknown key {name!r}")


def cursor():
    p = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


class Player:
    """Plays steps; stop() aborts between steps (and inside long moves/waits)."""

    def __init__(self):
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _sleep(self, ms):
        return not self._stop.wait(ms / 1000.0)

    def _move(self, dx, dy, ms):
        steps = max(1, int(ms // 5)) if ms else 1
        done_x = done_y = 0
        for k in range(1, steps + 1):
            tx, ty = round(dx * k / steps), round(dy * k / steps)
            _send(_mouse(MOVE, tx - done_x, ty - done_y))
            done_x, done_y = tx, ty
            if steps > 1 and not self._sleep(ms / steps):
                return False
        return True

    def _move_to(self, x, y, ms):
        if ms:
            cx, cy = cursor()
            return self._move(x - cx, y - cy, ms)
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        ax = round((x - vx) * 65535 / max(1, vw - 1))
        ay = round((y - vy) * 65535 / max(1, vh - 1))
        _send(_mouse(MOVE | ABSOLUTE | VIRTUALDESK, ax, ay))
        return True

    def step(self, s: dict) -> bool:
        if "move" in s:
            return self._move(*s["move"], s.get("ms", 0))
        if "move_to" in s:
            return self._move_to(*s["move_to"], s.get("ms", 0))
        if "click" in s or "down" in s or "up" in s:
            name = s.get("click") or s.get("down") or s.get("up")
            dn, up, data = BUTTONS[name]
            if "click" in s:
                _send(_mouse(dn, data=data), _mouse(up, data=data))
            else:
                _send(_mouse(dn if "down" in s else up, data=data))
            return True
        if "scroll" in s:
            _send(_mouse(WHEEL, data=int(s["scroll"]) * 120))
            return True
        if "key" in s:
            vk = vk_of(s["key"])
            _send(_key(vk), _key(vk, up=True))
            return True
        if "key_down" in s or "key_up" in s:
            _send(_key(vk_of(s.get("key_down") or s.get("key_up")), up="key_up" in s))
            return True
        if "wait" in s:
            return self._sleep(s["wait"])
        raise ValueError(f"unknown step {s!r}")

    def play(self, steps, repeat: int = 1):
        self._stop.clear()
        for _ in range(max(1, repeat)):
            for s in steps:
                if self._stop.is_set() or not self.step(s):
                    return False
        return True


def play(steps, repeat=1):
    return Player().play(steps, repeat)


def record(seconds: float = 10.0, rate_hz: int = 100, stop_vk: int = 0x1B):
    """Record cursor movement and clicks until `stop_vk` (Esc) or timeout."""
    steps, last, t_last = [], cursor(), time.perf_counter()
    held = {n: False for n in ("left", "right", "middle")}
    vks = {"left": 0x01, "right": 0x02, "middle": 0x04}
    end = time.perf_counter() + seconds
    while time.perf_counter() < end and not (user32.GetAsyncKeyState(stop_vk) & 0x8000):
        time.sleep(1 / rate_hz)
        now, pos = time.perf_counter(), cursor()
        if pos != last:
            steps.append({"move": [pos[0] - last[0], pos[1] - last[1]],
                          "ms": round((now - t_last) * 1000)})
            last, t_last = pos, now
        for n, vk in vks.items():
            down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
            if down != held[n]:
                held[n] = down
                steps.append({"down" if down else "up": n})
                t_last = now
    return steps


def record_hold(button: str = "left", wait: float = 15.0, rate_hz: int = 200):
    """Wait for `button` to go down, record the cursor path until it is released.

    Returns relative move steps, ready to be replayed by a hold_<button> macro.
    """
    vk = {"left": 0x01, "right": 0x02, "middle": 0x04, "x1": 0x05, "x2": 0x06}[button]
    pressed = lambda: bool(user32.GetAsyncKeyState(vk) & 0x8000)  # noqa: E731
    end = time.perf_counter() + wait
    while not pressed():
        if time.perf_counter() > end:
            return []
        time.sleep(0.005)
    steps, last, t_last = [], cursor(), time.perf_counter()
    while pressed():
        time.sleep(1 / rate_hz)
        now, pos = time.perf_counter(), cursor()
        if pos != last:
            steps.append({"move": [pos[0] - last[0], pos[1] - last[1]],
                          "ms": round((now - t_last) * 1000)})
            last, t_last = pos, now
    return steps


class _MSLL(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]

# Low-level mouse messages -> (button, pressed)
_LL_BUTTONS = {0x0201: ("left", True), 0x0202: ("left", False), 0x0204: ("right", True),
               0x0205: ("right", False), 0x0207: ("middle", True), 0x0208: ("middle", False)}
HOLD_TRIGGERS = {"hold_left": "left", "hold_right": "right", "hold_middle": "middle",
                 "hold_x1": "x1", "hold_x2": "x2"}


class Listener(threading.Thread):
    """Plays movement macros on their trigger.

    trigger "hotkey" (default): the macro's F13-F24 key, usually sent by a mouse
    button. Pressing it again while the macro runs stops it.
    trigger "hold_left" (also hold_right/middle/x1/x2): the macro plays while that
    button is held and stops on release. Hold macros only fire while armed; the arm
    key (Scroll Lock by default) toggles that, so normal clicking stays normal.
    """

    def __init__(self, macros, on_event=None, arm_key="scrolllock", armed=False):
        super().__init__(daemon=True)
        if isinstance(macros, dict):   # {"F13": macro, ...} from older callers
            macros = [dict(m, hotkey=k) for k, m in macros.items()]
        self.hotkeys = {m["hotkey"]: m for m in macros
                        if m.get("trigger", "hotkey") == "hotkey" and m.get("hotkey")}
        self.holds = {HOLD_TRIGGERS[m["trigger"]]: m for m in macros if m.get("trigger") in HOLD_TRIGGERS}
        self.on_event = on_event
        self.arm_key = arm_key
        self.armed = armed
        self._tid = None
        self._running: dict[str, Player] = {}
        self._hook = None
        self._proc = _HOOKPROC(self._mouse_proc)   # keep a reference for the hook's lifetime

    def _say(self, msg):
        if self.on_event:
            self.on_event(msg)

    def run(self):
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        ids = {}
        keys = list(self.hotkeys) + ([self.arm_key] if self.holds else [])
        for n, key in enumerate(keys, start=1):
            if user32.RegisterHotKey(None, n, 0x4000, vk_of(key)):  # MOD_NOREPEAT
                ids[n] = key
            else:
                self._say(f"could not register {key} (in use by another app?)")
        if self.holds:
            self._hook = user32.SetWindowsHookExW(14, self._proc, None, 0)  # WH_MOUSE_LL
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in ids:  # WM_HOTKEY
                key = ids[msg.wParam]
                if key == self.arm_key and self.holds:
                    self.armed = not self.armed
                    self._say(f"Hold macros {'ARMED' if self.armed else 'off'} ({key} toggles)")
                else:
                    self._toggle(key, self.hotkeys[key])
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
        for n in ids:
            user32.UnregisterHotKey(None, n)

    def _mouse_proc(self, code, wparam, lparam):
        if code == 0 and self.armed:
            info = ctypes.cast(lparam, ctypes.POINTER(_MSLL)).contents
            if not info.flags & 0x01:  # ignore injected input (our own playback)
                ev = _LL_BUTTONS.get(wparam)
                if wparam in (0x020B, 0x020C):  # X buttons
                    ev = ("x1" if (info.mouseData >> 16) == 1 else "x2", wparam == 0x020B)
                if ev and ev[0] in self.holds:
                    btn, pressed = ev
                    if pressed:
                        self._start(f"hold:{btn}", self.holds[btn])
                    elif f"hold:{btn}" in self._running:
                        self._running.pop(f"hold:{btn}").stop()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _toggle(self, key, macro):
        if key in self._running:
            self._running.pop(key).stop()
        else:
            self._start(key, macro)

    def _start(self, key, macro):
        if key in self._running:
            return
        player = Player()
        self._running[key] = player

        def go():
            self._say(f"{key}: playing")
            player.play(macro.get("steps", []), macro.get("repeat", 1))
            if self._running.get(key) is player:
                del self._running[key]

        threading.Thread(target=go, daemon=True).start()

    def stop(self):
        for p in list(self._running.values()):
            p.stop()
        if self._tid:
            user32.PostThreadMessageW(self._tid, 0x0012, 0, 0)  # WM_QUIT
