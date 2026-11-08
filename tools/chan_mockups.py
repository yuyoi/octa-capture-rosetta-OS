"""Mockups of the Rosetta Channel (gain) page at the OCTA's real LCD size (128x64, 1 bit).
Fonts are stand-ins for the unit's own (6x10 text, 9x21 big digits, 6x6 small).
python tools/chan_mockups.py -> art/mockups/chan_pages.png (+ one PNG per version)"""
import math
import os

from PIL import Image, ImageDraw

W, H = 128, 64
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUT = os.path.join(ROOT, 'art', 'mockups')

# 5x7 text font (stand-in for the unit's 6x10, font 18)
F5 = {
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11110', '00001', '00001', '01110', '00001', '00001', '11110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
    'A': ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
    'B': ['11110', '10001', '10001', '11110', '10001', '10001', '11110'],
    'C': ['01110', '10001', '10000', '10000', '10000', '10001', '01110'],
    'D': ['11110', '10001', '10001', '10001', '10001', '10001', '11110'],
    'E': ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
    'G': ['01110', '10001', '10000', '10111', '10001', '10001', '01111'],
    'H': ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
    'I': ['01110', '00100', '00100', '00100', '00100', '00100', '01110'],
    'L': ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
    'N': ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
    'O': ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
    'P': ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
    'R': ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
    'S': ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
    'T': ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
    'U': ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
    'V': ['10001', '10001', '10001', '10001', '10001', '01010', '00100'],
    'M': ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
    'X': ['10001', '10001', '01010', '00100', '01010', '10001', '10001'],
    '.': ['00000', '00000', '00000', '00000', '00000', '01100', '01100'],
    '-': ['00000', '00000', '00000', '11111', '00000', '00000', '00000'],
    '+': ['00000', '00100', '00100', '11111', '00100', '00100', '00000'],
    ' ': ['00000'] * 7,
}
F3 = {  # 3x5 small font (stand-in for font 12)
    '0': '111101101101111', '1': '010110010010111', '2': '111001111100111', '3': '111001111001111',
    '4': '101101111001001', '5': '111100111001111', '6': '111100111101111', '7': '111001010010010',
    '8': '111101111101111', '9': '111101111001111', '-': '000000111000000', '+': '000010111010000',
    ' ': '000000000000000', 'D': '110101101101110', 'B': '110101110101110', 'G': '011100101101011',
    'I': '111010010010111', 'N': '110101101101101', 'V': '101101101101010', 'U': '101101101101111',
    'P': '110101110100100', 'R': '110101110101101', 'E': '111100110100111', 'A': '010101111101101',
    'K': '101101110101101', 'C': '011100100100011', 'H': '101101111101101', 'L': '100100100100111',
    'M': '101111111101101', 'O': '010101101101010', 'T': '111010010010010', 'S': '011100010001110',
}

GMIN, GMAX = 0, 44          # stand-in preamp range in dB; the code reads the real one from the unit
GAIN = 29.5
VU, HOLD = -11.0, -7.0      # dBFS
PH48 = True


class Lcd:
    def __init__(self):
        self.im = Image.new('1', (W, H), 0)
        self.d = ImageDraw.Draw(self.im)

    def px(self, x, y, on=1):
        if 0 <= x < W and 0 <= y < H:
            self.im.putpixel((x, y), on)

    def text(self, x, y, s, on=1):           # 6x10 cell, 5x7 glyph
        for ch in s:
            for r, row in enumerate(F5.get(ch, F5[' '])):
                for c, b in enumerate(row):
                    if b == '1':
                        self.px(x + c, y + 1 + r, on)
            x += 6
        return x

    def small(self, x, y, s, on=1):          # 4x6 cell
        for ch in s:
            g = F3.get(ch, F3[' '])
            for i, b in enumerate(g):
                if b == '1':
                    self.px(x + i % 3, y + i // 3, on)
            x += 4
        return x

    def big(self, x, y, s, on=1):            # ~10x21 digits (stand-in for font 6)
        for ch in s:
            g = F5.get(ch, F5[' '])
            if ch == '.':
                self.rect(x + 1, y + 17, x + 3, y + 19, fill=on, outline=on)
                x += 6
                continue
            for r, row in enumerate(g):
                for c, b in enumerate(row):
                    if b == '1':
                        self.rect(x + c * 2, y + r * 3, x + c * 2 + 1, y + r * 3 + 2, fill=on, outline=on)
            x += 12
        return x

    def tw(self, s):
        return len(s) * 6 - 1

    def bw(self, s):
        return sum(6 if c == '.' else 12 for c in s) - 2

    def rect(self, x0, y0, x1, y1, fill=None, outline=1):
        self.d.rectangle([x0, y0, x1, y1], fill=fill, outline=outline)

    def invert(self, x0, y0, x1, y1):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.px(x, y, 1 - self.im.getpixel((x, y)))

    def line(self, x0, y0, x1, y1, on=1):
        self.d.line([x0, y0, x1, y1], fill=on)


def gfrac(g=GAIN):
    return (g - GMIN) / (GMAX - GMIN)


def vfrac(db):
    return max(0.0, min(1.0, (db + 60) / 60))


def gain_str():
    return f'{GAIN:.1f}'


def header_bar(l, ch=1, tags=True):
    """inverted title strip: CH n left, tags right"""
    l.rect(0, 0, 127, 10, fill=1, outline=1)
    l.text(2, 1, f'CH {ch}', on=0)
    l.text(36, 1, 'GAIN', on=0)
    if tags and PH48:
        l.rect(104, 1, 126, 9, fill=0, outline=0)
        l.text(105, 1, '48V')


def seg_vu_h(l, x0, y0, x1, y1, db, hold, seg=3, gap=1):
    """horizontal segmented VU, -60..0 dBFS, peak-hold segment, red-zone segments hollow"""
    n = (x1 - x0 + gap) // (seg + gap)
    lit = round(vfrac(db) * n)
    hseg = round(vfrac(hold) * n)
    for i in range(n):
        x = x0 + i * (seg + gap)
        zone = (i + 0.5) / n * 60 - 60
        if i < lit:
            l.rect(x, y0, x + seg - 1, y1, fill=1, outline=1)
        elif i == hseg - 1:
            l.rect(x, y0, x + seg - 1, y1, fill=1, outline=1)
        elif zone > -6:
            l.rect(x, y0, x + seg - 1, y1, fill=0, outline=1)
        else:
            l.px(x + seg // 2, y1)


def seg_vu_v(l, x0, y0, x1, y1, db, hold, seg=2, gap=1):
    n = (y1 - y0 + gap) // (seg + gap)
    lit = round(vfrac(db) * n)
    hseg = round(vfrac(hold) * n)
    for i in range(n):
        y = y1 - i * (seg + gap) - seg + 1
        zone = (i + 0.5) / n * 60 - 60
        if i < lit or i == hseg - 1:
            l.rect(x0, y, x1, y + seg - 1, fill=1, outline=1)
        elif zone > -6:
            l.rect(x0, y, x1, y + seg - 1, fill=0, outline=1)
        else:
            l.px(x0, y)
            l.px(x1, y)


# ---------------- A: console fader ----------------
def v_fader():
    l = Lcd()
    header_bar(l)
    s = gain_str()
    x = 64 - (l.bw(s) + 4 + l.tw('DB')) // 2
    xe = l.big(x, 13, s)
    l.text(xe + 3, 25, 'DB')
    # gain rail: y 37..43, handle travels 4..123
    y = 40
    x0, x1 = 4, 123
    hx = round(x0 + gfrac() * (x1 - x0))
    l.line(x0, y, x1, y)
    l.rect(x0, y - 1, hx, y + 1, fill=1, outline=1)          # filled part of the rail
    for g in range(GMIN, GMAX + 1, 5):
        tx = round(x0 + (g - GMIN) / (GMAX - GMIN) * (x1 - x0))
        l.line(tx, y + 3, tx, y + (5 if g % 10 == 0 else 3))
    l.rect(hx - 3, y - 4, hx + 3, y + 4, fill=0, outline=1)   # fader cap
    l.line(hx, y - 3, hx, y + 3)
    # VU
    seg_vu_h(l, 4, 50, 123, 55, VU, HOLD)
    l.small(4, 58, '-60')
    l.small(43, 58, '-40')
    l.small(83, 58, '-20')
    l.small(120, 58, '0')
    return l


# ---------------- B: twin verticals ----------------
def v_vertical():
    l = Lcd()
    # left: gain fader, x 2..14
    l.small(2, 0, 'GAIN')
    y0, y1 = 10, 60
    l.line(8, y0, 8, y1)
    hy = round(y1 - gfrac() * (y1 - y0))
    l.rect(7, hy, 9, y1, fill=1, outline=1)
    for g in range(GMIN, GMAX + 1, 10):
        ty = round(y1 - (g - GMIN) / (GMAX - GMIN) * (y1 - y0))
        l.line(2, ty, 4, ty)
        l.line(12, ty, 14, ty)
    l.rect(3, hy - 3, 13, hy + 3, fill=1, outline=1)
    l.line(4, hy, 12, hy, on=0)
    # middle: big channel + value
    l.text(24, 0, 'CH')
    l.big(38, 0, '1')
    if PH48:
        l.rect(70, 0, 92, 9, fill=1, outline=1)
        l.text(71, 1, '48V', on=0)
    l.line(20, 23, 98, 23)
    s = gain_str()
    xe = l.big(22, 30, s)
    l.text(xe + 3, 42, 'DB')
    l.small(22, 56, 'PRE GAIN')
    # right: VU, x 108..120
    l.small(106, 0, 'IN')
    seg_vu_v(l, 108, 8, 116, 61, VU, HOLD)
    for db in (0, -12, -24, -48):
        ty = round(61 - vfrac(db) * 53)
        l.line(118, ty, 120, ty)
    l.small(121, 7, '0')
    return l


# ---------------- C: knob ----------------
def v_knob():
    l = Lcd()
    cx, cy, r = 26, 30, 20
    a0, a1 = math.radians(225), math.radians(-45)       # 270 deg sweep, 0 dB at 7 o'clock
    steps = 24
    for i in range(steps + 1):
        t = i / steps
        a = a0 + (a1 - a0) * t
        lit = t <= gfrac()
        ri = r - (6 if lit else 2)
        for dd in ((0, 0.04) if lit else (0,)):
            aa = a + dd
            x_o, y_o = cx + r * math.cos(aa), cy - r * math.sin(aa)
            x_i, y_i = cx + ri * math.cos(aa), cy - ri * math.sin(aa)
            l.line(round(x_i), round(y_i), round(x_o), round(y_o))
    l.d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], outline=1)
    a = a0 + (a1 - a0) * gfrac()
    l.line(cx + round(4 * math.cos(a)), cy - round(4 * math.sin(a)), cx + round(11 * math.cos(a)), cy - round(11 * math.sin(a)))
    l.small(10, 53, '0')
    l.small(36, 53, str(GMAX))
    # right panel
    l.text(56, 0, 'CH 1')
    if PH48:
        l.rect(104, 0, 126, 9, fill=1, outline=1)
        l.text(105, 1, '48V', on=0)
    s = gain_str()
    xe = l.big(56, 13, s)
    l.text(xe + 2, 25, 'DB')
    l.small(56, 37, 'GAIN')
    seg_vu_h(l, 56, 46, 127, 53, VU, HOLD, seg=2)
    l.small(56, 57, '-60')
    l.small(86, 57, '-30')
    l.small(124, 57, '0')
    return l


# ---------------- D: tabs + bars ----------------
def v_tabs():
    l = Lcd()
    lv = [VU, -24, -60, -60, -18, -60, -40, -60]
    for i in range(8):
        x = i * 16
        if i == 0:
            l.rect(x, 0, x + 15, 10, fill=1, outline=1)
            l.text(x + 5, 1, str(i + 1), on=0)
        else:
            l.rect(x, 0, x + 15, 10, fill=0, outline=1)
            l.text(x + 5, 1, str(i + 1))
            w = round(vfrac(lv[i]) * 13)
            if w:
                l.line(x + 1, 9, x + w, 9)
    l.line(0, 10, 127, 10)
    l.line(1, 10, 14, 10, on=0)                      # open tab: no line under the selected one
    s = gain_str()
    xe = l.big(3, 15, s)
    l.text(xe + 3, 27, 'DB')
    if PH48:
        l.rect(104, 14, 126, 23, fill=1, outline=1)
        l.text(105, 15, '48V', on=0)
    l.small(104, 28, 'GAIN')
    # gain ladder: 22 blocks
    n = 22
    lit = round(gfrac() * n)
    for i in range(n):
        x = 3 + i * 5
        h = 2 + i * 5 // n
        top = 45 - h
        if i < lit:
            l.rect(x, top, x + 3, 45, fill=1, outline=1)
        else:
            l.line(x, 45, x + 3, 45)
    seg_vu_h(l, 3, 50, 124, 56, VU, HOLD)
    l.small(3, 59, '-60')
    l.small(62, 59, '-30')
    l.small(121, 59, '0')
    return l


VERSIONS = [
    ('A  FADER: console fader cap on a rail + segment VU', v_fader()),
    ('B  VERTICAL: gain fader left, VU right, big number middle', v_vertical()),
    ('C  KNOB: lit arc like a pot, number + VU right', v_knob()),
    ('D  TABS: channel tabs 1-8 with mini meters, gain ladder', v_tabs()),
]


def lcd_render(l, s=5, gap=1):
    on, off, bg = (22, 26, 34), (196, 206, 214), (150, 162, 172)
    im = Image.new('RGB', (W * s, H * s), bg)
    d = ImageDraw.Draw(im)
    for y in range(H):
        for x in range(W):
            d.rectangle([x * s, y * s, x * s + s - 1 - gap, y * s + s - 1 - gap], fill=on if l.im.getpixel((x, y)) else off)
    return im


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    tiles = []
    for name, l in VERSIONS:
        t = lcd_render(l)
        t.save(os.path.join(OUT, 'chan_' + name.split()[0] + '.png'))
        tiles.append((name, t))
    tw, th = tiles[0][1].size
    sheet = Image.new('RGB', (2 * tw + 60, 2 * (th + 40) + 20), (30, 30, 34))
    ds = ImageDraw.Draw(sheet)
    for i, (name, t) in enumerate(tiles):
        x, y = 20 + (i % 2) * (tw + 20), 20 + (i // 2) * (th + 40)
        sheet.paste(t, (x, y + 22))
        ds.text((x, y + 4), name, fill=(230, 230, 230))
    p = os.path.join(OUT, 'chan_pages.png')
    sheet.save(p)
    print('wrote', p)
