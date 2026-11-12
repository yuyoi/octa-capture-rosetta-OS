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
    P('WHISPER', 'MIC', ('hpf', 'low', 'peak', 'high', 'comp'), 'Close and quiet: proximity boom cut, air lifted, heavy 5:1 so every breath is even. No tube.',
      track=VOX, **eq(120, (200, -3.0), (2500, 1.0, 1.0), (12000, 4.0)), **comp(-34, 5.0, 3, 120, 10.0)),
    P('SHOUT', 'MIC', ALL, 'Hardcore / punk shouts: 1.5 kHz pushed so the words cut through guitars, tube grit, 6:1. Gate on.',
      gate=-48.0, track=VOX, **eq(130, (250, -2.0), (1500, 3.0, 0.9), (8000, -1.0)), **tube(12.0, 0.30), **comp(-22, 6.0, 4, 90, 5.0)),
    P('SHRIEK', 'MIC', ALL, 'Black-metal shriek: thin and cold, lows gone, top up, hard tube and a crushed 10:1. Gate on.',
      gate=-46.0, track=VOX, **eq(260, (400, -3.0), (2800, 2.0, 1.2), (8000, 2.0)), **tube(18.0, 0.40), **comp(-26, 10.0, 1, 60, 6.0)),
    P('MONK', 'MIC', ALL, 'Very low chanting voice: chest at 120 Hz brought up, top rounded off, slow 3:1 that swells.',
      track=VOX, **eq(50, (120, 3.0), (2500, -2.0, 0.8), (8000, -2.0)), **tube(5.0, 0.20), **comp(-22, 3.0, 25, 300, 3.0)),
    P('BROADCAST', 'MIC', ALL, 'Late-night radio host: close-mic warmth kept, the box at 500 Hz out, glued with 4:1. Gate on.',
      gate=-55.0, track=('Voice', 'vocal'), **eq(70, (150, 2.0), (500, -3.0, 1.0), (10000, 2.0)), **tube(5.0, 0.20),
      **comp(-28, 4.0, 5, 200, 7.0)),
    P('STREAM VOICE', 'MIC', ALL, 'For Discord / streams: no rumble, no boom, loud and even (6:1), gate for keyboard and room noise.',
      gate=-50.0, track=('Voice', 'vocal'), **eq(120, (220, -2.0), (3000, 1.5, 1.0), (9000, 1.0)), **tube(2.0, 0.10),
      **comp(-30, 6.0, 3, 120, 9.0)),
    P('DIST VOX', 'MIC', ('hpf', 'peak', 'high', 'tube', 'comp'), 'Industrial distorted vocal: tube flat out, lows and highs cut, a 1.2 kHz snarl.',
      gate=-45.0, track=VOX, **eq(300, (200, 0.0), (1200, 5.0, 1.0), (5000, -8.0)), **tube(24.0, 0.50), **comp(-20, 8.0, 2, 80, 2.0)),
    P('CLEAN TENOR', 'MIC', ALL, 'High clean lead: thin out the low-mids, 5 kHz presence and air so it floats over the band.',
      track=VOX, **eq(110, (300, -2.0), (5000, 2.0, 1.0), (12000, 3.0)), **tube(4.0, 0.15), **comp(-20, 3.0, 8, 100, 3.0)),
    P('BARITONE', 'MIC', ALL, 'Low male lead: chest at 150 Hz kept, a little 2 kHz for the words, warm tube.',
      track=VOX, **eq(70, (150, 1.5), (2000, 1.5, 0.9), (10000, 1.0)), **tube(7.0, 0.25), **comp(-20, 3.0, 12, 150, 3.0)),
    P('GOTHIC CROON', 'MIC', ALL, 'Dark crooner: deep and velvety, the top rolled off, lots of tube, slow 3:1.',
      track=VOX, **eq(60, (180, 2.0), (3000, -2.0, 1.0), (8000, -2.0)), **tube(10.0, 0.30), **comp(-22, 3.0, 20, 250, 4.0)),
    P('CHOIR', 'MIC', ALL, 'For stacking choir layers: thinner, softer edge, more air, so ten takes sum without mud.',
      track=('Choir', 'vocal'), **eq(180, (300, -3.0), (3000, -2.0, 1.0), (11000, 3.0)), **tube(3.0, 0.15), **comp(-22, 3.0, 15, 200, 3.0)),
    P('INCANTATION', 'MIC', ALL, 'Spoken ritual voice, close and dark: big low end, top cut, heavy tube, 6:1. Gate on.',
      gate=-52.0, track=VOX, **eq(60, (130, 3.0), (2500, -1.5, 0.9), (6000, -5.0)), **tube(14.0, 0.35), **comp(-28, 6.0, 5, 250, 6.0)),
    P('PUNK SNOT', 'MIC', ALL, 'Nasal punk vocal: lows gone, a 1 kHz honk, raw tube, 5:1.',
      gate=-48.0, track=VOX, **eq(180, (300, -3.0), (1000, 4.0, 1.2), (8000, -1.0)), **tube(12.0, 0.35), **comp(-22, 5.0, 4, 90, 4.0)),
    P('LOW GUTTURAL', 'MIC', ALL, 'The lowest death vocals: the sub kept, 800 Hz box cut, hard tube, crushed 10:1. Gate on.',
      gate=-46.0, track=VOX, **eq(60, (150, 2.0), (800, -3.0, 1.0), (6000, -2.0)), **tube(16.0, 0.40), **comp(-28, 10.0, 2, 70, 6.0)),
    P('TELEPHONE', 'MIC', ('hpf', 'peak', 'high', 'comp'), 'Phone call: 400 Hz-3 kHz only, a 1.8 kHz bump, clean (no tube), squashed.',
      track=('Voice', 'vocal'), **eq(400, (200, 0.0), (1800, 4.0, 1.0), (3000, -15.0)), **comp(-24, 6.0, 3, 80, 5.0)),
    P('CAB MIC', 'MIC', ('hpf', 'peak', 'high'), 'The e935 on a guitar cab: rumble out, its presence peak and the fizz above 7 kHz pulled down. No comp, no tube.',
      track=('Cab', 'amp'), **eq(80, (200, 0.0), (3500, -2.5, 1.5), (7000, -3.0))),
    P('ACOUSTIC MIC', 'MIC', ALL, 'The e935 on an acoustic guitar, 20 cm off the 12th fret: body boom out, sparkle up, gentle 3:1.',
      track=('Acoustic', 'acoustic'), **eq(90, (200, -3.0), (800, -1.5, 1.0), (10000, 3.0)), **tube(2.0, 0.10), **comp(-20, 3.0, 15, 150, 3.0)),
    P('HARMONICA', 'MIC', ('hpf', 'peak', 'high', 'tube', 'comp'), 'Blues harp cupped on the e935: lows and highs cut, 1.2 kHz pushed, tube driven hard like a small amp.',
      track=('Harp', 'vocal'), **eq(200, (200, 0.0), (1200, 4.0, 0.9), (4000, -6.0)), **tube(20.0, 0.40), **comp(-20, 4.0, 5, 120, 3.0)),
    P('SNARE MIC', 'MIC', ALL, 'The e935 on a snare: body at 200 Hz, the 900 Hz box out, crack up top, 4:1 that lets the hit through.',
      track=('Snare', 'snare'), **eq(80, (200, 1.0), (900, -3.0, 1.2), (6000, 3.0)), **comp(-20, 4.0, 10, 100, 3.0)),
    P('TOM MIC', 'MIC', ('hpf', 'low', 'peak', 'high', 'comp'), 'On a tom: 100 Hz boom up, 500 Hz cardboard out, stick attack at 5 kHz.',
      track=('Tom', 'snare'), **eq(60, (100, 2.0), (500, -4.0, 1.0), (5000, 2.0)), **comp(-20, 3.0, 15, 150, 2.0)),
    P('BASS CAB MIC', 'MIC', ('hpf', 'low', 'peak', 'high', 'comp'), 'On a bass cab: the fundamental at 80 Hz up, 500 Hz mud out, the fizzy top off, 4:1.',
      track=('Bass', 'bass'), **eq(40, (80, 2.0), (500, -3.0, 1.0), (5000, -2.0)), **comp(-22, 4.0, 15, 200, 3.0)),
    P('HORN MIC', 'MIC', ('hpf', 'low', 'peak', 'high', 'comp'), 'Sax or trumpet: rumble out, the honk at 1 kHz eased, some air, 3:1.',
      track=('Horn', 'vocal'), **eq(80, (250, -1.0), (1000, -2.0, 1.0), (9000, 2.0)), **comp(-20, 3.0, 10, 150, 3.0)),
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
    P('DROP TUNED DI', 'GUITAR', ALL, 'Drop C and lower straight in: the low end kept tight (flub at 120 Hz cut), 2 kHz attack, slow comp.',
      track=GTR, **eq(60, (120, -2.0), (2000, 2.0, 1.0), (6000, -4.0)), **tube(10.0, 0.30), **comp(-22, 3.0, 20, 150, 3.0)),
    P('TWANG DI', 'GUITAR', ALL, 'Bridge single-coil chicken pickin\': bright, mids pulled back, squashed 6:1 for even notes.',
      track=GTR, **eq(90, (250, -2.0), (800, -2.0, 0.8), (6000, 3.0)), **tube(3.0, 0.15), **comp(-28, 6.0, 5, 120, 7.0)),
    P('FUNK DI', 'GUITAR', ALL, 'Funk rhythm: thin and snappy, low-mids out, top up, fast 8:1 so every scratch is the same level.',
      track=GTR, **eq(120, (300, -4.0), (1500, 1.0, 1.0), (7000, 3.0)), **tube(2.0, 0.10), **comp(-26, 8.0, 2, 80, 6.0)),
    P('ACOUSTIC DI', 'GUITAR', ALL, 'Piezo pickup straight in: the 1 kHz quack and the harsh top tamed, body put back at 200 Hz.',
      track=('Acoustic', 'acoustic'), **eq(70, (200, 2.0), (1000, -4.0, 1.4), (8000, -3.0)), **tube(2.0, 0.10), **comp(-20, 3.0, 15, 200, 3.0)),
    P('SINGLE COIL DI', 'GUITAR', ALL, 'Neck or middle single coil: glassy, a little 3 kHz, the top left open, 4:1 comp.',
      track=GTR, **eq(70, (200, -1.0), (3000, 1.5, 1.0), (9000, 2.0)), **tube(4.0, 0.15), **comp(-22, 4.0, 10, 180, 4.0)),
    P('P90 DI', 'GUITAR', ALL, 'P-90: thick and gritty, the spiky 2.5 kHz tamed, top rolled off a bit.',
      track=GTR, **eq(80, (200, 0.0), (2500, -2.5, 1.2), (6000, -4.0)), **tube(10.0, 0.30), **comp(-22, 3.0, 20, 180, 3.0)),
    P('HOLLOW BODY DI', 'GUITAR', ALL, 'Hollow / semi-hollow: the 200 Hz body boom (feedback) out, warm top, slow comp.',
      track=GTR, **eq(70, (200, -3.0), (1500, 1.0, 0.9), (6000, -3.0)), **tube(5.0, 0.20), **comp(-22, 3.0, 25, 220, 3.0)),
    P('7-STRING DI', 'GUITAR', ALL, 'Seven strings: low B kept tight (100 Hz flub out), 2.5 kHz attack, fizz trimmed.',
      track=GTR, **eq(55, (100, -2.5), (2500, 2.0, 1.0), (6000, -4.0)), **tube(10.0, 0.30), **comp(-22, 3.0, 15, 150, 3.0)),
    P('BARITONE DI', 'GUITAR', ALL, 'Baritone guitar: the deep notes kept but not boomy, mids forward so it still reads as guitar.',
      track=GTR, **eq(45, (110, -1.5), (1200, 2.0, 0.9), (6000, -3.0)), **tube(8.0, 0.25), **comp(-22, 3.0, 20, 180, 3.0)),
    P('12-STRING DI', 'GUITAR', ALL, '12-string jangle: low end thinned, chime at 4 kHz and up, 5:1 comp for the shimmer.',
      track=GTR, **eq(110, (250, -3.0), (4000, 2.0, 1.0), (10000, 3.0)), **tube(3.0, 0.15), **comp(-26, 5.0, 8, 150, 6.0)),
    P('SLIDE DI', 'GUITAR', ALL, 'Slide: mids pushed at 1 kHz to sing, top tamed against string squeal, long sustain from 5:1.',
      track=GTR, **eq(80, (200, -1.0), (1000, 3.0, 0.9), (5000, -4.0)), **tube(12.0, 0.30), **comp(-26, 5.0, 15, 300, 6.0)),
    P('SUSTAIN DI', 'GUITAR', ALL, 'Endless sustain for swells and leads: 10:1 with a long release, tube, the top softened.',
      track=GTR, **eq(80, (200, -1.0), (2000, 1.5, 1.0), (6000, -3.0)), **tube(10.0, 0.30), **comp(-34, 10.0, 20, 600, 12.0)),
    P('PEDALBOARD DI', 'GUITAR', ('hpf', 'peak', 'high'), 'Drive pedals straight into the box: the fizz above 5 kHz cut like a speaker would, harsh 3 kHz down.',
      track=GTR, **eq(70, (200, 0.0), (3000, -3.0, 1.0), (5000, -8.0))),
    P('FUZZ PEDAL DI', 'GUITAR', ('hpf', 'low', 'peak', 'high'), 'A fuzz pedal straight in: hard speaker-like roll-off at 4 kHz, the 2.5 kHz rasp scooped, low end back.',
      track=GTR, **eq(60, (120, 2.0), (2500, -4.0, 1.0), (4000, -12.0))),
    P('TUBE DIRT DI', 'GUITAR', ALL, 'Distortion with no amp: the tube flat out does the dirt, lows tightened and top rolled off after.',
      track=GTR, **eq(100, (200, -2.0), (1500, 2.0, 0.9), (5000, -8.0)), **tube(24.0, 0.50), **comp(-20, 3.0, 20, 150, 2.0)),
    P('LO-FI DI', 'GUITAR', ('hpf', 'peak', 'high', 'tube', 'comp'), 'Old radio guitar: 300 Hz-3 kHz only, a 1 kHz honk, driven tube, squashed.',
      track=GTR, **eq(300, (200, 0.0), (1000, 4.0, 1.0), (3000, -12.0)), **tube(16.0, 0.40), **comp(-22, 6.0, 5, 100, 4.0)),
    P('AMBIENT DI', 'GUITAR', ALL, 'For reverb-heavy ambient parts later: soft attack, airy top, slow 4:1 that evens out swells.',
      track=GTR, **eq(90, (250, -2.0), (2500, -1.0, 1.0), (10000, 4.0)), **tube(2.0, 0.10), **comp(-26, 4.0, 40, 400, 5.0)),
    P('BASS DI', 'GUITAR', ALL, 'Bass guitar straight in: sub kept, 400 Hz mud out, 2 kHz growl for the string noise, 4:1.',
      track=('Bass', 'bass'), **eq(35, (80, 2.0), (400, -3.0, 1.0), (2000, 2.0)), **tube(6.0, 0.25), **comp(-22, 4.0, 15, 200, 4.0)),
    P('BASS PICK DI', 'GUITAR', ALL, 'Picked bass: bright clank at 2.5 kHz, low-mids cleaned up, tight 5:1.',
      track=('Bass', 'bass'), **eq(40, (300, -3.0), (2500, 3.0, 1.0), (6000, 2.0)), **tube(6.0, 0.25), **comp(-24, 5.0, 8, 150, 5.0)),
    P('BASS DEEP DI', 'GUITAR', ALL, 'Finger bass, old-school round: lots of low end, the top rolled away, slow comp.',
      track=('Bass', 'bass'), **eq(30, (90, 3.0), (700, -1.0, 0.8), (2500, -8.0)), **tube(8.0, 0.30), **comp(-22, 4.0, 20, 250, 4.0)),
    P('BASS DRIVE DI', 'GUITAR', ALL, 'Overdriven bass: tube pushed hard, low end kept solid, 1 kHz grind, fizz cut.',
      track=('Bass', 'bass'), **eq(35, (90, 1.0), (1000, 3.0, 1.0), (4000, -6.0)), **tube(20.0, 0.40), **comp(-22, 4.0, 15, 200, 3.0)),
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
