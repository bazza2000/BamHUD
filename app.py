# Bambuddy status board for the ESP32-2432S028 (CYD).
# Main view: one card per printer with a bed strip (CLEAR BED needs a confirm tap).
# Tap a card for a full-screen detail view. Backlight sleeps when everything is idle.
# main.py just calls run(); tools/simulate.py imports this module on a PC with stubs.
import time
import network
import gc
import json
try:
    import urequests as requests
except ImportError:
    import requests
import config
from ili9341 import Display, rgb
from touch import Touch
from font import Font
try:
    from touch_cal import CAL
except ImportError:
    CAL = None

BG = rgb(10, 12, 18)
CARD = rgb(24, 28, 40)
WHITE = rgb(240, 240, 245)
GREY = rgb(130, 135, 150)
GREEN = rgb(60, 200, 110)
BLUE = rgb(70, 150, 255)
AMBER = rgb(255, 180, 40)
RED = rgb(240, 70, 70)
TRACK = rgb(50, 55, 70)

# Fonts (tools/make_font.py; see CLAUDE.md for the exact commands)
F_NAME = Font("f_name.bfn")     # Roboto Condensed Bold 28  (h 29)
F_BIG = Font("f_big.bfn")       # Roboto Bold 52, digits/%/- only (h 39)
F_MED = Font("f_med.bfn")       # Roboto SemiBold 20 (h 20)
F_SMALL = Font("f_small.bfn")   # Roboto Condensed Medium 15 (h 16)
F_LABEL = Font("f_label.bfn")   # Roboto Condensed Bold 12, +1 tracking (h 13)

STATE_STYLE = {
    "RUNNING": ("PRINTING", BLUE), "PREPARE": ("PREPARING", BLUE),
    "PAUSE": ("PAUSED", AMBER), "FINISH": ("DONE", GREEN),
    "IDLE": ("IDLE", GREEN), "FAILED": ("FAILED", RED),
}
RUNNING = ("RUNNING", "PAUSE", "PREPARE")
SPEEDS = {1: "Silent", 2: "Standard", 3: "Sport", 4: "Ludicrous"}
CONFIRM_MS = 5000
HTTP_TIMEOUT = 6           # seconds; without it a stalled socket freezes the whole loop
# Optional Bambuddy API key (config.API_KEY). Needs "Read Status" (status + queue) and, for the
# CLEAR BED button, "Control Printer". Ignored by Bambuddy servers that have auth switched off.
HEADERS = {"X-API-Key": config.API_KEY} if getattr(config, "API_KEY", "") else {}

# Runtime settings: config.py defaults, overridden by settings.json (written from the web page).
LAYOUTS = (2, 4, 8)          # printers per screen: two tall cards, quarters, or 2 x 4 rows


def _default_layout():
    n = len(config.PRINTER_IDS)
    return getattr(config, "LAYOUT", 2 if n <= 2 else 4 if n <= 4 else 8)


S = {"poll": config.POLL_SECONDS, "idle": getattr(config, "IDLE_OFF_MINUTES", 15),
     "bambuddy": config.BAMBUDDY, "layout": _default_layout()}
try:
    with open("settings.json") as _f:
        S.update(json.load(_f))
except (OSError, ValueError):
    pass
if S["layout"] not in LAYOUTS:
    S["layout"] = 2
last_error = ""
boot_ms = time.ticks_ms()
DEBUG = getattr(config, "DEBUG", False)

d = Display()
d.fill(BG)
d.brightness(100)
tp = Touch(CAL)

CARD_W, CARD_H, GAP = 154, 226, 4
IN_W = CARD_W - 16                 # inner text width of a card
BED_Y, BED_H = 196, 30
BTN_Y, BTN_H = 216, 22             # detail view buttons

data = [None] * 8            # latest (slim) status per slot; S["layout"] slots are used
queue_cache = {}             # printer index -> (ticks, [next jobs])
confirm = {}                 # printer index -> ticks deadline for the confirm tap
flash = {}                   # printer index -> (text, colour, ticks until)
view = "main"                # "main" or printer index for the detail view
cache = {}                   # what is on screen now: key -> signature
screen_on = True
last_activity = time.ticks_ms()
last_poll = 0


# ---------- helpers ----------

def log(*a):
    if DEBUG:
        print(*a)


def fmt_secs(s):
    m = int(s or 0) // 60
    return "%dh%02dm" % (m // 60, m % 60) if m >= 60 else "%dm" % m


def fmt_mins(m):
    return fmt_secs(int(m or 0) * 60)


def hex_colour(c):
    try:
        return rgb(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except Exception:
        return TRACK


def txt(key, x, y, s, font, fg, bg, w, align=0):
    """Draw text in a fixed field, only if it differs from what is on screen."""
    sig = (s, fg, bg)
    if cache.get(key) != sig:
        d.ftext(x, y, s, font, fg, bg, w, align)
        cache[key] = sig


def box(key, x, y, w, h, col):
    sig = (x, y, w, h, col)
    if cache.get(key) != sig:
        d.rect(x, y, w, h, col)
        cache[key] = sig
        return True
    return False


def badge_for(st):
    label, col = STATE_STYLE.get(st["state"], (st["state"][:9], GREY))
    if st.get("hms_errors"):
        return "ERROR", RED
    if not st.get("connected", True):
        return "OFFLINE", RED
    return label, col


def bed_mode(i):
    """What the bed strip / button shows for printer i: (text, fg, bg, tappable)."""
    st = data[i]
    now = time.ticks_ms()
    if i in flash and time.ticks_diff(flash[i][2], now) > 0:
        return flash[i][0], BG, flash[i][1], False
    if i in confirm and time.ticks_diff(confirm[i], now) > 0:
        return "TAP TO CONFIRM", WHITE, RED, True
    if st is None:
        return "NO DATA", GREY, TRACK, False
    t = st.get("temperatures") or {}
    if st["state"] in RUNNING:
        return "BED IN USE  %dC" % round(t.get("bed") or 0), WHITE, TRACK, False
    if st.get("awaiting_plate_clear"):
        return "CLEAR BED", BG, AMBER, True
    return "BED READY", GREEN, CARD, False


# ---------- Bambuddy API ----------

KEEP = ("name", "state", "connected", "progress", "remaining_time", "layer_num", "total_layers",
        "subtask_name", "temperatures", "wifi_signal", "speed_level", "cooling_fan_speed",
        "awaiting_plate_clear", "tray_now", "model")
TRAY_KEEP = ("id", "tray_type", "tray_sub_brands", "tray_color", "remain", "exists")


def slim(st):
    """Keep only what the screen/web page use. A full status dict is ~10 KB of heap,
    a slim one ~1 KB; two full ones push the heap past its first 56 KB block (CLAUDE.md)."""
    out = {k: st.get(k) for k in KEEP}
    out["hms_errors"] = [str(e)[:60] for e in (st.get("hms_errors") or [])[:2]]
    out["ams"] = [{"id": u.get("id", 0),
                   "tray": [{k: t.get(k) for k in TRAY_KEEP} for t in (u.get("tray") or [])]}
                  for u in (st.get("ams") or [])[:1]]
    out["vt_tray"] = [] if out["ams"] else [{k: t.get(k) for k in TRAY_KEEP}
                                           for t in (st.get("vt_tray") or [])[:1]]
    return out


fetch_ms = 0                 # duration of the last status fetch (shown on the web page)
AUTH_HINT = {401: " - Bambuddy auth is on: set API_KEY in config.py",
             403: " - API key lacks permission (needs Read Status) or this printer"}


def fetch(pid):
    global last_error, fetch_ms
    t0 = time.ticks_ms()
    try:
        r = requests.get("%s/printers/%d/status" % (S["bambuddy"], pid), headers=HEADERS,
                         timeout=HTTP_TIMEOUT)
        out = slim(r.json()) if r.status_code == 200 else None
        if out is None:
            last_error = "printer %d: HTTP %d%s" % (pid, r.status_code, AUTH_HINT.get(r.status_code, ""))
        r.close()
        gc.collect()
        fetch_ms = time.ticks_diff(time.ticks_ms(), t0)
        return out
    except Exception as e:
        print("fetch", pid, e)
        last_error = "printer %d: %r" % (pid, e)
        return None


def clear_plate(pid):
    try:
        r = requests.post("%s/printers/%d/clear-plate" % (S["bambuddy"], pid), headers=HEADERS,
                          timeout=HTTP_TIMEOUT)
        code = r.status_code
        r.close()
        if code >= 300:
            global last_error
            last_error = "clear-plate printer %d: HTTP %d%s" % (
                pid, code, " - API key needs Control Printer" if code == 403 else AUTH_HINT.get(code, ""))
        return code
    except Exception as e:
        print("clear", pid, e)
        return 0


def next_jobs(pid, model, want=2):
    """Pending queue jobs for this printer, parsed one object at a time to save RAM."""
    jobs = []
    try:
        r = requests.get("%s/queue/?status=pending" % S["bambuddy"], headers=HEADERS,
                         timeout=HTTP_TIMEOUT)
        sock = r.raw
        depth, in_str, esc, obj = 0, False, False, None
        while len(jobs) < want:
            chunk = sock.read(256)
            if not chunk:
                break
            for c in chunk:
                if obj is not None:
                    obj.append(c)
                if in_str:
                    if esc:
                        esc = False
                    elif c == 92:        # backslash
                        esc = True
                    elif c == 34:        # quote
                        in_str = False
                    continue
                if c == 34:
                    in_str = True
                elif c == 123:           # {
                    depth += 1
                    if depth == 1:
                        obj = bytearray(b"{")
                elif c == 125:           # }
                    depth -= 1
                    if depth == 0 and obj is not None:
                        j = json.loads(bytes(obj))
                        obj = None
                        p = j.get("printer_id")
                        if p == pid or (p is None and j.get("target_model") in (None, model)):
                            jobs.append((j.get("archive_name") or j.get("library_file_name") or "?",
                                         j.get("print_time_seconds") or 0,
                                         p is None))
                            if len(jobs) >= want:
                                break
        r.close()
    except Exception as e:
        print("queue", e)
    gc.collect()
    return jobs


# ---------- main view ----------
# S["layout"] = 2 (two tall cards), 4 (quarters) or 8 (2 columns x 4 rows).
# Slot i shows printer PRINTER_IDS[i]; slots without an id are drawn as "empty slot".

def pid_of(i):
    ids = config.PRINTER_IDS
    return ids[i] if i < len(ids) else None


def open_detail(i):
    global view, last_poll
    view = i
    queue_cache.pop(i, None)
    last_poll = time.ticks_add(time.ticks_ms(), -60_000)   # load queue on next loop pass
    d.fill(BG)
    cache.clear()


def draw_main():
    n = S["layout"]
    if n == 2:
        draw_main2()
    else:
        for i in range(n):
            draw_cell(i, n)


def tap_main(x, y):
    n = S["layout"]
    if n == 2:
        return tap_main2(x, y)
    for i in range(n):
        x0, y0, w, h = cell_box(i, n)
        if x0 <= x < x0 + w + 4 and y0 <= y < y0 + h + 4:
            if pid_of(i) is None:
                return
            if n == 4 and y >= y0 + h - CELL4_BED - 4 and bed_mode(i)[3]:
                bed_tap(i)
            else:
                open_detail(i)
            return


# --- 2-printer layout: two tall cards ---

def card_x(i):
    return 4 + i * (CARD_W + GAP)


def draw_main2():
    for i in (0, 1):
        x0 = card_x(i)
        xi = x0 + 8
        k = "m%d" % i
        if box(k + "bg", x0, 7, CARD_W, CARD_H, CARD):
            d.ftext(xi, 152, "LEFT", F_LABEL, GREY, CARD)
            d.ftext(xi, 176, "LAYER", F_LABEL, GREY, CARD)
        st = data[i]
        if st is None:
            box(k + "badgebg", xi, 42, IN_W, 17, RED)
            txt(k + "badge", xi, 44, "NO DATA", F_LABEL, BG, RED, IN_W, 1)
        else:
            label, col = badge_for(st)
            txt(k + "name", xi, 10, st["name"], F_NAME, WHITE, CARD, IN_W)
            if box(k + "badgebg", xi, 42, IN_W, 17, col):
                cache.pop(k + "badge", None)
            txt(k + "badge", xi + 2, 44, label, F_LABEL, BG, col, IN_W - 4, 1)
            pct = int(st.get("progress") or 0)
            txt(k + "pct", xi, 63, "%d%%" % pct, F_BIG, WHITE, CARD, IN_W)
            if cache.get(k + "bar") != (pct, col):
                d.rect(xi, 108, IN_W, 6, TRACK)
                d.rect(xi, 108, IN_W * pct // 100, 6, col)
                cache[k + "bar"] = (pct, col)
            txt(k + "file", xi, 122, st.get("subtask_name") or "-", F_SMALL, GREY, CARD, IN_W)
            run = st["state"] in RUNNING
            txt(k + "left", xi + 44, 146, fmt_mins(st.get("remaining_time")) if run else "-",
                F_MED, WHITE, CARD, IN_W - 44, 2)
            lay = "%s/%s" % (st.get("layer_num") or 0, st.get("total_layers") or 0)
            txt(k + "layer", xi + 44, 170, lay if run else "-", F_MED, WHITE, CARD, IN_W - 44, 2)
        text, fg, bg, _ = bed_mode(i)
        if box(k + "bed", x0 + 6, BED_Y, CARD_W - 12, BED_H, bg):
            cache.pop(k + "bedtxt", None)
        txt(k + "bedtxt", x0 + 8, BED_Y + 7, text, F_SMALL, fg, bg, CARD_W - 16, 1)


def tap_main2(x, y):
    i = 0 if x < 160 else 1
    x0 = card_x(i)
    on_bed = x0 + 6 <= x <= x0 + CARD_W - 6 and BED_Y - 4 <= y <= BED_Y + BED_H + 4
    if on_bed and bed_mode(i)[3]:
        bed_tap(i)
    else:
        open_detail(i)


# --- 4- and 8-printer grids ---

CELL4_BED = 16      # height of the bed strip / CLEAR BED button at the bottom of a 4-up cell


def cell_box(i, n):
    """(x, y, w, h) of slot i. Row-major: slots 0,1 on the top row."""
    col, row = i % 2, i // 2
    if n == 4:
        return 4 + col * 158, 4 + row * 118, 154, 114
    return 4 + col * 158, 4 + row * 59, 154, 55


def bed_short(i):
    """Compact bed status for 8-up cells: (text, colour)."""
    t, fg, bg, tappable = bed_mode(i)
    if tappable or t == "CLEAR BED":
        return ("CONFIRM?" if t == "TAP TO CONFIRM" else "CLEAR BED"), AMBER
    st = data[i]
    if st and st["state"] in RUNNING:
        return "%dC" % round((st.get("temperatures") or {}).get("bed") or 0), GREY
    return ("READY", GREEN) if st else ("", GREY)


def draw_cell(i, n):
    x0, y0, w, h = cell_box(i, n)
    k = "c%d" % i
    xi, iw = x0 + 10, w - 16                 # text column right of the 4 px state accent
    box(k + "bg", x0, y0, w, h, CARD)
    pid = pid_of(i)
    if pid is None:
        txt(k + "name", xi, y0 + 5, "empty slot", F_SMALL, TRACK, CARD, iw)
        return
    st = data[i]
    if st is None:
        label, col, name = "NO DATA", RED, "Printer %d" % pid
    else:
        label, col = badge_for(st)
        name = st["name"]
    box(k + "acc", x0, y0, 4, h, col)
    run = bool(st) and st["state"] in RUNNING
    pct = int(st.get("progress") or 0) if st else 0
    # % means nothing for an idle/offline printer; FINISH/FAILED keep theirs
    pct_s = "%d%%" % pct if st and st["state"] not in ("IDLE",) else "-"
    left = fmt_mins(st.get("remaining_time")) if run else ""
    if n == 4:
        txt(k + "name", xi, y0 + 5, name, F_MED, WHITE, CARD, iw)
        txt(k + "pct", xi, y0 + 28, pct_s, F_BIG, WHITE, CARD, iw)
        bar_y, bar_h = y0 + 70, 5
        txt(k + "state", xi, y0 + 81, label, F_LABEL, col, CARD, 72)
        txt(k + "left", xi + 72, y0 + 79, left, F_SMALL, WHITE, CARD, iw - 72, 2)
        text, fg, bg, _ = bed_mode(i)
        by = y0 + h - CELL4_BED - 2
        if box(k + "bed", x0 + 8, by, w - 12, CELL4_BED, bg):
            cache.pop(k + "bedt", None)
        txt(k + "bedt", x0 + 10, by + 2, text, F_LABEL, fg, bg, w - 16, 1)
    else:
        txt(k + "name", xi, y0 + 4, name, F_SMALL, WHITE, CARD, 88)
        txt(k + "pct", xi + 88, y0 + 2, pct_s, F_MED, WHITE, CARD, iw - 88, 2)
        bar_y, bar_h = y0 + 25, 4
        # the accent colour already says "printing", so a running printer just shows its time left
        state = left if st and st["state"] in ("RUNNING", "PREPARE") else label
        txt(k + "state", xi, y0 + 36, state, F_LABEL, col, CARD, 66)
        bs, bc = bed_short(i)
        txt(k + "bed", xi + 66, y0 + 36, bs, F_LABEL, bc, CARD, iw - 66, 2)
    if cache.get(k + "bar") != (pct, col):
        d.rect(xi, bar_y, iw, bar_h, TRACK)
        d.rect(xi, bar_y, iw * pct // 100, bar_h, col)
        cache[k + "bar"] = (pct, col)


def bed_tap(i):
    now = time.ticks_ms()
    if i in confirm and time.ticks_diff(confirm[i], now) > 0:
        confirm.pop(i)
        flash[i] = ("CLEARING...", AMBER, time.ticks_add(now, 30_000))
        redraw()
        code = clear_plate(pid_of(i))
        ok = 200 <= code < 300
        log("clear-plate", i, code)
        msg = "BED CLEARED" if ok else "NO PERMISSION" if code in (401, 403) else "CLEAR FAILED"
        flash[i] = (msg, GREEN if ok else RED, time.ticks_add(time.ticks_ms(), 4000))
        data[i] = fetch(pid_of(i)) or data[i]
    else:
        confirm[i] = time.ticks_add(now, CONFIRM_MS)


# ---------- detail view ----------

def draw_detail(i):
    st = data[i]
    box("dhead", 0, 0, 320, 34, CARD)
    if st is None:
        txt("dname", 8, 3, "NO DATA", F_NAME, RED, CARD, 196)
    else:
        label, col = badge_for(st)
        txt("dname", 8, 3, st["name"], F_NAME, WHITE, CARD, 196)
        if box("dbadge", 212, 9, 100, 17, col):
            cache.pop("dbadget", None)
        txt("dbadget", 214, 11, label, F_LABEL, BG, col, 96, 1)
        txt("dfile", 8, 40, st.get("subtask_name") or "-", F_SMALL, GREY, BG, 304)
        pct = int(st.get("progress") or 0)
        if cache.get("dbar") != (pct, col):
            d.rect(8, 62, 230, 8, TRACK)
            d.rect(8, 62, 230 * pct // 100, 8, col)
            cache["dbar"] = (pct, col)
        txt("dpct", 246, 56, "%d%%" % pct, F_MED, WHITE, BG, 66, 2)
        run = st["state"] in RUNNING
        line = "Left %s     Layer %s/%s" % (fmt_mins(st.get("remaining_time")) if run else "-",
                                             st.get("layer_num") or 0, st.get("total_layers") or 0)
        txt("dtime", 8, 80, line, F_SMALL, WHITE, BG, 304)
        t = st.get("temperatures") or {}
        line = "Nozzle %d / %dC     Bed %d / %dC" % (
            round(t.get("nozzle") or 0), round(t.get("nozzle_target") or 0),
            round(t.get("bed") or 0), round(t.get("bed_target") or 0))
        txt("dtemp", 8, 98, line, F_SMALL, AMBER, BG, 304)
        line = "Speed %s     Fan %s     WiFi %s dBm" % (SPEEDS.get(st.get("speed_level"), "-"),
                                                      st.get("cooling_fan_speed") or 0,
                                                      st.get("wifi_signal") or 0)
        txt("dmisc", 8, 116, line, F_SMALL, GREY, BG, 304)
        draw_filament(st)
        draw_next(i, st)
    text, fg, bg, tappable = bed_mode(i)
    if box("dbed", 8, BTN_Y, 196, BTN_H, bg):
        cache.pop("dbedt", None)
    txt("dbedt", 10, BTN_Y + 3, text, F_SMALL, fg, bg, 192, 1)
    if box("dback", 232, BTN_Y, 80, BTN_H, TRACK):
        d.ftext(234, BTN_Y + 3, "BACK", F_SMALL, WHITE, TRACK, 76, 1)


def draw_filament(st):
    trays = []
    for unit in st.get("ams") or []:
        for tr in unit.get("tray") or []:
            trays.append((int(unit.get("id", 0)) * 4 + int(tr.get("id", 0)), tr))
    if not trays:
        for tr in st.get("vt_tray") or []:
            trays.append((int(tr.get("id", 254)), tr))
    try:
        active = int(st.get("tray_now"))
    except Exception:
        active = -1
    y = 136
    for n in range(4):
        x = 8 + n * 78
        if n >= len(trays):
            box("ft%d" % n, x, y, 72, 36, BG)
            continue
        idx, tr = trays[n]
        exists = tr.get("exists", bool(tr.get("tray_type")))
        box("ff%d" % n, x, y, 72, 36, WHITE if idx == active else TRACK)
        if box("ft%d" % n, x + 1, y + 1, 70, 34, CARD):
            for k in ("fa", "fb", "fc"):
                cache.pop(k + str(n), None)
        # colour strip doubles as a remaining-filament gauge (fills from the bottom)
        rem = tr.get("remain") if exists else 0
        rem = 100 if rem is None or rem < 0 else min(100, rem)
        col = hex_colour(tr.get("tray_color") or "") if exists else TRACK
        if cache.get("fc%d" % n) != (col, rem):
            fill = 28 * rem // 100
            d.rect(x + 4, y + 4, 10, 28 - fill, TRACK)
            d.rect(x + 4, y + 32 - fill, 10, fill, col)
            cache["fc%d" % n] = (col, rem)
        if exists:
            sub = (tr.get("tray_sub_brands") or "").replace(tr.get("tray_type") or "", "").strip()
            txt("fa%d" % n, x + 18, y + 3, tr.get("tray_type") or "?", F_SMALL, WHITE, CARD, 52)
            txt("fb%d" % n, x + 18, y + 20, sub, F_LABEL, GREY, CARD, 52)
        else:
            txt("fa%d" % n, x + 18, y + 3, "Empty", F_SMALL, GREY, CARD, 52)
            txt("fb%d" % n, x + 18, y + 20, "", F_LABEL, GREY, CARD, 52)


def draw_next(i, st):
    hms = st.get("hms_errors") or []
    if hms:
        txt("nhead", 8, 183, "HMS", F_LABEL, RED, BG, 40)
    else:
        txt("nhead", 8, 183, "NEXT", F_LABEL, GREY, BG, 40)
    q = queue_cache.get(i)
    jobs = q[1] if q else None
    lines = []                        # (text, colour, right-hand time)
    if hms:
        lines.append((str(hms[0]), RED, ""))
    if jobs is None:
        lines.append(("loading...", GREY, ""))
    elif not jobs and not hms:
        lines.append(("Queue empty", GREY, ""))
    else:
        for name, secs, any_printer in jobs:   # * = unassigned job, may go to either printer
            lines.append((name, WHITE, fmt_secs(secs) + (" *" if any_printer else "")))
    for n in range(2):
        s, col, t = lines[n] if n < len(lines) else ("", WHITE, "")
        txt("nq%d" % n, 52, 180 + n * 17, s, F_SMALL, col, BG, 196)
        txt("nt%d" % n, 252, 180 + n * 17, t, F_SMALL, GREY, BG, 60, 2)


def tap_detail(x, y):
    global view
    i = view
    if 8 <= x <= 204 and BTN_Y - 4 <= y <= BTN_Y + BTN_H + 4 and bed_mode(i)[3]:
        bed_tap(i)
        return
    view = "main"
    d.fill(BG)
    cache.clear()


# ---------- web server interface ----------

class App:
    """What the web server can see and change."""

    def status(self):
        printers = []
        for i in range(S["layout"]):
            st, pid = data[i], pid_of(i)
            if pid is None:
                continue
            if not st:
                printers.append({"ok": False, "name": "Printer %d" % pid})
                continue
            t = st.get("temperatures") or {}
            trays = []
            for unit in st.get("ams") or []:
                for tr in unit.get("tray") or []:
                    if tr.get("tray_type"):
                        trays.append({"t": tr.get("tray_type"), "c": (tr.get("tray_color") or "888888")[:6],
                                      "r": tr.get("remain") or 0})
            printers.append({
                "ok": True, "name": st["name"], "state": st["state"], "hms": bool(st.get("hms_errors")),
                "progress": int(st.get("progress") or 0), "file": st.get("subtask_name"),
                "remaining": st.get("remaining_time") or 0, "layer": st.get("layer_num") or 0,
                "layers": st.get("total_layers") or 0, "nozzle": round(t.get("nozzle") or 0),
                "bed": round(t.get("bed") or 0), "awaiting_clear": bool(st.get("awaiting_plate_clear")),
                "trays": trays})
        wl = network.WLAN(network.STA_IF)
        try:
            rssi = wl.status("rssi")
        except Exception:
            rssi = 0
        idf = idf_free()
        return {"printers": printers, "device": {
            "screen_on": screen_on, "view": "main" if view == "main" else "printer %d" % (view + 1),
            "rssi": rssi, "ip": wl.ifconfig()[0], "uptime_s": time.ticks_diff(time.ticks_ms(), boot_ms) // 1000,
            "free": gc.mem_free(), "idf_free": idf, "poll_s": S["poll"], "idle_min": S["idle"], "bambuddy": S["bambuddy"], "layout": S["layout"], "fetch_ms": fetch_ms, "api_key": bool(HEADERS),
            "last_error": last_error}}

    def apply(self, form):
        try:
            if form.get("poll"):
                S["poll"] = max(2, min(120, int(form["poll"])))
            if form.get("idle"):
                S["idle"] = max(1, min(600, int(form["idle"])))
            if form.get("bambuddy", "").startswith("http"):
                S["bambuddy"] = form["bambuddy"].rstrip("/")
            if form.get("layout") and int(form["layout"]) in LAYOUTS and int(form["layout"]) != S["layout"]:
                global view, last_poll
                S["layout"] = int(form["layout"])
                view = "main"                       # repaint the new grid and fetch its printers now
                d.fill(BG)
                cache.clear()
                last_poll = time.ticks_add(time.ticks_ms(), -60_000)
        except ValueError:
            pass
        with open("settings.json", "w") as f:
            json.dump(S, f)
        return S

    def screen(self, on):
        global screen_on, last_activity
        screen_on = on
        d.brightness(100 if on else 0)
        if on:
            last_activity = time.ticks_ms()


# ---------- loop ----------

def redraw():
    if view == "main":
        draw_main()
    else:
        draw_detail(view)


def connect():
    try:
        network.hostname(getattr(config, "HOSTNAME", "bamhud"))   # reachable as <HOSTNAME>.local
    except Exception:
        pass
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    # Power-save toggles the radio at every beacon; the current spikes dip the
    # backlight supply and show as flicker. Keep the radio steadily on instead.
    try:
        wlan.config(pm=wlan.PM_NONE)
        wlan.config(txpower=getattr(config, "WIFI_TXPOWER", 13))
    except Exception as e:
        print("wifi config", e)
    if not wlan.isconnected():
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        t = time.time()
        while not wlan.isconnected() and time.time() - t < 20:
            time.sleep_ms(200)
    return wlan


def idf_free():
    """Free ESP-IDF heap (what Wi-Fi/lwIP live on); below ~10 KB networking starts failing. -1 off-device."""
    try:
        import esp32
        return sum(h[1] for h in esp32.idf_heap_info(esp32.HEAP_DATA))
    except Exception:
        return -1


def self_heal(net_ok):
    """Restart the board if networking is starved or dead, instead of sitting frozen (CLAUDE.md 5.5).
    Called once per poll: 3 polls in a row with < 3 KB IDF heap, or ~2 min of failed fetches while the IDF heap is
    low (< 10 KB). A Bambuddy outage alone (fetches fail, memory fine) never triggers a reset."""
    global _low_mem, _net_fail
    idf = idf_free()
    _low_mem = _low_mem + 1 if 0 <= idf < 3000 else 0
    _net_fail = 0 if net_ok else _net_fail + 1
    if _low_mem >= 3 or (_net_fail * S["poll"] >= 120 and 0 <= idf < 10000):
        print("self-heal reset: idf_free", idf, "failed polls", _net_fail)
        time.sleep_ms(200)
        import machine
        machine.reset()


_low_mem = 0
_net_fail = 0


def needs_attention():
    for st in data[:S["layout"]]:
        if st and (st["state"] in RUNNING or st.get("awaiting_plate_clear") or st.get("hms_errors")):
            return True
    return False


def run():
    global screen_on, last_activity, last_poll, view
    d.ftext(10, 110, "Connecting to WiFi...", F_MED, AMBER, BG)
    wlan = connect()
    d.fill(BG)
    web = None
    try:
        from web import Web
        web = Web(App(), getattr(config, "WEB_PASSWORD", ""))
        print("web server on http://%s/" % wlan.ifconfig()[0])
    except Exception as e:
        print("web server failed", e)

    last_poll = time.ticks_add(time.ticks_ms(), -60_000)
    pressed = False
    rr = 0                                  # round-robin slot to fetch next
    while True:
        now = time.ticks_ms()

        # --- touch ---
        p = tp.point()
        if p and not pressed:
            pressed = True
            last_activity = now
            if not screen_on:                 # first tap only wakes the screen
                screen_on = True
                d.brightness(100)
                log("wake by touch")
            else:
                log("tap", p, "view", view)
                (tap_main if view == "main" else tap_detail)(*p)
                redraw()
        elif not p and pressed and tp.raw() is None:
            pressed = False

        # --- poll Bambuddy: ONE request per step, spread evenly over the poll interval ---
        # Fetching every printer back-to-back blocked the loop for seconds with 8 printers,
        # so taps were missed. Now the loop never blocks for more than one HTTP request.
        slots = S["layout"]
        # min 1.2 s between requests: a fetch takes ~0.3-0.9 s on the ESP32, leave time for touch
        if time.ticks_diff(now, last_poll) >= max(S["poll"] * 1000 // slots, 1200):
            last_poll = now
            if not wlan.isconnected():
                wlan = connect()
            q = queue_cache.get(view) if view != "main" else None
            if view != "main" and (q is None or time.ticks_diff(now, q[0]) > 30_000):
                st = data[view]                  # detail view: refresh its queue as its own step
                queue_cache[view] = (now, next_jobs(pid_of(view), st and st.get("model") or "A1"))
            else:
                if rr >= slots:
                    rr = 0
                pid = pid_of(rr)
                data[rr] = fetch(pid) if pid is not None else None
                gc.collect()
                rr += 1
                if rr >= slots:                  # finished a full round of printers
                    rr = 0
                    self_heal(any(data[:slots]) or not wlan.isconnected())  # no Wi-Fi: connect() handles it
            if needs_attention():
                last_activity = time.ticks_ms()
                if not screen_on:
                    screen_on = True
                    d.brightness(100)
                    log("wake by printer")
            redraw()

        # --- expire confirm / flash so the strip reverts ---
        for dct in (confirm, flash):
            for k in list(dct):
                dl = dct[k] if dct is confirm else dct[k][2]
                if time.ticks_diff(dl, now) <= 0:
                    dct.pop(k)
                    redraw()

        # --- sleep ---
        if screen_on and time.ticks_diff(now, last_activity) > S["idle"] * 60_000:
            screen_on = False
            d.brightness(0)
            if view != "main":
                view = "main"
                d.fill(BG)
                cache.clear()
                redraw()
            log("screen off")

        if web:
            web.poll()

        time.sleep_ms(25)
