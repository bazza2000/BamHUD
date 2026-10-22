"""Render the real dashboard code to PNGs on a PC, using live Bambuddy data.

    python tools/simulate.py            # live data from your Bambuddy -> sim/*.png (2x scale, git-ignored)
    python tools/simulate.py --demo     # invented data, no network/config needed -> docs/screenshot_*.png

It imports the real app.py / ili9341.py / font.py with stub hardware modules:
the ILI9341 command stream (CASET/RASET/RAMWR) is emulated into an image, so what
you see is what the driver would send to the panel (orientation/inversion aside).
Bambuddy writes (POST) are refused, so CLEAR BED can never fire from here.

Scenarios: live main view, live detail view per printer, and a "demo" main view with
one printer printing and the other waiting for its plate to be cleared.
"""
import json
import os
import sys
import time
import types
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PIL import Image  # noqa: E402

W, H = 320, 240
fb = bytearray(W * H * 3)


# ---------- MicroPython time shims ----------
time.ticks_ms = lambda: int(time.monotonic() * 1000)
time.ticks_diff = lambda a, b: a - b
time.ticks_add = lambda a, b: a + b
time.sleep_ms = lambda ms: None


# ---------- ILI9341 emulator behind a fake machine module ----------
class Panel:
    dc = None
    cmd = None
    params = bytearray()
    win = [0, W - 1, 0, H - 1]
    cur = 0

    @classmethod
    def write(cls, buf):
        if cls.dc is None or not cls.dc.v:
            cls.cmd = buf[0]
            cls.params = bytearray()
            if cls.cmd == 0x2C:
                cls.cur = 0
            return
        if cls.cmd in (0x2A, 0x2B):
            cls.params += buf
            if len(cls.params) >= 4:
                a = (cls.params[0] << 8) | cls.params[1]
                b = (cls.params[2] << 8) | cls.params[3]
                if cls.cmd == 0x2A:
                    cls.win[0:2] = [a, b]
                else:
                    cls.win[2:4] = [a, b]
        elif cls.cmd == 0x2C:
            x0, x1, y0, y1 = cls.win
            ww = x1 - x0 + 1
            for i in range(0, len(buf) - 1, 2):
                c = (buf[i] << 8) | buf[i + 1]
                x = x0 + cls.cur % ww
                y = y0 + cls.cur // ww
                cls.cur += 1
                if 0 <= x < W and 0 <= y < H:
                    o = (y * W + x) * 3
                    fb[o] = ((c >> 11) & 31) * 255 // 31
                    fb[o + 1] = ((c >> 5) & 63) * 255 // 63
                    fb[o + 2] = (c & 31) * 255 // 31


class Pin:
    IN, OUT = 0, 1

    def __init__(self, n, mode=None, value=0):
        self.n, self.v = n, value
        if n == 2:
            Panel.dc = self

    def __call__(self, v=None):
        if v is None:
            return self.v
        self.v = v

    def value(self, v=None):
        return self(v)


class SPI:
    def __init__(self, *a, **k):
        pass

    def write(self, buf):
        Panel.write(bytes(buf))


class SoftSPI(SPI):
    def write_readinto(self, out, into):
        for i in range(len(into)):
            into[i] = 0


framebuf = types.ModuleType("framebuf")   # only the legacy 8x8 text() uses it
framebuf.MONO_HLSB = 0
framebuf.FrameBuffer = lambda *a: types.SimpleNamespace(fill=lambda c: None, text=lambda *a: None)
sys.modules["framebuf"] = framebuf

machine = types.ModuleType("machine")
machine.Pin, machine.SPI, machine.SoftSPI = Pin, SPI, SoftSPI
machine.PWM = lambda *a, **k: None
sys.modules["machine"] = machine


# ---------- network + HTTP stubs ----------
network = types.ModuleType("network")
network.STA_IF = 0
network.hostname = lambda *a: None


class WLAN:
    PM_NONE = 0

    def __init__(self, *a): pass
    def active(self, *a): return True
    def config(self, **k): pass
    def isconnected(self): return True
    def ifconfig(self): return ("simulator", "", "", "")
    def status(self, *a): return -50


network.WLAN = WLAN
sys.modules["network"] = network


class Resp:
    def __init__(self, url):
        self.r = urllib.request.urlopen(url, timeout=15)
        self.status_code = self.r.status
        self.raw = self.r

    def json(self):
        return json.loads(self.r.read())

    def close(self):
        self.r.close()


urequests = types.ModuleType("urequests")
urequests.get = lambda url, **k: Resp(url)


def _refuse(url, *a, **k):
    raise RuntimeError("simulator refuses writes: POST " + url)


urequests.post = _refuse
sys.modules["urequests"] = urequests


def save(name):
    img = Image.frombytes("RGB", (W, H), bytes(fb)).resize((W * 2, H * 2), Image.NEAREST)
    os.makedirs("sim", exist_ok=True)
    path = os.path.join("sim", name)
    img.save(path)
    print("wrote", path)


def demo_status(name, **kw):
    """A made-up printer status in the same (slim) shape app.fetch() returns."""
    tray = lambda i, t, sub, col, rem: {"id": i, "tray_type": t, "tray_sub_brands": sub,
                                        "tray_color": col, "remain": rem, "exists": True}
    st = {"name": name, "state": "IDLE", "connected": True, "progress": 0, "remaining_time": 0,
          "layer_num": 0, "total_layers": 0, "subtask_name": "", "wifi_signal": -48,
          "speed_level": 2, "cooling_fan_speed": 0, "awaiting_plate_clear": False, "tray_now": 255,
          "model": "A1", "hms_errors": [], "vt_tray": [],
          "temperatures": {"nozzle": 24, "nozzle_target": 0, "bed": 23, "bed_target": 0},
          "ams": [{"id": 0, "tray": [tray(0, "PLA", "PLA Matte", "AE835BFF", 80),
                                     tray(1, "PLA", "PLA Basic", "000000FF", 35),
                                     tray(2, "PETG", "PETG HF", "1F6FD1FF", 100),
                                     tray(3, "PLA", "PLA Basic", "FF9016FF", 60)]}]}
    st.update(kw)
    return st


def demo(out_dir):
    """Screenshots from invented data only: no network, no config.py needed (used for the README)."""
    import app
    global save
    _save = save

    def save(name):                      # noqa: F811 - write into out_dir instead of sim/
        _save(name)
        os.replace(os.path.join("sim", name), os.path.join(out_dir, name))

    os.makedirs(out_dir, exist_ok=True)
    app.data[0] = demo_status("PRINTER-01", state="RUNNING", progress=47, remaining_time=83,
                              layer_num=61, total_layers=180, tray_now=0,
                              subtask_name="desk_organiser_plate02_2col",
                              temperatures={"nozzle": 219.6, "nozzle_target": 220,
                                            "bed": 65.2, "bed_target": 65})
    app.data[1] = demo_status("PRINTER-02", state="FINISH", progress=100, layer_num=212,
                              total_layers=212, awaiting_plate_clear=True,
                              subtask_name="cable_clips_x24")
    app.queue_cache[0] = (0, [("phone_stand_v3", 5340, False), ("spool_holder_left", 8100, True)])
    for name, view in (("screenshot_main.png", "main"), ("screenshot_detail.png", 0)):
        app.view = view
        app.d.fill(app.BG)
        app.cache.clear()
        app.redraw()
        save(name)
    app.view = "main"
    app.confirm[1] = time.ticks_add(time.ticks_ms(), 60_000)
    app.d.fill(app.BG)
    app.cache.clear()
    app.draw_main()
    save("screenshot_confirm.png")


if __name__ == "__main__":
    if not os.path.exists(os.path.join(ROOT, "config.py")):
        # fresh clone: borrow the example config so app.py can be imported
        import importlib.util
        spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.example.py"))
        sys.modules["config"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules["config"])
    if "--demo" in sys.argv:
        demo("docs")
        sys.exit()

    import app

    def show_main(name):
        app.view = "main"
        app.d.fill(app.BG)
        app.cache.clear()
        app.draw_main()
        save(name)

    for n, pid in enumerate(app.config.PRINTER_IDS[:2]):
        app.data[n] = app.fetch(pid)
    show_main("main_live.png")

    for n, pid in enumerate(app.config.PRINTER_IDS[:2]):
        st = app.data[n]
        app.queue_cache[n] = (0, app.next_jobs(pid, st and st.get("model") or "A1"))
        app.view = n
        app.d.fill(app.BG)
        app.cache.clear()
        app.draw_detail(n)
        save("detail_%d.png" % (n + 1))

    # demo: printer 1 printing, printer 2 waiting for its plate to be cleared
    import copy
    a, b = copy.deepcopy(app.data[0]), copy.deepcopy(app.data[1])
    if a and b:
        a.update(state="RUNNING", progress=47, remaining_time=83, layer_num=61, total_layers=180,
                 subtask_name="2026-10-21_bauble_set_plate02_4col",
                 temperatures={"nozzle": 219.6, "nozzle_target": 220, "bed": 65.2, "bed_target": 65})
        b.update(awaiting_plate_clear=True)
        app.data[0], app.data[1] = a, b
        show_main("main_demo.png")
        app.confirm[1] = time.ticks_add(time.ticks_ms(), 60_000)
        show_main("main_demo_confirm.png")
        app.confirm.clear()
        app.view = 0
        app.d.fill(app.BG)
        app.cache.clear()
        app.draw_detail(0)
        save("detail_demo.png")
