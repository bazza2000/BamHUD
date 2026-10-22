"""Print the board's serial output for N seconds (default 30).

    python tools/serial_log.py [seconds] [--port COM3]

Opening the port resets the board, so you see a fresh boot. Set DEBUG = True in
config.py to log taps, redraws and timings.
"""
import sys
import time
import serial

secs = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else 30
port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "COM3"
s = serial.Serial(port, 115200, timeout=0.5)
end = time.time() + secs
while time.time() < end:
    chunk = s.read(2000)
    if chunk:
        sys.stdout.write(chunk.decode("utf8", "replace"))
        sys.stdout.flush()
