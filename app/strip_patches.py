"""Strip patches: whole channel strips (HPF, EQ, tube, comp, gate) for the mic and the guitar, factory ones here, yours
in strip_patches_user.json next to setup.json (no limit). Guitar amp tones live in amp_presets.py; the PATCH browser
lists both.

A patch = which blocks are on + their knobs + the gate threshold (None = gate off). The preamp gain is never touched:
that's the hand on the knob. Everything is app-side, so new patches need no firmware; the GATE part needs strip10.

MIC patches are for the Sennheiser e935 (dynamic, its own presence lift ~4-5 kHz, proximity bass up close), unit 2 input 1.
GUITAR patches are for the Les Paul straight in (Hi-Z, 35 dB) with the amp off. Starting points by reasoning, not by ear yet.
"""
import json
import os

import octa_setup

USER_PATH = os.path.join(os.path.dirname(octa_setup.PATH), 'strip_patches_user.json')
BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp')
PARAMS = ('hpf_f', 'low_f', 'low_g', 'peak_f', 'peak_g', 'peak_q', 'high_f', 'high_g', 'drive', 'bias', 'tube_out',
          'thresh', 'ratio', 'attack', 'release', 'makeup')
ALL = BLOCKS


def P(name, group, on, note, gate=None, track=None, **kv):
    return dict(name=name, group=group, on=tuple(on), gate=gate, track=track, params=kv, note=note)


def eq(hpf, low, peak, high):
    """(f) / (f, g) / (f, g, q) / (f, g) -> knob dict"""
    return dict(hpf_f=hpf, low_f=low[0], low_g=low[1], peak_f=peak[0], peak_g=peak[1], peak_q=peak[2],
                high_f=high[0], high_g=high[1])


def tube(drive, bias):
    return dict(drive=drive, bias=bias, tube_out=0.0)


def comp(thresh, ratio, attack, release, makeup):
    return dict(thresh=thresh, ratio=ratio, attack=attack, release=release, makeup=makeup)


VOX = ('Vocal', 'vocal')
GTR = ('Guitar', 'guitar')
FACTORY = [
    # ---------------- MIC (e935) ----------------
    P('E935 VOCAL', 'MIC', ALL, 'Main sung vocal: mud out at 400 Hz, the mic\'s own top left nearly alone, gentle 3:1.',
      track=VOX, **eq(100, (250, -2.0), (400, -2.5, 1.2), (11000, 1.5)), **tube(3.0, 0.15), **comp(-20, 3.0, 8, 110, 3.0)),
    P('VOCAL', 'MIC', ALL, 'Any vocal mic: low cut, a little 3 kHz and air, warm tube, 3:1.',
      track=VOX, **eq(90, (200, -1.5), (3000, 2.0, 1.0), (10000, 2.5)), **tube(6.0, 0.20), **comp(-18, 3.0, 10, 120, 3.0)),
    P('DOOM CHANT', 'MIC', ALL, 'Low clean chant: more chest at 160 Hz, the 3.5 kHz edge rounded off, slow comp so it swells.',
      track=VOX, **eq(70, (160, 2.0), (3500, -1.5, 1.0), (10000, 1.5)), **tube(6.0, 0.20), **comp(-20, 3.0, 15, 200, 3.0)),
    P('SCREAM', 'MIC', ALL, 'High screams: the e935\'s 3.5 kHz bite pulled back so it doesn\'t pierce, tube grit, fast 6:1. Gate on.',
      gate=-50.0, track=VOX, **eq(150, (300, -3.0), (3500, -2.5, 1.5), (9000, 1.0)), **tube(10.0, 0.30), **comp(-24, 6.0, 3, 80, 5.0)),
    P('GROWL', 'MIC', ALL, 'Death growl right on the grille: proximity boom tamed, boxy 600 Hz out, hard tube, 8:1 that never lets go. Gate on.',
      gate=-48.0, track=VOX, **eq(110, (220, -3.0), (600, -2.5, 1.0), (7000, -1.0)), **tube(14.0, 0.35), **comp(-26, 8.0, 2, 60, 6.0)),
    P('BACKING VOX', 'MIC', ALL, 'Thinner and brighter so it sits behind the lead: more low cut, top lift, 4:1.',
      track=VOX, **eq(160, (250, -3.0), (2500, 1.0, 1.0), (10000, 3.0)), **tube(4.0, 0.15), **comp(-22, 4.0, 8, 120, 4.0)),
    P('TALK', 'MIC', ALL, 'Spoken word / videos: no boom, no box, even level (4:1, -26 dB), gate for the room between sentences.',
      gate=-55.0, track=('Voice', 'vocal'), **eq(100, (180, -1.0), (350, -2.0, 1.2), (9000, 1.0)), **tube(2.0, 0.10),
      **comp(-26, 4.0, 5, 150, 6.0)),
    P('RADIO', 'MIC', ('hpf', 'peak', 'high', 'tube', 'comp'), 'Old radio / megaphone: only 450 Hz-3.5 kHz left, a 1.6 kHz honk, driven tube, squashed.',
      track=VOX, **eq(450, (200, 0.0), (1600, 6.0, 1.0), (3500, -14.0)), **tube(18.0, 0.40), **comp(-22, 6.0, 3, 80, 4.0)),
    P('CAB MIC', 'MIC', ('hpf', 'peak', 'high'), 'The e935 on a guitar cab: rumble out, its presence peak and the fizz above 7 kHz pulled down. No comp, no tube.',
      track=('Cab', 'guitar'), **eq(80, (200, 0.0), (3500, -2.5, 1.5), (7000, -3.0))),
    # ---------------- GUITAR, amp off ----------------
    P('REAMP DI', 'GUITAR', ('hpf',), 'Record the raw guitar to re-amp later: only a 30 Hz rumble cut, nothing else.',
      track=('Guitar DI', 'guitar'), **eq(30, (200, 0.0), (2500, 0.0, 1.0), (8000, 0.0))),
    P('CLEAN DI', 'GUITAR', ALL, 'Clean chords straight in: a touch of 2.5 kHz and top, soft tube, 4:1 comp for sparkle and sustain.',
      track=GTR, **eq(70, (200, -1.0), (2500, 1.5, 1.0), (8000, 2.0)), **tube(4.0, 0.15), **comp(-22, 4.0, 10, 200, 4.0)),
    P('LES PAUL DI', 'GUITAR', ALL, 'Bridge humbucker direct: the 3.2 kHz bite tamed, top rolled off like a speaker would, soft tube, slow comp.',
      track=GTR, **eq(80, (200, -1.0), (3200, -3.0, 1.4), (6000, -6.0)), **tube(10.0, 0.30), **comp(-22, 3.0, 25, 180, 4.0)),
    P('GUITAR DI', 'GUITAR', ALL, 'Pushed direct guitar: low cut, 2.5 kHz forward, top down 3 dB, 12 dB of tube, 4:1.',
      track=GTR, **eq(80, (200, -2.0), (2500, 2.0, 1.0), (7000, -3.0)), **tube(12.0, 0.30), **comp(-20, 4.0, 20, 150, 3.0)),
    P('NECK JAZZ', 'GUITAR', ALL, 'Neck pickup, warm and round: low-mids kept, the top rolled away, slow 3:1.',
      track=GTR, **eq(60, (180, 1.0), (1200, -1.5, 0.8), (5000, -7.0)), **tube(6.0, 0.25), **comp(-20, 3.0, 30, 250, 3.0)),
    P('FLAT', 'GUITAR', (), 'Everything off: the input as it comes in.'),
]
OLD_NAMES = dict(vocal='VOCAL', guitar='GUITAR DI', e935='E935 VOCAL', lespaul='LES PAUL DI', flat='FLAT')


def clean_patch(p):
    """the usable part of a patch from a file, or None"""
    if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not 0 < len(p['name'].strip()) <= 24:
        return None
    on = p.get('on') if isinstance(p.get('on'), (list, tuple)) else ()
    raw = p.get('params') if isinstance(p.get('params'), dict) else {}
    params = {}
    for k in PARAMS:
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            lo, hi = octa_setup.RANGES.get(k, (-1e9, 1e9))
            params[k] = float(min(hi, max(lo, v)))
    g = p.get('gate')
    gate = float(min(-20, max(-80, g))) if isinstance(g, (int, float)) and not isinstance(g, bool) and g == g else None
    return dict(name=p['name'].strip().upper(), group='USER', on=tuple(b for b in BLOCKS if b in on), gate=gate,
                track=None, params=params, note=p.get('note') if isinstance(p.get('note'), str) else '')


def load_user(path=None):
    try:
        with open(path or USER_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = [clean_patch(p) for p in data] if isinstance(data, list) else []
    return [p for p in out if p]


def save_user(patches, path=None):
    path = path or USER_PATH
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump([dict(p, on=list(p['on'])) for p in patches], f, indent=1)
    os.replace(tmp, path)


def find(name, user=None):
    name = OLD_NAMES.get(name, name)
    for p in (user if user is not None else load_user()) + FACTORY:
        if p['name'] == name:
            return p
    return None


def from_chan(c, name):
    """a patch from what an input runs now (amp off)"""
    return dict(name=name.strip().upper()[:24], group='USER', on=tuple(b for b in BLOCKS if c['on'].get(b)),
                gate=float(c.get('gate_thr', -45.0)) if c['on'].get('gate') else None, track=None,
                params={k: float(c[k]) for k in PARAMS if k in c}, note='')


def apply(c, p):
    """put a patch on an input's saved state (the caller sends it): amp off, blocks and knobs as the patch says"""
    c['amp'] = 'off'
    c.pop('amp_preset', None)
    c.pop('amp_tweaks', None)
    c.update(p['params'])
    c['on'].update({b: b in p['on'] for b in BLOCKS})
    c['on']['gate'] = p['gate'] is not None
    if p['gate'] is not None:
        c['gate_thr'] = p['gate']
    c['patch'] = p['name']
