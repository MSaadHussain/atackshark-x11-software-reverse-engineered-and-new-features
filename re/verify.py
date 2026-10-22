"""Run the official app's packet builders under Unicorn and diff them against sharkx11.

    python re/verify.py

For several configs, a fake X11 profile struct is filled in (field offsets from
PROTOCOL.md), the exe's own code builds each report, and the bytes it hands to
its send routine (0x4a1970) are compared with sharkx11.protocol's output.
"""
import os
import struct
import sys

import pefile
from unicorn import UC_ARCH_X86, UC_HOOK_CODE, UC_MODE_32, Uc
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBP, UC_X86_REG_ECX, UC_X86_REG_EIP, UC_X86_REG_ESI, UC_X86_REG_ESP

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import protocol as P  # noqa: E402

EXE = r"C:\Program Files (x86)\Attack Shark Software\AttackShark.exe"
SEND = 0x4a1970
MEMSET = 0x4dc8f3
STACK, STACK_SIZE = 0x00100000, 0x00100000
OBJ = 0x00300000          # fake page object (needs 0x5dbac+ bytes)
PROF = 0x00400000 - 0x10000  # below the image; profile struct (0x80e0 bytes)
STOP = 0x00000100         # return address that ends emulation

pe = pefile.PE(EXE)
BASE = pe.OPTIONAL_HEADER.ImageBase
IMAGE = pe.get_memory_mapped_image()


def profile_bytes(cfg: P.Config) -> bytearray:
    p = bytearray(0x80E0)
    w = lambda off, v: struct.pack_into("<i", p, off, v)  # noqa: E731
    for i in range(8):
        v = P.dpi_to_value(cfg.dpi[i]) if cfg.dpi[i] else 0
        w(0x8C4 + 4 * i, v)
        w(0x8E4 + 4 * i, v)
        w(0x904 + 4 * i, int(cfg.dpi_enabled[i]))
        r, g, b = cfg.dpi_colors[i]
        p[0x960 + 4 * i: 0x963 + 4 * i] = bytes([r, g, b])
    w(0x924, cfg.brightness)
    w(0x928, cfg.breath_speed)
    w(0x92C, cfg.sleep_timer)
    w(0x930, cfg.light_off_timer)
    w(0x934, cfg.dpi_stage)
    w(0x938, {v: k for k, v in {0: 125, 1: 250, 2: 500, 3: 1000}.items()}[cfg.polling_hz])
    w(0x940, int(cfg.ripple_control))
    w(0x944, int(cfg.angle_snap))
    w(0x94C, cfg.light_mode)
    w(0x958, cfg.debounce)
    p[0x95C:0x95F] = bytes(cfg.light_color)
    for slot, b in enumerate(cfg.buttons):
        s = 0x9A0 + slot * 0x4F8
        p[s] = slot
        p[s + 1] = b.code
        p[s + 3] = b.key
        p[s + 7] = b.modifiers
    for slot, m in cfg.macros.items():
        s = 0x9A0 + slot * 0x4F8
        struct.pack_into("<i", p, s + 0x0C, m.mode)
        p[s + 0x40] = m.repeat
        struct.pack_into("<H", p, s + 0x44, len(m.events))
        for i, e in enumerate(m.events):
            struct.pack_into("<BBH", p, s + 0x440 + 4 * i, e.key, 1 if e.down else 0, e.delay_ms)
    return p


def run(cfg, entry, stop_at=None, args=(), regs=None, pokes=None):
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    size = (len(IMAGE) + 0xFFF) & ~0xFFF
    uc.mem_map(BASE, size)
    uc.mem_write(BASE, IMAGE)
    uc.mem_map(0, 0x1000)
    uc.mem_map(STACK, STACK_SIZE)
    uc.mem_map(OBJ, 0x80000)
    uc.mem_map(PROF, 0x10000)
    uc.mem_write(PROF, bytes(profile_bytes(cfg)))
    uc.mem_write(OBJ + 0x5DBA8, struct.pack("<I", PROF))
    for a, b in (pokes or {}).items():
        uc.mem_write(a, b)
    sent = []
    trail = []

    def hook(uc, addr, size, _):
        if addr >= BASE:
            trail.append(addr)
            del trail[:-12]
        if addr == MEMSET:  # CRT import stub: memset(dst, val, n), cdecl
            esp = uc.reg_read(UC_X86_REG_ESP)
            ret, dst, val, n = struct.unpack("<IIII", uc.mem_read(esp, 16))
            uc.mem_write(dst, bytes([val & 0xFF]) * n)
            uc.reg_write(UC_X86_REG_EAX, dst)
            uc.reg_write(UC_X86_REG_ESP, esp + 4)
            uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == SEND:
            esp = uc.reg_read(UC_X86_REG_ESP)
            ret, buf, n = struct.unpack("<III", uc.mem_read(esp, 12))
            sent.append(bytes(uc.mem_read(buf, n)))
            uc.reg_write(UC_X86_REG_ESP, esp + 12)   # ret 8
            uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == STOP or addr == stop_at:
            uc.emu_stop()

    uc.hook_add(UC_HOOK_CODE, hook)
    esp = STACK + STACK_SIZE - 0x1000
    frame = list(args)
    esp -= 4 * (len(frame) + 1)
    uc.mem_write(esp, struct.pack("<%dI" % (len(frame) + 1), STOP, *frame))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_ECX, OBJ)
    uc.reg_write(UC_X86_REG_ESI, OBJ)
    for r, v in (regs or {}).items():
        uc.reg_write(r, v)
    try:
        uc.emu_start(entry, STOP, count=2_000_000)
    except Exception as e:
        raise RuntimeError(f"{e} after {[hex(a) for a in trail]}") from None
    return sent, uc


def official_system_and_polling(cfg):
    # Run the inline part of Apply (0x4a1e13..0x4a1f7e) with only the
    # "lighting changed" (0xfb) and "polling changed" (0xf6) flags set.
    ebp = STACK + STACK_SIZE - 0x4000
    sent, _ = run(cfg, 0x4a1e13, stop_at=0x4a1f7e,
                  regs={UC_X86_REG_EBP: ebp, UC_X86_REG_ESP: ebp - 0x200},
                  pokes={OBJ + 0xFB: b"", OBJ + 0xF6: b""})
    return sent


def official_transport(report: bytes):
    """Run the send routine itself (dongle path) and capture each SetFeature call."""
    FEAT, SLEEP = 0x200, 0x300
    captured = []
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(BASE, (len(IMAGE) + 0xFFF) & ~0xFFF)
    uc.mem_write(BASE, IMAGE)
    uc.mem_map(0, 0x1000)
    uc.mem_map(STACK, STACK_SIZE)
    uc.mem_map(OBJ, 0x80000)
    uc.mem_write(OBJ + 0x3DC, struct.pack("<I", 1))      # dongle opened
    uc.mem_write(OBJ + 0x40, struct.pack("<I", FEAT))    # SetFeature pointer
    uc.mem_write(0x4E4444, struct.pack("<I", SLEEP))     # Sleep import
    buf = STACK + 0x1000
    uc.mem_write(buf, report.ljust(0x100, bytes(1)))

    def hook(uc, addr, size, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        if addr == FEAT:                       # BOOL SetFeature(buf, len), stdcall
            ret, b, n = struct.unpack("<III", uc.mem_read(esp, 12))
            captured.append(bytes(uc.mem_read(b, n)))
            uc.reg_write(UC_X86_REG_EAX, 1)
            uc.reg_write(UC_X86_REG_ESP, esp + 12)
            uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == SLEEP:                    # VOID Sleep(ms), stdcall
            ret = struct.unpack("<I", uc.mem_read(esp, 4))[0]
            uc.reg_write(UC_X86_REG_ESP, esp + 8)
            uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == MEMSET:
            ret, dst, val, n = struct.unpack("<IIII", uc.mem_read(esp, 16))
            uc.mem_write(dst, bytes([val & 0xFF]) * n)
            uc.reg_write(UC_X86_REG_EAX, dst)
            uc.reg_write(UC_X86_REG_ESP, esp + 4)
            uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == STOP:
            uc.emu_stop()

    uc.hook_add(UC_HOOK_CODE, hook)
    esp = STACK + STACK_SIZE - 0x1000 - 12
    uc.mem_write(esp, struct.pack("<III", STOP, buf, len(report)))
    uc.reg_write(UC_X86_REG_ESP, esp)
    uc.reg_write(UC_X86_REG_ECX, OBJ)
    uc.emu_start(SEND, STOP, count=2_000_000)
    return captured


def configs():
    yield "factory defaults", P.Config()
    c = P.Config(dpi=[400, 1200, 6400, 10000, 12000, 20000, 22000, 50],
                 dpi_enabled=[True, True, False, True, True, True, True, False],
                 dpi_stage=3, angle_snap=True, ripple_control=False, polling_hz=500,
                 debounce=7, light_mode=5, light_color=[12, 34, 56], brightness=3,
                 breath_speed=2, sleep_timer=0x21, light_off_timer=15)
    c.buttons[5] = P.Button(P.FUNCTIONS["DPI up"])
    c.buttons[6] = P.Button(P.SHORTCUT, P.MOD_CTRL | P.MOD_SHIFT, 0x68)  # Ctrl+Shift+F13
    c.buttons[7] = P.Button(P.MACRO)
    c.buttons[16] = P.Button(P.FUNCTIONS["Volume up"])
    c.macros[7] = P.Macro("test", [P.MacroEvent(0x04, True, 30), P.MacroEvent(0x04, False, 55),
                                   P.MacroEvent(0x05, True, 1500), P.MacroEvent(0x05, False, 0)],
                          repeat=3)
    yield "custom", c
    yield "user's current ms_4", P.Config(polling_hz=500, light_mode=3, breath_speed=5,
                                          sleep_timer=0x21, light_off_timer=15)


def main():
    bad = 0
    for name, cfg in configs():
        checks = [("DPI", run(cfg, 0x4a2050, args=(0,))[0], [P.build_dpi(cfg)]),
                  ("buttons", run(cfg, 0x4a26c0, args=(0,))[0], [P.build_buttons(cfg)]),
                  ("system+polling", official_system_and_polling(cfg),
                   [P.build_system(cfg), P.build_polling(cfg.polling_hz)])]
        for slot, m in cfg.macros.items():
            checks.append((f"macro slot {slot}",
                           run(cfg, 0x4a2430, args=(slot + 1, PROF + 0x9A0 + slot * 0x4F8))[0],
                           [P.build_macro(slot, m)]))
        for r in [P.build_dpi(cfg)] + [P.build_macro(k, m) for k, m in cfg.macros.items()]:
            ours = P.split_macro(r) if r[0] == 0x09 else [r]
            checks.append((f"dongle transport of report {r[0]:#04x}", official_transport(r), ours))
        for what, theirs, ours in checks:
            ok = theirs == ours
            bad += not ok
            print(f"[{'OK' if ok else 'DIFF'}] {name}: {what}")
            if not ok:
                for t, o in zip(theirs, ours):
                    print("   exe :", t.hex(" "))
                    print("   ours:", o.hex(" "))
                if len(theirs) != len(ours):
                    print("   count exe", len(theirs), "ours", len(ours))
    print("ALL MATCH" if not bad else f"{bad} mismatches")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
