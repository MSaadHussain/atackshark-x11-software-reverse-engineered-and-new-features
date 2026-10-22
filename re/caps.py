"""Print HID report sizes for every collection of the Attack Shark dongle (read-only)."""
import ctypes
from ctypes import wintypes

import hid

hidlib = ctypes.WinDLL("hid.dll")
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wintypes.HANDLE


class CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort), ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort), ("Reserved", ctypes.c_ushort * 17),
                ("rest", ctypes.c_ushort * 10)]


for d in hid.enumerate(0x1D57, 0xFA60):
    path = d["path"].decode()
    h = k32.CreateFileW(path, 0, 3, None, 3, 0, None)  # no access rights needed for caps
    if h in (None, wintypes.HANDLE(-1).value):
        print("open fail", path, ctypes.get_last_error())
        continue
    pp = ctypes.c_void_p()
    caps = CAPS()
    if hidlib.HidD_GetPreparsedData(h, ctypes.byref(pp)):
        hidlib.HidP_GetCaps(pp, ctypes.byref(caps))
        hidlib.HidD_FreePreparsedData(pp)
    k32.CloseHandle(h)
    print(f"if{d['interface_number']} page={caps.UsagePage:#06x} usage={caps.Usage:#04x} "
          f"in={caps.InputReportByteLength} out={caps.OutputReportByteLength} feat={caps.FeatureReportByteLength}  {path[-60:]}")
