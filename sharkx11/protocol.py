"""Attack Shark X11 settings model and HID packet builders.

Everything here was recovered from AttackShark.exe (the "ms_4" page, which the
official app uses for the X11: wireless dongle 1D57:FA60, wired 1D57:FA55).
See PROTOCOL.md for the byte layouts and the addresses they came from.

This module is pure: it builds bytes and never talks to the device.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .tables import BUTTON_BYTES, DPI_HIGH, DPI_LOW, DPI_MID

DPI_MIN, DPI_MAX = 50, 22000
STAGES = 8
SLOTS = 18

POLLING_CODES = {125: 8, 250: 4, 500: 2, 1000: 1}

# Button function codes, as the official app's menu assigns them (0x4a09f0).
FUNCTIONS = {
    "Disabled": 0,
    "Left click": 1,
    "Right click": 2,
    "Middle click": 3,
    "Forward": 4,
    "Backward": 5,
    "DPI cycle": 6,
    "DPI up": 7,
    "DPI down": 8,
    "Double click": 9,
    "Fire (rapid click)": 10,
    "Scroll up": 11,
    "Scroll down": 12,
    "Tilt left": 13,
    "Tilt right": 14,
    "Easy aim": 0x16,
    "LED loop": 0x17,
    "Cut": 0x20,
    "Copy": 0x21,
    "Paste": 0x22,
    "Open": 0x23,
    "Save": 0x24,
    "Find": 0x25,
    "Redo": 0x26,
    "Select all": 0x28,
    "Print": 0x29,
    "Close window": 0x2A,
    "Swap windows": 0x2B,
    "Show desktop": 0x2C,
    "Run command": 0x2D,
    "Lock PC": 0x2E,
    "Media player": 0x40,
    "Play/Pause": 0x41,
    "Media stop": 0x42,
    "Previous track": 0x43,
    "Next track": 0x44,
    "Volume up": 0x45,
    "Volume down": 0x46,
    "Mute": 0x47,
    "Browser home": 0x50,
    "Browser favorites": 0x51,
    "Browser forward": 0x52,
    "Browser back": 0x53,
    "Browser stop": 0x54,
    "Browser refresh": 0x55,
    "Browser search": 0x56,
    "Email": 0x57,
    "Calculator": 0x58,
    "My computer": 0x59,
}
SHORTCUT = 0x10   # keyboard key + modifiers
MACRO = 0x11      # play the firmware macro stored for this slot

# Physical buttons of the X11 and the slot each one occupies in the button report.
# Slots 3, 4 and 8-15 exist in the protocol but the X11 has no button for them.
X11_SLOTS = {
    0: "Left button",
    1: "Right button",
    2: "Wheel click",
    5: "DPI button (top)",
    6: "Side button (front)",
    7: "Side button (back)",
    16: "Wheel up",
    17: "Wheel down",
}

# HID keyboard modifier bits used by shortcuts.
MOD_CTRL, MOD_SHIFT, MOD_ALT, MOD_WIN = 0x01, 0x02, 0x04, 0x08


@dataclass
class Button:
    code: int = 0
    modifiers: int = 0    # SHORTCUT only: MOD_* bits
    key: int = 0          # SHORTCUT only: HID keyboard usage (A = 0x04, F13 = 0x68)


@dataclass
class MacroEvent:
    key: int              # HID keyboard usage
    down: bool            # True = press, False = release
    delay_ms: int = 10    # wait before this event


@dataclass
class Macro:
    name: str = "Macro"
    events: list[MacroEvent] = field(default_factory=list)
    repeat: int = 1       # times to play per press (1-255)
    mode: int = 0         # raw firmware play mode, 0 = play `repeat` times


def _default_buttons():
    codes = [1, 2, 3, 0, 0, 6, 4, 5, 0, 0, 0, 0, 0, 0, 0, 0, 11, 12]  # table at 0x4ec8f0
    return [Button(c) for c in codes]


@dataclass
class Config:
    """Factory defaults match the official app's X11 reset (0x4a4670)."""
    dpi: list[int] = field(default_factory=lambda: [800, 1600, 2400, 3200, 5000, 22000, 0, 0])
    dpi_enabled: list[bool] = field(default_factory=lambda: [True] * 6 + [False] * 2)
    dpi_colors: list[list[int]] = field(default_factory=lambda: [
        [255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0],
        [0, 255, 255], [255, 0, 255], [255, 64, 0], [255, 255, 255]])
    dpi_stage: int = 1             # active stage, 0-based
    angle_snap: bool = False
    ripple_control: bool = True
    polling_hz: int = 1000
    debounce: int = 4              # x2 ms (4 = 8 ms)
    light_mode: int = 0            # 0 = off/default effect; firmware effect index
    light_color: list[int] = field(default_factory=lambda: [0, 255, 0])
    brightness: int = 8
    breath_speed: int = 6
    sleep_timer: int = 10          # raw slider value of the official app
    light_off_timer: int = 1       # raw slider value of the official app
    buttons: list[Button] = field(default_factory=_default_buttons)
    macros: dict[int, Macro] = field(default_factory=dict)   # slot -> macro

    # ----- persistence -----
    def to_json(self) -> str:
        d = asdict(self)
        d["macros"] = {str(k): v for k, v in d["macros"].items()}
        return json.dumps(d, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Config":
        d = json.loads(text)
        d["buttons"] = [Button(**b) for b in d.get("buttons", [])] or _default_buttons()
        d["macros"] = {int(k): Macro(name=m.get("name", "Macro"),
                                     events=[MacroEvent(**e) for e in m.get("events", [])],
                                     repeat=m.get("repeat", 1), mode=m.get("mode", 0))
                       for k, m in d.get("macros", {}).items()}
        return cls(**d)


# ----------------------------------------------------------------- helpers

def _checksum(buf: bytearray, start: int, end: int, at: int):
    """Big-endian 16-bit sum of buf[start:end], written at buf[at:at+2]."""
    s = sum(buf[start:end]) & 0xFFFF
    buf[at], buf[at + 1] = s >> 8, s & 0xFF


def dpi_to_value(dpi: int) -> int:
    """DPI -> the app's internal value (50-DPI steps to 10000, 100 to 20000, 200 above)."""
    dpi = max(DPI_MIN, min(DPI_MAX, int(dpi)))
    if dpi <= 10000:
        return max(1, round(dpi / 50))
    v = round(dpi / 100) + 100
    if v > 300:
        v = 300 + (v - 300) // 2 * 2
    return v


def value_to_dpi(v: int) -> int:
    return v * 50 if v <= 200 else (v - 100) * 100


def snap_dpi(dpi: int) -> int:
    """Nearest DPI the mouse can actually store."""
    return value_to_dpi(dpi_to_value(dpi))


def _encode_dpi(v: int):
    """Internal value -> (sensor code, range flag), as in 0x4a21e6."""
    if v > 300:
        return DPI_HIGH[(v - 300) // 2], 1
    if 221 <= v <= 300:
        return DPI_MID[v], 0
    return DPI_LOW[v], 1 if 201 <= v <= 220 else 0


# ----------------------------------------------------------------- reports

def build_reset() -> bytes:
    """Factory reset, sent first by the app's Reset button."""
    return bytes([0x0C, 0x0A, 0x01, 0xFE, 0x01, 0xFE, 0, 0, 0, 0])


def build_dpi(cfg: Config) -> bytes:
    buf = bytearray(0x38)
    buf[0:3] = b"\x04\x38\x01"
    buf[3] = int(cfg.angle_snap)
    buf[4] = int(cfg.ripple_control)
    values = [dpi_to_value(d) if d else 0 for d in (list(cfg.dpi) + [0] * STAGES)[:STAGES]]
    enabled = (list(cfg.dpi_enabled) + [False] * STAGES)[:STAGES]
    buf[5] = sum(1 << i for i in range(STAGES) if enabled[i])
    high = sum(1 << i for i in range(STAGES) if values[i] > 220)
    buf[6] = buf[7] = high
    for i, v in enumerate(values):
        if v:
            buf[8 + i], buf[16 + i] = _encode_dpi(v)
    buf[24] = cfg.dpi_stage + 1
    colors = (list(cfg.dpi_colors) + [[255, 255, 255]] * STAGES)[:STAGES]
    for i, (r, g, b) in enumerate(colors):
        buf[25 + 3 * i: 28 + 3 * i] = bytes([r & 0xFF, g & 0xFF, b & 0xFF])
    buf[49] = 2
    _checksum(buf, 3, 50, 50)
    return bytes(buf)


def build_system(cfg: Config) -> bytes:
    """Lighting + sleep + debounce report (0x4a1e20)."""
    buf = bytearray(0x0F)
    buf[0:3] = b"\x05\x0F\x01"
    buf[3] = (cfg.light_mode << 4) & 0xFF
    buf[4] = (cfg.sleep_timer & 0xF0) | ((9 - cfg.breath_speed) & 0x0F)
    low = cfg.brightness if cfg.light_mode in (1, 5) else 8
    buf[5] = ((cfg.sleep_timer << 4) & 0xF0) | (low & 0x0F)
    buf[6:9] = bytes(c & 0xFF for c in cfg.light_color)
    buf[9] = cfg.light_off_timer & 0xFF
    buf[10] = cfg.debounce & 0xFF
    _checksum(buf, 3, 11, 11)
    return bytes(buf)


def build_polling(hz: int) -> bytes:
    v = POLLING_CODES[hz]
    return bytes([0x06, 0x09, 0x01, v, ~v & 0xFF, 0, 0, 0, 0])


def button_bytes(slot: int, b: Button) -> tuple[int, int, int]:
    b0, b1, b2 = BUTTON_BYTES.get(b.code, (0, 0, 0))
    if b.code == SHORTCUT:
        return b0, b.modifiers & 0xFF, b.key & 0xFF
    if b.code == MACRO:
        return b0, 0, slot + 1
    return b0, b1, b2


def build_buttons(cfg: Config) -> bytes:
    buf = bytearray(0x3B)
    buf[0:3] = b"\x08\x3B\x01"
    buttons = (list(cfg.buttons) + [Button()] * SLOTS)[:SLOTS]
    for slot, b in enumerate(buttons):
        buf[3 + 3 * slot: 6 + 3 * slot] = bytes(button_bytes(slot, b))
    _checksum(buf, 3, 57, 57)
    return bytes(buf)


MACRO_MAX_PAIRS = 50


def _macro_pairs(events):
    """Event list -> byte pairs, mirroring 0x4a24f0 (10 ms units, >1270 ms split)."""
    pairs = []
    for e in events:
        flag = 0x01 if e.down else 0x81
        d = max(0, int(e.delay_ms))
        if d > 1270:
            rest = max(1, d % 200)
            pairs.append((int(rest / 10.0 + 0.5) | flag, e.key & 0xFF))
            pairs.append((d // 200, 0x03))
        else:
            pairs.append((int(max(d, 1) / 10.0 + 0.5) | flag, e.key & 0xFF))
    return pairs


def build_macro(slot: int, macro: Macro) -> bytes:
    """131-byte macro report for a 0-based button slot."""
    pairs = _macro_pairs(macro.events)
    if len(pairs) > MACRO_MAX_PAIRS:
        raise ValueError(f"macro too long: {len(pairs)} steps (max {MACRO_MAX_PAIRS})")
    buf = bytearray(0x83)
    buf[0:3] = bytes([0x09, 0x83, slot + 1])
    buf[3] = macro.mode & 0xFF
    buf[7] = max(1, min(255, macro.repeat))
    buf[28] = len(pairs)
    for i, (a, b) in enumerate(pairs):
        buf[29 + 2 * i], buf[30 + 2 * i] = a, b
    _checksum(buf, 3, 129, 129)
    return bytes(buf)


def split_macro(report: bytes) -> list[bytes]:
    """Dongle transport for report 9: three 64-byte chunks of 60 payload bytes."""
    payload = report[3:].ljust(180, b"\0")
    return [bytes([0x09, 0x40, report[2], i]) + payload[60 * i: 60 * i + 60] for i in range(3)]


def build_all(cfg: Config, reset: bool = False) -> list[bytes]:
    """Every feature report the app's Apply sends, in its order (0x4a1d20)."""
    out = [build_reset()] if reset else []
    out += [build_dpi(cfg), build_system(cfg), build_polling(cfg.polling_hz), build_buttons(cfg)]
    for slot, macro in sorted(cfg.macros.items()):
        if cfg.buttons[slot].code == MACRO:
            out += split_macro(build_macro(slot, macro))
    return out
