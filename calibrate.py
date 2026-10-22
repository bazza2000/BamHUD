# One-off touch calibration: tap the centre of each crosshair. Writes touch_cal.py.
import time
from ili9341 import Display, rgb
from touch import Touch
from font import Font

d = Display()
d.brightness(100)
t = Touch()
F = Font("f_small.bfn")
BG, FG, HI = rgb(10, 12, 18), rgb(240, 240, 245), rgb(255, 180, 40)


def target(x, y, label):
    d.fill(BG)
    d.ftext(60, 110, label, F, FG, BG)
    d.rect(x - 10, y, 21, 1, HI)
    d.rect(x, y - 10, 1, 21, HI)
    d.rect(x - 2, y - 2, 5, 5, HI)


def tap():
    while t.raw() is None:          # wait for press
        time.sleep_ms(20)
    time.sleep_ms(80)
    samples = []
    while True:
        r = t.raw()
        if r is None:
            break
        samples.append(r)
        time.sleep_ms(15)
    if not samples:
        return tap()
    samples.sort(key=lambda s: s[0]); rx = samples[len(samples) // 2][0]
    samples.sort(key=lambda s: s[1]); ry = samples[len(samples) // 2][1]
    time.sleep_ms(300)
    return rx, ry


pts = {}
for name, x, y in (("TL", 20, 20), ("TR", 300, 20), ("BL", 20, 220)):
    target(x, y, "Tap the crosshair  (%s)" % name)
    pts[name] = tap()
    print("CAL", name, pts[name])

tl, tr, bl = pts["TL"], pts["TR"], pts["BL"]
swap = abs(tr[1] - tl[1]) > abs(tr[0] - tl[0])
if swap:
    cal = (True, tl[1], tr[1], tl[0], bl[0])
else:
    cal = (False, tl[0], tr[0], tl[1], bl[1])
with open("touch_cal.py", "w") as f:
    f.write("CAL = %r\n" % (cal,))
print("CAL RESULT", cal)
d.fill(BG)
d.ftext(60, 110, "Calibrated - restarting", F, FG, BG)
