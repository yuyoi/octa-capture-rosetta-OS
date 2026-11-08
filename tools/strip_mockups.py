"""Mockups of the Rosetta STRIP pages at the OCTA's real LCD size (128x64, 1 bit), rendered like the LCD.
python tools/strip_mockups.py -> art/mockups/strip_pages.png (+ one PNG per page)"""
import math
import os

from PIL import Image, ImageDraw

W, H = 128, 64
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
OUT = os.path.join(ROOT, 'art', 'mockups')

F3x5 = {  # 3x5 pixel font, rows top -> bottom
    '0': '111101101101111', '1': '010110010010111', '2': '111001111100111', '3': '111001111001111',
    '4': '101101111001001', '5': '111100111001111', '6': '111100111101111', '7': '111001010010010',
    '8': '111101111101111', '9': '111101111001111', 'A': '010101111101101', 'B': '110101110101110',
    'C': '011100100100011', 'D': '110101101101110', 'E': '111100110100111', 'F': '111100110100100',
    'G': '011100101101011', 'H': '101101111101101', 'I': '111010010010111', 'J': '001001001101010',
    'K': '101101110101101', 'L': '100100100100111', 'M': '101111111101101', 'N': '110101101101101',
    'O': '010101101101010', 'P': '110101110100100', 'Q': '010101101110011', 'R': '110101110101101',
    'S': '011100010001110', 'T': '111010010010010', 'U': '101101101101111', 'V': '101101101101010',
    'W': '101101111111101', 'X': '101101010101101', 'Y': '101101010010010', 'Z': '111001010100111',
    ' ': '000000000000000', '.': '000000000000010', '-': '000000111000000', '+': '000010111010000',
    '/': '001001010100100', ':': '000010000010000', '%': '101001010100101', '>': '100010001010100',
    '<': '001010100010001', '^': '010101000000000', 'v': '000000000101010', '|': '010010010010010',
}


class Lcd:
    def __init__(self):
        self.im = Image.new('1', (W, H), 0)
        self.d = ImageDraw.Draw(self.im)

    def text(self, x, y, s, scale=1, on=1):
        for ch in s.upper() if s != 'v' else s:
            g = F3x5.get(ch, F3x5[' '])
            for i, b in enumerate(g):
                if b == '1':
                    px, py = x + (i % 3) * scale, y + (i // 3) * scale
                    self.d.rectangle([px, py, px + scale - 1, py + scale - 1], fill=on)
            x += 4 * scale
        return x

    def width(self, s, scale=1):
        return len(s) * 4 * scale - scale

    def rect(self, x0, y0, x1, y1, fill=None, outline=1):
        self.d.rectangle([x0, y0, x1, y1], fill=fill, outline=outline)

    def invert(self, x0, y0, x1, y1):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.im.putpixel((x, y), 1 - self.im.getpixel((x, y)))

    def vbar(self, x, y0, y1, frac, w=4, down=False, ticks=True):
        self.rect(x, y0, x + w + 1, y1, outline=1)
        h = round((y1 - y0 - 2) * frac)
        if h > 0:
            if down:
                self.rect(x + 1, y0 + 1, x + w, y0 + h, fill=1, outline=1)
            else:
                self.rect(x + 1, y1 - h, x + w, y1 - 1, fill=1, outline=1)
        if ticks:
            for k in range(1, 4):
                yy = y0 + (y1 - y0) * k // 4
                self.im.putpixel((x - 1, yy), 1)

    def hline(self, x0, x1, y):
        self.d.line([x0, y, x1, y], fill=1)


BLOCKS = ['GAIN', 'HPF', 'TUBE', 'LOW', 'PEAK', 'HIGH', 'COMP']
ABBR = ['G', 'H', 'T', 'L', 'P', 'H', 'C']


def block_bar(l, cur, active):
    """footer: the 7 blocks, current one inverted, active ones with an underline dot"""
    x = 2
    for i, a in enumerate(ABBR):
        l.text(x + 3, 58, a)
        if i in active:
            l.hline(x + 3, x + 5, 63)
        if i == cur:
            l.rect(x + 1, 56, x + 7, 62, outline=1)
        x += 13


def meters(l, vin, gr=None):
    """always-on meters on the right: input VU (+ GR bar for the comp)"""
    l.text(104, 1, 'IN')
    l.vbar(105, 8, 54, vin)
    if gr is not None:
        l.text(115, 1, 'GR')
        l.vbar(116, 8, 54, gr, down=True)


def editor(name, idx, params, sel, vin, gr=None, extra=None, top=0, active=(0, 1, 2, 6)):
    l = Lcd()
    l.text(1, 1, 'IN 1')
    l.text(22, 1, name)
    l.text(78, 1, f'{idx + 1}/7')
    l.hline(0, 100, 7)
    y = 10
    if extra:
        extra(l)
        y = 32
    rows = (55 - y) // 7
    vis = params[top:top + rows]
    for i, (k, v) in enumerate(vis):
        yy = y + i * 7
        l.text(6, yy, k)
        l.text(94 - l.width(v), yy, v)
        if top + i == sel:
            l.text(1, yy, '>')
            l.rect(4, yy - 2, 96, yy + 6, outline=1)
    if top > 0:
        l.text(98, y, '^')
    if top + rows < len(params):
        l.text(98, y + (rows - 1) * 7, 'v')
    meters(l, vin, gr)
    block_bar(l, idx, active)
    return l


def peak_curve(l):
    """tiny log-frequency response of the PEAK band (2.5 kHz, +4 dB, Q 1) in a 98x20 box"""
    l.rect(1, 9, 99, 29, outline=1)
    fs, f0, g, q = 44100, 2500, 4, 1.0
    A, w0 = 10 ** (g / 40), 2 * math.pi * f0 / fs
    al = math.sin(w0) / (2 * q)
    b = [1 + al * A, -2 * math.cos(w0), 1 - al * A]
    a = [1 + al / A, -2 * math.cos(w0), 1 - al / A]
    prev = None
    for x in range(2, 99):
        f = 20 * (1000 ** ((x - 2) / 96))
        z = complex(math.cos(2 * math.pi * f / fs), -math.sin(2 * math.pi * f / fs))
        h = abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z))
        db = 20 * math.log10(h)
        y = round(22 - db * 2.6)
        if prev is not None:
            l.d.line([x - 1, prev, x, y], fill=1)
        prev = y
    for f in (100, 1000, 10000):
        x = 2 + round(96 * math.log(f / 20, 1000))
        l.im.putpixel((x, 28), 1)


def home():
    l = Lcd()
    l.text(1, 1, '44.1K')
    l.text(28, 1, 'STRIP ON 1 2')
    l.text(100, 1, '41%')
    l.hline(0, 127, 7)
    lv = [0.72, 0.55, 0.05, 0.0, 0.31, 0.0, 0.12, 0.0]
    for i, v in enumerate(lv):
        x = 4 + i * 16
        l.vbar(x, 10, 50, v, w=6, ticks=False)
        l.text(x + 2, 53, str(i + 1))
        if i in (0, 1):
            l.rect(x + 2, 60, x + 5, 62, fill=1)  # strip active on this input
    return l


def grid():
    l = Lcd()
    l.text(1, 1, 'STRIP')
    l.text(76, 1, 'CPU 53%')
    l.hline(0, 127, 7)
    act = {0: (0, 1, 2, 6), 1: (1, 6), 4: (3, 4, 5)}
    lv = [0.7, 0.5, 0.0, 0.0, 0.3, 0.0, 0.1, 0.0]
    for i in range(8):
        cx, cy = (i % 4) * 32, 9 + (i // 4) * 27
        l.rect(cx + 1, cy, cx + 30, cy + 25, outline=1)
        l.text(cx + 4, cy + 3, str(i + 1), scale=2)
        l.vbar(cx + 23, cy + 2, cy + 22, lv[i], w=3, ticks=False)
        for k in range(7):
            on = k in act.get(i, ())
            px = cx + 3 + k * 3
            l.rect(px, cy + 18, px + 1, cy + 22, fill=1 if on else None, outline=1)
        if i == 0:
            l.rect(cx, cy - 1, cx + 31, cy + 26, outline=1)
            l.rect(cx + 2, cy + 1, cx + 29, cy + 24, outline=1)
    return l


def idle():
    src = Image.open(os.path.join(ROOT, 'art', 'rosetta_splash_v4_128x64.png')).convert('L')
    l = Lcd()
    for y in range(H):
        for x in range(W):
            l.im.putpixel((x, y), 1 if src.getpixel((x, y)) < 128 else 0)
    return l


PAGES = [
    ('1 HOME: slim meters, block under a number = strip on', home()),
    ('2 STRIP: channel grid, 7 dots = blocks on', grid()),
    ('3 IN 1 / TUBE (3/7)', editor('TUBE', 2, [('DRIVE', '+12.0DB'), ('BIAS', '0.20'), ('OUTPUT', '0.0DB'),
                                               ('ON', 'YES')], 0, 0.66)),
    ('4 IN 1 / PEAK (5/7) + curve', editor('PEAK', 4, [('FREQ', '2.50K'), ('GAIN', '+4.0DB'), ('Q', '1.0'),
                                                       ('ON', 'YES')], 1, 0.58, extra=peak_curve,
                                          active=(0, 1, 2, 4, 6))),
    ('5 IN 1 / COMP (7/7), scrolling', editor('COMP', 6, [('THRESH', '-18.0DB'), ('RATIO', '4.0:1'), ('ATTACK', '10MS'),
                                                          ('RELEASE', '150MS'), ('KNEE', '6.0DB'), ('MAKEUP', '+2.0DB'),
                                                          ('ON', 'YES')], 3, 0.8, gr=0.35, top=1)),
    ('6 IDLE: nothing sounding -> Hex girl', idle()),
]


def lcd_render(l, s=5, gap=1):
    on, off, bg = (22, 26, 34), (196, 206, 214), (150, 162, 172)
    im = Image.new('RGB', (W * s, H * s), bg)
    d = ImageDraw.Draw(im)
    for y in range(H):
        for x in range(W):
            d.rectangle([x * s, y * s, x * s + s - 1 - gap, y * s + s - 1 - gap], fill=on if l.im.getpixel((x, y)) else off)
    return im


os.makedirs(OUT, exist_ok=True)
tiles = []
for name, l in PAGES:
    t = lcd_render(l)
    t.save(os.path.join(OUT, 'strip_' + name.split()[0] + '.png'))
    tiles.append((name, t))
tw, th = tiles[0][1].size
sheet = Image.new('RGB', (2 * tw + 60, 3 * (th + 40) + 20), (30, 30, 34))
ds = ImageDraw.Draw(sheet)
for i, (name, t) in enumerate(tiles):
    x, y = 20 + (i % 2) * (tw + 20), 20 + (i // 2) * (th + 40)
    sheet.paste(t, (x, y + 22))
    ds.text((x, y + 4), name, fill=(230, 230, 230))
sheet.save(os.path.join(OUT, 'strip_pages.png'))
print('wrote', os.path.join(OUT, 'strip_pages.png'))
