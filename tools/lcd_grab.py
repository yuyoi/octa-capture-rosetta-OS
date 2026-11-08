"""Screenshot of the OCTA-CAPTURE LCD over the lab: reads the 128x64 1bpp surface ([[0x445300]], 16 B/row, MSB = left)
and renders it like the real LCD.
python -I tools/lcd_grab.py NAME   -> art/screens/NAME.png (5x LCD look) + NAME_1x.png (raw pixels)"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import octa_lab  # noqa: E402

SURF_OBJ = 0x445300                 # -> surface object: [0] bits, [1] h<<16|w, [2] stride
W, H = 128, 64
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'art', 'screens')


def grab(lab):
    import struct
    obj = struct.unpack('<I', lab.peek(SURF_OBJ, 4, 4))[0]
    bits = struct.unpack('<I', lab.peek(obj, 4, 4))[0]
    raw = lab.peek(bits, W * H // 8, 4)
    im = Image.new('1', (W, H), 0)
    for y in range(H):
        for xb in range(16):
            b = raw[y * 16 + xb]
            for k in range(8):
                if not b & (0x80 >> k):          # bit 0 = ink
                    im.putpixel((xb * 8 + k, y), 1)
    return im


def lcd_render(im, s=6, gap=1, border=24):
    on, off, bg = (70, 52, 120), (226, 230, 232), (196, 200, 204)     # violet ink on the white backlit glass
    out = Image.new('RGB', (W * s + 2 * border, H * s + 2 * border), bg)
    d = ImageDraw.Draw(out)
    for y in range(H):
        for x in range(W):
            x0, y0 = border + x * s, border + y * s
            d.rectangle([x0, y0, x0 + s - 1 - gap, y0 + s - 1 - gap], fill=on if im.getpixel((x, y)) else off)
    return out


if __name__ == '__main__':
    name = sys.argv[1] if len(sys.argv) > 1 else 'screen'
    invert = '--invert' in sys.argv
    lab = octa_lab.Lab()
    # the UI clears + redraws every 50 ms and a peek can land mid-draw: grab 9 times, keep each row's most
    # common version (a torn row is rare and differs from grab to grab)
    from collections import Counter
    grabs = [grab(lab).tobytes() for _ in range(9)]
    rows = [Counter(g[y * 16:(y + 1) * 16] for g in grabs).most_common(1)[0][0] for y in range(H)]
    im = Image.frombytes('1', (W, H), b''.join(rows))
    if invert:
        im = im.point(lambda v: 1 - v)
    os.makedirs(OUT, exist_ok=True)
    im.save(os.path.join(OUT, name + '_1x.png'))
    lcd_render(im).save(os.path.join(OUT, name + '.png'))
    ink = sum(1 for v in im.tobytes() for k in range(8) if v >> k & 1)
    print(f'{name}: {ink} ink pixels of {W * H} -> {os.path.join(OUT, name + ".png")}')
