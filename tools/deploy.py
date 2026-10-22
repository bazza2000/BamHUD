"""Copy code to the CYD display, over Wi-Fi (default) or USB serial.

    python tools/deploy.py app.py web.py               # Wi-Fi, then reboot
    python tools/deploy.py --usb app.py                # USB serial (COM3), then soft reboot
    python tools/deploy.py --host 192.168.1.60 app.py
    python tools/deploy.py --no-reboot f_med.bfn
    python tools/deploy.py --source app.py             # send the .py instead of compiling

Modules in COMPILE are compiled to .mpy with mpy-cross first (build/ folder) and the board
deletes the matching .py, because MicroPython imports X.py in preference to X.mpy.
Compiling on the PC matters: compiling a big .py on the board spikes RAM, MicroPython's heap
grows into the Wi-Fi driver's memory and never gives it back, and networking dies (CLAUDE.md).

Run with Thonny's Python (has pyserial + mpy-cross 1.29):
    & "$env:LOCALAPPDATA\\Programs\\Thonny\\python.exe" tools\\deploy.py app.py
The Wi-Fi upload password is read from WEB_PASSWORD in config.py and never printed.
"""
import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")
DEFAULT_HOST = "bamhud.local"
# Big modules are precompiled. main.py, config.py, touch_cal.py stay as editable source.
COMPILE = {"app.py", "web.py", "font.py", "ili9341.py", "touch.py", "calibrate.py"}


def web_password():
    src = open(os.path.join(HERE, "config.py"), encoding="utf8").read()
    m = re.search(r'^WEB_PASSWORD\s*=\s*["\'](.*?)["\']', src, re.M)
    if not m or not m.group(1):
        sys.exit("WEB_PASSWORD is not set in config.py")
    return m.group(1)


def prepare(files, source):
    """[(local path, name on the board)] with COMPILE modules turned into .mpy."""
    out = []
    os.makedirs(BUILD, exist_ok=True)
    for name in files:
        base = os.path.basename(name)
        src = os.path.join(HERE, name)
        if base in COMPILE and not source:
            dst = os.path.join(BUILD, base[:-3] + ".mpy")
            r = subprocess.run([sys.executable, "-m", "mpy_cross", "-o", dst, src],
                               capture_output=True, text=True)
            if r.returncode:
                sys.exit("mpy-cross failed for %s:\n%s%s" % (base, r.stdout, r.stderr))
            out.append((dst, base[:-3] + ".mpy"))
        else:
            out.append((src, base))
    return out


def preflight(host, force):
    """Don't upload into a board that is still booting or short of Wi-Fi memory: an upload landing
    on top of the first draw/fetch after a reboot once exhausted the IDF heap (CLAUDE.md 5.5)."""
    import json
    for _ in range(30):
        dev, err = None, None
        for _attempt in range(3):          # the display answers between printer fetches; be patient
            try:
                with urllib.request.urlopen("http://%s/api/status" % host, timeout=20) as r:
                    dev = json.loads(r.read())["device"]
                break
            except Exception as e:
                err = e
                time.sleep(2)
        if dev is None:
            sys.exit("display not answering at %s (%s) - try --usb" % (host, err))
        up, idf = dev.get("uptime_s", 999), dev.get("idf_free", -1)
        if up < 45:
            print("display booted %ds ago, waiting for it to settle..." % up)
            time.sleep(45 - up)
            continue
        if 0 <= idf < 12000 and not force:
            sys.exit("display has only %d bytes of Wi-Fi memory free; reboot it first "
                     "(web page) or use --usb, or pass --force" % idf)
        return
    sys.exit("display kept rebooting - use --usb")


def via_wifi(host, pairs, reboot, force=False):
    preflight(host, force)
    pw = web_password()
    for path, remote in pairs:
        data = open(path, "rb").read()
        req = urllib.request.Request("http://%s/upload/%s" % (host, remote),
                                     data=data, method="PUT", headers={"X-Password": pw})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print(remote, "->", r.read().decode())
        except urllib.error.HTTPError as e:
            sys.exit("%s -> HTTP %d: %s" % (remote, e.code, e.read().decode(errors="replace")))
    if reboot:
        req = urllib.request.Request("http://%s/api/reboot" % host,
                                     data=("pw=" + pw).encode(), method="POST")
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass                     # the board resets mid-reply; that's expected
        print("rebooting")


def via_usb(port, pairs, reboot):
    import serial
    s = serial.Serial(port, 115200, timeout=1)   # NB: opening the port resets the board

    def rd(t):
        time.sleep(t)
        return s.read(10000)

    r = b""
    for _ in range(8):               # main.py may be mid-HTTP-request; keep interrupting
        s.write(b"\x03\x03")
        rd(0.7)
        s.write(b"\x01")             # raw REPL
        r = rd(0.7)
        if b"raw REPL" in r:
            break
    if b"raw REPL" not in r:
        sys.exit("could not get a raw REPL on %s: %r" % (port, r[-200:]))

    def run(code):
        s.write(code.encode() + b"\x04")
        out, t = b"", time.time()
        while time.time() - t < 20:
            out += s.read(1000)
            if out.count(b"\x04") >= 2:
                break
        if not (out.startswith(b"OK") and out.count(b"\x04") >= 2 and out.split(b"\x04")[1] == b""):
            sys.exit("board error: %r" % out)

    for path, remote in pairs:
        data = open(path, "rb").read()
        run("f=open(%r,'wb')" % remote)
        for i in range(0, len(data), 512):
            run("f.write(%r)" % data[i:i + 512])
        run("f.close()")
        if remote.endswith(".mpy"):
            run("import os\ntry:\n os.remove(%r)\nexcept OSError:\n pass" % (remote[:-4] + ".py"))
        print("uploaded", remote, len(data))
    s.write(b"\x02")                 # back to the normal REPL
    rd(0.3)
    if reboot:
        s.write(b"\x04")             # soft reboot -> main.py runs
        print("rebooting")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--usb", action="store_true", help="use USB serial instead of Wi-Fi")
    ap.add_argument("--port", default="COM3")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--source", action="store_true", help="upload .py source, don't compile")
    ap.add_argument("--no-reboot", action="store_true")
    ap.add_argument("--force", action="store_true", help="upload even if the display reports low Wi-Fi memory")
    a = ap.parse_args()
    pairs = prepare(a.files, a.source)
    if a.usb:
        via_usb(a.port, pairs, not a.no_reboot)
    else:
        via_wifi(a.host, pairs, not a.no_reboot, a.force)
