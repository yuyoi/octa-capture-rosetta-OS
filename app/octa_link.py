"""Device link for Rosetta Octa Control: one worker thread per unit owns that OCTA's CTRL MIDI port
(unit n = the n-th CTRL port in name order, like octa_lab.py --unit).

- Strip DSP: tools/octa_lab.py Strip (lab SysEx F0 7D 4F): EQ biquads via the mailbox, tube, comp, block flags.
- Preamp gain / 48V: written with Roland's own DT1 (re/CTRL_PROTOCOL.md 3.1: 00 05 c 04 SENS raw 0..100,
  00 05 c 00 +48V), read back with a lab CALL of the firmware getter 0x3097e4(0x43f444, id, ch).
- Meters: the 8 input peaks the firmware keeps at 0x43ed10 (Q27, 50 ms), GR of the shown channel from the strip.

Writes are coalesced per key (a knob drag sends only its newest value). Every lab error is caught and reported;
the link then goes offline instead of killing the app. Close Roland's Control Panel first (one program per port).
"""
import math
import os
import queue
import struct
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))

PREAMP = 0x43f444
R_GET = 0x3097e4
METER_IN = 0x43ed10


def fit_eq(kind, k):
    """(coefficients that fit the box's Q30, dB of level they leave out). A HIGH shelf boost arrives here already as
    the identical low-shelf cut (exact, 0.0000 dB); a big wide PEAK boost has its gain (b0-b2) halved until it fits,
    6.02 dB a halving. The caller puts the left-out level back after the comp."""
    n = 0
    while max(abs(x) for x in k[:3]) / (1 << n) >= 1.9999 and n < 4:
        n += 1
    if not n:
        return k, 0.0
    return [x / (1 << n) for x in k[:3]] + list(k[3:]), 6.0206 * n
LOAD_VARS = 0x41D5E4 + 8                     # lab VARS: +8 sample period, +0xC busy (cycles), the box's own load meter
PATCHBAY = 0x43f780
PATCH_GET = 0x30a92c
BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gate')


def dt1(addr, data):
    body = list(addr) + list(data)
    return [0x41, 0x10, 0x00, 0x00, 0x4D, 0x12] + body + [(-sum(body)) & 0x7F]


class Link(QObject):
    status = Signal(str, bool)                 # text, online
    meters = Signal(list, float)               # 8 x dBFS, GR dB of the watched channel
    load = Signal(int, int)                    # busy cycles, period cycles
    preamp = Signal(int, int, int, int)        # ch, gain raw (half dB), 48V, Hi-Z (-1 = this input has none)
    flags = Signal(list)                       # per channel: set of block names that are on in the unit

    def __init__(self, unit=0):
        super().__init__()
        self.unit = unit
        self.transport = None                  # callable -> (out, in) ports when the box is on our USB engine
        self.pending = {}
        self.lock = threading.Lock()
        self.watch = 0
        self.stop = False
        self.paused = False                    # the engine sees fewer boxes than links: this one waits
        self.mode, self.fast_on, self.fast = 'fat', False, None      # FAT/SLIM: SLIM needs the fast-memory path running
        self.busy = False                      # a long job (bench) holds the link: not a dead box
        self.last_ok = time.time()             # the last time the box answered
        self.lab = self.strip = None
        self.fs = 48000
        self.fx_ok = None                      # None = not known yet, False = this firmware has no FX/gate
        self.makeup = [dict(high=0.0, peak=0.0) for _ in range(8)]   # dB a boost was sent short by (fit_eq), per input
        self.last_c = [None] * 8
        self.th = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.th.start()

    # ---- called from the UI thread ----
    def send(self, key, fn):
        with self.lock:
            self.pending[key] = fn

    def set_block(self, ch, name, on, c):
        if on:
            self.send(('blk', ch, name), lambda: self._apply(ch, name, c))
        else:
            def off():
                if name == 'gate':
                    self.strip.gate_off(ch)
                    return
                self.strip.off(ch, [name])
                if name in ('high', 'peak'):
                    self._makeup(ch, name, 0.0)
            self.send(('blk', ch, name), off)

    def _makeup(self, ch, name, db):
        """level a boost couldn't carry in its own filter, put back after the comp (strip output gain). The comp's
        threshold moves down by the same dB, so it works on the same signal as before (see fit_eq)."""
        if self.makeup[ch][name] == db:
            return
        self.makeup[ch][name] = db
        total = sum(self.makeup[ch].values())
        import octa_lab
        s = self.strip
        if total > 0:
            s.gain(ch, 0.0, total)
        else:
            s.set_flags(ch, s.flags(ch) & ~octa_lab.F['gain'])
        c = self.last_c[ch]
        if c is not None and s.flags(ch) & octa_lab.F['comp']:
            self._apply(ch, 'comp', c)

    def set_amp(self, ch, voice, gain_db=None, master_db=None, pres_db=None, hard=None, tweaks=None):
        def go():
            if not self.strip.amp0:                          # older firmware: say so, don't drop the link
                self.status.emit('this firmware has no amp yet', True)
            elif voice == 'off':
                self.strip.amp_off(ch)
            else:
                self.strip.amp(ch, voice, gain_db, master_db, pres_db, hard, tweaks)
                if hard and not getattr(self.strip, 'hard_ok', False):
                    self.status.emit('HARD clip needs the strip9 firmware: soft curve for now', False)
        self.send(('amp', ch), go)

    def set_fx(self, ch, p):
        """echo + reverb on input ch (p = amp_presets fx dict), or off (p None)"""
        def go():
            try:
                if p is None:
                    self.strip.fx_off()
                else:
                    self.strip.fx(ch, **p)
                    self.fx_ok = True
            except SystemExit as e:                          # older firmware: say so, keep the link
                self.fx_ok = False
                self.status.emit(str(e), False)
        self.send(('fx',), go)

    def set_gain(self, ch, raw):
        raw = max(0, min(100, int(raw)))
        self.send(('gain', ch), lambda: self._dt1([0x00, 0x05, ch, 0x04], [raw]))

    def set_48v(self, ch, on):
        self.send(('48v', ch), lambda: self._dt1([0x00, 0x05, ch, 0x00], [1 if on else 0]))

    def set_hiz(self, ch, on):
        self.send(('hiz', ch), lambda: self._dt1([0x00, 0x05, ch, 0x03], [1 if on else 0]))

    def ensure_patchbay(self, pair, wire, told=None):
        """output pair -> source, written only when the box differs (the getter returns wire + 1)"""
        def go():
            if self.lab.call(PATCH_GET, PATCHBAY, pair, 0)[0] != wire + 1:
                self._dt1([0x00, 0x03, 0x00, pair], [wire])
                if told:
                    told(pair, wire)
        self.send(('patch', pair), go)

    # ---- worker ----
    def _dt1(self, addr, data):
        import mido
        self.lab.out.send(mido.Message('sysex', data=dt1(addr, data)))

    def _apply(self, ch, name, c):
        from octa_lab import COMP_DIV, rbj
        self.last_c[ch] = c
        s, fs = self.strip, self.fs
        slim = self.mode == 'slim' and self.fast_on
        if name == 'hpf':
            s.eq(ch, 'hpf', rbj('hpf', fs, c['hpf_f']))
        elif name == 'low':
            s.eq(ch, 'low', rbj('low', fs, c['low_f'], c['low_g']))
        elif name == 'peak':
            k, mk = fit_eq('peak', rbj('peak', fs, c['peak_f'], c['peak_g'], c['peak_q']))
            s.eq(ch, 'peak', k, slim=slim)
            self._makeup(ch, 'peak', mk)
        elif name == 'high':
            if c.get('high_kind'):                           # the amp's TREBLE: its MASTER carries the level
                s.eq(ch, 'high', rbj(c['high_kind'], fs, c['high_f'], c['high_g']), slim=slim)
                self._makeup(ch, 'high', 0.0)
            else:
                g = c['high_g']
                k = rbj('low', fs, c['high_f'], -g) if g > 0 else rbj('high', fs, c['high_f'], g)
                s.eq(ch, 'high', k, slim=slim)
                self._makeup(ch, 'high', max(0.0, g))
        elif name == 'tube':
            s.tube(ch, c['drive'], min(0.6, c['bias']), c['tube_out'])
        elif name == 'gate':
            try:
                s.gate(ch, c.get('gate_thr', -45.0))
            except SystemExit as e:                          # older firmware: say so, keep the link
                self.fx_ok = False
                self.status.emit(str(e), False)
        elif name == 'comp':
            thr = max(-60.0, c['thresh'] - sum(self.makeup[ch].values()))
            s.comp(ch, thr, c['ratio'], c['attack'], c['release'], 6.0, c['makeup'], div=COMP_DIV if slim else 1)

    def connect(self):
        import octa_lab
        self.lab = octa_lab.Lab(unit=self.unit, ports=self.transport() if self.transport else None)
        self.strip = octa_lab.Strip(self.lab)
        try:
            self.fs = self.strip.fs
        except SystemExit:
            self.fs = 48000
        fl = []
        for ch in range(8):
            v = self.strip.flags(ch)
            fl.append({n for n in BLOCKS if v & octa_lab.F[n]})
        self.flags.emit(fl)
        self.status.emit(f'ONLINE  {self.fs / 1000:g} kHz', True)

    def poll(self, n):
        raw = self.lab.peek(METER_IN, 32, 4)
        db = []
        for v in struct.unpack('<8I', raw):
            db.append(20 * math.log10(v / (1 << 27)) if v > 0 else -120.0)
        ch = self.watch
        gr = 0.0
        if self.strip.flags(ch) & 32:
            gr = self.strip.gr(ch, reset=False)[0]
        self.meters.emit(db, gr)
        if n % 8 == 3:                                       # DSP load, ~3 Hz
            per, busy = struct.unpack('<2I', self.lab.peek(LOAD_VARS, 8, 4))
            if per and busy:
                self.load.emit(busy, per)
        if n % 6 == 0:                                       # preamp state of the shown channel, ~3 Hz
            g = self.lab.call(R_GET, PREAMP, 0, ch)[0]
            p = self.lab.call(R_GET, PREAMP, 11, ch)[0]
            z = self.lab.call(R_GET, PREAMP, 12, ch)[0] if ch < 2 else -1
            self.preamp.emit(ch, g, p, z)

    def run(self):
        n = 0
        while not self.stop:
            if self.paused:
                self.last_ok = time.time()
                time.sleep(0.25)
                continue
            try:
                if self.lab is None:
                    self.status.emit('CONNECTING...', False)
                    self.connect()
                with self.lock:
                    work, self.pending = list(self.pending.values()), {}
                for fn in work:
                    fn()
                self.poll(n)
                self.last_ok = time.time()
                n += 1
                time.sleep(0.04)
            except BaseException as e:                       # SystemExit from octa_lab included
                msg = str(e) or e.__class__.__name__
                self.status.emit('OFFLINE  ' + msg[:70], False)
                try:
                    if self.lab:
                        self.lab.out.close()
                        self.lab.inp.close()
                except Exception:
                    pass
                self.lab = self.strip = None
                time.sleep(2.0)
