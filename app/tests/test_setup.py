"""Off-hardware tests for settings persistence: the setup file, the restore logic in the real window (offscreen, fake
links, real signal paths) and the Focusrite keeper against a fake Focusrite Control server.
python -I app/tests/test_setup.py"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, APP)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import focusrite                                    # noqa: E402
import octa_setup                                   # noqa: E402

fails = []


def check(name, ok, info=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'   {info}' if info and not ok else ''))
    if not ok:
        fails.append(name)


# ---------------- the file ----------------
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, 'setup.json')
blank = [[{} for _ in range(8)] for _ in range(2)]
check('no file = nothing to restore', octa_setup.load(path) is None)
open(path, 'w').write('{"units": [[1,2')
check('half-written file is ignored', octa_setup.load(path) is None)
units = [[dict(c) for c in u] for u in blank]
units[0][0] = dict(hpf_f=80.0, drive=10.0, pre_gain=35.0, gain_known=True, hiz=True, on=dict(hpf=True, tube=True, comp=False), amp='crunch')
octa_setup.save(dict(units=units, rig=dict(patchbay={'0': {'4': 0}}, focusrite_inputs=['Analogue 3-4', 'S/PDIF 1-2'])), path)
d = octa_setup.load(path)
check('round trip keeps an input', d['units'][0][0]['pre_gain'] == 35.0 and d['units'][0][0]['on']['tube'] is True and d['units'][0][0]['amp'] == 'crunch')
check('round trip keeps the rig', d['rig']['patchbay'] == {'0': {'4': 0}} and d['rig']['focusrite_inputs'][1] == 'S/PDIF 1-2')
check('no temp file left behind', os.listdir(tmp) == ['setup.json'])
bad = octa_setup.clean_chan(dict(drive=999, thresh=-999, ratio='x', pre_gain=float('nan'), bias=True, amp='nuclear', on=dict(tube='yes', hpf=True)))
check('wild values are clamped or dropped', bad.get('drive') == 24 and bad.get('thresh') == -60 and 'ratio' not in bad and 'pre_gain' not in bad
      and 'bias' not in bad and 'amp' not in bad and bad['on'] == dict(tube=False, hpf=True), bad)
check('gain without a value is not "known"', 'gain_known' not in octa_setup.clean_chan(dict(gain_known=True)))
rig = octa_setup.clean_rig(dict(patchbay={'0': {'4': 99, '9': 1, '1': 3}, '7': {'0': 0}}, focusrite_inputs=['ok', 5]))
check('bad routes are dropped', rig['patchbay'] == {'0': {'1': 3}} and rig['focusrite_inputs'] == ['ok'] and rig['serials'] == {}, rig)
check('two serials claiming one unit are not trusted', octa_setup.clean_rig(dict(serials={'A': 1, 'B': 1}))['serials'] == {})
check('good serial map survives', octa_setup.clean_rig(dict(serials={'A': 0, 'B': 1, 'C': True}))['serials'] == {'A': 0, 'B': 1})

# ---------------- the window ----------------
from PySide6.QtCore import QPoint, Qt                # noqa: E402
from PySide6.QtTest import QTest                     # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402
import rosetta_control as rc                         # noqa: E402

app = QApplication([])


class FakeLink:
    def __init__(self):
        self.calls, self.last_ok, self.paused = [], time.time(), False

    def set_block(self, ch, name, on, c):
        self.calls.append(('blk', ch, name, on, c['drive']))

    def set_gain(self, ch, raw):
        self.calls.append(('gain', ch, raw))

    def set_48v(self, ch, on):
        self.calls.append(('48v', ch, on))

    def set_hiz(self, ch, on):
        self.calls.append(('hiz', ch, on))

    def set_amp(self, ch, v):
        self.calls.append(('amp', ch, v))

    def ensure_patchbay(self, pair, wire, told=None):
        self.calls.append(('patch', pair, wire))


def window(p):
    w = rc.Main(demo=True)
    w.timer.stop()
    w.setup_path = p
    w.load_setup()
    for u in w.units:
        u['link'] = FakeLink()
    w.strip.link = w.units[0]['link']
    w.select_unit(0)
    w.resize(1460, 950)
    w.show()
    app.processEvents()
    return w


p2 = os.path.join(tmp, 's2.json')
w = window(p2)
EMPTY = [set() for _ in range(8)]
w.on_flags(0, EMPTY)
check('nothing saved yet: a fresh box is left alone', not [c for c in w.units[0]['link'].calls if c[0] in ('blk', 'gain')])

b = w.strip.blk['tube']                              # turn the tube on with a real click
w.units[0]['chans'][0]['on'].update(dict.fromkeys(rc.PRESETS['flat'] and () or ('hpf', 'low', 'peak', 'high', 'tube', 'comp'), False))
w.strip.show_chan(0)
QTest.mouseClick(b, Qt.LeftButton)
check('clicking TUBE turns it on', w.units[0]['chans'][0]['on']['tube'] is True)
check('the click starts the save timer', w.save_timer.isActive())
k = w.strip.k['drive']
k.set_frac(1.0)
w.strip.on_preamp(0, 70, 0, 1)                       # the box reports gain 35 dB and Hi-Z on
w.save_timer.stop()
w.save_setup()
s = octa_setup.load(p2)
check('saved drive, gain and Hi-Z', s['units'][0][0]['drive'] == 24.0 and s['units'][0][0]['pre_gain'] == 35.0 and s['units'][0][0]['hiz'] is True, s['units'][0][0])
check('an input never looked at keeps no gain', 'gain_known' not in s['units'][0][3])
w.close()
app.processEvents()

w = window(p2)
check('new window shows the saved drive', w.strip.k['drive'].val == 24.0 or w.units[0]['chans'][0]['drive'] == 24.0)
L = w.units[0]['link']
w.on_flags(0, EMPTY)
blk = [c for c in L.calls if c[0] == 'blk']
check('wiped box gets its block back with its values', ('blk', 0, 'tube', True, 24.0) in blk and len(blk) == 1, L.calls)
check('wiped box gets its gain and Hi-Z', ('gain', 0, 70) in L.calls and ('hiz', 0, True) in L.calls, L.calls)
check('gains of unseen inputs are not sent', not [c for c in L.calls if c[0] == 'gain' and c[1] != 0], L.calls)
check('48V is never restored', not [c for c in L.calls if c[0] == '48v'], L.calls)
check('saved block stays on in the app', w.units[0]['chans'][0]['on']['tube'] is True)
w.strip.on_preamp.__self__.chans[0]['pre_gain'] = 35.0
L.calls.clear()
fl = [set() for _ in range(8)]
fl[0] = {'hpf'}
w.on_flags(0, fl)
check('a box that still has its strip is not rewritten', not [c for c in L.calls if c[0] in ('blk', 'gain', 'hiz')], L.calls)
check('...and the app shows what the box has', w.units[0]['chans'][0]['on']['hpf'] is True and w.units[0]['chans'][0]['on']['tube'] is False)
w.rig = octa_setup.clean_rig(dict(patchbay={'0': {'4': 0}}))
L.calls.clear()
w.on_flags(0, fl)
check('routes are checked on every connect', ('patch', 4, 0) in L.calls, L.calls)

L.calls.clear()
L2 = w.units[1]['link']
w.has_saved = True
saved_before = json.dumps(octa_setup.load(p2))
w.engine = type('E', (), dict(ports=lambda self, k: ('port', k), restart=lambda self: None, fgain=lambda self, b, g: None, feed=lambda self, d: None))()
rc.RECORDINGS = tmp
w.rig['serials'] = {}
w.on_engine_ready(2, 44100, ['SERIAL_A', 'SERIAL_B'])
check('first time with both on: learns the serials in order', w.rig['serials'] == {'SERIAL_A': 0, 'SERIAL_B': 1} and w.box_unit == [0, 1], w.rig)
check('...and saves them', octa_setup.load(p2)['rig']['serials'] == {'SERIAL_A': 0, 'SERIAL_B': 1})
w.on_engine_ready(1, 44100, ['SERIAL_B'])
check('only unit 2 on: it is still unit 2', w.box_unit == [1] and w.unit_ok == {1}, (w.box_unit, w.unit_ok))
check('...unit 1 waits', w.units[0]['link'].paused is True and L2.paused is False)
check('...unit 2 talks through engine box 1', L2.transport() == ('port', 0), L2.transport())
L.calls.clear()
L2.calls.clear()
w.on_flags(1, EMPTY)
check('...and gets unit 2 settings back, not unit 1', [c for c in L2.calls if c[0] == 'blk'] and not L.calls, (L.calls, L2.calls))
w.on_engine_ready(1, 44100, ['STRANGER'])
check('an unknown box alone is placed nowhere', w.box_unit == [None] and not w.unit_ok, w.box_unit)
L.calls.clear()
before = json.dumps(w.units[0]['chans'][0], sort_keys=True)
w.on_flags(0, EMPTY)
w.strip.on_preamp(0, 0, 0, 0) if False else None
check('...its empty strip is not written into your settings', json.dumps(w.units[0]['chans'][0], sort_keys=True) == before and not L.calls)
w.on_engine_ready(2, 44100, ['SERIAL_A', 'NEWBOX'])
check('a new box next to a known one takes the free place', w.box_unit == [0, 1] and w.rig['serials'] == {'SERIAL_A': 0, 'NEWBOX': 1}, w.rig['serials'])
check('...and the saved map stays valid', octa_setup.load(p2)['rig']['serials'] == {'SERIAL_A': 0, 'NEWBOX': 1}, octa_setup.load(p2)['rig'])
w.on_engine_ready(2, 44100, ['SERIAL_A', 'SERIAL_B'])
check('the old box comes back as unit 2 too', w.box_unit == [0, 1], w.box_unit)
w.rig['serials'] = {}
w.engine = None
w.usb_rate, w.usb_boxes, w.unit_ok, w.box_unit = 0, 0, set(), []

restarts = []
w.engine = type('E', (), dict(restart=lambda self: restarts.append(time.time()), fgain=lambda self, b, g: None, feed=lambda self, d: None))()
w.usb_boxes = 2
w.unit_ok = {0, 1}
w.units[0]['link'].last_ok = w.units[1]['link'].last_ok = time.time()
w.watch_links()
check('answering boxes: no restart', not restarts)
w.units[1]['link'].last_ok = time.time() - 30
w.watch_links()
check('a silent box restarts the engine', len(restarts) == 1)
w.watch_links()
check('...but not in a tight loop', len(restarts) == 1)
w.engine_next = 0
w.recording = True
w.watch_links()
check('never during a take', len(restarts) == 1)
w.recording = False
restarts.clear()
w.engine_nobox, w.engine_next = True, 0
w.units[1]['link'].last_ok = time.time()
w.watch_links()
check('rack off: keeps looking for the boxes', len(restarts) == 1)
w.watch_links()
check('...at a calm pace', len(restarts) == 1)
w.engine_next = 0
w.recording = True
w.watch_links()
check('...and never during a take', len(restarts) == 1)
w.recording = False
w.engine_dead = True
w.header.units.clear()
w.rec_info.setText('')
rc.RECORDINGS = tmp
w.engine.ports = lambda k: ('port', k)
w.on_engine_ready(2, 44100, ['SERIAL_A', 'SERIAL_B'])
check('boxes found: stops looking', w.engine_nobox is False and w.engine_dead is False and w.usb_boxes == 2)
restarts.clear()
for u in w.units:
    u['link'].last_ok = time.time()
w.engine_next = 0
w.watch_links()
check('...and does not restart what just connected', not restarts)
w.units[1]['link'].busy = True
w.units[1]['link'].last_ok = time.time() - 60
w.watch_links()
check('a box busy with a long test is not taken for dead', not restarts)
w.units[1]['link'].busy = False
w.units[1]['link'].last_ok = time.time()
w.usb_boxes, w.unit_ok, w.engine_next, w.count_next = 1, {1}, 0, 1e18
w.octas_seen = 2
w.watch_links()
check('a box that booted late: the engine looks again', len(restarts) == 1)
w.watch_links()
check('...once, not in a loop', len(restarts) == 1)
w.recording = True
w.octas_seen, w.engine_next = 2, 0
w.watch_links()
check('...and never during a take', len(restarts) == 1)
w.recording = False
w.engine = None
w.close()
app.processEvents()

# ---------------- send to stream ----------------
st = octa_setup.clean_stream(dict(name='  ', device='x' * 80, sends={'0': [0, 0, 9, 'a', True], '5': [1]}))
check('stream route: bad names, devices and inputs fall back or drop', st == dict(name='Discord', device='CABLE Input', sends={'0': [0]}), st)
w = window(os.path.join(tmp, 's3.json'))


class FeedEngine:
    def __init__(self):
        self.gains, self.devices = {}, []

    def fgain(self, b, g):
        self.gains[b] = list(g)

    def feed(self, d):
        self.devices.append(d)


E = w.engine = FeedEngine()
w.usb_rate, w.usb_boxes, w.box_unit, w.unit_ok = 44100, 2, [1, 0], {0, 1}     # engine box 0 is unit 2 here, on purpose
m = w.rail.build_menu(0)
acts = {a.text(): a for a in m.actions() if a.text()}
check('right-click menu has the send, named Discord by default', 'Send to Discord' in acts and acts['Send to Discord'].isCheckable(), list(acts))
check('...and the settings', 'Discord settings...' in acts)
acts['Send to Discord'].trigger()
app.processEvents()
check('send ticks the input and shows the mark', w.rail.btns[0].stream and not w.rail.btns[1].stream)
check('the send goes to the right box (unit 1 = engine box 1 here)', E.gains.get(1, [0])[0] == 1.0 and not any(E.gains.get(0, [1])), E.gains)
check('sending starts on the VB-Cable device', E.devices[-1] == 'CABLE Input', E.devices)
check('only that input, no others', sum(1 for g in E.gains[1] if g) == 1, E.gains)
check('the mark draws without trouble', not w.rail.btns[0].grab().isNull())
w.toggle_send(2)
check('a second input joins the same send', E.gains[1][2] == 1.0 and E.gains[1][0] == 1.0 and E.devices.count('CABLE Input') == 1, (E.gains, E.devices))
check('it is saved', octa_setup.load(w.setup_path)['rig']['stream']['sends'] == {'0': [0, 2]})
w.select_unit(1)
check('the other unit shows its own marks (none)', not any(b.stream for b in w.rail.btns))
w.select_unit(0)
check('...and back', w.rail.btns[0].stream and w.rail.btns[2].stream)
w.set_stream_route('OBS', 'Headphones (Oculus')
m = w.rail.build_menu(0)
check('renamed: the menu says the new name', 'Send to OBS' in [a.text() for a in m.actions()], [a.text() for a in m.actions()])
check('a new device restarts the sending on it', E.devices[-1] == 'Headphones (Oculus', E.devices)
w.toggle_send(0)
w.toggle_send(2)
check('nothing ticked: the sending stops', E.devices[-1] is None and not any(E.gains[1]), (E.devices, E.gains))
w.on_feed_status('FEED nodevice')
check('missing device says what to do', 'VB-Cable' in w.rec_info.text() and 'OBS' in w.rec_info.text(), w.rec_info.text())
w.on_feed_status('FEED on 48000 2 CABLE Input (VB-Audio Virtual Cable)')
check('running says where it goes', 'sending to CABLE Input' in w.rec_info.text(), w.rec_info.text())
w.engine = None
w.close()
app.processEvents()

# ---------------- the Focusrite keeper ----------------
ARRIVAL = ('<device-arrival><device id="%s" protocol="USB" model="Scarlett 6i6 (2nd Gen)"><mixer><inputs><add-stereo-input id="15"/>'
           '<free-inputs id="17"/><input><source id="18"/><stereo id="19"/></input><input><source id="20"/><stereo id="21"/></input>'
           '<input><source id="22"/><stereo id="23"/></input></inputs><mixes></mixes></mixer><inputs>'
           '<analogue id="807" name="Analogue 3" stereo-name="Analogue 3-4"/><spdif-rca id="815" name="S/PDIF 1" stereo-name="S/PDIF 1-2"/>'
           '<analogue id="795" name="Analogue 1" stereo-name="Analogue 1-2"/></inputs></device></device-arrival>')


class FakeServer:
    """speaks just enough of Focusrite Control: accepts sets only for the current device number"""

    def __init__(self, devid='3', approved=True, have=()):
        self.devid, self.approved, self.slots, self.sets = devid, approved, list(have), []
        self.ls = socket.socket()
        self.ls.bind(('127.0.0.1', 0))
        self.ls.listen(1)
        self.port = self.ls.getsockname()[1]
        self.conn = None
        threading.Thread(target=self.run, daemon=True).start()

    def tx(self, x):
        self.conn.sendall(focusrite.frame(x))

    def values(self):
        return '<set devid="%s">' % self.devid + ''.join('<item id="%d" value="%s"/>' % (18 + 2 * i, s) for i, s in enumerate(self.slots)) + '</set>'

    def run(self):
        self.conn, _ = self.ls.accept()
        self.conn.settimeout(0.1)
        buf = b''
        while True:
            try:
                d = self.conn.recv(65536)
                if not d:
                    return
                buf += d
            except socket.timeout:
                continue
            except OSError:
                return
            msgs, buf = focusrite.split_frames(buf)
            for t in msgs:
                if 'client-details' in t:
                    self.tx('<approval hostname="x" id="1" type="response" authorised="%s"/>' % ('true' if self.approved else 'false'))
                    self.tx(ARRIVAL % self.devid)
                elif 'device-subscribe' in t:
                    self.tx(self.values())
                elif '<set' in t:
                    self.sets.append(t)
                    if 'devid="%s"' % self.devid in t:
                        self.slots.append(t.split('value="')[1].split('"')[0])
                        self.tx(self.values())

    def rearrive(self, devid):
        self.devid, self.slots = devid, ['795']
        self.tx('<device-removal id="3"/>')
        self.tx(ARRIVAL % devid)


WANT = ['Analogue 3-4', 'S/PDIF 1-2']
a = focusrite.parse_arrival(ARRIVAL % '3')
check('reads the device number from the server', a['devid'] == '3' and a['add'] == '15')
check('finds inputs by name', a['names']['S/PDIF 1-2'] == '815' and a['slots'] == ['18', '20', '22'], a)
ml, rest = focusrite.split_frames(focusrite.frame('<a/>') + focusrite.frame('<b/>')[:6])
check('frames split, partial one waits', ml == ['<a/>'] and rest.startswith(b'Length'))


def wait(fn, secs):
    end = time.time() + secs
    while time.time() < end and not fn():
        time.sleep(0.05)
    return fn()


logs = []
srv = FakeServer(have=['795'])
kp = focusrite.Keeper(WANT, log=logs.append, port_finder=lambda: srv.port)
kp.start()
check('adds both missing inputs', wait(lambda: '807' in srv.slots and '815' in srv.slots, 12), srv.slots)
check('uses the current device number', all('devid="3"' in s for s in srv.sets), srv.sets)
time.sleep(4)
check('never adds duplicates', srv.slots.count('807') == 1 and srv.slots.count('815') == 1, srv.slots)
srv.rearrive('7')
check('follows a power cycle to a new device number', wait(lambda: srv.slots.count('815') == 1 and '807' in srv.slots, 12), srv.slots)
check('...and writes to the new number', any('devid="7"' in s for s in srv.sets), srv.sets)
kp.stop = True

srv = FakeServer(approved=False, have=['795'])
kp = focusrite.Keeper(WANT, log=logs.append, port_finder=lambda: srv.port)
kp.start()
time.sleep(5)
check('not approved: changes nothing', not srv.sets, srv.sets)
kp.stop = True

srv = FakeServer(have=['795', '807', '815'])
kp = focusrite.Keeper(WANT, log=logs.append, port_finder=lambda: srv.port)
kp.start()
time.sleep(4)
check('all present: changes nothing', not srv.sets, srv.sets)
kp.stop = True


class Deaf(FakeServer):                              # server swallows the add: must give up, not hammer
    def run(self):
        self.conn, _ = self.ls.accept()
        self.conn.settimeout(0.1)
        buf = b''
        while True:
            try:
                d = self.conn.recv(65536)
                if not d:
                    return
                buf += d
            except socket.timeout:
                continue
            except OSError:
                return
            msgs, buf = focusrite.split_frames(buf)
            for t in msgs:
                if 'client-details' in t:
                    self.tx('<approval type="response" authorised="true"/>')
                    self.tx(ARRIVAL % '3')
                elif '<set' in t:
                    self.sets.append(t)


srv = Deaf(have=['795'])
kp = focusrite.Keeper(['S/PDIF 1-2'], log=logs.append, port_finder=lambda: srv.port)
kp.start()
time.sleep(12)
check('gives up after 3 tries', len(srv.sets) == 3, len(srv.sets))
kp.stop = True

print('\n%s' % ('ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
