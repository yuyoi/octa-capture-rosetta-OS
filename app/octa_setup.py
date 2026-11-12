"""What the boxes forget at power-off, kept on the PC: app/setup.json (not in git).

One entry per input per box (the strip's knobs and blocks, preamp gain, Hi-Z, amp voice) plus a small "rig" part for the
things that belong to the monitoring setup rather than to an input (OCTA output routing, Focusrite mixer inputs).
Everything is range-checked on load, so a damaged or hand-edited file can't send wild values to a box.
+48V is deliberately not kept: phantom power is never switched on by software behind your back.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get('RD_SETUP', os.path.join(HERE, 'setup.json'))

BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gate')
VOICES = ('off', 'clean', 'crunch', 'lead', 'cranked', 'fuzz')
RANGES = dict(hpf_f=(20, 600), low_f=(30, 1000), low_g=(-15, 15), peak_f=(100, 12000), peak_g=(-15, 15), peak_q=(0.3, 8),
              high_f=(1000, 16000), high_g=(-15, 15), drive=(0, 24), bias=(0, 0.6), tube_out=(-24, 24), thresh=(-60, 0),
              ratio=(1, 20), attack=(0.1, 100), release=(10, 2000), makeup=(0, 24), pre_gain=(0, 50),
              amp_gain=(0, 10), amp_bass=(0, 10), amp_mid=(0, 10), amp_treble=(0, 10), amp_pres=(0, 10), amp_master=(0, 10), amp_hard=(0, 1),
              amp_echo=(0, 10), amp_time=(0, 10), amp_rev=(0, 10), amp_gate=(0, 10), gate_thr=(-80, -20))
STREAM_DEFAULT = dict(name='Discord', device='CABLE Input', sends={})
DEFAULT_RIG = dict(patchbay={}, focusrite_inputs=[], serials={}, stream=dict(STREAM_DEFAULT))


def clean_chan(d):
    """the valid part of one saved input; anything missing or wrong is simply left out"""
    out = {}
    if not isinstance(d, dict):
        return out
    for k, (lo, hi) in RANGES.items():
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            out[k] = float(min(hi, max(lo, v)))
    on = d.get('on')
    if isinstance(on, dict):
        out['on'] = {b: on[b] is True for b in BLOCKS if b in on}
    if d.get('gain_known') is True and 'pre_gain' in out:
        out['gain_known'] = True
    if isinstance(d.get('hiz'), bool):
        out['hiz'] = d['hiz']
    if d.get('amp') in VOICES:
        out['amp'] = d['amp']
    if isinstance(d.get('amp_tweaks'), dict):                # a tone preset's amp insides (amp_presets.py)
        import amp_presets
        tw = amp_presets.clean_tweaks(d['amp_tweaks'])
        if tw:
            out['amp_tweaks'] = tw
    if isinstance(d.get('amp_preset'), str) and 0 < len(d['amp_preset']) <= 24:
        out['amp_preset'] = d['amp_preset']
        if d.get('amp_edited') is True:
            out['amp_edited'] = True
    if isinstance(d.get('patch'), str) and 0 < len(d['patch']) <= 24:   # the strip patch it came from (strip_patches.py)
        out['patch'] = d['patch']
        if d.get('patch_edited') is True:
            out['patch_edited'] = True
    if isinstance(d.get('mic'), str) and 0 < len(d['mic']) < 40:
        out['mic'] = d['mic']
    if isinstance(d.get('mic_prev'), dict):                   # the EQ to go back to when the mic correction is switched off
        prev = clean_chan({k: v for k, v in d['mic_prev'].items() if k not in ('mic', 'mic_prev')})
        if prev:
            out['mic_prev'] = prev
    return out


def clean_stream(s):
    """send to stream: route name (shown on the button), playback device to feed, ticked inputs per unit"""
    out = dict(STREAM_DEFAULT, sends={})
    if not isinstance(s, dict):
        return out
    for k in ('name', 'device'):
        if isinstance(s.get(k), str) and 0 < len(s[k].strip()) < 60:
            out[k] = s[k].strip()
    sends = s.get('sends')
    if isinstance(sends, dict):
        for unit, chans in sends.items():
            if unit in ('0', '1') and isinstance(chans, list):
                good = sorted({c for c in chans if isinstance(c, int) and not isinstance(c, bool) and 0 <= c < 8})
                if good:
                    out['sends'][unit] = good
    return out


def clean_rig(r):
    out = dict(patchbay={}, focusrite_inputs=[], serials={}, stream=clean_stream(None))
    if not isinstance(r, dict):
        return out
    pb = r.get('patchbay')
    if isinstance(pb, dict):                                  # {"0": {"4": 0}} = unit 1, output pair 4 (coax), mix A
        for unit, routes in pb.items():
            if unit in ('0', '1') and isinstance(routes, dict):
                good = {p: w for p, w in routes.items()
                        if p in tuple('01234') and isinstance(w, int) and not isinstance(w, bool) and 0 <= w <= 8}
                if good:
                    out['patchbay'][unit] = good
    fi = r.get('focusrite_inputs')
    if isinstance(fi, list):
        out['focusrite_inputs'] = [x for x in fi if isinstance(x, str) and 0 < len(x) < 40][:8]
    se = r.get('serials')                                     # USB serial -> unit (0 / 1): which box is which
    if isinstance(se, dict):
        good = {s: u for s, u in se.items() if isinstance(s, str) and 0 < len(s) < 40 and u in (0, 1) and not isinstance(u, bool)}
        if len(set(good.values())) == len(good):
            out['serials'] = good
    out['stream'] = clean_stream(r.get('stream'))
    if r.get('monitor') is True:                             # MONITOR OTHER BOX survives a restart
        out['monitor'] = True
    return out


def load(path=PATH):
    """{'units': [[8 inputs], [8 inputs]], 'rig': {...}}, or None when there is nothing usable"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get('units'), list) or len(d['units']) != 2:
        return None
    units = []
    for u in d['units']:
        if not isinstance(u, list) or len(u) != 8:
            return None
        units.append([clean_chan(c) for c in u])
    return dict(units=units, rig=clean_rig(d.get('rig')))


def save(data, path=PATH):
    """write beside the target, then swap, so a crash or power cut can never leave half a file"""
    keep = dict(units=[[clean_chan(c) for c in u] for u in data['units']], rig=clean_rig(data.get('rig')))
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(keep, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
