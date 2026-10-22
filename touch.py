# XPT2046 resistive touch on the CYD's own SPI pins (CLK 25, MOSI 32, MISO 39, CS 33).
from machine import Pin, SoftSPI

PRESS_Z = 120          # Z1 pressure reading above this counts as a touch


class Touch:
    def __init__(self, cal=None):
        self.spi = SoftSPI(baudrate=1_000_000, polarity=0, phase=0,
                           sck=Pin(25), mosi=Pin(32), miso=Pin(39))
        self.cs = Pin(33, Pin.OUT, value=1)
        self.cal = cal   # (swap, x20, x300, y20, y220): raw readings at those screen coords
        self._buf = bytearray(3)

    def _read(self, cmd):
        self.spi.write_readinto(bytes([cmd, 0, 0]), self._buf)
        return ((self._buf[1] << 8) | self._buf[2]) >> 3

    def raw(self):
        """Median-filtered (rx, ry, z) while pressed, else None."""
        self.cs(0)
        z = self._read(0xB1)
        if z < PRESS_Z:
            self.cs(1)
            return None
        xs, ys = [], []
        for _ in range(5):
            xs.append(self._read(0xD1))
            ys.append(self._read(0x91))
        self._read(0x90)          # power-down command so PENIRQ re-arms
        self.cs(1)
        xs.sort()
        ys.sort()
        return xs[2], ys[2], z

    def point(self):
        """Screen (x, y) while pressed, else None."""
        r = self.raw()
        if r is None or self.cal is None:
            return None
        swap, x20, x300, y20, y220 = self.cal
        a, b = (r[1], r[0]) if swap else (r[0], r[1])
        x = 20 + (a - x20) * 280 // (x300 - x20)
        y = 20 + (b - y20) * 200 // (y220 - y20)
        return min(319, max(0, x)), min(239, max(0, y))
