# Attack Shark X11: Reverse-Engineered Software & New Features

An open-source Python replacement for the official Attack Shark X11 configuration
software. The X11's HID protocol was recovered by static analysis of the vendor
application. Every packet this tool sends is verified **byte for byte** against the
original program's own code, run in a CPU emulator.

Beyond what the official software does, it adds movement macros, battery analytics and
a stutter/dropout watchdog with automatic fixes.

> Unofficial project, not affiliated with Attack Shark. Use at your own risk.

---

## Features

### Everything the official app does
| Area | What you can set |
|------|------------------|
| DPI | 8 stages (50–22000), per-stage on/off and LED colour, active stage, angle snapping, ripple control |
| Buttons | Every function of the official app, custom keyboard shortcuts, on-board macros |
| Performance | Polling rate (125–1000 Hz), key debounce, sleep and light-off timers |
| Lighting | Effect, colour, brightness, breathing speed |
| On-board macros | Record or edit key sequences that are stored on the mouse (up to 50 steps) |
| Factory reset | Same reset command the official app sends |

### New features
* **Movement macros.** Cursor moves, recorded paths, clicks, scrolling and keys, played by the PC.
  * *Hotkey trigger:* bind any mouse button to F13–F24, and the macro runs when that button is pressed.
  * *Hold trigger:* the pattern plays while a button (e.g. left click) is held and stops on release.
    Hold macros only fire while **armed** (Scroll Lock toggles this), so normal clicking is unaffected.
  * Record a pattern by simply holding the button and drawing it.
* **Battery.** Live percentage and charging state, a 24-hour chart and a CSV history. From that
  history it estimates drain rate, time remaining, time to full, charge cycles and wear. If you
  enter the rated capacity, it also estimates mAh remaining and average current.
  The mouse itself reports only percentage and charging state, so the other values are
  clearly marked as estimates.
* **Health watchdog.**
  * Uses Windows Raw Input to time every movement report from the mouse.
  * Detects stutters (hitches), freezes and wireless dropouts, and logs them.
  * Runs system checks for known causes: USB selective suspend, USB power management on the
    dongle, polling rate, battery level and the measured report rate.
  * Auto-fix re-sends the mouse settings when problems appear.
  * A one-click fix (with a UAC prompt) disables USB power saving for the dongle.

---

## Installation

Requires Windows 10/11 and Python 3.10+.

```bash
git clone https://github.com/MSaadHussain/atackshark-x11-software-reverse-engineered-and-new-features.git
cd atackshark-x11-software-reverse-engineered-and-new-features
pip install -r requirements.txt
python -m sharkx11
```

Close the official Attack Shark software first. Only one program can configure the mouse at a time.

## Command line

```bash
python -m sharkx11 info                         # detect the mouse
python -m sharkx11 apply profiles/current.json  # send a saved profile (--dry-run prints the packets)
python -m sharkx11 reset                        # factory reset
python -m sharkx11 battery [--watch]            # battery status / continuous logging
python -m sharkx11 listen profiles/movement.json  # run movement macros without the GUI
```

## Scripting

```python
from sharkx11.softmacro import play
play([{"move": [300, 0], "ms": 200}, {"click": "left"}, {"wait": 100}])
```

Step types: `move`, `move_to`, `click`, `down`, `up`, `scroll`, `key`, `key_down`, `key_up`, `wait`.

---

## How it was reverse engineered

1. **Disassembly.** The vendor's `AttackShark.exe` (x86) was disassembled with Capstone.
   * Each supported mouse model has its own code page. The X11 is page `ms_4`
     (dongle `1D57:FA60`, cable `1D57:FA55`).
2. **Packet builders.** The *Apply* routine and the functions it calls were traced. Together they
   produce six HID feature reports: DPI, lighting/system, polling, button map, macro and reset.
3. **Transport.** The vendor's `hiddriver` DLL was analysed. It turned out to be a thin wrapper
   around `HidD_SetFeature` on the dongle's vendor collection. Macros are split into three
   64-byte chunks.
4. **Status events.** The same DLL's monitor thread was decoded. It is the source of the
   battery and charging reports.
5. **Verification.** `re/verify.py` loads the original executable into the Unicorn emulator,
   runs its packet builders against crafted profiles, and diffs their output with this
   project's encoder. Every report matches byte for byte.

The full byte-level description is in [PROTOCOL.md](PROTOCOL.md).

## Project layout

```
sharkx11/
  protocol.py   settings model and packet builders
  device.py     HID transport (HidD_SetFeature, same as the vendor driver)
  battery.py    battery reports, history log and estimates
  health.py     stutter/dropout detection, system checks, fixes
  softmacro.py  movement macros, hotkey/hold triggers, recording
  gui.py        Tkinter application
re/             analysis helpers and the emulator-based verifier
tests/          unit tests
```

## Development

```bash
python -m unittest discover tests
pip install pefile capstone unicorn
python re/verify.py        # needs the official software installed to compare against
```

## Notes

* Relative cursor movement follows Windows pointer settings. Turn off "Enhance pointer
  precision" for 1:1 playback.
* Many online games prohibit input automation and may detect injected input. You are
  responsible for how you use movement macros.
* Sleep and light-off timer values are the official app's raw slider units.

## License

MIT
