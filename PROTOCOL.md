# Attack Shark X11 HID protocol

Recovered by static analysis of `AttackShark.exe` (v. 11/2025, x86). The X11 is the
app's `ms_4` page: dongle `1D57:FA60`, wired `1D57:FA55`. `re/verify.py` runs the
exe's own report builders in Unicorn and checks them byte for byte against `sharkx11/protocol.py`.

## Transport

* Collection: interface 2, usage page `0x000B` (`MI_02&Col04`), feature report length 134.
* The app sends `HidD_SetFeature(handle, report, len)` with the **exact** report length.
  It sleeps 200 ms after each report and retries up to 5 times (`0x4a1970`).
* Byte 0 is the report ID. Every report ends with a big-endian 16-bit sum of the bytes
  from byte 3 up to the checksum.

## Reports (in the order Apply sends them, `0x4a1d20`)

| ID | Len | Builder | Content |
|----|-----|---------|---------|
| `0C` | 10 | inline | Factory reset: `0c 0a 01 fe 01 fe`, sent only by Reset |
| `04` | 56 | `0x4a2050` | DPI and angle/ripple |
| `05` | 15 | inline | Lighting, sleep, light-off, debounce |
| `06` | 9 | inline | Polling rate |
| `08` | 59 | `0x4a26c0` | Button map (18 slots × 3 bytes) |
| `09` | 131 | `0x4a2430` | Macro for one slot, sent as 3 × 64-byte chunks |

### 04: DPI
`04 38 01 | angle_snap | ripple | enabled_mask | hi_mask | hi_mask | code[8] | flag[8] | active+1 | rgb[8×3] | 02 | sum_hi sum_lo`

* Value `v`: DPI = `v*50` when `v ≤ 200`, otherwise `(v-100)*100`. 22000 DPI is `v = 320`.
* Encoding: `v ≤ 220` uses `DPI_LOW[v]`, with flag 1 when `201 ≤ v ≤ 220`. `221…300` uses
  `DPI_MID[v]` with flag 0. `> 300` uses `DPI_HIGH[(v-300)/2]` with flag 1. `hi_mask` has
  bit *i* set when `v_i > 220`.

### 05: lighting / system
`05 0f 01 | mode<<4 | (sleep&F0)|(9-breath) | (sleep<<4)|(mode∈{1,5} ? brightness : 8) | r g b | light_off | debounce | sum`

* `debounce` is in 2 ms steps (default 4 = 8 ms).
* `sleep` and `light_off` are the app's raw slider values (defaults 10 and 1).

### 06: polling
`06 09 01 v ~v`, where `v`: 1000 Hz = 1, 500 = 2, 250 = 4, 125 = 8.

### 08: buttons
For each slot 0…17, three bytes come from `BUTTON_BYTES[code]` (table `0x4ec3b8`):

* Shortcut (code `0x10`): `11 <modifier bits> <HID usage>`.
* Macro (code `0x11`): `12 00 <slot+1>`.

| Slot | X11 button | Default |
|------|-----------|---------|
| 0 | Left | Left click (1) |
| 1 | Right | Right click (2) |
| 2 | Wheel click | Middle (3) |
| 5 | DPI button | DPI cycle (6) |
| 6 / 7 | Side front / back | Forward (4) / Backward (5) |
| 16 / 17 | Wheel up / down | Scroll up (11) / down (12) |

Slots 3, 4 and 8–15 are disabled (0) by default.

### 09: macro
`09 83 slot+1 | mode | 0 0 0 | repeat | … | count@28 | pairs@29 | sum@129`

* Each pair is `(round(delay/10) | (press ? 0x01 : 0x81), HID usage)`.
* A delay over 1270 ms becomes the remainder `delay % 200` plus an extra pair `(delay // 200, 0x03)`.
* At most 50 pairs.
* Dongle transport: three chunks of `09 40 slot+1 chunk# + 60 payload bytes` (payload = report[3:]).

## Status events (input report 3)

The dongle's status collection (interface 2, usage page `0x000A`, 5-byte input) sends
`03 55 <event> <lo> <hi>`. `hiddriver_4.dll` (`0x10001b37`) forwards it to the app as
window message `0x633`, and the app parses it in `0x4a1690`.

| Event | Meaning |
|-------|---------|
| `0x10` | DPI stage changed on the mouse (`lo` = stage 1…8) |
| `0x20` | Polling changed |
| `0x40` | Battery: `lo` = state (1 on battery, 2 full, 3 charging), `hi` = percent 0…100. Sent about every 2 s. |
| `0x50` | Link status |
| `0x60` | Battery level 1…10 |
| `0x70` | Light mode changed (`lo >> 4`) |

The mouse exposes no capacity, current, voltage or health.

The query request `A0 <report> <len> 00 <flag>` (`0x4a3f50`), followed by a GetFeature,
reads settings back. Report `0x0B` returns the device id: the high byte is `0x55` for the X11.

## Profile file (`Data/ms_4/pro.data`)

The file is a 4-byte header followed by 6 profiles of `0x80E0` bytes each. Field offsets
used by the app:

| Offset | Field |
|--------|-------|
| `8C4` | DPI X[8] |
| `8E4` | DPI Y[8] |
| `904` | Stage on[8] |
| `924` | Brightness |
| `928` | Breath speed |
| `92C` | Sleep |
| `930` | Light-off |
| `934` | Active stage |
| `938` | Polling index |
| `940` | Ripple |
| `944` | Angle snap |
| `948` | Motion sync |
| `8B4` | LOD |
| `94C` | Light mode |
| `958` | Debounce |
| `95C` | Light RGB |
| `960` | DPI colours |
| `9A0 + slot×0x4F8` | Button slot (+1 code, +3 key, +7 modifiers, +0xC macro mode, +0x40 repeat, +0x44 count, +0x440 events) |

Motion sync and LOD are stored by the X11 page but never sent by its Apply.
