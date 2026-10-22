# Copy this file to config.py and fill it in. config.py is git-ignored: it holds your
# Wi-Fi and web passwords and is copied onto the board, never into the repository.
WIFI_SSID = "your-2.4GHz-ssid"      # the ESP32 cannot join 5 GHz networks
WIFI_PASSWORD = "your-wifi-password"
BAMBUDDY = "http://192.168.1.50:8000/api/v1"   # your Bambuddy server + /api/v1
PRINTER_IDS = (1, 2)                # Bambuddy printer ids, up to 8, in screen order (row by row)
LAYOUT = 2                          # printers per screen: 2 (large cards), 4 (quarters) or 8 (compact).
                                    # Also changeable on the web page; omit to pick from len(PRINTER_IDS).
HOSTNAME = "bamhud"                 # network name -> http://bamhud.local/ ; unique per unit (bamhud-2, ...)
POLL_SECONDS = 4                    # how often to refresh printer status
WIFI_TXPOWER = 13                   # dBm; lower = smaller current spikes (see CLAUDE.md, flicker)
IDLE_OFF_MINUTES = 15               # backlight off after this long with no prints, alerts or touches
WEB_PASSWORD = "change-me"          # protects settings + code uploads on the web page. Use a strong one.
DEBUG = False                       # True: log taps, redraws and timings on the USB serial console
