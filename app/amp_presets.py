"""Tone presets for the amp head: factory ones here, yours in amp_presets_user.json next to setup.json (no limit).

A preset = a channel voice + the six knobs (0-10) + HARD CLIP + the amp's insides (`tweaks`) that the knobs don't reach:
  tight  Hz   high-pass between the two gain stages (low tighter / looser into the 2nd stage)
  pre    Hz   low-pass before the 1st stage (fizz in)
  b1/b2       stage bias (0 = even clipping, odd harmonics; more = uneven, more even harmonics)
  split       share of the gain in stage 1 (each stage tops out at 23.9 dB, the rest moves over)
  res    Hz   speaker low resonance
  lp1/lp2 Hz  speaker top roll-off
  mid_f/mid_q where MIDDLE works
Everything is app-side: the box just gets filter coefficients and gains, so no firmware is needed for new tones.

MASTER is set so each factory tone peaks near -6 dBFS for a -18 dBFS 110 Hz note in the C model of the box
(patches/tests/test_strip_host.py). The rest is a starting point by reasoning, not yet by ear (the user plays a Les Paul, Hi-Z at 35 dB). Things this amp
can't do and the real records have: a noise gate (thrash), delay/reverb (solos), tuning down (sludge).
"""
import json
import os

import octa_setup

USER_PATH = os.path.join(os.path.dirname(octa_setup.PATH), 'amp_presets_user.json')
KNOBS = ('amp_gain', 'amp_bass', 'amp_mid', 'amp_treble', 'amp_pres', 'amp_master', 'amp_gate', 'amp_echo', 'amp_time',
         'amp_rev')
TWEAK_RANGES = dict(tight=(20, 400), pre=(2000, 12000), res=(50, 150), b1=(0, 0.5), b2=(0, 0.5), split=(0.2, 1.0),
                    lp1=(2500, 9000), lp2=(3000, 12000), mid_f=(300, 1500), mid_q=(0.4, 2.0),
                    echo_fb=(0, 0.9), echo_tone=(500, 12000), rev_size=(0, 1), rev_damp=(0, 1))


def P(name, voice, gain, bass, mid, treble, pres, master, hard, note, fx=(0.0, 6.0, 0.0), gate=0.0, **tw):
    """fx = (ECHO, TIME, REVERB) knobs; their character (echo_fb, echo_tone, rev_size, rev_damp) in tw; gate = GATE knob"""
    return dict(name=name, voice=voice, knobs=dict(amp_gain=gain, amp_bass=bass, amp_mid=mid, amp_treble=treble,
                                                   amp_pres=pres, amp_master=master, amp_gate=gate, amp_echo=fx[0],
                                                   amp_time=fx[1], amp_rev=fx[2]),
                hard=hard, tweaks=tw, note=note)


FACTORY = [
    P('HOLY WARS', 'lead', 9.0, 6.0, 4.0, 6.5, 7.0, 4.9, False,
      'Thrash rhythm: tight low end into the 2nd stage, bright and cutting, a shallow mid dip, not a full scoop.',
      fx=(0.0, 6.0, 1.5), gate=6.5, rev_size=0.3, rev_damp=0.5,
      tight=170, pre=7500, b1=0.2, b2=0.1, split=0.45, res=100, lp1=5800, lp2=8000, mid_f=750, mid_q=0.9),
    P('FUNERALOPOLIS', 'fuzz', 10.0, 8.5, 3.5, 3.0, 3.0, 4.9, True,
      'Doom fuzz wall: everything flat out, loose bass, dark top, a wide 900 Hz scoop. Uneven 1st stage for grit.',
      fx=(2.0, 6.0, 4.0), gate=5.0, echo_fb=0.3, echo_tone=2000, rev_size=0.8, rev_damp=0.7,
      tight=35, pre=2800, b1=0.15, b2=0.0, split=0.5, res=65, lp1=4200, lp2=5500, mid_f=900, mid_q=0.6),
    P('FLOODS', 'lead', 9.6, 6.5, 2.5, 7.0, 7.5, 4.5, True,
      'Groove-metal solid-state: hard clipping, very tight, deep 650 Hz scoop, lots of presence.',
      fx=(3.0, 6.5, 2.5), gate=7.0, echo_fb=0.35, echo_tone=4000, rev_size=0.6, rev_damp=0.4,
      tight=200, pre=8500, b1=0.1, b2=0.0, split=0.4, res=105, lp1=6200, lp2=8500, mid_f=650, mid_q=0.8),
    P('DETROIT SOLO', 'crunch', 7.0, 5.0, 7.0, 5.5, 5.0, 5.1, False,
      '70s humbucker lead into a cranked British head: moderate gain, mids up, even harmonics from the bias.',
      fx=(3.5, 5.8, 3.0), gate=5.0, echo_fb=0.3, echo_tone=3500, rev_size=0.5, rev_damp=0.5,
      tight=110, pre=6500, b1=0.3, b2=0.15, split=0.45, res=95, lp1=5000, lp2=7000, mid_f=800, mid_q=0.8),
    P('CROWBAR', 'lead', 9.4, 7.5, 5.5, 4.0, 4.0, 4.9, False,
      'Sludge: high gain but thick, low end left loose, low-mids kept (450 Hz), dark top.',
      fx=(0.0, 6.0, 1.5), gate=5.5, rev_size=0.4, rev_damp=0.6,
      tight=75, pre=4500, b1=0.25, b2=0.12, split=0.5, res=80, lp1=4600, lp2=6500, mid_f=450, mid_q=0.8),
    P('SABBATH', 'crunch', 7.6, 4.5, 7.5, 5.5, 5.5, 5.4, False,
      'Treble booster into an old head: lows cut before the 2nd stage, mid-heavy, raw uneven clipping.',
      fx=(0.0, 6.0, 2.0), gate=5.0, rev_size=0.5, rev_damp=0.5,
      tight=240, pre=6000, b1=0.35, b2=0.2, split=0.5, res=95, lp1=5000, lp2=7000, mid_f=800, mid_q=0.7),
    P('GLASS CLEAN', 'clean', 3.0, 4.5, 5.0, 6.5, 6.5, 7.6, False,
      'Clean and bright: low gain, open top, a short slapback and some room. No gate, so chords ring out.',
      fx=(2.0, 3.0, 4.5), gate=0.0, echo_fb=0.15, echo_tone=6000, rev_size=0.5, rev_damp=0.3,
      tight=60, pre=11000, b1=0.05, b2=0.05, split=0.6, res=90, lp1=7500, lp2=10000, mid_f=700, mid_q=0.7),
    P('DOOM CLEAN', 'clean', 5.0, 7.0, 4.5, 3.5, 3.5, 5.6, False,
      'Dark clean for slow arpeggios: thick low end, top rolled off, long dark echo into a big reverb.',
      fx=(4.0, 7.5, 7.0), gate=3.0, echo_fb=0.45, echo_tone=1800, rev_size=0.9, rev_damp=0.7,
      tight=40, pre=6000, b1=0.15, b2=0.1, split=0.5, res=70, lp1=4500, lp2=6000, mid_f=600, mid_q=0.7),
    P('BLUES BREAK', 'crunch', 5.0, 5.0, 6.5, 6.0, 5.5, 5.9, False,
      'Edge of breakup: cleans up when you play soft, bites when you dig in. Mids forward, a touch of echo.',
      fx=(2.5, 5.5, 2.5), gate=0.0, echo_fb=0.25, echo_tone=3000, rev_size=0.5, rev_damp=0.5,
      tight=90, pre=7000, b1=0.3, b2=0.15, split=0.5, res=95, lp1=5200, lp2=7500, mid_f=750, mid_q=0.8),
    P('PLEXI', 'cranked', 8.0, 5.5, 7.0, 6.5, 6.0, 4.8, False,
      'Old British head flat out: big mids, crunchy chords, uneven bias for even harmonics. Classic rock rhythm.',
      fx=(0.0, 6.0, 2.0), gate=4.0, rev_size=0.5, rev_damp=0.5,
      tight=120, pre=7000, b1=0.3, b2=0.2, split=0.5, res=100, lp1=5500, lp2=7500, mid_f=850, mid_q=0.8),
    P('JAZZ CLEAN', 'clean', 2.5, 6.0, 5.0, 3.5, 3.0, 7.3, False,
      'Warm jazz box: barely any gain, dark top, round low-mids, a little room. No gate.',
      fx=(0.0, 6.0, 2.5), gate=0.0, rev_size=0.4, rev_damp=0.7,
      tight=40, pre=6000, b1=0.05, b2=0.05, split=0.6, res=85, lp1=4500, lp2=6500, mid_f=500, mid_q=0.6),
    P('SPRING SURF', 'clean', 3.5, 5.0, 4.5, 7.5, 7.0, 7.0, False,
      'Surf clean: bright and snappy into a splashy, barely damped reverb turned way up.',
      fx=(0.0, 6.0, 8.0), gate=0.0, rev_size=0.6, rev_damp=0.15,
      tight=80, pre=11000, b1=0.1, b2=0.05, split=0.6, res=95, lp1=8000, lp2=11000, mid_f=800, mid_q=0.7),
    P('AMBIENT WASH', 'clean', 3.0, 5.0, 4.5, 5.5, 5.0, 7.4, False,
      'Clean into a long dark echo and the biggest reverb: notes turn into pads. Play slow.',
      fx=(6.0, 8.0, 9.0), gate=0.0, echo_fb=0.6, echo_tone=2500, rev_size=1.0, rev_damp=0.5,
      tight=60, pre=9000, b1=0.05, b2=0.05, split=0.6, res=85, lp1=6500, lp2=9000, mid_f=700, mid_q=0.6),
    P('FUNK CLEAN', 'clean', 3.0, 4.0, 4.0, 7.0, 7.0, 9.5, False,
      'Funk rhythm: thin, bright and dry, lows tightened so muted scratches stay crisp.',
      fx=(0.0, 6.0, 1.0), gate=3.0, rev_size=0.3, rev_damp=0.5,
      tight=150, pre=11000, b1=0.05, b2=0.05, split=0.6, res=100, lp1=8500, lp2=11000, mid_f=600, mid_q=0.8),
    P('COUNTRY SLAP', 'clean', 4.0, 4.5, 5.0, 7.0, 6.5, 7.1, False,
      'Chicken-pickin clean: bright, a hair of grit when you dig in, short slapback echo.',
      fx=(3.5, 2.5, 1.5), gate=0.0, echo_fb=0.1, echo_tone=5000, rev_size=0.3, rev_damp=0.5,
      tight=100, pre=10000, b1=0.1, b2=0.05, split=0.6, res=100, lp1=7500, lp2=10000, mid_f=800, mid_q=0.8),
    P('INDIE CRUNCH', 'crunch', 4.5, 4.5, 5.5, 6.5, 6.5, 6.8, False,
      'Jangly light crunch: chords stay clear, a bit of hair on top, short echo and some room.',
      fx=(1.5, 4.5, 3.0), gate=0.0, echo_fb=0.2, echo_tone=4000, rev_size=0.5, rev_damp=0.5,
      tight=110, pre=8000, b1=0.25, b2=0.1, split=0.5, res=95, lp1=6000, lp2=8500, mid_f=900, mid_q=0.8),
    P('BLUES LEAD', 'crunch', 6.5, 5.0, 7.5, 5.5, 5.0, 5.0, False,
      'Singing blues lead: fat mids, smooth top, uneven bias for even harmonics, echo behind it.',
      fx=(3.0, 5.5, 3.0), gate=2.0, echo_fb=0.3, echo_tone=3000, rev_size=0.5, rev_damp=0.5,
      tight=100, pre=6500, b1=0.35, b2=0.2, split=0.5, res=95, lp1=5000, lp2=7000, mid_f=700, mid_q=0.7),
    P('POST ROCK', 'crunch', 5.5, 5.5, 5.0, 6.5, 6.0, 5.6, False,
      'Light crunch with a dotted echo and a big hall: tremolo-picked builds and crescendos.',
      fx=(5.0, 7.0, 6.0), gate=0.0, echo_fb=0.5, echo_tone=3500, rev_size=0.8, rev_damp=0.4,
      tight=90, pre=8000, b1=0.2, b2=0.1, split=0.5, res=90, lp1=6000, lp2=8500, mid_f=800, mid_q=0.7),
    P('ARENA ROCK', 'cranked', 7.0, 5.5, 6.5, 6.5, 6.5, 5.1, False,
      'Big rock rhythm: open, punchy chords, mids up, a little echo and room.',
      fx=(1.5, 5.5, 3.0), gate=3.0, echo_fb=0.25, echo_tone=3500, rev_size=0.5, rev_damp=0.5,
      tight=120, pre=7500, b1=0.25, b2=0.15, split=0.5, res=100, lp1=5600, lp2=8000, mid_f=800, mid_q=0.8),
    P('PUNK', 'cranked', 8.5, 6.0, 6.0, 6.5, 6.0, 4.8, False,
      'Raw punk: a cranked head with nothing in the way, fast and dry, mids left in.',
      fx=(0.0, 6.0, 1.0), gate=5.0, rev_size=0.3, rev_damp=0.5,
      tight=130, pre=7500, b1=0.3, b2=0.2, split=0.5, res=100, lp1=5500, lp2=7500, mid_f=900, mid_q=0.9),
    P('GRUNGE', 'cranked', 9.0, 6.5, 5.0, 5.0, 5.0, 5.0, False,
      'Grunge: loose and dirty, dark top, uneven clipping, chords that smear a little.',
      fx=(0.0, 6.0, 1.5), gate=4.0, rev_size=0.4, rev_damp=0.6,
      tight=90, pre=6000, b1=0.35, b2=0.25, split=0.5, res=90, lp1=5000, lp2=7000, mid_f=700, mid_q=0.7),
    P('80S METAL', 'lead', 8.5, 6.0, 6.5, 6.5, 6.5, 4.5, False,
      '80s metal: tight high gain with the mids left in, echo and reverb for the big sound.',
      fx=(2.5, 6.0, 3.5), gate=6.0, echo_fb=0.3, echo_tone=3500, rev_size=0.6, rev_damp=0.4,
      tight=150, pre=7500, b1=0.2, b2=0.1, split=0.45, res=100, lp1=5800, lp2=8000, mid_f=800, mid_q=0.8),
    P('SHRED', 'lead', 9.0, 5.5, 7.5, 6.0, 6.5, 4.2, False,
      'Shred lead: tons of gain and sustain, mids pushed so fast runs stay clear, echo behind.',
      fx=(3.5, 6.2, 3.5), gate=6.5, echo_fb=0.35, echo_tone=3500, rev_size=0.6, rev_damp=0.5,
      tight=180, pre=7000, b1=0.15, b2=0.1, split=0.45, res=100, lp1=5500, lp2=7500, mid_f=750, mid_q=0.7),
    P('NU METAL', 'lead', 9.0, 8.0, 2.0, 6.5, 6.5, 5.1, True,
      'Nu metal: hard clipping, huge lows, mids scooped deep for drop-tuned bounce.',
      fx=(0.0, 6.0, 1.5), gate=7.5, rev_size=0.4, rev_damp=0.5,
      tight=140, pre=8000, b1=0.1, b2=0.0, split=0.45, res=75, lp1=6000, lp2=8000, mid_f=600, mid_q=0.6),
    P('DJENT', 'lead', 8.5, 5.5, 6.0, 6.5, 7.0, 5.8, True,
      'Djent: hard clipping, the lows cut before the 2nd stage so palm mutes snap, gate shut tight.',
      fx=(0.0, 6.0, 1.0), gate=8.5, rev_size=0.3, rev_damp=0.5,
      tight=320, pre=9000, b1=0.05, b2=0.0, split=0.4, res=110, lp1=6500, lp2=9000, mid_f=900, mid_q=0.8),
    P('CHAINSAW', 'lead', 10.0, 8.5, 8.5, 6.5, 6.0, 3.8, True,
      'Swedish death metal buzzsaw: everything up, lows and a 1.4 kHz bump both boosted hard.',
      fx=(0.0, 6.0, 1.5), gate=6.0, rev_size=0.4, rev_damp=0.5,
      tight=60, pre=9000, b1=0.1, b2=0.0, split=0.5, res=90, lp1=6000, lp2=8000, mid_f=1400, mid_q=1.0),
    P('BLACK METAL', 'lead', 9.5, 3.5, 3.5, 7.5, 7.5, 5.5, False,
      'Black metal: thin, cold and fizzy, little low end, tremolo picking into a big cold reverb.',
      fx=(1.5, 6.0, 6.0), gate=5.0, echo_fb=0.3, echo_tone=3000, rev_size=0.8, rev_damp=0.3,
      tight=220, pre=11000, b1=0.1, b2=0.05, split=0.45, res=110, lp1=7500, lp2=10000, mid_f=700, mid_q=0.7),
    P('STONER', 'fuzz', 8.5, 7.5, 6.5, 5.0, 4.5, 4.8, False,
      'Desert stoner fuzz: warm and thick, the mids kept (no scoop) so riffs sit forward, a little echo.',
      fx=(2.0, 6.5, 2.5), gate=3.0, echo_fb=0.3, echo_tone=2500, rev_size=0.5, rev_damp=0.6,
      tight=50, pre=4000, b1=0.2, b2=0.05, split=0.5, res=75, lp1=4800, lp2=6500, mid_f=600, mid_q=0.7),
    P('SHOEGAZE', 'fuzz', 9.0, 5.5, 5.0, 6.0, 5.0, 5.4, False,
      'Shoegaze: fuzz drowned in a long echo and a huge bright reverb, a wall of noise. No gate.',
      fx=(5.5, 7.0, 8.0), gate=0.0, echo_fb=0.55, echo_tone=3000, rev_size=1.0, rev_damp=0.3,
      tight=80, pre=7000, b1=0.1, b2=0.0, split=0.5, res=85, lp1=5500, lp2=8000, mid_f=900, mid_q=0.6),
    P('DRONE', 'fuzz', 10.0, 9.0, 4.0, 3.0, 3.0, 4.9, True,
      'Drone doom: maxed fuzz, the most low end, very dark, long dark echo into a cave. Let chords ring forever.',
      fx=(2.5, 9.0, 7.0), gate=0.0, echo_fb=0.45, echo_tone=1500, rev_size=0.9, rev_damp=0.8,
      tight=25, pre=2500, b1=0.1, b2=0.0, split=0.5, res=55, lp1=3500, lp2=4500, mid_f=700, mid_q=0.5),
]

def clean_tweaks(d):
    out = {}
    if isinstance(d, dict):
        for k, (lo, hi) in TWEAK_RANGES.items():
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
                out[k] = float(min(hi, max(lo, v)))
    return out


def clean_preset(p):
    """the usable part of a preset from a file, or None"""
    if not isinstance(p, dict) or not isinstance(p.get('name'), str) or not 0 < len(p['name'].strip()) <= 24:
        return None
    if p.get('voice') not in ('clean', 'crunch', 'lead', 'cranked', 'fuzz'):
        return None
    k = p.get('knobs') if isinstance(p.get('knobs'), dict) else {}
    knobs = {n: float(min(10, max(0, k[n]))) for n in KNOBS if isinstance(k.get(n), (int, float)) and not isinstance(k.get(n), bool)}
    return dict(name=p['name'].strip().upper(), voice=p['voice'], knobs=knobs, hard=p.get('hard') is True,
                tweaks=clean_tweaks(p.get('tweaks')), note=p.get('note') if isinstance(p.get('note'), str) else '')


def load_user(path=None):
    try:
        with open(path or USER_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = [clean_preset(p) for p in data] if isinstance(data, list) else []
    return [p for p in out if p]


def save_user(presets, path=None):
    path = path or USER_PATH
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(presets, f, indent=1)
    os.replace(tmp, path)


def find(name, user=None):
    for p in (user if user is not None else load_user()) + FACTORY:
        if p['name'] == name:
            return p
    return None


def from_chan(c, name):
    """a preset from what an input runs now"""
    return dict(name=name.strip().upper()[:24], voice=c.get('amp', 'crunch'), knobs={k: c[k] for k in KNOBS if k in c},
                hard=bool(c.get('amp_hard')), tweaks=clean_tweaks(c.get('amp_tweaks')), note='')


def apply(c, p):
    """put a preset on an input's saved state (the caller sends it)"""
    import amp_panel
    c['amp'] = p['voice']
    c.update(amp_panel.voice_defaults(p['voice']))
    c.update({k: 5.0 for k in ('amp_bass', 'amp_mid', 'amp_treble')})
    c.update(amp_panel.FX_DEFAULT)
    c.update(p['knobs'])
    c['amp_hard'] = 1.0 if p['hard'] else 0.0
    c['amp_tweaks'] = dict(p['tweaks'])
    c['amp_preset'] = p['name']
    c.pop('amp_edited', None)
    c['on'].update(tube=False, comp=False)
    if c.get('patch'):                                  # the strip patch under the amp no longer runs as saved
        c['patch_edited'] = True
