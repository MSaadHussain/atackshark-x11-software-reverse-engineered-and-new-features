"""Log every HID packet the official Attack Shark app exchanges with the mouse.

Usage (from an *Administrator* terminal, the app requires elevation):
        python tools/sniff.py [logfile]
Starts AttackShark.exe under Frida (read-only hooks, nothing is changed) and
appends one JSON line per packet to the log (default: captures/sniff.jsonl).
"""
import json
import os
import sys
import threading
import time

import frida

APP = r"C:\Program Files (x86)\Attack Shark Software\AttackShark.exe"

JS = r"""
const handles = {};   // handle -> device path (only our mouse)
const IOCTL = {0xB0191: 'set_feature', 0xB0192: 'get_feature',
               0xB01A2: 'get_input', 0xB0195: 'set_output'};

function hex(p, n) {
  if (p.isNull() || n <= 0) return '';
  return Array.from(new Uint8Array(p.readByteArray(n)))
      .map(b => ('0' + b.toString(16)).slice(-2)).join('');
}

function hookCreate(name, wide) {
  const f = Process.getModuleByName('kernelbase.dll').findExportByName(name);
  Interceptor.attach(f, {
    onEnter(a) { this.path = wide ? a[0].readUtf16String() : a[0].readAnsiString(); },
    onLeave(r) {
      if (this.path && /vid_1d57/i.test(this.path) && r.toInt32() !== -1) {
        handles[r.toString()] = this.path;
        send({ev: 'open', h: r.toString(), path: this.path});
      }
    }
  });
}
hookCreate('CreateFileA', false);
hookCreate('CreateFileW', true);

Interceptor.attach(Process.getModuleByName('kernelbase.dll').findExportByName('DeviceIoControl'), {
  onEnter(a) {
    this.h = a[0].toString(); this.code = a[1].toUInt32();
    if (!(this.h in handles) || !(this.code in IOCTL)) { this.skip = true; return; }
    this.inb = a[2]; this.inl = a[3].toUInt32(); this.outb = a[4]; this.outl = a[5].toUInt32();
    if (this.code === 0xB0191 || this.code === 0xB0195)
      send({ev: IOCTL[this.code], h: this.h, data: hex(this.inb, this.inl)});
  },
  onLeave(r) {
    if (this.skip) return;
    if (this.code === 0xB0192 || this.code === 0xB01A2)
      send({ev: IOCTL[this.code], h: this.h, ok: r.toInt32(), data: hex(this.outb, this.outl)});
  }
});

for (const [name, dir] of [['WriteFile', 'write'], ['ReadFile', 'read']]) {
  Interceptor.attach(Process.getModuleByName('kernelbase.dll').findExportByName(name), {
    onEnter(a) {
      this.h = a[0].toString(); this.buf = a[1]; this.len = a[2].toUInt32();
      if (!(this.h in handles)) { this.skip = true; return; }
      if (dir === 'write') send({ev: 'write', h: this.h, data: hex(this.buf, this.len)});
    },
    onLeave(r) {
      if (!this.skip && dir === 'read' && r.toInt32())
        send({ev: 'read', h: this.h, data: hex(this.buf, this.len)});
    }
  });
}
send({ev: 'ready'});
"""


def main():
    log = sys.argv[1] if len(sys.argv) > 1 else os.path.join("captures", "sniff.jsonl")
    os.makedirs(os.path.dirname(log) or ".", exist_ok=True)
    out = open(log, "a", encoding="utf-8")

    def on_message(msg, _data):
        if msg["type"] != "send":
            print("!", msg)
            return
        p = msg["payload"]
        p["t"] = round(time.time(), 3)
        out.write(json.dumps(p) + "\n")
        out.flush()
        if p["ev"] != "read":  # reads are noisy (polling); keep them in the file only
            print(p["ev"], p.get("data", p.get("path", "")))

    pid = frida.spawn([APP], cwd=os.path.dirname(APP))
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    frida.resume(pid)
    print(f"Sniffing AttackShark.exe (pid {pid}) -> {log}.")
    print("Before each change in the app, type a label here (e.g. 'dpi 800->1600') and press Enter.")
    print("Close the app or press Ctrl+C to stop.")

    def read_marks():
        # A label line before each action makes the packets easy to tell apart later.
        while True:
            try:
                label = input()
            except EOFError:
                return
            on_message({"type": "send", "payload": {"ev": "mark", "label": label}}, None)

    threading.Thread(target=read_marks, daemon=True).start()
    try:
        while not session.is_detached:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    out.close()


if __name__ == "__main__":
    main()
