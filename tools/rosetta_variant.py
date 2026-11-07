"""Render one numbered variant from art/rosetta_splash_candidates.png as a 128x64 splash PNG (white = ink).

usage: python -I rosetta_variant.py N   -> art/rosetta_splash_vN_128x64.png + art/rosetta_splash_vN_preview.png
Variant order = sheet order: zoom (180, 230, whole) x text scale (2, 3) x pick (lanczos, box coverage).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PIL import Image

import rosetta_chan_octa as r
import splash

PICKS = ('lanczos + sharpen + dither', 'box coverage 18% (current)')
VARIANTS = [(z, tsc, p) for z in (180, 230, 'whole') for tsc in (2, 3) for p in PICKS]
ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'art')


def frame(z, tsc, pick):
    r.ZOOM = (r.FACE_BOT - r.HEAD_TOP) / r.H if z == 'whole' else z / r.H
    r.TALL = round((r.FACE_BOT - r.HEAD_TOP) / r.ZOOM)
    r.END_OFF = 0 if z == 'whole' else -round((140 - r.HEAD_TOP) / r.ZOOM)
    r.OPEN = r.tall(r.base, r.INTERP[pick])
    r.TSC = tsc
    mouth = round((285 - r.HEAD_TOP) / r.ZOOM) + r.END_OFF
    r.TEXT_Y = min(r.H - 7 * tsc - 1, mouth - 7 * tsc // 2)
    full = np.zeros((64, 128), bool)
    full[:56] = r.screen('open', r.END_OFF, 'ROSETTA')     # rows 56-63 = the boot bar
    return full


if __name__ == '__main__':
    n = int(sys.argv[1])
    a = frame(*VARIANTS[n - 1])
    p = os.path.join(ART, f'rosetta_splash_v{n}_128x64.png')
    Image.fromarray((a * 255).astype(np.uint8)).save(p)
    splash.lcd(a, 4).save(os.path.join(ART, f'rosetta_splash_v{n}_preview.png'))
    print('wrote', p, VARIANTS[n - 1])
