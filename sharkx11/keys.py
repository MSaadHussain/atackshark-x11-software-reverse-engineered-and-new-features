"""Human names for HID keyboard usages (what the firmware stores for keys)."""
from .tables import VK_TO_HID

HID_KEYS: dict[str, int] = {}
for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    HID_KEYS[c] = 0x04 + i
for i, c in enumerate("1234567890"):
    HID_KEYS[c] = 0x1E + i
HID_KEYS.update({"Enter": 0x28, "Esc": 0x29, "Backspace": 0x2A, "Tab": 0x2B, "Space": 0x2C,
                 "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34,
                 "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38, "CapsLock": 0x39,
                 "PrintScreen": 0x46, "ScrollLock": 0x47, "Pause": 0x48, "Insert": 0x49,
                 "Home": 0x4A, "PageUp": 0x4B, "Delete": 0x4C, "End": 0x4D, "PageDown": 0x4E,
                 "Right": 0x4F, "Left": 0x50, "Down": 0x51, "Up": 0x52,
                 "LCtrl": 0xE0, "LShift": 0xE1, "LAlt": 0xE2, "LWin": 0xE3,
                 "RCtrl": 0xE4, "RShift": 0xE5, "RAlt": 0xE6, "RWin": 0xE7})
for i in range(1, 13):
    HID_KEYS[f"F{i}"] = 0x3A + i - 1
for i in range(13, 25):
    HID_KEYS[f"F{i}"] = 0x68 + i - 13

HID_NAMES = {v: k for k, v in HID_KEYS.items()}

# Generic Ctrl/Shift/Alt virtual keys (what Tk reports) -> left-hand HID modifiers.
_GENERIC = {0x10: 0xE1, 0x11: 0xE0, 0x12: 0xE2}


def vk_to_hid(vk: int) -> int | None:
    return _GENERIC.get(vk) or VK_TO_HID.get(vk)


def name(usage: int) -> str:
    return HID_NAMES.get(usage, f"0x{usage:02X}")
