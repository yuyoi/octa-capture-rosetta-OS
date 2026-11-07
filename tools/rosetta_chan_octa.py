"""Rosetta boot art (same method as sp808/tools/roland_chan.py) resized for the OCTA-CAPTURE: 128x56 above the bar.

Writes art/rc_candidates.png: framing candidates, scaled up on an LCD-green background.
"""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, '..', '..', 'U110_Card_Studio', 'hexwizard', 'art')  # the source drawings are not in this repo; the rendered splashes are in art/
OUT = os.path.join(HERE, '..', 'art'); os.makedirs(OUT, exist_ok=True)
W, H = 128, 56
PAD = 600

base = Image.open(os.path.join(ART, 'girl_redraw2.png')).convert('L')
print('source', base.size)
ImageDraw.Draw(base).rectangle((485, 345, 540, 372), fill=255)    # collarbone dash, as in the U-110 intro
src = ImageOps.expand(base, PAD, fill=255)


def crop(cx, cy, w, h=None):
    h = h or w * H / W
    x0, y0 = cx - w / 2 + PAD, cy - h / 2 + PAD
    return src.crop((int(x0), int(y0), int(x0 + w), int(y0 + h)))


def dither(c, w=W, h=H):        # far shots: Floyd-Steinberg keeps the shading
    g = c.resize((w, h), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.2, 250, 0))
    return np.asarray(g.convert('1')) == 0


def thresh(c, w=W, h=H, t=170):  # close-ups: clean lines
    g = ImageOps.autocontrast(c.resize((w, h), Image.LANCZOS), cutoff=3)
    return np.asarray(g) < t


def cover(c, w=W, h=H, k=0.24):  # box-average ink coverage: keeps thin lines thin at 9x reduction
    g = np.asarray(c.resize((w, h), Image.BOX), dtype=float) / 255
    return (1 - g) > k


def lcd(a, s=4):
    on, off = (40, 52, 30), (168, 196, 92)
    im = Image.new('RGB', (a.shape[1], a.shape[0]), off)
    im.paste(Image.new('RGB', im.size, on), mask=Image.fromarray((a * 255).astype(np.uint8)))
    return im.resize((a.shape[1] * s, a.shape[0] * s), Image.NEAREST)


import sys


def whole_fig(k=0.24, h=H):
    """the whole figure, cropped to her ink bounding box, scaled to h px tall"""
    ink = np.asarray(base) < 128
    ys, xs = np.nonzero(ink)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    fig = base.crop((x0, y0, x1, y1))
    w = round(fig.size[0] * h / fig.size[1])
    return cover(fig, w, h, k), (x0, y0, x1, y1)


closed = Image.open(os.path.join(ART, 'girl_closed.png')).convert('L')   # the user's own closed-eye drawing
FACE_CX = 460                                    # between the eyes (407/519, y 208); the "w" mouth is at y ~285


def no_legs(img):
    """erase the kicking legs (right of the right wing, and the toes above it); head, hair and both wings stay"""
    img = img.copy(); d = ImageDraw.Draw(img)
    d.rectangle((790, 0, img.size[0], img.size[1]), fill=255)
    d.rectangle((690, 0, 790, 257), fill=255)
    return img


def face(img, top, bot, k, aspect=1.35):
    """crop rows top..bot of the drawing around the face (white outside the drawing), scaled to 32 px tall"""
    h = bot - top; w = int(h * aspect)
    img = ImageOps.expand(img, PAD, fill=255)
    c = img.crop((PAD + FACE_CX - w // 2, PAD + top, PAD + FACE_CX + w // 2, PAD + bot))
    fw = min(W, round(c.size[0] * H / c.size[1]))
    return cover(c, fw, H, k)


# The face view the user liked is source rows 140-320 at full width (5.625 source px per LCD px).
# The picture keeps that zoom but starts at the top of her head (row 52), so it is 48 px tall:
# she slides up from below with her scalp intact and stops on the 140-320 face view.
FACE_K, ZOOM, HEAD_TOP, FACE_BOT = 0.18, 180 / H, 52, 320
TALL = round((FACE_BOT - HEAD_TOP) / ZOOM)
END_OFF = -round((140 - HEAD_TOP) / ZOOM)        # image row 0 sits this far above the screen at the end


def tall(img, conv=None):
    img = ImageOps.expand(no_legs(img), PAD, fill=255); w = W * ZOOM
    c = img.crop((int(PAD + FACE_CX - w / 2), PAD + HEAD_TOP, int(PAD + FACE_CX + w / 2), PAD + FACE_BOT))
    return conv(c, W, TALL) if conv else cover(c, W, TALL, FACE_K)


# downscale / 1-bit conversion options (c = cropped source, w x h = LCD size)
def _lz(c, w, h): return np.asarray(c.resize((w, h), Image.LANCZOS), dtype=float) / 255
INTERP = {
    'box coverage 18% (current)': lambda c, w, h: cover(c, w, h, 0.18),
    'box coverage 28% (thinner)': lambda c, w, h: cover(c, w, h, 0.28),
    'box coverage 12% (bolder)': lambda c, w, h: cover(c, w, h, 0.12),
    'lanczos + threshold': lambda c, w, h: _lz(ImageOps.autocontrast(c, cutoff=1), w, h) < 0.72,
    'lanczos + sharpen + dither': lambda c, w, h: np.asarray(
        c.resize((w, h), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.2, 250, 0)).convert('1')) == 0,
    'bilinear + threshold': lambda c, w, h: np.asarray(c.resize((w, h), Image.BILINEAR), dtype=float) / 255 < 0.8,
    'thin lines first (erode ink) + box': lambda c, w, h: cover(c.filter(ImageFilter.MaxFilter(5)), w, h, 0.10),
    'max ink per cell (boldest)': lambda c, w, h: np.asarray(c.filter(ImageFilter.MinFilter(9)).resize((w, h), Image.BOX)) < 140,
}


PICK = INTERP['lanczos + sharpen + dither']     # user's pick: option 5
OPEN, SHUT = tall(base, PICK), tall(closed, PICK)
FW, FX = W, 0

# 5x7 pixel font from the Hex Wizard intro (only the letters we use), drawn at 2x = bold 10x14 glyphs
GL = {'A': [".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"],
      'E': ["XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"],
      'J': ["..XXX", "...X.", "...X.", "...X.", "...X.", "X..X.", ".XX.."],
      'O': [".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."],
      'R': ["XXXX.", "X...X", "X...X", "XXXX.", "X.X..", "X..X.", "X...X"],
      'S': [".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."],
      'T': ["XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "..X.."],
      'W': ["X...X", "X...X", "X...X", "X.X.X", "X.X.X", "XX.XX", "X...X"],
      ' ': ["....."] * 7}
TSC, TSP = 2, 2                                  # glyph scale, gap between letters (px)
MOUTH_Y = round((285 - 140) / ZOOM)              # her "w" mouth on the face view (~26)
TEXT_Y = min(H - 7 * TSC - 1, MOUTH_Y - 7 * TSC // 2)


def text_mask(t):
    tw = len(t) * 5 * TSC + (len(t) - 1) * TSP
    m = np.zeros((H, W), bool); x0 = (W - tw) // 2
    for i, ch in enumerate(t):
        for r, row in enumerate(GL[ch]):
            for c, v in enumerate(row):
                if v == 'X':
                    x = x0 + i * (5 * TSC + TSP) + c * TSC; y = TEXT_Y + r * TSC
                    m[y:y + TSC, x:x + TSC] = True
    return m


def overlay(s, t):
    """big text in front of her mouth: clear a solid box (1 px margin) behind the letters, then draw them"""
    if not t: return s
    m = text_mask(t); ys, xs = np.nonzero(m)
    s = s.copy(); s[max(0, ys.min() - 1):ys.max() + 2, max(0, xs.min() - 2):xs.max() + 3] = False; s[m] = True
    return s


# boot sequence: (eyes, offset of image row 0 on screen, ticks, text).
# Slide up, blink, closed-eye smile, then "JSW OS" types in, then "ROSETTA" types in (with a blink).
E = END_OFF
SEQ = [('open', off, 1, '') for off in range(H - 1, E - 1, -2)] + [
    ('open', E, 8, ''), ('shut', E, 2, ''), ('open', E, 6, ''), ('shut', E, 10, '')]
SEQ += [('open', E, 2, 'JSW OS'[:n]) for n in range(1, 7)] + [('open', E, 14, 'JSW OS')]
SEQ += [('open', E, 2, 'ROSETTA'[:n]) for n in range(1, 8)] + [
    ('open', E, 6, 'ROSETTA'), ('shut', E, 2, 'ROSETTA'), ('open', E, 14, 'ROSETTA')]
TICK_MS = 50


def screen(name, off, t=''):
    s = np.zeros((H, W), bool); f = OPEN if name == 'open' else SHUT
    top = max(0, off); src = max(0, -off); n = min(H - top, TALL - src)
    s[top:top + n] = f[src:src + n]
    return overlay(s, t)

