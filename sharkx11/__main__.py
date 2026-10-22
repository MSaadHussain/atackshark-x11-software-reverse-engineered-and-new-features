"""Command line entry point.

    python -m sharkx11                      open the GUI
    python -m sharkx11 info                 is the mouse connected?
    python -m sharkx11 apply FILE [--dry-run]   send a saved profile to the mouse
    python -m sharkx11 reset [--dry-run]    factory reset + factory settings
    python -m sharkx11 listen FILE          run movement macros (hotkeys) without the GUI
    python -m sharkx11 battery [--watch]    battery % and charging state (--watch logs it)
"""
import argparse
import json
import sys
import time

from . import protocol as P


def _send(reports, dry_run):
    if dry_run:
        for r in reports:
            print(r.hex(" "))
        return 0
    from .device import X11
    with X11() as dev:
        print(f"Mouse: {dev.info['mode']} (PID {dev.info['pid']:04X})")
        ok = dev.send_all(reports, lambda i, n, r, good: print(
            f"  [{i}/{n}] report {r[0]:#04x} {'ok' if good else 'FAILED'}"))
    return 0 if all(ok) else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sharkx11")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("info")
    a = sub.add_parser("apply")
    a.add_argument("file")
    a.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("reset")
    r.add_argument("--dry-run", action="store_true")
    li = sub.add_parser("listen")
    li.add_argument("file")
    b = sub.add_parser("battery")
    b.add_argument("--watch", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd is None:
        from .gui import main as gui
        return gui()
    if args.cmd == "info":
        from .device import find
        d = find()
        print(d if d else "Attack Shark X11 not found")
        return 0 if d else 1
    if args.cmd == "apply":
        cfg = P.Config.from_json(open(args.file, encoding="utf-8").read())
        return _send(P.build_all(cfg), args.dry_run)
    if args.cmd == "reset":
        return _send(P.build_all(P.Config(), reset=True), args.dry_run)
    if args.cmd == "battery":
        from . import battery as B
        if not args.watch:
            r = B.read_once()
            print(f"Battery {r.percent}% - {r.state_name}" if r else "No battery report (is the mouse on?)")
            return 0 if r else 1
        import os
        log = B.Log(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "profiles", "battery_log.csv"))
        B.Monitor(log, on_reading=lambda r: print(time.strftime("%H:%M:%S"), f"{r.percent}%", r.state_name),
                  on_status=print).start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
    if args.cmd == "listen":
        from .softmacro import Listener
        data = json.load(open(args.file, encoding="utf-8"))
        listener = Listener(list(data.values()), on_event=print)
        listener.start()
        print("Listening for", ", ".join(listener.hotkeys) or "no hotkeys",
              "| hold macros:", ", ".join(listener.holds) or "none", "(Scroll Lock arms) - Ctrl+C to quit")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
