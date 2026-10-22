"""Tkinter GUI: the official app's settings plus firmware and movement macros."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from . import protocol as P
from . import battery as B
from . import health as Hl
from . import softmacro
from .keys import HID_KEYS, name as key_name, vk_to_hid

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(HERE, "profiles", "current.json")
MOVES = os.path.join(HERE, "profiles", "movement.json")
BATTERY_LOG = os.path.join(HERE, "profiles", "battery_log.csv")
BATTERY_CFG = os.path.join(HERE, "profiles", "battery.json")
HEALTH_LOG = os.path.join(HERE, "profiles", "health_log.csv")

SHORTCUT_LABEL, MACRO_LABEL = "Keyboard shortcut…", "Firmware macro"
FUNC_NAMES = list(P.FUNCTIONS) + [SHORTCUT_LABEL, MACRO_LABEL]
CODE_TO_NAME = {v: k for k, v in P.FUNCTIONS.items()}
HOTKEYS = [f"F{i}" for i in range(13, 25)]
TRIGGERS = {"Hotkey (F13-F24)": "hotkey", "Hold left click": "hold_left", "Hold right click": "hold_right",
            "Hold wheel click": "hold_middle", "Hold side front": "hold_x2", "Hold side back": "hold_x1"}
TRIGGER_NAMES = {v: k for k, v in TRIGGERS.items()}
SLOT_LABELS = {f"{lbl} (slot {s})": s for s, lbl in P.X11_SLOTS.items()}


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Shark X11 Control")
        self.minsize(760, 560)
        os.makedirs(os.path.dirname(PROFILE), exist_ok=True)
        self.cfg = self._load_profile(PROFILE) or P.Config()
        self.moves = self._load_moves()
        self.listener = None
        self._ui = queue.Queue()   # worker threads -> Tk main thread
        self._pump()

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        for title, build in (("Battery", self._tab_battery), ("DPI", self._tab_dpi), ("Buttons", self._tab_buttons),
                             ("Performance & light", self._tab_perf),
                             ("Firmware macros", self._tab_macros),
                             ("Movement macros", self._tab_moves), ("Health", self._tab_health)):
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=title)
            build(f)

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        ttk.Button(bar, text="Apply to mouse", command=self.apply).pack(side="right")
        ttk.Button(bar, text="Factory reset mouse", command=self.factory_reset).pack(side="right", padx=4)
        ttk.Button(bar, text="Save as…", command=self.save_as).pack(side="right")
        ttk.Button(bar, text="Open…", command=self.open_profile).pack(side="right", padx=4)
        self._refresh()
        self.after(300, self._check_device)
        self.watchdog.start()
        self._health_tick()
        self.bat_monitor = B.Monitor(self.bat_log, on_reading=lambda r: self._post(self._on_battery, r),
                                     on_status=lambda m: self._post(self.bat_state.set, m))
        self.bat_monitor.start()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _post(self, fn, *args):
        """Run fn(*args) on the Tk thread; safe to call from any thread."""
        self._ui.put((fn, args))

    def _pump(self):
        try:
            while True:
                fn, args = self._ui.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def _close(self):
        self.bat_monitor.stop()
        self.watchdog.stop()
        if self.listener:
            self.listener.stop()
        self.destroy()


    # ------------------------------------------------------------ battery
    def _tab_battery(self, f):
        self.bat_log = B.Log(BATTERY_LOG)
        try:
            with open(BATTERY_CFG, encoding="utf-8") as fh:
                cap = json.load(fh).get("capacity_mah", "")
        except (OSError, ValueError):
            cap = ""
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.bat_pct = tk.StringVar(value="--%")
        self.bat_state = tk.StringVar(value="Waiting for the mouse (it reports every ~2 s)…")
        ttk.Label(top, textvariable=self.bat_pct, font=("Segoe UI", 32, "bold")).pack(side="left")
        info = ttk.Frame(top)
        info.pack(side="left", padx=16)
        ttk.Label(info, textvariable=self.bat_state, font=("Segoe UI", 11)).pack(anchor="w")
        self.bat_bar = ttk.Progressbar(info, length=320, maximum=100)
        self.bat_bar.pack(anchor="w", pady=4)

        cfg = ttk.Frame(f)
        cfg.pack(fill="x", pady=(10, 4))
        ttk.Label(cfg, text="Rated battery capacity (mAh, from the box/manual):").pack(side="left")
        self.bat_cap = tk.StringVar(value=str(cap))
        ttk.Entry(cfg, textvariable=self.bat_cap, width=7).pack(side="left", padx=4)
        ttk.Button(cfg, text="Save", command=self._save_capacity).pack(side="left")

        grid = ttk.LabelFrame(f, text="Estimates from the battery log (the mouse reports only % and charging state)",
                              padding=8)
        grid.pack(fill="x", pady=6)
        self.bat_fields = {}
        for i, (key, label) in enumerate((
                ("mah_left", "Charge left"), ("hours_left", "Time left on battery"),
                ("discharge_pct_per_h", "Drain rate"), ("avg_current_ma", "Average current draw"),
                ("minutes_to_full", "Time to full (charging)"), ("charge_current_ma", "Charging current"),
                ("cycles", "Charge cycles logged"), ("health_pct", "Health (wear estimate)"))):
            ttk.Label(grid, text=label + ":").grid(row=i // 2, column=(i % 2) * 2, sticky="w", padx=(0, 6), pady=2)
            v = tk.StringVar(value="—")
            ttk.Label(grid, textvariable=v, width=22).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w")
            self.bat_fields[key] = v
        ttk.Label(f, text="Last 24 hours", foreground="gray").pack(anchor="w")
        self.bat_chart = tk.Canvas(f, height=150, bg="white", highlightthickness=1, highlightbackground="#ccc")
        self.bat_chart.pack(fill="x")
        self.bat_chart.bind("<Configure>", lambda e: self._draw_battery_chart())
        ttk.Label(f, text="Rates need ~15 min of readings while this app is open; health needs 2+ weeks of log.",
                  foreground="gray").pack(anchor="w", pady=(4, 0))
        if self.bat_log.rows:
            self.after(100, self._render_battery, self.bat_log.rows[-1], True)

    def _save_capacity(self):
        try:
            cap = float(self.bat_cap.get()) if self.bat_cap.get().strip() else None
        except ValueError:
            return messagebox.showerror("Capacity", "Enter a number in mAh", parent=self)
        with open(BATTERY_CFG, "w", encoding="utf-8") as fh:
            json.dump({"capacity_mah": cap}, fh)
        if self.bat_log.rows:
            self._render_battery(self.bat_log.rows[-1])

    def _on_battery(self, r):
        self.watchdog.status_seen(r.t)
        self.bat_last_pct = r.percent
        self._render_battery(r)

    def _render_battery(self, r, from_log=False):
        self.bat_pct.set(f"{r.percent}%")
        self.bat_bar["value"] = r.percent
        when = "last logged " + time.strftime("%d %b %H:%M", time.localtime(r.t)) if from_log else "live"
        self.bat_state.set(f"{r.state_name}  ·  {when}")
        try:
            cap = float(self.bat_cap.get())
        except ValueError:
            cap = None
        est = B.estimates(self.bat_log.rows, cap)
        fmt = {
            "mah_left": lambda v: f"≈ {v:.0f} mAh",
            "hours_left": lambda v: f"≈ {int(v)} h {int(v * 60 % 60)} min",
            "discharge_pct_per_h": lambda v: f"{v:.1f} % / hour",
            "avg_current_ma": lambda v: f"≈ {v:.1f} mA",
            "minutes_to_full": lambda v: f"≈ {v:.0f} min",
            "charge_current_ma": lambda v: f"≈ {v:.0f} mA",
            "cycles": lambda v: f"{v:.2f}",
            "health_pct": lambda v: f"≈ {v:.0f} %",
        }
        for k, var in self.bat_fields.items():
            v = est.get(k)
            if v is None:
                var.set("enter capacity" if k in ("mah_left", "avg_current_ma", "charge_current_ma") and not cap
                        else "not enough data")
            else:
                var.set(fmt[k](v))
        self._draw_battery_chart()

    def _draw_battery_chart(self):
        c = self.bat_chart
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50:
            return
        now = time.time()
        pts = [r for r in self.bat_log.rows if r.t > now - 86400]
        for p in (0, 50, 100):
            y = h - 10 - (h - 20) * p / 100
            c.create_line(30, y, w - 5, y, fill="#eee")
            c.create_text(15, y, text=str(p), fill="gray", font=("Segoe UI", 7))
        xy = [(30 + (w - 35) * (r.t - (now - 86400)) / 86400, h - 10 - (h - 20) * r.percent / 100) for r in pts]
        if len(xy) >= 2:
            c.create_line(*[v for p in xy for v in p], fill="#2b7de9", width=2)
        elif xy:
            x, y = xy[0]
            c.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#2b7de9", outline="")

    # ------------------------------------------------------------ health
    def _tab_health(self, f):
        self.bat_last_pct = None
        self.watchdog = Hl.Watchdog(reapply=self._reapply_saved, log_path=HEALTH_LOG,
                                    on_event=lambda e: self._post(self._health_event, e))
        top = ttk.LabelFrame(f, text="Last hour", padding=8)
        top.pack(fill="x")
        self.hl_vals = {}
        for i, (k, label) in enumerate((("hitches", "Stutters (hitches)"), ("freezes", "Freezes"),
                                        ("dropouts", "Wireless dropouts"), ("report_rate", "Report rate while moving"),
                                        ("fixes", "Automatic fixes"))):
            ttk.Label(top, text=label + ":").grid(row=i // 3, column=(i % 3) * 2, sticky="w", padx=(0, 4))
            v = tk.StringVar(value="0")
            ttk.Label(top, textvariable=v, width=12, font=("Segoe UI", 10, "bold")).grid(
                row=i // 3, column=(i % 3) * 2 + 1, sticky="w")
            self.hl_vals[k] = v
        self.hl_auto = tk.BooleanVar(value=True)
        self.hl_diag = tk.StringVar(value="Diagnosis appears once events are recorded.")
        ttk.Label(f, textvariable=self.hl_diag, wraplength=700, foreground="#a33").pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(f, text="Auto-fix: re-send mouse settings after the mouse wakes up / reconnects "
                                "(waits until the mouse is still)",
                        variable=self.hl_auto,
                        command=lambda: setattr(self.watchdog, "auto_fix", self.hl_auto.get())).pack(anchor="w", pady=6)

        chk = ttk.LabelFrame(f, text="Checks", padding=8)
        chk.pack(fill="x")
        self.hl_checks = ttk.Frame(chk)
        self.hl_checks.pack(fill="x")
        row = ttk.Frame(chk)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Run checks", command=self._run_checks).pack(side="left")
        ttk.Button(row, text="Fix USB power settings (admin)", command=self._fix_usb).pack(side="left", padx=6)

        ttk.Label(f, text="Events (also saved to profiles/health_log.csv)").pack(anchor="w", pady=(8, 0))
        self.hl_list = tk.Listbox(f, height=8, font=("Consolas", 9))
        self.hl_list.pack(fill="both", expand=True)
        self.after(1500, self._run_checks)

    def _reapply_saved(self):
        cfg = self._load_profile(PROFILE) or self.cfg
        from .device import X11
        with X11() as dev:
            return all(dev.send_all(P.build_all(cfg)))

    def _health_event(self, e):
        self.hl_list.insert(0, f"{time.strftime('%H:%M:%S', time.localtime(e.t))}  {e.kind:<9} {e.detail}")
        self.hl_list.delete(200, "end")
        self._health_summary()

    def _health_summary(self):
        s = self.watchdog.summary()
        self.hl_diag.set("\n".join(self.watchdog.diagnose()))
        for k, v in self.hl_vals.items():
            v.set("—" if s[k] is None else (f"{s[k]}/s" if k == "report_rate" else str(s[k])))

    def _health_tick(self):
        self.watchdog.tick()
        self._health_summary()
        self.after(1000, self._health_tick)

    def _run_checks(self):
        cfg = self._load_profile(PROFILE) or self.cfg
        s = self.watchdog.summary()

        def work():
            self._post(self._show_checks, Hl.run_checks(cfg, self.bat_last_pct, s["report_rate"], s["link_ok"]))
        threading.Thread(target=work, daemon=True).start()

    def _show_checks(self, checks):
        for w in self.hl_checks.winfo_children():
            w.destroy()
        for i, c in enumerate(checks):
            ttk.Label(self.hl_checks, text="✔" if c.ok else "✖",
                      foreground="green" if c.ok else "red").grid(row=i, column=0, padx=(0, 6))
            ttk.Label(self.hl_checks, text=c.title, width=24).grid(row=i, column=1, sticky="w")
            ttk.Label(self.hl_checks, text=c.detail).grid(row=i, column=2, sticky="w")
            if c.fix == "settings":
                ttk.Button(self.hl_checks, text="Fix", command=self._fix_polling).grid(row=i, column=3, padx=4)

    def _fix_polling(self):
        self.cfg.polling_hz = 1000
        self._refresh()
        self.apply()

    def _fix_usb(self):
        self.status.set("Waiting for admin approval…")

        def work():
            ok = Hl.fix_usb_power()
            self._post(self.status.set, "USB power settings fixed - mouse reconnected" if ok
                       else "USB fix cancelled or failed")
            self._post(self._run_checks)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ tabs
    def _tab_dpi(self, f):
        self.dpi_en = [tk.BooleanVar() for _ in range(P.STAGES)]
        self.dpi_val = [tk.IntVar() for _ in range(P.STAGES)]
        self.dpi_btn = []
        self.dpi_active = tk.IntVar()
        ttk.Label(f, text="Stage").grid(row=0, column=0)
        ttk.Label(f, text="On").grid(row=0, column=1)
        ttk.Label(f, text="DPI (50-22000, 0 = unused)").grid(row=0, column=2)
        ttk.Label(f, text="LED color").grid(row=0, column=3)
        ttk.Label(f, text="Active").grid(row=0, column=4)
        for i in range(P.STAGES):
            ttk.Label(f, text=str(i + 1)).grid(row=i + 1, column=0, pady=2)
            ttk.Checkbutton(f, variable=self.dpi_en[i]).grid(row=i + 1, column=1)
            sb = ttk.Spinbox(f, from_=0, to=22000, increment=50, width=8, textvariable=self.dpi_val[i])
            sb.grid(row=i + 1, column=2, padx=6)
            sb.bind("<FocusOut>", lambda e, i=i: self._snap(i))
            b = tk.Button(f, width=4, relief="groove", command=lambda i=i: self._pick_dpi_color(i))
            b.grid(row=i + 1, column=3)
            self.dpi_btn.append(b)
            ttk.Radiobutton(f, variable=self.dpi_active, value=i).grid(row=i + 1, column=4)
        self.angle = tk.BooleanVar()
        self.ripple = tk.BooleanVar()
        ttk.Checkbutton(f, text="Angle snapping", variable=self.angle).grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Checkbutton(f, text="Ripple control", variable=self.ripple).grid(row=11, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Above 10000 DPI the mouse stores 100-DPI steps, above 20000 200-DPI steps.",
                  foreground="gray").grid(row=12, column=0, columnspan=5, sticky="w", pady=(12, 0))

    def _snap(self, i):
        try:
            v = self.dpi_val[i].get()
        except tk.TclError:
            v = 0
        self.dpi_val[i].set(P.snap_dpi(v) if v > 0 else 0)

    def _pick_dpi_color(self, i):
        c = colorchooser.askcolor(_hex(self.cfg.dpi_colors[i]), parent=self)[0]
        if c:
            self.cfg.dpi_colors[i] = [int(x) for x in c]
            self.dpi_btn[i].configure(bg=_hex(self.cfg.dpi_colors[i]))

    def _tab_buttons(self, f):
        self.btn_rows = {}
        ttk.Label(f, text="Button").grid(row=0, column=0, sticky="w")
        ttk.Label(f, text="Function").grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="Shortcut (only for 'Keyboard shortcut…')").grid(row=0, column=2, columnspan=5, sticky="w")
        for r, (slot, label) in enumerate(P.X11_SLOTS.items(), start=1):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=3)
            fn = tk.StringVar()
            ttk.Combobox(f, textvariable=fn, values=FUNC_NAMES, state="readonly", width=20).grid(row=r, column=1, padx=6)
            mods = [tk.BooleanVar() for _ in range(4)]
            for k, m in enumerate(("Ctrl", "Shift", "Alt", "Win")):
                ttk.Checkbutton(f, text=m, variable=mods[k]).grid(row=r, column=2 + k)
            key = tk.StringVar()
            ttk.Combobox(f, textvariable=key, values=list(HID_KEYS), width=10).grid(row=r, column=6, padx=4)
            self.btn_rows[slot] = (fn, mods, key)
        ttk.Label(f, text="Tip: set a button to a shortcut like F13 and give a movement macro the same hotkey.",
                  foreground="gray").grid(row=20, column=0, columnspan=7, sticky="w", pady=(12, 0))

    def _tab_perf(self, f):
        self.polling = tk.StringVar()
        self.debounce = tk.IntVar()
        self.sleep = tk.IntVar()
        self.light_off = tk.IntVar()
        self.light_mode = tk.IntVar()
        self.brightness = tk.IntVar()
        self.breath = tk.IntVar()
        g = ttk.LabelFrame(f, text="Performance", padding=8)
        g.pack(fill="x")
        ttk.Label(g, text="Polling rate (Hz)").grid(row=0, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.polling, values=["125", "250", "500", "1000"],
                     state="readonly", width=8).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(g, text="Key debounce").grid(row=1, column=0, sticky="w")
        self.debounce_lbl = ttk.Label(g, width=8)
        ttk.Scale(g, from_=0, to=20, variable=self.debounce, length=220,
                  command=lambda v: self.debounce_lbl.configure(text=f"{round(float(v)) * 2} ms")).grid(row=1, column=1)
        self.debounce_lbl.grid(row=1, column=2)
        ttk.Label(g, text="Sleep timer (app units, default 10)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(g, from_=1, to=255, textvariable=self.sleep, width=6).grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(g, text="Light-off timer (app units, default 1)").grid(row=3, column=0, sticky="w")
        ttk.Spinbox(g, from_=0, to=255, textvariable=self.light_off, width=6).grid(row=3, column=1, sticky="w")

        l = ttk.LabelFrame(f, text="Lighting", padding=8)
        l.pack(fill="x", pady=10)
        ttk.Label(l, text="Effect (0 = default)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(l, from_=0, to=15, textvariable=self.light_mode, width=6).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(l, text="Color").grid(row=1, column=0, sticky="w")
        self.light_btn = tk.Button(l, width=4, relief="groove", command=self._pick_light)
        self.light_btn.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(l, text="Brightness (effects 1 and 5)").grid(row=2, column=0, sticky="w")
        ttk.Scale(l, from_=0, to=15, variable=self.brightness, length=220).grid(row=2, column=1)
        ttk.Label(l, text="Breathing speed").grid(row=3, column=0, sticky="w")
        ttk.Scale(l, from_=0, to=9, variable=self.breath, length=220).grid(row=3, column=1)

    def _pick_light(self):
        c = colorchooser.askcolor(_hex(self.cfg.light_color), parent=self)[0]
        if c:
            self.cfg.light_color = [int(x) for x in c]
            self.light_btn.configure(bg=_hex(self.cfg.light_color))

    def _tab_macros(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Button").pack(side="left")
        self.mac_slot = tk.StringVar(value=next(iter(SLOT_LABELS)))
        cb = ttk.Combobox(top, textvariable=self.mac_slot, values=list(SLOT_LABELS), state="readonly", width=32)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._show_macro())
        ttk.Label(top, text="Repeat").pack(side="left", padx=(12, 0))
        self.mac_repeat = tk.IntVar(value=1)
        ttk.Spinbox(top, from_=1, to=255, textvariable=self.mac_repeat, width=5).pack(side="left", padx=4)
        self.mac_use = tk.BooleanVar()
        ttk.Checkbutton(top, text="Button plays this macro", variable=self.mac_use,
                        command=self._use_macro).pack(side="left", padx=12)

        mid = ttk.Frame(f)
        mid.pack(fill="both", expand=True, pady=8)
        self.mac_list = tk.Listbox(mid, height=14, font=("Consolas", 10))
        self.mac_list.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(mid)
        side.pack(side="left", fill="y", padx=8)
        self.rec_btn = ttk.Button(side, text="● Record keys", command=self._toggle_key_record)
        self.rec_btn.pack(fill="x")
        ttk.Separator(side).pack(fill="x", pady=6)
        self.ev_key = tk.StringVar(value="A")
        self.ev_down = tk.StringVar(value="press")
        self.ev_delay = tk.IntVar(value=20)
        ttk.Combobox(side, textvariable=self.ev_key, values=list(HID_KEYS), width=10).pack(fill="x")
        ttk.Combobox(side, textvariable=self.ev_down, values=["press", "release"], state="readonly", width=10).pack(fill="x", pady=2)
        row = ttk.Frame(side)
        row.pack(fill="x")
        ttk.Label(row, text="delay ms").pack(side="left")
        ttk.Spinbox(row, from_=0, to=60000, increment=10, textvariable=self.ev_delay, width=7).pack(side="left")
        ttk.Button(side, text="Add step", command=self._add_event).pack(fill="x", pady=2)
        ttk.Button(side, text="Delete step", command=self._del_event).pack(fill="x")
        ttk.Button(side, text="Clear", command=self._clear_events).pack(fill="x", pady=2)
        ttk.Label(f, text="Firmware macros replay keyboard keys from the mouse itself (max 50 steps, 10 ms resolution).",
                  foreground="gray").pack(anchor="w")
        self._recording = None
        self.bind_all("<KeyPress>", self._rec_key, add="+")
        self.bind_all("<KeyRelease>", self._rec_key, add="+")

    def _cur_slot(self):
        return SLOT_LABELS[self.mac_slot.get()]

    def _cur_macro(self) -> P.Macro:
        return self.cfg.macros.setdefault(self._cur_slot(), P.Macro())

    def _show_macro(self):
        m = self.cfg.macros.get(self._cur_slot(), P.Macro())
        self.mac_repeat.set(m.repeat)
        self.mac_use.set(self.btn_rows[self._cur_slot()][0].get() == MACRO_LABEL)
        self.mac_list.delete(0, "end")
        for e in m.events:
            self.mac_list.insert("end", f"wait {e.delay_ms:>5} ms  {'press  ' if e.down else 'release'}  {key_name(e.key)}")

    def _use_macro(self):
        # Keep the Buttons tab as the single source of truth for what a button does.
        fn = self.btn_rows[self._cur_slot()][0]
        if self.mac_use.get():
            fn.set(MACRO_LABEL)
        elif fn.get() == MACRO_LABEL:
            fn.set("Disabled")

    def _add_event(self):
        k = HID_KEYS.get(self.ev_key.get())
        if k is None:
            return messagebox.showerror("Key", "Pick a key from the list", parent=self)
        self._cur_macro().events.append(P.MacroEvent(k, self.ev_down.get() == "press", self.ev_delay.get()))
        self._show_macro()

    def _del_event(self):
        sel = self.mac_list.curselection()
        if sel:
            del self._cur_macro().events[sel[0]]
            self._show_macro()

    def _clear_events(self):
        self._cur_macro().events.clear()
        self._show_macro()

    def _toggle_key_record(self):
        if self._recording is None:
            self._cur_macro().events.clear()
            self._recording = time.perf_counter()
            self.rec_btn.configure(text="■ Stop recording")
            self.mac_list.focus_set()
            self.status.set("Recording keys… press keys now, then Stop")
        else:
            self._recording = None
            self.rec_btn.configure(text="● Record keys")
            self.status.set(f"Recorded {len(self._cur_macro().events)} steps")
        self._show_macro()

    def _rec_key(self, e):
        if self._recording is None:
            return
        usage = vk_to_hid(e.keycode)
        if usage is None:
            return
        down = e.type == tk.EventType.KeyPress
        events = self._cur_macro().events
        if down and events and events[-1].key == usage and events[-1].down:
            return  # auto-repeat
        now = time.perf_counter()
        events.append(P.MacroEvent(usage, down, round((now - self._recording) * 1000) if events else 10))
        self._recording = now
        self._show_macro()
        return "break"

    def _tab_moves(self, f):
        left = ttk.Frame(f)
        left.pack(side="left", fill="y")
        self.mv_list = tk.Listbox(left, width=22, exportselection=False)
        self.mv_list.pack(fill="y", expand=True)
        self.mv_list.bind("<<ListboxSelect>>", lambda e: self._show_move())
        ttk.Button(left, text="New", command=self._new_move).pack(fill="x", pady=(6, 0))
        ttk.Button(left, text="Delete", command=self._del_move).pack(fill="x")

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        row = ttk.Frame(right)
        row.pack(fill="x")
        self.mv_name = tk.StringVar()
        self.mv_hotkey = tk.StringVar()
        self.mv_repeat = tk.IntVar(value=1)
        ttk.Label(row, text="Name").pack(side="left")
        ttk.Entry(row, textvariable=self.mv_name, width=16).pack(side="left", padx=4)
        self.mv_trigger = tk.StringVar(value=next(iter(TRIGGERS)))
        ttk.Label(row, text="Trigger").pack(side="left")
        ttk.Combobox(row, textvariable=self.mv_trigger, values=list(TRIGGERS), width=16, state="readonly").pack(side="left", padx=4)
        ttk.Label(row, text="Hotkey").pack(side="left")
        ttk.Combobox(row, textvariable=self.mv_hotkey, values=[""] + HOTKEYS, width=5, state="readonly").pack(side="left", padx=4)
        ttk.Label(row, text="Repeat").pack(side="left")
        ttk.Spinbox(row, from_=1, to=9999, textvariable=self.mv_repeat, width=5).pack(side="left", padx=4)
        ttk.Label(right, text='Steps, one JSON object per line: {"move":[dx,dy],"ms":200}  {"click":"left"}  '
                              '{"move_to":[x,y]}  {"scroll":-3}  {"key":"F5"}  {"wait":50}',
                  foreground="gray", wraplength=520).pack(anchor="w", pady=4)
        self.mv_text = tk.Text(right, height=14, font=("Consolas", 10))
        self.mv_text.pack(fill="both", expand=True)
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Save", command=self._save_move).pack(side="left")
        ttk.Button(btns, text="● Record mouse (Esc stops)", command=self._record_move).pack(side="left", padx=4)
        ttk.Button(btns, text="● Record while holding", command=self._record_hold).pack(side="left")
        ttk.Button(btns, text="▶ Test in 2 s", command=self._test_move).pack(side="left", padx=4)
        bind = ttk.Frame(right)
        bind.pack(fill="x")
        ttk.Label(bind, text="Send this hotkey from").pack(side="left")
        self.mv_slot = tk.StringVar(value=list(SLOT_LABELS)[4])
        ttk.Combobox(bind, textvariable=self.mv_slot, values=list(SLOT_LABELS), state="readonly", width=30).pack(side="left", padx=4)
        ttk.Button(bind, text="Bind", command=self._bind_hotkey).pack(side="left")
        self.listen_var = tk.BooleanVar()
        ttk.Checkbutton(right, text="Run movement macros", variable=self.listen_var,
                        command=self._toggle_listener).pack(anchor="w", pady=(8, 0))
        ttk.Label(right, text="Hold macros play while the button is held and stop on release. They only fire "
                              "while ARMED: press Scroll Lock to arm/disarm, so normal clicks stay normal.",
                  foreground="gray", wraplength=520).pack(anchor="w")
        self._fill_moves()

    def _fill_moves(self, select=None):
        self.mv_list.delete(0, "end")
        for n in self.moves:
            self.mv_list.insert("end", n)
        if select in self.moves:
            i = list(self.moves).index(select)
            self.mv_list.selection_set(i)
            self._show_move()

    def _sel_move(self):
        s = self.mv_list.curselection()
        return list(self.moves)[s[0]] if s else None

    def _show_move(self):
        n = self._sel_move()
        if not n:
            return
        m = self.moves[n]
        self.mv_name.set(n)
        self.mv_hotkey.set(m.get("hotkey", ""))
        self.mv_trigger.set(TRIGGER_NAMES.get(m.get("trigger", "hotkey"), next(iter(TRIGGERS))))
        self.mv_repeat.set(m.get("repeat", 1))
        self.mv_text.delete("1.0", "end")
        self.mv_text.insert("1.0", "\n".join(json.dumps(s) for s in m.get("steps", [])))

    def _new_move(self):
        n = f"Macro {len(self.moves) + 1}"
        self.moves[n] = {"hotkey": "", "repeat": 1, "steps": [{"move": [200, 0], "ms": 300}, {"click": "left"}]}
        self._fill_moves(n)

    def _del_move(self):
        n = self._sel_move()
        if n:
            del self.moves[n]
            self._write_moves()
            self._fill_moves()

    def _parse_steps(self):
        steps = []
        for ln, line in enumerate(self.mv_text.get("1.0", "end").splitlines(), start=1):
            if line.strip():
                try:
                    steps.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ValueError(f"line {ln}: {e.msg}") from None
        return steps

    def _save_move(self):
        old = self._sel_move()
        try:
            steps = self._parse_steps()
        except ValueError as e:
            return messagebox.showerror("Steps", str(e), parent=self)
        n = self.mv_name.get().strip() or "Macro"
        if old and old != n:
            del self.moves[old]
        trig = TRIGGERS[self.mv_trigger.get()]
        if trig == "hotkey" and not self.mv_hotkey.get():
            return messagebox.showerror("Trigger", "Pick a hotkey (F13-F24) or a hold trigger", parent=self)
        taken = [k for k, m in self.moves.items() if k != old and trig != "hotkey" and m.get("trigger") == trig]
        if taken:
            return messagebox.showerror("Trigger", f"'{taken[0]}' already uses this trigger", parent=self)
        self.moves[n] = {"trigger": trig, "hotkey": self.mv_hotkey.get() if trig == "hotkey" else "",
                         "repeat": self.mv_repeat.get(), "steps": steps}
        self._write_moves()
        self._fill_moves(n)
        self.status.set(f"Saved movement macro '{n}'")
        if self.listener:
            self._toggle_listener(restart=True)

    def _record_move(self):
        self.status.set("Recording starts in 2 s… move the mouse, press Esc to stop")
        self.update()

        def go():
            time.sleep(2)
            steps = softmacro.record(seconds=60)
            self._post(lambda: (self.mv_text.delete("1.0", "end"),
                                   self.mv_text.insert("1.0", "\n".join(json.dumps(s) for s in steps)),
                                   self.status.set(f"Recorded {len(steps)} steps - press Save to keep")))
        threading.Thread(target=go, daemon=True).start()

    def _record_hold(self):
        btn = softmacro.HOLD_TRIGGERS.get(TRIGGERS[self.mv_trigger.get()], "left")
        self.status.set(f"Hold the {btn} button and draw the pattern (15 s to start)…")

        def go():
            steps = softmacro.record_hold(btn)
            self._post(lambda: (self.mv_text.delete("1.0", "end"),
                                self.mv_text.insert("1.0", "\n".join(json.dumps(s) for s in steps)),
                                self.status.set(f"Recorded {len(steps)} steps - press Save to keep"
                                                if steps else "Nothing recorded")))
        threading.Thread(target=go, daemon=True).start()

    def _test_move(self):
        try:
            steps = self._parse_steps()
        except ValueError as e:
            return messagebox.showerror("Steps", str(e), parent=self)
        self.status.set("Playing in 2 s…")
        threading.Thread(target=lambda: (time.sleep(2), softmacro.play(steps, self.mv_repeat.get()),
                                         self._post(lambda: self.status.set("Played"))), daemon=True).start()

    def _bind_hotkey(self):
        hk = self.mv_hotkey.get()
        if not hk:
            return messagebox.showinfo("Hotkey", "Pick a hotkey (F13-F24) for this macro first", parent=self)
        slot = SLOT_LABELS[self.mv_slot.get()]
        self.cfg.buttons[slot] = P.Button(P.SHORTCUT, 0, HID_KEYS[hk])
        self._refresh()
        self.status.set(f"{P.X11_SLOTS[slot]} now sends {hk} - press Apply to mouse")

    def _toggle_listener(self, restart=False):
        if self.listener:
            self.listener.stop()
            self.listener = None
        if self.listen_var.get() or restart:
            macros = list(self.moves.values())
            self.listener = softmacro.Listener(macros, on_event=lambda s: self._post(self.status.set, s))
            self.listener.start()
            names = [m["hotkey"] for m in macros if m.get("trigger", "hotkey") == "hotkey" and m.get("hotkey")]
            names += [TRIGGER_NAMES[m["trigger"]] for m in macros if m.get("trigger") in softmacro.HOLD_TRIGGERS]
            self.status.set("Listening: " + (", ".join(names) or "nothing")
                            + ("  (Scroll Lock arms hold macros)" if self.listener.holds else ""))

    # ------------------------------------------------------------ data
    def _refresh(self):
        c = self.cfg
        for i in range(P.STAGES):
            self.dpi_en[i].set(c.dpi_enabled[i])
            self.dpi_val[i].set(c.dpi[i])
            self.dpi_btn[i].configure(bg=_hex(c.dpi_colors[i]))
        self.dpi_active.set(c.dpi_stage)
        self.angle.set(c.angle_snap)
        self.ripple.set(c.ripple_control)
        for slot, (fn, mods, key) in self.btn_rows.items():
            b = c.buttons[slot]
            fn.set(SHORTCUT_LABEL if b.code == P.SHORTCUT else MACRO_LABEL if b.code == P.MACRO
                   else CODE_TO_NAME.get(b.code, "Disabled"))
            for k, bit in enumerate((P.MOD_CTRL, P.MOD_SHIFT, P.MOD_ALT, P.MOD_WIN)):
                mods[k].set(bool(b.modifiers & bit))
            key.set(key_name(b.key) if b.code == P.SHORTCUT else "")
        self.polling.set(str(c.polling_hz))
        self.debounce.set(c.debounce)
        self.debounce_lbl.configure(text=f"{c.debounce * 2} ms")
        self.sleep.set(c.sleep_timer)
        self.light_off.set(c.light_off_timer)
        self.light_mode.set(c.light_mode)
        self.brightness.set(c.brightness)
        self.breath.set(c.breath_speed)
        self.light_btn.configure(bg=_hex(c.light_color))
        self._show_macro()

    def _collect(self):
        c = self.cfg
        for i in range(P.STAGES):
            self._snap(i)
        c.dpi = [self.dpi_val[i].get() for i in range(P.STAGES)]
        c.dpi_enabled = [self.dpi_en[i].get() for i in range(P.STAGES)]
        c.dpi_stage = self.dpi_active.get()
        for i in range(P.STAGES):
            if c.dpi_enabled[i] and not c.dpi[i]:
                raise ValueError(f"DPI stage {i + 1} is on but has no DPI value")
        if not c.dpi_enabled[c.dpi_stage]:
            raise ValueError("The active DPI stage must be switched on")
        c.angle_snap, c.ripple_control = self.angle.get(), self.ripple.get()
        for slot, (fn, mods, key) in self.btn_rows.items():
            name = fn.get()
            if name == SHORTCUT_LABEL:
                if key.get() not in HID_KEYS:
                    raise ValueError(f"{P.X11_SLOTS[slot]}: pick a key for the shortcut")
                bits = sum(bit for k, bit in enumerate((P.MOD_CTRL, P.MOD_SHIFT, P.MOD_ALT, P.MOD_WIN)) if mods[k].get())
                c.buttons[slot] = P.Button(P.SHORTCUT, bits, HID_KEYS[key.get()])
            elif name == MACRO_LABEL:
                c.buttons[slot] = P.Button(P.MACRO)
            else:
                c.buttons[slot] = P.Button(P.FUNCTIONS[name])
        slot = self._cur_slot()
        if slot in c.macros:
            c.macros[slot].repeat = self.mac_repeat.get()
        for s, b in enumerate(c.buttons):
            if b.code == P.MACRO and not c.macros.get(s, P.Macro()).events:
                raise ValueError(f"{P.X11_SLOTS.get(s, s)} is set to a firmware macro with no steps")
        c.polling_hz = int(self.polling.get())
        c.debounce = round(self.debounce.get())
        c.sleep_timer, c.light_off_timer = self.sleep.get(), self.light_off.get()
        c.light_mode = self.light_mode.get()
        c.brightness, c.breath_speed = round(self.brightness.get()), round(self.breath.get())
        self._refresh()

    def _load_profile(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                return P.Config.from_json(fh.read())
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _save_profile(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.cfg.to_json())

    def _load_moves(self):
        try:
            with open(MOVES, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"Flick right": {"hotkey": "", "repeat": 1,
                                    "steps": [{"move": [300, 0], "ms": 150}, {"click": "left"}]}}

    def _write_moves(self):
        with open(MOVES, "w", encoding="utf-8") as fh:
            json.dump(self.moves, fh, indent=2)

    def open_profile(self):
        p = filedialog.askopenfilename(parent=self, filetypes=[("Profile", "*.json")], initialdir=os.path.dirname(PROFILE))
        if p:
            cfg = self._load_profile(p)
            if not cfg:
                return messagebox.showerror("Open", "Not a valid profile", parent=self)
            self.cfg = cfg
            self._refresh()

    def save_as(self):
        try:
            self._collect()
        except ValueError as e:
            return messagebox.showerror("Settings", str(e), parent=self)
        p = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialdir=os.path.dirname(PROFILE))
        if p:
            self._save_profile(p)

    # ------------------------------------------------------------ device
    def _check_device(self):
        from .device import find
        d = find()
        self.status.set(f"Mouse connected ({d['mode']})" if d else "Mouse not found - plug in the dongle")

    def apply(self):
        try:
            self._collect()
            reports = P.build_all(self.cfg)
        except ValueError as e:
            return messagebox.showerror("Settings", str(e), parent=self)
        self._save_profile(PROFILE)
        self._run_send(reports, "Applied")

    def factory_reset(self):
        if not messagebox.askyesno("Factory reset", "Reset the mouse to factory settings?", parent=self):
            return
        self.cfg = P.Config()
        self._refresh()
        self._save_profile(PROFILE)
        self._run_send(P.build_all(self.cfg, reset=True), "Factory reset done")

    def _run_send(self, reports, done):
        from .device import DeviceNotFound, X11

        def work():
            try:
                with X11() as dev:
                    res = dev.send_all(reports, lambda i, n, r, ok: self.after(
                        0, self.status.set, f"Sending {i}/{n}…"))
                msg = done if all(res) else f"{res.count(False)} of {len(res)} reports failed"
            except (DeviceNotFound, OSError) as e:
                msg = f"Error: {e}"
            self._post(self.status.set, msg)

        self.status.set("Sending…")
        threading.Thread(target=work, daemon=True).start()


def main():
    App().mainloop()
    return 0
