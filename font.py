# Anti-aliased bitmap fonts (BFN1 files made by tools/make_font.py).
# Runs on the board and in CPython (tools/simulate.py), so: no machine/micropython imports.

_cache = {}
_cache_bytes = 0
CACHE_LIMIT = 5_000       # bytes of rendered glyphs kept; Python heap growth starves Wi-Fi (CLAUDE.md)
MAX_TABLES = 3            # blend tables (1 KB each) kept for recent fg/bg colour pairs


def _split(c):
    return (c >> 11) & 31, (c >> 5) & 63, c & 31


def _blend_table(fg, bg):
    """One flat 1 KB block: packed byte i (two 4-bit alpha pixels) -> RGB565 bytes at [i*4:i*4+4].
    Kept as a single bytes object on purpose: a list of 256 small bytes objects costs ~5 KB
    of heap per colour pair, and Python heap growth starves the Wi-Fi driver (CLAUDE.md)."""
    fr, fgr, fb = _split(fg)
    br, bgr, bb = _split(bg)
    pal = bytearray(32)
    for a in range(16):
        r = br + (fr - br) * a // 15
        g = bgr + (fgr - bgr) * a // 15
        b = bb + (fb - bb) * a // 15
        c = (r << 11) | (g << 5) | b
        pal[a * 2] = c >> 8
        pal[a * 2 + 1] = c & 255
    t = bytearray(1024)
    for i in range(256):
        hi, lo = (i >> 4) * 2, (i & 15) * 2
        t[i * 4:i * 4 + 2] = pal[hi:hi + 2]
        t[i * 4 + 2:i * 4 + 4] = pal[lo:lo + 2]
    return bytes(t)


_tables = {}


class Font:
    def __init__(self, path):
        self.f = open(path, "rb")
        h = self.f.read(12)
        assert h[:4] == b"BFN1", path
        self.height, self.first, self.count, self.baseline, self.space = h[4], h[5], h[6], h[7], h[8]
        idx = self.f.read(5 * self.count)
        self.offs = []
        self.widths = bytearray(self.count)
        for i in range(self.count):
            o = i * 5
            self.offs.append(idx[o] | idx[o + 1] << 8 | idx[o + 2] << 16 | idx[o + 3] << 24)
            self.widths[i] = idx[o + 4]
        self.data_start = 12 + 5 * self.count
        self.id = path

    def _i(self, ch):
        i = ord(ch) - self.first
        if 0 <= i < self.count and self.widths[i]:
            return i
        i = ord("?") - self.first
        return i if 0 <= i < self.count and self.widths[i] else -1

    def char_width(self, ch):
        if ch == " ":
            return self.space
        i = self._i(ch)
        return self.widths[i] if i >= 0 else self.space

    def width(self, s):
        return sum(self.char_width(c) for c in s)

    def fit(self, s, w, ell="..."):
        """Truncate s (adding an ellipsis) so it fits in w pixels."""
        if self.width(s) <= w:
            return s
        ew = self.width(ell)
        while s and self.width(s) + ew > w:
            s = s[:-1]
        return s.rstrip() + ell

    def glyph(self, ch, fg, bg):
        """(width, RGB565 big-endian bytes) for ch blended from bg to fg. Cached."""
        global _cache_bytes
        key = (self.id, ch, fg, bg)
        g = _cache.get(key)
        if g:
            return g
        if ch == " ":
            w = self.space
            buf = bytes((bg >> 8, bg & 255)) * (w * self.height)
        else:
            i = self._i(ch)
            if i < 0:
                return self.glyph(" ", fg, bg)
            w = self.widths[i]
            rb = (w + 1) // 2
            self.f.seek(self.data_start + self.offs[i])
            raw = self.f.read(rb * self.height)
            tk = (fg, bg)
            t = _tables.get(tk)
            if t is None:
                if len(_tables) >= MAX_TABLES:
                    _tables.clear()
                t = _tables[tk] = _blend_table(fg, bg)
            tv = memoryview(t)
            out = bytearray(w * 2 * self.height)
            line = bytearray(rb * 4)
            row = w * 2
            o = 0
            p = 0
            for y in range(self.height):
                q = 0
                for k in range(rb):
                    b = raw[p] * 4
                    line[q:q + 4] = tv[b:b + 4]
                    q += 4
                    p += 1
                out[o:o + row] = line[:row]
                o += row
            buf = out
        if _cache_bytes > CACHE_LIMIT:
            _cache.clear()
            _cache_bytes = 0
        _cache[key] = (w, buf)
        _cache_bytes += len(buf)
        return w, buf
