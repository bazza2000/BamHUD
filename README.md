# BamHUD

**A heads-up display for [Bambuddy](https://github.com/maziggy/bambuddy).** A desk touchscreen for Bambu Lab printers managed by Bambuddy,
built on the cheap **ESP32-2432S028 "Cheap Yellow Display"** (CYD) and MicroPython.

| 2 printers (default) | Detail view (tap a printer) | Clear-bed confirm |
|---|---|---|
| ![2-printer layout](docs/screenshot_main.png) | ![Detail view](docs/screenshot_detail.png) | ![Confirm](docs/screenshot_confirm.png) |

| 4 printers | 8 printers |
|---|---|
| ![4-printer layout](docs/screenshot_4up.png) | ![8-printer layout](docs/screenshot_8up.png) |

*(Screenshots are rendered by `tools/simulate.py --demo` from the real display code, using invented data.)*

## Features

- **2, 4 or 8 printers per screen**: large cards for two printers, quarters for four, or a compact
  2 × 4 grid for eight, switchable from the web page. Each shows name, state, progress and time left.
- **Bed status + CLEAR BED button**: when Bambuddy is waiting for a plate to be cleared, an amber
  button appears; tap it, then **TAP TO CONFIRM** within 5 s, and Bambuddy's scheduler can start
  the next queued job.
- **Detail view**: temperatures (actual / target), speed, fan, Wi-Fi signal, **AMS filament** with real
  spool colours and a remaining-filament gauge, the next queued jobs with print times, HMS errors.
- **Sleeps when idle** (default 15 min with nothing printing). The first tap only wakes it; it also
  wakes itself when a print starts, finishes and needs clearing, or reports an error.
- **Built-in web page** with live status, device health, settings (poll interval, sleep time,
  Bambuddy URL), screen on/off and reboot.
- **Over-the-air updates**: push new code over Wi-Fi; uploads are password-protected and validated
  on the device before they replace the running files.
- **Smooth anti-aliased text** (Roboto / Roboto Condensed) with fixed-width digits, flicker-free updates.
- **PC simulator** that renders the real display code to PNGs, so layouts can be checked without the hardware.

## What you need

| | |
|---|---|
| Display | ESP32-2432S028 / 2432S028R "Cheap Yellow Display" (2.8", 320×240, ILI9341 + XPT2046 resistive touch, CH340 USB) |
| Server | A running **Bambuddy** instance reachable over HTTP from the display, with **auth disabled** on your LAN (the display sends no API key) |
| Network | 2.4 GHz Wi-Fi (the ESP32 can't join 5 GHz) |
| PC | Python 3.9+ and a USB cable. Windows, macOS or Linux. (On Windows, [Thonny](https://thonny.org)'s bundled Python is fine.) |

> Tested with MicroPython **v1.29.0** and Bambuddy **v1.2.5.7** on two Bambu Lab A1 printers with AMS lite.
> Other CYD revisions may need different display settings; see *Troubleshooting*.

## Layouts: 2, 4 or 8 printers

BamHUD has three main-screen layouts. All of them open the same full-screen **detail view** when you
tap a printer, and all of them refresh every printer in turn.

| Layout | Each printer shows | Tapping |
|---|---|---|
| **2** (default) | Large card: name, state badge, big %, progress bar, file name, time left, layer, bed strip | Card → detail view. Amber **CLEAR BED** strip → clear (with confirm). |
| **4** | Quarter: coloured state edge, name, big %, bar, state + time left, bed strip | Cell → detail view. Amber **CLEAR BED** strip → clear (with confirm). |
| **8** | Compact row: coloured state edge, name, %, thin bar, time left (or state), bed status | Cell → detail view. Clear the bed from the detail view (cells are too small for a safe button). |

The coloured left edge in the 4- and 8-printer layouts is the printer's state: blue printing, green
done/idle, amber paused, red error/failed/offline. Idle and offline printers show "–" instead of a percentage.

### Setting it up

1. List your printers in `config.py`, in screen order (row by row: left, right, next row…), using
   Bambuddy's printer ids (`GET /api/v1/printers/` lists them):
   ```python
   PRINTER_IDS = (1, 2, 3, 4, 5, 6)   # up to 8
   LAYOUT = 8                         # 2, 4 or 8; leave it out to pick automatically from the count
   ```
2. Deploy `config.py` (`python tools/deploy.py config.py`).
3. Switch layouts any time from the web page: **Settings → Layout → Save settings** (password
   required). The screen redraws immediately. The choice is saved on the display and overrides `LAYOUT`.

Slots without a printer show "empty slot". A layout smaller than your printer list shows the first
2 or 4 printers.

### Things to know

- **Refresh rate.** One printer is fetched at a time, at least 1.2 s apart (a fetch takes ~0.5 s on the
  ESP32), so the screen stays responsive to taps. With 8 printers each one refreshes every ~10 s; with 2,
  every `POLL_SECONDS`. The detail view's job queue is fetched as its own step.
- **Taps during a fetch.** The loop is single-threaded, so a very quick tap that lands exactly during a
  fetch can be missed. A slightly longer press always registers.
- **Memory.** Each printer adds ~1 KB; 8 printers run fine (see *Memory*).
- **More than 8, or displays beside the printers?** Run several BamHUDs, each with its own
  `PRINTER_IDS` and a unique `HOSTNAME` (e.g. `bamhud`, `bamhud-2`), then deploy with
  `--host bamhud-2.local`. Every unit talks to Bambuddy on its own.

## How it works

```
Bambuddy server ──HTTP (poll every 4 s)──▶ ESP32 CYD ──SPI──▶ 320×240 display
   /api/v1/printers/{id}/status              app.py          XPT2046 touch
   /api/v1/queue/?status=pending             web.py ◀──HTTP── your browser / tools/deploy.py
   /api/v1/printers/{id}/clear-plate (only on confirmed tap)
```

- `main.py` → `app.run()`: one cooperative loop that reads touch, polls Bambuddy, redraws only the
  fields that changed, handles sleep, and services the web server.
- Text uses pre-rendered 4-bit anti-aliased fonts (`*.bfn`) blended against the background at draw time.
- Big modules are **precompiled to `.mpy`** on the PC. This matters on the ESP32: see *Memory* below.

---

## Build & first-time setup

### 1. Get the code and PC tools

```bash
git clone https://github.com/<you>/BamHUD.git
cd BamHUD
python -m pip install -r requirements.txt     # esptool, pyserial, mpy-cross 1.29, Pillow
```

`mpy-cross` **must match the MicroPython version on the board** (1.29.x ↔ .mpy format v6.3).
If you flash a different MicroPython version, install the matching `mpy-cross`.

### 2. Find the serial port

Plug the CYD in by USB. It shows up as a CH340 serial port: `COM3`-style on Windows (Device Manager → Ports),
`/dev/ttyUSB0` on Linux, `/dev/cu.wchusbserial*` on macOS. Examples below use `COM3`; substitute yours.

### 3. Back up the factory firmware (recommended)

On these boards the automatic reset into download mode often **doesn't work** ("Wrong boot mode
detected (0x13)"). What does work: start the command, then **press and hold the BOOT button** until you
see `Connected`/`Stub flasher running`, then release.

```bash
python -m esptool --port COM3 --connect-attempts 30 read-flash 0 0x400000 cyd-factory-4MB.bin
```

Keep that file somewhere safe; it restores the board exactly as shipped (see *Restoring*).

### 4. Flash MicroPython

Download the **ESP32_GENERIC** firmware (v1.29.0) from https://micropython.org/download/ESP32_GENERIC/
and flash it **in one command** (hold BOOT again while it connects):

```bash
python -m esptool --port COM3 --connect-attempts 30 write-flash --erase-all -z 0x1000 ESP32_GENERIC-20260824-v1.29.0.bin
```

> Don't run `erase-flash` and `write-flash` as two separate commands: the reset after the erase drops
> the board out of download mode and leaves it blank until you hold BOOT again.

### 5. Configure

```bash
cp config.example.py config.py      # Windows: copy config.example.py config.py
```

Edit `config.py`: Wi-Fi SSID/password, your Bambuddy URL (ending in `/api/v1`), the Bambuddy
printer ids to show (`PRINTER_IDS`, up to 8, see *Layouts*), and a **strong `WEB_PASSWORD`**. `config.py` is git-ignored and is only ever
copied to the board.

### 6. First deploy (USB)

```bash
python tools/deploy.py --usb --port COM3 config.py touch_cal.py ili9341.py font.py touch.py web.py index.html f_name.bfn f_big.bfn f_med.bfn f_small.bfn f_label.bfn app.py main.py
```

`deploy.py` compiles `app.py`, `web.py`, `font.py`, `ili9341.py`, `touch.py`, `calibrate.py` to `.mpy`
(in `build/`), uploads them, and soft-reboots the board. The display shows *Connecting to WiFi…*,
then the two printer cards.

### 7. Calibrate the touch screen (if taps land in the wrong place)

`touch_cal.py` holds the calibration of the unit this was developed on, which may be close for yours.
If taps miss:

```bash
python tools/deploy.py --usb --no-reboot calibrate.py
```

Then open a REPL (e.g. Thonny, or `python -m serial.tools.miniterm COM3 115200`), press Ctrl-C a few
times to stop the app, run `import calibrate`, and tap the centre of each of the 3 crosshairs. It writes
`touch_cal.py` on the board and prints `CAL RESULT (...)`. Copy that line into your local `touch_cal.py`,
then reset the board.

### 8. Find the web page

The display registers the hostname `bamhud`: try **http://bamhud.local/**. Otherwise find its
IP in your router's DHCP list (and give it a reservation). The page shows live status and device health,
and lets you change settings, turn the screen on/off and reboot (password = `WEB_PASSWORD`).

---

## Deploying updates (over Wi-Fi)

```bash
python tools/simulate.py                       # optional: preview the screen as PNGs first (sim/)
python tools/deploy.py app.py                  # compile, upload over Wi-Fi to bamhud.local, reboot
python tools/deploy.py --host 192.168.1.60 app.py web.py index.html
python tools/deploy.py --no-reboot f_med.bfn
python tools/deploy.py --usb --port COM3 app.py   # fallback if the Wi-Fi/web server is down
```

On the device, each upload is written to a temporary file and **validated** before it replaces the
live one: `.py` must compile, `.mpy` must be MicroPython v6 bytecode, fonts must be BFN1, and the page must be
HTML. Only known file names are accepted, and the board deletes `X.py` when `X.mpy` arrives (MicroPython
prefers `.py`). A runtime bug in `app.py` can still stop the web server; recover over USB.

Before uploading, `deploy.py` checks the display: it waits until it has been up for 45 s (uploads landing during
the first draw after a reboot can exhaust its Wi-Fi memory) and refuses if Wi-Fi memory is already low
(reboot it or use `--usb`; `--force` overrides).

After an update, check `http://<display>/api/status`: `device.last_error` should be empty and
`device.idf_free` (free Wi-Fi memory) should stay above ~15 KB.

### Adding a new module

Add it to `UPLOADABLE` in `web.py` and to `COMPILE` in `tools/deploy.py`, deploy `web.py` and reboot,
then deploy the new module.

---

## Development

### Simulator

```bash
python tools/simulate.py           # live data from your Bambuddy -> sim/*.png (main, detail per printer, demo states)
python tools/simulate.py --demo    # invented data, no network or config.py needed -> docs/screenshot_*.png
                                  # (2-, 4- and 8-printer layouts, detail view, clear-bed confirm)
```

It imports the real `app.py`, `ili9341.py` and `font.py` with stub hardware: the display's SPI command
stream is emulated into an image. All HTTP **POSTs are refused**, so the simulator can never clear a plate.

### Fonts

Fonts are generated from the TTFs in `fonts/` with `tools/make_font.py`
(4-bit alpha, tight shared height, advance-width glyphs; format documented in the script):

```bash
python tools/make_font.py fonts/RobotoCondensed-Variable.ttf f_name.bfn  --size 28 --wght 700
python tools/make_font.py fonts/Roboto-Variable.ttf          f_big.bfn   --size 52 --wght 700 --chars "0123456789%-"
python tools/make_font.py fonts/Roboto-Variable.ttf          f_med.bfn   --size 20 --wght 600
python tools/make_font.py fonts/RobotoCondensed-Variable.ttf f_small.bfn --size 15 --wght 500 --gamma 1.2
python tools/make_font.py fonts/RobotoCondensed-Variable.ttf f_label.bfn --size 12 --wght 700 --gamma 1.2 --tracking 1
```

### Memory (read before adding features)

On the ESP32, MicroPython's heap and the Wi-Fi driver share the same RAM. The heap starts at 56 KB and,
when full, grows by taking memory from the Wi-Fi driver, **permanently**. A second growth starves Wi-Fi and
networking fails silently (timeouts, DNS errors). The code is shaped around this: precompiled `.mpy`, status
responses slimmed to ~1 KB, the web page streamed from flash, small glyph/colour caches, a streaming queue
parser, and timeouts on every request. Watch `idf_free` on `/api/status` after changes. Details: `CLAUDE.md` §5.5.

### Logs

```bash
python tools/serial_log.py 30 --port COM3      # note: opening the serial port resets the board
```

Set `DEBUG = True` in `config.py` to log taps, redraws and timings.

`CLAUDE.md` is the full developer handbook (pin map, every gotcha, layout coordinates, API fields).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| esptool: *Wrong boot mode detected (0x13)* | Hold **BOOT** while esptool connects (see step 3). |
| White background, dark text (colours inverted) | Display inversion: `_init()` sends `0x20` (inversion off). Some other CYD panels need `0x21` instead. |
| Red and blue swapped (green looks lime, amber looks blue) | MADCTL colour-order bit: `0x36` = `0x28` (BGR) here; try `0x20` (RGB) on other panels. |
| Upside down | MADCTL `0x28` ↔ `0xE8`, then recalibrate touch. |
| Faint flicker | Wi-Fi power-save current spikes dip the backlight; the code sets `pm=PM_NONE` and lower TX power. Keep those. |
| Taps land in the wrong place | Recalibrate (step 7). |
| Screen frozen / web page times out / `ETIMEDOUT` | Wi-Fi memory exhausted. The display restarts itself if this persists (self-heal); check `idf_free` and see *Memory*. Make sure big modules are deployed as `.mpy`. |
| White screen | The board is powered but the app isn't drawing (stuck or crashed during boot). Power-cycle it; if it persists, read `tools/serial_log.py` and redeploy over `--usb`. |
| *Connecting to WiFi…* forever | Wrong SSID/password, or a 5 GHz-only network. |
| Cards say NO DATA | Bambuddy URL wrong/unreachable, or Bambuddy auth is enabled (the display sends no API key). |

## Restoring

- **Factory firmware**: hold BOOT and run
  `python -m esptool --port COM3 --connect-attempts 30 write-flash 0x0 cyd-factory-4MB.bin`
- **Clean reinstall**: repeat steps 4–6.

## Security notes

- Keep the display on your LAN; don't expose it to the internet. The web page's status is open to anyone
  on the network; settings, reboot and code uploads require `WEB_PASSWORD` (sent in plain HTTP).
- The only write the display makes to Bambuddy is `clear-plate`, and only after two taps. Clearing a plate
  lets Bambuddy's scheduler start the next queued print, so make sure the bed really is clear.
- Bambuddy's `GET /printers/` response includes printers' LAN access codes; this project never displays,
  logs or stores it.

## Project layout

```
main.py            boot entry: import app; app.run()
app.py             the application (views, polling, touch, sleep, web API glue)
ili9341.py         display driver        touch.py / touch_cal.py   touch driver + calibration
font.py            anti-aliased font renderer (runs on board and PC)
web.py, index.html web server + page     calibrate.py              touch calibration tool
f_*.bfn            generated fonts       fonts/                    source TTFs + OFL licence
config.example.py  copy to config.py     tools/                    deploy, simulate, make_font, serial_log
docs/              README screenshots    CLAUDE.md                 developer handbook
```

## Credits

- [Bambuddy](https://github.com/maziggy/bambuddy) by maziggy, the self-hosted printer manager this display talks to.
- [MicroPython](https://micropython.org).
- [Roboto / Roboto Condensed](https://github.com/googlefonts/roboto-classic) by Google, under the
  SIL Open Font License 1.1 (`fonts/OFL.txt`); the `.bfn` files are bitmap renderings of them under the same licence.
- Not affiliated with Bambu Lab or the Bambuddy project.

## Licence

Code: [MIT](LICENSE). Fonts (`fonts/`, `f_*.bfn`): SIL Open Font License 1.1.
