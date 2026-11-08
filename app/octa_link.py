"""Device link for Rosetta Control: one worker thread owns the OCTA's CTRL MIDI port.

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
BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp')


def dt1(addr, data):
    body = list(addr) + list(data)
    return [0x41, 0x10, 0x00, 0x00, 0x4D, 0x12] + body + [(-sum(body)) & 0x7F]


class Link(QObject):
    status = Signal(str, bool)                 # text, online
    meters = Signal(list, float)               # 8 x dBFS, GR dB of the watched channel
    preamp = Signal(int, int, int)             # ch, gain raw (half dB), 48V
    flags = Signal(list)                       # per channel: set of block names that are on in the unit

    def __init__(self):
        super().__init__()
        self.pending = {}
        self.lock = threading.Lock()
        self.watch = 0
        self.stop = False
        self.lab = self.strip = None
        self.fs = 48000
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
            self.send(('blk', ch, name), lambda: self.strip.off(ch, [name]))

    def set_gain(self, ch, raw):
        raw = max(0, min(100, int(raw)))
        self.send(('gain', ch), lambda: self._dt1([0x00, 0x05, ch, 0x04], [raw]))

    def set_48v(self, ch, on):
        self.send(('48v', ch), lambda: self._dt1([0x00, 0x05, ch, 0x00], [1 if on else 0]))

    # ---- worker ----
    def _dt1(self, addr, data):
        import mido
        self.lab.out.send(mido.Message('sysex', data=dt1(addr, data)))

    def _apply(self, ch, name, c):
        from octa_lab import rbj
        s, fs = self.strip, self.fs
        if name == 'hpf':
            s.eq(ch, 'hpf', rbj('hpf', fs, c['hpf_f']))
        elif name == 'low':
            s.eq(ch, 'low', rbj('low', fs, c['low_f'], c['low_g']))
        elif name == 'peak':
            s.eq(ch, 'peak', rbj('peak', fs, c['peak_f'], c['peak_g'], c['peak_q']))
        elif name == 'high':
            s.eq(ch, 'high', rbj('high', fs, c['high_f'], c['high_g']))
        elif name == 'tube':
            s.tube(ch, c['drive'], min(0.6, c['bias']), c['tube_out'])
        elif name == 'comp':
            s.comp(ch, c['thresh'], c['ratio'], c['attack'], c['release'], 6.0, c['makeup'])

    def connect(self):
        import octa_lab
        self.lab = octa_lab.Lab()
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
        if n % 6 == 0:                                       # preamp state of the shown channel, ~3 Hz
            g = self.lab.call(R_GET, PREAMP, 0, ch)[0]
            p = self.lab.call(R_GET, PREAMP, 11, ch)[0]
            self.preamp.emit(ch, g, p)

    def run(self):
        n = 0
        while not self.stop:
            try:
                if self.lab is None:
                    self.status.emit('CONNECTING...', False)
                    self.connect()
                with self.lock:
                    work, self.pending = list(self.pending.values()), {}
                for fn in work:
                    fn()
                self.poll(n)
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
