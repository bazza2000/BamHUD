"""Convert a TrueType font into a BFN1 anti-aliased bitmap font for the display.

    python tools/make_font.py fonts/Roboto-Variable.ttf f_big.bfn --size 48 --wght 700 --chars "0123456789%-"
    python tools/make_font.py fonts/RobotoCondensed-Variable.ttf f_small.bfn --size 15 --wght 500

Glyphs are 4 bits per pixel (16 alpha levels). Every glyph shares one height: the tight
vertical band covering all requested characters, so text of one font lines up at a
fixed y. Each glyph's width is its advance width (bearings included), so strings are
drawn by simply placing glyphs side by side.

BFN1 file layout (little-endian):
    b"BFN1", u8 height, u8 first_char, u8 count, u8 baseline (px from top), u8 space_width, 3 pad
    count x (u32 offset into data, u8 width)     width 0 = glyph missing
    data: per glyph, height rows of ceil(width/2) bytes, high nibble = left pixel
"""
import argparse
import struct
from PIL import Image, ImageDraw, ImageFont

ASCII = "".join(chr(c) for c in range(32, 127))


def load(path, size, wght=None, wdth=None):
    f = ImageFont.truetype(path, size)
    try:
        axes = f.get_variation_axes()
    except OSError:
        axes = []
    if axes:
        vals = []
        for a in axes:
            name = a.get("name", b"")
            name = name.decode() if isinstance(name, bytes) else str(name)
            v = a["default"]
            if name.lower().startswith("weight") and wght:
                v = wght
            if name.lower().startswith("width") and wdth:
                v = wdth
            vals.append(max(a["minimum"], min(a["maximum"], v)))
        f.set_variation_by_axes(vals)
    return f


def render(font, ch, pad):
    """Render one char into an advance-wide strip. Returns (width, L-mode image)."""
    adv = max(1, round(font.getlength(ch)))
    ascent, descent = font.getmetrics()
    img = Image.new("L", (adv + 2 * pad, ascent + descent + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad, pad), ch, font=font, fill=255)
    return adv, img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ttf")
    ap.add_argument("out")
    ap.add_argument("--size", type=int, required=True, help="em size in pixels")
    ap.add_argument("--wght", type=float)
    ap.add_argument("--wdth", type=float)
    ap.add_argument("--chars", default=ASCII)
    ap.add_argument("--gamma", type=float, default=1.0,
                    help=">1 thickens edges slightly (helps small text on TFTs)")
    ap.add_argument("--tracking", type=int, default=0, help="extra px between letters")
    a = ap.parse_args()

    font = load(a.ttf, a.size, a.wght, a.wdth)
    chars = "".join(sorted(set(a.chars) | {" "}))
    first, last = ord(min(chars)), ord(max(chars))
    pad = a.size // 4

    rendered = {}
    top, bottom = 10 ** 6, -1
    for ch in chars:
        adv, img = render(font, ch, pad)
        bbox = img.getbbox()
        if bbox:
            top, bottom = min(top, bbox[1]), max(bottom, bbox[3])
        rendered[ch] = (adv, img)
    height = bottom - top
    ascent, _ = font.getmetrics()
    baseline = ascent + pad - top

    lut = [round(15 * (v / 255) ** (1 / a.gamma)) for v in range(256)]
    index, data = [], bytearray()
    for code in range(first, last + 1):
        ch = chr(code)
        if ch not in rendered:
            index.append((0, 0))
            continue
        adv, img = rendered[ch]
        w = adv + a.tracking
        # crop to the advance box (bearings that overhang are clipped) and the shared band
        crop = img.crop((pad, top, pad + adv, bottom))
        px = crop.load()
        index.append((len(data), w))
        for y in range(height):
            row = [lut[px[x, y]] if x < adv else 0 for x in range(w)]
            if w % 2:
                row.append(0)
            for i in range(0, len(row), 2):
                data.append((row[i] << 4) | row[i + 1])

    count = last - first + 1
    space = rendered[" "][0] + a.tracking
    with open(a.out, "wb") as f:
        f.write(b"BFN1" + bytes([height, first, count, baseline, space, 0, 0, 0]))
        for off, w in index:
            f.write(struct.pack("<IB", off, w))
        f.write(data)

    digits = [rendered[d][0] for d in "0123456789" if d in rendered]
    print("%s: size %d -> height %dpx, baseline %d, %d glyphs, %d bytes%s" % (
        a.out, a.size, height, baseline, len(chars), 12 + 5 * count + len(data),
        "" if not digits else ", digits %s" % ("tabular %dpx" % digits[0] if len(set(digits)) == 1
                                                else "PROPORTIONAL %s" % digits)))


if __name__ == "__main__":
    main()
