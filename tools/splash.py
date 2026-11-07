"""Hex girl splash for the OCTA-CAPTURE LCD (1 bit). Same line art + method as the SP-808 boot art.

usage: python splash.py W H [k]      -> art/splash_WxH_candidates.png (LCD preview, several ink thresholds)
       python splash.py W H k out.png -> writes the chosen 1-bit image (white = pixel on)
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', '..', 'U110_Card_Studio', 'hexwizard', 'art', 'girl_redraw2.png')  # the source drawings are not in this repo; the rendered splashes are in art/
OUT = os.path.join(HERE, '..', 'art')
os.makedirs(OUT, exist_ok=True)


def cover(img, w, h, k):
    """box-average ink coverage: keeps thin lines visible at big reductions"""
    g = np.asarray(img.resize((w, h), Image.BOX), dtype=float) / 255
    return (1 - g) > k


def figure(W, H, k, text_rows=0):
    base = Image.open(SRC).convert('L')
    ImageDraw.Draw(base).rectangle((485, 345, 540, 372), fill=255)  # collarbone dash (as U-110/SP-808)
    ink = np.asarray(base) < 128
    ys, xs = np.nonzero(ink)
    fig = base.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    avail_h = H - text_rows
    w = round(fig.size[0] * avail_h / fig.size[1])
    h = avail_h
    if w > W:
        h = round(fig.size[1] * W / fig.size[0])
        w = W
    a = cover(fig, w, h, k)
    out = np.zeros((H, W), bool)
    x0, y0 = (W - w) // 2, (avail_h - h) // 2
    out[y0:y0 + h, x0:x0 + w] = a
    return out


def lcd(a, s=4):
    on, off = (20, 24, 40), (150, 190, 230)  # dark pixels on a blue-white backlight
    im = Image.new('RGB', (a.shape[1], a.shape[0]), off)
    im.paste(Image.new('RGB', im.size, on), mask=Image.fromarray((a * 255).astype(np.uint8)))
    return im.resize((a.shape[1] * s, a.shape[0] * s), Image.NEAREST)


if __name__ == '__main__':
    W, H = int(sys.argv[1]), int(sys.argv[2])
    if len(sys.argv) >= 5:
        a = figure(W, H, float(sys.argv[3]), int(sys.argv[5]) if len(sys.argv) > 5 else 0)
        Image.fromarray((a * 255).astype(np.uint8)).convert('1').save(sys.argv[4])
        lcd(a).save(sys.argv[4].replace('.png', '_lcd.png'))
        print('wrote', sys.argv[4])
    else:
        ks = [0.14, 0.18, 0.22, 0.26, 0.30, 0.36]
        tiles = [lcd(figure(W, H, k, 8)) for k in ks]
        sheet = Image.new('RGB', (tiles[0].width * 2 + 12, (tiles[0].height + 24) * 3), (40, 40, 40))
        d = ImageDraw.Draw(sheet)
        for i, (k, t) in enumerate(zip(ks, tiles)):
            x, y = (i % 2) * (t.width + 12), (i // 2) * (t.height + 24)
            sheet.paste(t, (x, y + 20))
            d.text((x + 4, y + 4), f'k={k}', fill=(255, 255, 255))
        p = os.path.join(OUT, f'splash_{W}x{H}_candidates.png')
        sheet.save(p)
        print('wrote', p)
