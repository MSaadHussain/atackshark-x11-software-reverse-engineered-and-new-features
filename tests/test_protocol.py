"""Pure checks of the packet builders. Byte-exact equality with the official
app is covered by re/verify.py, which runs the exe's own code in an emulator."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from sharkx11 import protocol as P  # noqa: E402


class Protocol(unittest.TestCase):
    def test_lengths_and_headers(self):
        cfg = P.Config()
        self.assertEqual(P.build_dpi(cfg)[:3], b"\x04\x38\x01")
        self.assertEqual(len(P.build_dpi(cfg)), 0x38)
        self.assertEqual(len(P.build_system(cfg)), 0x0F)
        self.assertEqual(len(P.build_buttons(cfg)), 0x3B)
        self.assertEqual(P.build_polling(1000)[:5], bytes([6, 9, 1, 1, 0xFE]))
        self.assertEqual(P.build_polling(125)[3:5], bytes([8, 0xF7]))

    def test_dpi_values(self):
        self.assertEqual(P.dpi_to_value(800), 16)
        self.assertEqual(P.value_to_dpi(P.dpi_to_value(12000)), 12000)
        self.assertEqual(P.dpi_to_value(22000), 320)
        self.assertEqual(P.snap_dpi(12345), 12300)
        self.assertEqual(P.snap_dpi(21100), 21000)
        self.assertEqual(P.snap_dpi(99999), 22000)

    def test_checksum(self):
        r = P.build_buttons(P.Config())
        s = sum(r[3:57])
        self.assertEqual((r[57] << 8) | r[58], s)

    def test_shortcut_and_macro_buttons(self):
        cfg = P.Config()
        cfg.buttons[6] = P.Button(P.SHORTCUT, P.MOD_CTRL, 0x68)
        cfg.buttons[7] = P.Button(P.MACRO)
        r = P.build_buttons(cfg)
        self.assertEqual(r[3 + 18:3 + 21], bytes([0x11, 0x01, 0x68]))
        self.assertEqual(r[3 + 21:3 + 24], bytes([0x12, 0x00, 8]))

    def test_macro_split(self):
        m = P.Macro(events=[P.MacroEvent(4, True, 20), P.MacroEvent(4, False, 3000)])
        rep = P.build_macro(7, m)
        self.assertEqual(rep[28], 3)          # long delay adds a step
        chunks = P.split_macro(rep)
        self.assertEqual([len(c) for c in chunks], [64, 64, 64])
        self.assertEqual([c[3] for c in chunks], [0, 1, 2])
        with self.assertRaises(ValueError):
            P.build_macro(0, P.Macro(events=[P.MacroEvent(4, True)] * 51))

    def test_json_roundtrip(self):
        cfg = P.Config(polling_hz=500)
        cfg.buttons[5] = P.Button(P.MACRO)
        cfg.macros[5] = P.Macro("x", [P.MacroEvent(5, True, 30)], repeat=2)
        back = P.Config.from_json(cfg.to_json())
        self.assertEqual(P.build_all(back), P.build_all(cfg))


if __name__ == "__main__":
    unittest.main()
