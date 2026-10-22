# Minimal ILI9341 driver for the ESP32-2432S028 ("CYD"), landscape 320x240.
# Text is drawn with font.Font (anti-aliased BFN1 fonts) via ftext().
import time
from machine import Pin, SPI

W, H = 320, 240


def rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)  # RGB565; sent high byte first


class Display:
    def __init__(self):
        self.spi = SPI(1, baudrate=40_000_000, polarity=0, phase=0,
                       sck=Pin(14), mosi=Pin(13), miso=Pin(12))
        self.cs = Pin(15, Pin.OUT, value=1)
        self.dc = Pin(2, Pin.OUT, value=0)
        self.bl = Pin(21, Pin.OUT, value=0)   # steady on/off: PWM dimming shimmers
        self._a = bytearray(4)                # reused for window coordinates (less heap churn)
        self._init()

    def _cmd(self, c, data=None):
        self.cs(0)
        self.dc(0)
        self.spi.write(bytes([c]))
        if data:
            self.dc(1)
            self.spi.write(data)
        self.cs(1)

    def _init(self):
        self._cmd(0x01)
        time.sleep_ms(120)
        self._cmd(0xCF, b"\x00\xC1\x30")
        self._cmd(0xED, b"\x64\x03\x12\x81")
        self._cmd(0xE8, b"\x85\x00\x78")
        self._cmd(0xCB, b"\x39\x2C\x00\x34\x02")
        self._cmd(0xF7, b"\x20")
        self._cmd(0xEA, b"\x00\x00")
        self._cmd(0xC0, b"\x23")
        self._cmd(0xC1, b"\x10")
        self._cmd(0xC5, b"\x3E\x28")
        self._cmd(0xC7, b"\x86")
        self._cmd(0x36, b"\x20")        # landscape (flipped vs factory 0xE8), RGB order: BGR bit off
        self._cmd(0x3A, b"\x55")        # 16-bit colour
        self._cmd(0xB1, b"\x00\x18")
        self._cmd(0xB6, b"\x08\x82\x27")
        self._cmd(0x26, b"\x01")
        self._cmd(0x21)                 # display inversion on (this panel needs it)
        self._cmd(0x11)
        time.sleep_ms(120)
        self._cmd(0x29)
        self.fill(0)

    def brightness(self, pct):
        self.bl(1 if pct > 0 else 0)

    def _window(self, x, y, w, h):
        a = self._a
        a[0], a[1], a[2], a[3] = x >> 8, x & 255, (x + w - 1) >> 8, (x + w - 1) & 255
        self._cmd(0x2A, a)
        a[0], a[1], a[2], a[3] = y >> 8, y & 255, (y + h - 1) >> 8, (y + h - 1) & 255
        self._cmd(0x2B, a)
        self._cmd(0x2C)

    def rect(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self._window(x, y, w, h)
        row = bytes([color >> 8, color & 255]) * w
        self.cs(0)
        self.dc(1)
        for _ in range(h):
            self.spi.write(row)
        self.cs(1)

    def fill(self, color):
        self.rect(0, 0, W, H, color)

    def ftext(self, x, y, s, font, fg, bg, w=None, align=0):
        """Anti-aliased text with a font.Font. If w is given the whole field [x, x+w) is
        painted (text aligned 0=left, 1=centre, 2=right, rest filled with bg), so a
        shorter string cleanly replaces a longer one. Returns the text width."""
        if w is not None:
            s = font.fit(s, w)
        tw = font.width(s)
        h = font.height
        if w is not None:
            lead = 0 if align == 0 else (w - tw) // 2 if align == 1 else w - tw
            self.rect(x, y, lead, h, bg)
            self.rect(x + lead + tw, y, w - lead - tw, h, bg)
            x += lead
        for ch in s:
            gw, buf = font.glyph(ch, fg, bg)
            if gw:
                self._window(x, y, gw, h)
                self.cs(0)
                self.dc(1)
                self.spi.write(buf)
                self.cs(1)
            x += gw
        return tw
