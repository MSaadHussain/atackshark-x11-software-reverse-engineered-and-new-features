"""Tiny static-analysis helper for AttackShark.exe (x86, 32-bit).

    import x; x.load()                # disassembles .text once, caches to x.pkl
    x.xrefs(0x4f1234)                 # instructions whose operands mention a VA
    x.func(0x401000)                  # instructions from a function start to its ret
    x.around(addr, before=30, after=10)
"""
import bisect
import os
import pickle
import re

import capstone
import pefile

EXE = r"C:\Program Files (x86)\Attack Shark Software\AttackShark.exe"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "x.pkl")

pe = None
base = 0
ins = []    # list of (addr, size, mnemonic, op_str)
addrs = []


def load():
    global pe, base, ins, addrs
    pe = pefile.PE(EXE, fast_load=False)
    base = pe.OPTIONAL_HEADER.ImageBase
    if os.path.exists(CACHE):
        ins = pickle.load(open(CACHE, "rb"))
    else:
        text = [s for s in pe.sections if s.Name.startswith(b".text")][0]
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        md.skipdata = True
        ins = [(i.address, i.size, i.mnemonic, i.op_str)
               for i in md.disasm(text.get_data(), base + text.VirtualAddress)]
        pickle.dump(ins, open(CACHE, "wb"))
    addrs = [i[0] for i in ins]


def off2va(off):
    return base + pe.get_rva_from_offset(off)


def va2off(va):
    return pe.get_offset_from_rva(va - base)


def idx(addr):
    return bisect.bisect_left(addrs, addr)


def fmt(i):
    return f"{i[0]:08x}  {i[2]:<7} {i[3]}"


def xrefs(va):
    pat = re.compile(r"0x0*%x\b" % va)
    return [i for i in ins if pat.search(i[3])]


def around(addr, before=30, after=10):
    k = idx(addr)
    return "\n".join(fmt(i) for i in ins[max(0, k - before):k + after])


def func_start(addr):
    """Walk back to the nearest 'push ebp; mov ebp, esp' (or int3 padding)."""
    k = idx(addr)
    while k > 0:
        a, b = ins[k - 1], ins[k]
        if b[2] == "push" and b[3] == "ebp" and k + 1 < len(ins) and ins[k + 1][3] == "ebp, esp":
            return b[0]
        if a[2] == "int3" and b[2] != "int3":
            return b[0]
        k -= 1
    return ins[0][0]


def func(addr, limit=3000):
    k = idx(func_start(addr))
    out = []
    for i in ins[k:k + limit]:
        out.append(i)
        if i[2] == "int3" and len(out) > 2 and out[-2][2] in ("ret", "jmp", "int3"):
            break
    return out


def string_at(va, wide=None):
    off = va2off(va)
    data = pe.__data__[off:off + 400]
    if wide or (wide is None and len(data) > 1 and data[1] == 0):
        return data.decode("utf-16le", "replace").split("\0")[0]
    return data.split(b"\0")[0].decode("latin1")
