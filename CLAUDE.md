# BamHUD — a heads-up display for Bambuddy on an ESP32 "Cheap Yellow Display"

Handover notes for anyone (human or Claude) changing this code with no prior context.
Read this whole file before touching the board: several settings below were found by
trial and error and look "wrong" compared with generic ILI9341 / XPT2046 / MicroPython examples.
**§5.5 (memory) is the one most likely to bite you.**

Site-specific details (IP addresses, hosts, serial port, backups) live in `CLAUDE.local.md`,
which is git-ignored. Read it too if it exists.

---

## 1. What it is

A 320×240 touchscreen that shows two Bambu Lab printers
(or any two printers) managed by **Bambuddy** (self-hosted printer manager, github.com/maziggy/bambuddy):

- **Main view** – one card per printer: printer name (fills the card width), state badge,
  big %, progress bar, filename, LEFT / LAYER rows, and a **bed strip** at the bottom
  (BED IN USE / BED READY / amber **CLEAR BED** button when Bambuddy is waiting for the plate to be cleared).
- **Detail view** – tap a card: file, progress, temps (actual/target), speed, fan, Wi-Fi,
  4 AMS filament swatches (real colour; the colour strip is also a **remaining-filament gauge**
  that fills from the bottom; active slot outlined white), the next 2 queued jobs with print time
  (`*` = unassigned job that may go to either printer), HMS error line, CLEAR BED and BACK buttons.
- **Sleep** – backlight off after `idle` minutes (default 15) with no print running,
  no plate waiting to be cleared, no HMS error and no touch. First tap only wakes it
  (it is swallowed, never treated as a button press). It also wakes itself when a
  printer starts, needs its plate cleared, or reports an error.
- **Web page** – `http://bamhud.local/` (or the display's IP): live status,
  device health (incl. free Python heap and free Wi-Fi/IDF heap), settings (poll interval,
  sleep minutes, Bambuddy URL), screen on/off, reboot. **Remote code updates** over Wi-Fi with `tools/deploy.py`.
- **Fonts** – anti-aliased Roboto / Roboto Condensed (SIL OFL, `fonts/OFL.txt`) pre-rendered
  to 16-level bitmap fonts, blended against the known background colour at draw time.

It is read-only towards the printers except **one** write: `POST /printers/{id}/clear-plate`
(see §6 — it can start the next queued print).

---

## 2. Hardware

| Item | Value |
|---|---|
| Board | ESP32-2432S028 "CYD" (Cheap Yellow Display), 2.8" |
| SoC | ESP32-D0WD-V3 rev 3.1, dual core, **no PSRAM**, 4 MB flash, 40 MHz crystal |
| USB-serial | CH340 (e.g. `COM3` on Windows, `/dev/ttyUSB0` on Linux) |
| Display | ILI9341-compatible, SPI1/HSPI: SCK 14, MOSI 13, MISO 12, CS 15, DC 2, backlight **GPIO 21** |
| Touch | XPT2046 on its **own** pins: CLK 25, MOSI 32, MISO 39, CS 33, IRQ 36 (IRQ unused) |
| Other | microSD slot (CS 5, unused), RGB LED, LDR — unused |

Firmware: **MicroPython v1.29.0** (`ESP32_GENERIC-20260824-v1.29.0.bin`) flashed at 0x1000.

---

## 3. Files

| File | On board as | Purpose |
|---|---|---|
| `main.py` | `main.py` | 3 lines: `import app; app.run()`. Runs at boot. |
| `app.py` | `app.mpy` | Everything app-level: fonts, drawing, views, touch handling, polling, sleep, `App` class for the web server, `run()` loop. Importable without starting the loop (the simulator relies on that). |
| `ili9341.py` | `ili9341.mpy` | Display driver: init sequence, `rect`, `fill`, `ftext` (anti-aliased text in a fixed-width field), backlight. |
| `font.py` | `font.mpy` | BFN1 font reader + glyph renderer/cache. Pure Python: runs on the board **and** in CPython. |
| `touch.py` | `touch.mpy` | XPT2046 driver: pressure-gated, median-filtered raw read + calibrated `point()`. |
| `touch_cal.py` | `touch_cal.py` | `CAL = (swap, x20, x300, y20, y220)` produced by `calibrate.py`. **Hardware-specific — keep this copy.** |
| `web.py` | `web.mpy` | Non-blocking HTTP server: serves `index.html`, JSON API, settings, OTA upload. |
| `index.html` | `index.html` | The web page. Streamed from flash in 512-byte chunks (kept out of RAM on purpose). |
| `config.py` | `config.py` | Wi-Fi SSID/password, Bambuddy URL, printer ids, defaults, `WEB_PASSWORD`, `DEBUG`. **Contains secrets.** |
| `calibrate.py` | `calibrate.mpy` | One-off touch calibration: 3 crosshairs, writes `touch_cal.py`. |
| `f_name/f_big/f_med/f_small/f_label.bfn` | same | Pre-rendered fonts (see §5.4 for how to regenerate). |
| `settings.json` | **board only** | Written by the web page; overrides `config.py` defaults for `poll`, `idle`, `bambuddy`. Delete it to fall back to config. |
| `config.example.py` | — | Template for `config.py` (which is git-ignored because it holds secrets). |
| `CLAUDE.local.md` | — | **Git-ignored** site-specific notes (IPs, hosts, serial port, backups). Never commit. |
| `requirements.txt` | PC | esptool, pyserial, mpy-cross 1.29.*, Pillow. |
| `docs/` | PC | README screenshots from `tools/simulate.py --demo` (invented data only). |
| `fonts/` | PC | Source TTFs (Roboto, Roboto Condensed variable fonts) + `OFL.txt` licence. |
| `build/` | PC | `.mpy` files produced by `deploy.py`. Safe to delete. |
| `sim/` | PC | PNGs from `tools/simulate.py`. Safe to delete. |
| `tools/deploy.py` | PC | Compile (`.mpy`) + upload over Wi-Fi (default) or `--usb`, then reboot. |
| `tools/simulate.py` | PC | Render the real app code to PNGs with live Bambuddy data — **look at these before deploying layout changes**. |
| `tools/make_font.py` | PC | TTF → BFN1 font converter (needs Pillow). |
| `tools/serial_log.py` | PC | Tail the USB serial console. |

Before first flashing, back up the factory firmware (README → *Flashing*). The backup location
for this installation is in `CLAUDE.local.md`.

---

## 4. Tooling

- Python 3 with `esptool`, `pyserial`, **`mpy-cross` 1.29.x** (must match the firmware's .mpy v6.3)
  and **Pillow** (fonts + simulator): `pip install -r requirements.txt`. On a PC without Python,
  Thonny's bundled interpreter works (`%LOCALAPPDATA%\Programs\Thonny\python.exe`).

### Workflow for a change

```powershell
$py = "python"                                    # or Thonny's python.exe
& $py tools\simulate.py                              # 1. render sim\*.png and LOOK at them (Read the PNGs)
& $py tools\deploy.py app.py                         # 2. compile + upload over Wi-Fi + reboot
& $py tools\deploy.py --host <display-ip> app.py web.py index.html     # if .local doesn't resolve
& $py tools\deploy.py --usb --port COM3 app.py       # USB if Wi-Fi/web server is broken
& $py tools\serial_log.py 30                         # watch the console (resets the board)
```

3. After deploying, check `http://<display-ip>/api/status` → `device.idf_free` should stay
   **> ~15 KB** and `last_error` empty (see §5.5).

`deploy.py` compiles the modules in its `COMPILE` set (`app, web, font, ili9341, touch, calibrate`)
to `build/*.mpy` and uploads the `.mpy`; the board then deletes the matching `.py` because
MicroPython imports `X.py` in preference to `X.mpy`. `--source` uploads the `.py` instead (debugging only:
it brings back the compile-time RAM spike). `main.py`, `config.py`, `touch_cal.py` stay as source.

Wi-Fi deploys first run a **preflight** (`/api/status`): wait until uptime ≥ 45 s, refuse if `idf_free` < 12 KB
(`--force` to override). Wi-Fi upload safety (in `web.py`): password required (`X-Password` header = `WEB_PASSWORD`);
only names allowed by `allowed()` (the `UPLOADABLE` list, their `.mpy`, `index.html`, `f_*.bfn`);
body written to `name.new` and validated (`.py` compiled on the board, `.mpy` must start `M\x06`,
fonts must start `BFN1`, html must start `<!doctype html`) before it replaces the live file.
A runtime error in `app.py` can still take the web server down → recover with `deploy.py --usb`.
**If you add a new module, add it to `UPLOADABLE` (web.py) and `COMPILE` (deploy.py) first**,
deploy `web.py`, reboot, then deploy the new module.

---

## 5. Gotchas (the important part)

### 5.1 Display (`ili9341.py`)
1. **MADCTL `0x36` = `0x28`**: landscape, **BGR colour order** (bit 3 set), like the factory firmware's `0xE8`
   but rotated 180° to suit how this unit is mounted (`0xE8` = the other way up; recalibrate touch if you flip it).
2. **Display inversion OFF**: `_init()` sends `0x20` (INVOFF) **explicitly**. Don't add `0x21` (INVON).
   History, so nobody repeats it: on day one a byte-order bug (gotcha 3) made colours look inverted, `0x21`
   was added to "fix" it, and then red/blue were swapped (`0x20` MADCTL) to "fix" caramel showing as
   blue-grey. The two mistakes partly cancelled on screen: navy came out white with dark text. Verified on
   2026-10-22 with photos: INVOFF + `0x28` gives the mock's dark navy, an emerald DONE badge, amber temperatures,
   caramel `#AE835B` and orange `#FF9016` spools. When checking colours, use a photo of the detail view (AMS
   swatches) and compare it with `docs/screenshot_detail.png`.
3. **Don't byte-swap RGB565.** `rgb()` returns the plain 16-bit value; everything is sent high byte first.
4. **Backlight is a plain GPIO output, not PWM.** 1 kHz PWM visibly shimmered. `brightness()` is on/off.
5. There is no framebuffer (320×240×2 = 150 KB won't fit). Everything draws straight to the panel.

### 5.2 Flicker
6. **Flicker was Wi-Fi, not drawing.** MicroPython's default Wi-Fi modem power-save switches the
   radio at every beacon; the current spikes dip the backlight supply. Fixed in `connect()` with
   `wlan.config(pm=wlan.PM_NONE)` and `txpower=13` (`WIFI_TXPOWER`). Don't remove these.
7. Only changed fields are redrawn: `txt()` / `box()` in `app.py` compare a signature against
   `cache` before touching the panel, and `ftext(..., w=...)` paints a whole fixed-width field so a
   shorter string cleanly replaces a longer one. **Any full-screen change (switching view, waking)
   must `d.fill(BG)` and `cache.clear()`**, or stale signatures will stop things being drawn.
   If a `box()` behind text is repainted, pop that text's cache key so the text is redrawn on top.

### 5.3 Touch (`touch.py`, `calibrate.py`)
8. Touch uses `SoftSPI` on pins 25/32/39/33 — not the display's SPI bus.
9. Press detection uses the **Z1 pressure** reading (`> PRESS_Z = 120`), not the IRQ pin.
10. **Axes are swapped** on this unit (raw Y → screen X). `touch_cal.py` holds
    `(True, 361, 3508, 572, 3516)` = (swap, raw at screen x=20, x=300, y=20, y=220).
    If the screen orientation (MADCTL) ever changes, **recalibrate**: deploy `calibrate.py`,
    stop main.py, in a raw REPL run `import calibrate` and tap the 3 crosshairs; it writes
    `touch_cal.py` on the board. Copy the printed `CAL RESULT` into this repo's `touch_cal.py`.
11. A tap is acted on at the press edge; `pressed` stays True until release, so one press = one tap.

### 5.4 Fonts (`font.py`, `tools/make_font.py`)
12. BFN1 = 4-bit alpha glyphs, one shared height per font (tight band over its character set), glyph
    width = advance width. Format is documented at the top of `tools/make_font.py`.
13. Current fonts (regenerate with exactly these to keep the layout):
    ```powershell
    $R="fonts\Roboto-Variable.ttf"; $C="fonts\RobotoCondensed-Variable.ttf"
    & $py tools\make_font.py $C f_name.bfn  --size 28 --wght 700                     # h29, a 10-char name like "PRINTER-01" fills the 138 px card
    & $py tools\make_font.py $R f_big.bfn   --size 52 --wght 700 --chars "0123456789%-"   # h39
    & $py tools\make_font.py $R f_med.bfn   --size 20 --wght 600                     # h20
    & $py tools\make_font.py $C f_small.bfn --size 15 --wght 500 --gamma 1.2         # h16
    & $py tools\make_font.py $C f_label.bfn --size 12 --wght 700 --gamma 1.2 --tracking 1   # h13
    ```
    Roboto's digits are **tabular** (the converter prints this) so numbers don't jiggle as they change.
    Don't go below ~12 px with TTF conversion — small sizes need hand-drawn bitmap fonts.
14. Text colours are blended against the background **at render time**, so a glyph is only correct on
    the `bg` it was rendered for. Always pass the real background colour of the area you draw on.
15. `F_BIG` only contains `0-9 % -` and space; anything else renders as a space.

### 5.5 Memory — the ESP32 heap budget (read this before adding features)
16. **MicroPython's heap and the Wi-Fi driver share the same RAM.** The GC heap starts at **56 KB**;
    when it fills up, MicroPython *doubles* it (+56 KB) by taking memory from the ESP-IDF heap that
    Wi-Fi/lwIP use, and **never gives it back**. Measured on this board:
    fresh IDF free 142 KB → Wi-Fi up 86 KB → after one heap doubling ~25 KB. A *second* growth
    (when the 112 KB heap is full) took the last ~25 KB and **all networking died silently**:
    sockets time out (`ETIMEDOUT`/116), DNS fails (`-203`), the web page hangs, `fetch` returns nothing.
17. Current steady state: GC heap 112 KB, ~48–57 KB used, **IDF free ~24 KB** (shown on the web page
    as "WiFi xx KB" and in `/api/status` as `device.idf_free`). Keep Python usage well under ~90 KB.
18. Things already done to stay inside the budget — don't undo them:
    - Code is **precompiled to .mpy** (compiling a 20 KB .py on the board spikes RAM).
    - `fetch()` passes the status through `slim()` → ~1 KB instead of ~10 KB per printer.
    - The web page lives in `index.html` and is streamed, not held in a Python string.
    - `font.py` blend tables are one flat 1 KB `bytes` each (a list of 256 small `bytes` was ~5 KB),
      max 3 kept; rendered-glyph cache capped at 5 KB.
    - The queue (`/queue/?status=pending`, ~1.6 KB per job) is stream-parsed one object at a time.
    - Every `requests` call has `timeout=HTTP_TIMEOUT` (6 s) — without it a starved/stalled socket
      froze the whole loop forever.
    - `deploy.py` preflight: Wi-Fi uploads wait until the display has been up ≥ 45 s and refuse if `idf_free` < 12 KB
      (`--force` overrides). An upload landing on the first draw/fetch after a reboot once exhausted the IDF heap
      (connection dropped, `idf_free` 0, board stuck with a white screen until power-cycled).
    - `web.py` uploads `gc.collect()` first and stream through one reused 512-byte buffer.
    - **Self-heal** (`app.self_heal`, once per poll): `machine.reset()` after 3 polls in a row with < 3 KB IDF heap, or
      ~2 min of failed fetches while IDF heap < 10 KB. A Bambuddy outage alone (memory fine) never resets the board.
19. How to measure (from a raw REPL with main.py stopped):
    `import esp32,micropython; print(sum(h[1] for h in esp32.idf_heap_info(esp32.HEAP_DATA))); micropython.mem_info()`
    — `GC: total` shows whether the heap has grown (56000 = not yet, 112000 = once).
    **`gc.mem_free()+gc.mem_alloc()` is misleading here; use `micropython.mem_info()`.**

### 5.6 Flashing / USB serial
20. **Opening the serial port resets the board** (CH340 DTR/RTS wired to EN/IO0). Any serial tool restarts main.py.
21. **Auto-reset into download mode does NOT work on its own.** esptool reports
    "Wrong boot mode detected (0x13)". Procedure that works: start the esptool command with the
    normal auto-reset (no `--before no-reset`), and have the user **hold BOOT for the whole
    connection attempt**; release once "Connected"/"Stub flasher running" appears.
    `--connect-attempts 30` gives them time.
22. **Never `erase-flash` with `--after hard-reset` and then write in a separate command** — the reset
    drops out of download mode and leaves a blank board (happened once; recovered by holding BOOT
    again). Use `write-flash --erase-all` in one command, or `--after no-reset` on the erase.
23. To interrupt main.py for the REPL, send Ctrl-C **repeatedly** (it may be blocked in an HTTP request);
    `deploy.py --usb` already retries 8 times. Ctrl-C while it runs prints a traceback showing
    exactly where the loop was — a quick way to find a hang.

---

## 6. Bambuddy API used

Base: `BAMBUDDY` in `config.py` (tested against Bambuddy v1.2.5.7). The display sends **no API key**:
it expects Bambuddy auth to be disabled on the LAN (`GET /auth/status` → `auth_enabled: false`).

| Call | Used for | Notes |
|---|---|---|
| `GET /printers/{id}/status` | everything on screen | ~3.7 KB JSON, immediately reduced by `slim()`. Fields used: `name, state, connected, progress, remaining_time` (minutes), `layer_num, total_layers, subtask_name, temperatures{nozzle,nozzle_target,bed,bed_target}, hms_errors, wifi_signal, speed_level (1-4), cooling_fan_speed, awaiting_plate_clear, tray_now, ams[0].tray[]{id,tray_type,tray_sub_brands,tray_color "RRGGBBAA",remain,exists}, vt_tray` |
| `GET /queue/?status=pending` | "NEXT" on detail view | Also filterable by `printer_id`. Items with `printer_id: null` + `target_model: "A1"` can go to either printer. `print_time_seconds` is seconds. |
| `POST /printers/{id}/clear-plate` | CLEAR BED button | **Side effect: tells the scheduler the plate is empty, so it may immediately start the next queued print on that printer.** No MQTT is sent to the printer itself. That is why the button needs a second "TAP TO CONFIRM" tap within 5 s. Do not add any automatic calls to it. The simulator refuses all POSTs. |

States seen: `RUNNING, PREPARE, PAUSE, FINISH, IDLE, FAILED`. Developed against two Bambu A1 with AMS lite.

The OpenAPI spec is at `http://<bambuddy-host>:8000/openapi.json` (≈1.1 MB) — check it before
using any new endpoint; other endpoints (pause/resume, bed temp, bed-jog) send real commands
to the printers.

Caution: `GET /printers/` returns each printer's **LAN access code in plain text**. Never display,
log or store those.

---

## 7. Network context

- The display needs: your Wi-Fi (2.4 GHz), and HTTP access to the Bambuddy server (`BAMBUDDY` in config).
- It sets its hostname from `HOSTNAME` in config (default `bamhud` → mDNS `bamhud.local`, if your network
  passes mDNS). Give each unit a unique one when running several.
- Give it a DHCP reservation so its IP never changes.
- For experiments, point the *Bambuddy URL* setting at a test instance rather than production.
- This installation's addresses, NAS and SSH details: `CLAUDE.local.md` (git-ignored).

---

## 8. How `app.py` is organised

1. **Constants & state**: colours (`rgb()` values), fonts `F_NAME/F_BIG/F_MED/F_SMALL/F_LABEL`,
   `STATE_STYLE`, `S` = runtime settings (config defaults ← `settings.json`), `data[0..1]` slim
   status, `queue_cache`, `confirm` (bed-clear confirm deadlines), `flash` (temporary bed-strip
   messages), `view` (`"main"` or printer index 0/1), `cache` (what's on screen), `screen_on`, `last_activity`.
2. **Helpers**: `txt(key, x, y, s, font, fg, bg, w, align)` / `box()` (draw-if-changed), `badge_for`,
   `bed_mode(i)` → `(text, fg, bg, tappable)` — single source of truth for the bed strip on both views.
3. **API**: `slim`, `fetch`, `clear_plate`, `next_jobs` (streaming parser).
4. **Views**: `draw_main`/`tap_main`, `draw_detail`/`draw_filament`/`draw_next`/`tap_detail`, `bed_tap`.
5. **`App`**: `status()`, `apply(form)`, `screen(on)` — the only interface `web.py` uses.
6. **`run()`** (25 ms tick): touch → poll Bambuddy every `S["poll"]` s (+ queue every 30 s on the
   detail view) → expire confirm/flash → sleep check → `web.poll()`.

Layout reference — main view: cards at x=4 and x=162, 154×226 from y=7, inner text x0+8, width 138.
Name y10 (F_NAME), badge box y42 h17 (F_LABEL at y44), % y63 (F_BIG), bar y108 h6, file y122 (F_SMALL),
LEFT label y152 / value y146 (F_MED right-aligned), LAYER label y176 / value y170, bed strip y196 h30.
Detail view: header 0–34 (name F_NAME y3, badge 212,9 100×17), file y40, bar y62 h8 w230, % F_MED at
(246,56) right-aligned in 66, lines y80/98/116 (F_SMALL), swatches y136 (4 × 72×36, pitch 78), NEXT
label y183 + 2 job rows y180/197 (name x52 w196, time x252 w60 right), buttons y216 h22 (bed 8–204, BACK 232–312).

HTTP requests block the loop (~0.1–0.9 s per printer), so touches/web requests during a poll
wait until it finishes. If that ever matters, the next step is uasyncio (mind §5.5).

---

## 9. Recovery / restoring

- **Bad app, web server down**: `tools\deploy.py --usb app.py` with a fixed file.
- **Board won't boot at all**: reflash MicroPython (hold BOOT through connection, see §5.6):
  `& $py -m esptool --port <port> --connect-attempts 30 write-flash --erase-all -z 0x1000 <firmware.bin>`
  then `& $py tools\deploy.py --usb config.py touch_cal.py ili9341.py font.py touch.py web.py index.html f_name.bfn f_big.bfn f_med.bfn f_small.bfn f_label.bfn app.py main.py`.
- **Back to the factory demo**: `& $py -m esptool --port <port> --connect-attempts 30 write-flash 0x0 <your-factory-backup.bin>`` (hold BOOT).

---

## 10. Ideas not yet built

- More than 2 printers: see README *More than two printers?*. Preferred is one unit per pair (`PRINTER_IDS` +
  unique `HOSTNAME` in config). On one screen, the plan is paging pairs of cards (`data` for all printers +
  a `page` index, auto-cycle, jump to printers needing attention) or a 2×2 compact grid.
- Alerts screen (full-screen red on HMS error / failure).
- Pause/resume from the detail view (needs a confirm step like CLEAR BED; check the API first).
- Job thumbnail on the detail view (`/printers/{id}/cover`, PNG — needs a decoder or server-side conversion; mind §5.5).
- Upload page in the browser (`index.html` + PUT `/upload/...`), so updates don't need the PC tool.
- uasyncio rewrite so polling doesn't block touch/web.
