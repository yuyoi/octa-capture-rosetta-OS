"""Off-hardware tests for mic compensation: the fit against curves with a known answer, and the MIC button in the real window.
python -I app/tests/test_mic_comp.py"""
import os
import shutil
import sys
import tempfile

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, APP)
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import mic_comp                                       # noqa: E402
import octa_setup                                     # noqa: E402
from PySide6.QtCore import Qt                         # noqa: E402
from PySide6.QtTest import QTest                      # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402
import rosetta_control as rc                          # noqa: E402

fails = []
FS = rc.FS


def check(name, ok, info=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'   {info}' if info != '' and not ok else ''))
    if not ok:
        fails.append(name)


def make(stages):
    """a mic curve that is exactly these filters: three bands must be able to cancel it"""
    return [(f, rc.mag_db(stages, f)) for f in [40, 60, 90, 130, 200, 300, 450, 700, 1000, 1500, 2200, 3300, 5000, 7000, 10000, 14000, 18000]]


# parsing
c = mic_comp.parse_curve('# e935\n100, -3\n1000;0\n\n2000, 1\n4000,5\n8000,2\n12000,-4 # roll off')
check('parses commas, semicolons, comments', c[0] == (100, -3) and c[-1] == (12000, -4) and len(c) == 6, c)
for bad in ('100,0\n200,1', 'abc,1\n' * 7, '100,0,5\n' * 7, ''.join(f'{f},99\n' for f in range(100, 800, 100))):
    try:
        mic_comp.parse_curve(bad)
        check('rejects bad curve', False, bad[:20])
    except ValueError:
        check('rejects bad curve', True)
check('interpolates on a log axis', abs(mic_comp.at([(100, 0), (10000, 10)], 1000) - 5) < 1e-9)
check('flat past the ends', mic_comp.at([(100, 2), (1000, 3)], 20) == 2 and mic_comp.at([(100, 2), (1000, 3)], 20000) == 3)

# the fit
presence = make([rc.rbj('peak', FS, 4000.0, 5.0, 1.2)])
p, rms, err = mic_comp.fit(mic_comp.correction(presence), rc.rbj, rc.mag_db, FS)
check('cancels a single presence bump', rms < 0.25, rms)
check('...by cutting near 4 kHz', 2800 < p['peak_f'] < 5600 and p['peak_g'] < -3, p)
mic = make([rc.rbj('low', FS, 300.0, -3.0), rc.rbj('peak', FS, 3000.0, 4.0, 1.5), rc.rbj('high', FS, 7000.0, -3.0)])
p, rms, err = mic_comp.fit(mic_comp.correction(mic), rc.rbj, rc.mag_db, FS)
check('undoes a mic built from 3 bands', rms < 0.5, (rms, p))
dark = make([rc.rbj('high', FS, 12000.0, -20.0), rc.rbj('low', FS, 90.0, -20.0)])
p, rms, err = mic_comp.fit(mic_comp.correction(dark), rc.rbj, rc.mag_db, FS)
check('natural roll-offs are not boosted back', p['low_g'] <= mic_comp.MAX_BOOST and p['high_g'] <= mic_comp.MAX_BOOST, p)
check('boosts stay under the cap', max(p['low_g'], p['peak_g'], p['high_g']) <= mic_comp.MAX_BOOST, p)
flat = [(100, 0.0), (500, 0.0), (1000, 0.0), (4000, 0.0), (8000, 0.0), (12000, 0.0)]
p, rms, err = mic_comp.fit(mic_comp.correction(flat), rc.rbj, rc.mag_db, FS)
check('a flat mic gets (almost) no EQ', max(abs(p['low_g']), abs(p['peak_g']), abs(p['high_g'])) < 0.3, p)
wild = [(f, 30.0 if i % 2 else -30.0) for i, f in enumerate((100, 200, 400, 800, 1600, 3200, 6400, 12800))]
p, rms, err = mic_comp.fit(mic_comp.correction(wild), rc.rbj, rc.mag_db, FS)
check('never asks for more than the limit', max(abs(p['low_g']), abs(p['peak_g']), abs(p['high_g'])) <= 9, p)
check('stays inside the knobs', 60 <= p['low_f'] <= 500 and 200 <= p['peak_f'] <= 10000 and 3000 <= p['high_f'] <= 14000 and 0.4 <= p['peak_q'] <= 4, p)
a = make([rc.rbj('peak', FS, 2000.0, 6.0, 1.0)])
b = make([rc.rbj('peak', FS, 2000.0, 6.0, 1.0), rc.rbj('high', FS, 8000.0, 3.0)])
want = mic_comp.correction(a, b)
p, rms, err = mic_comp.fit(want, rc.rbj, rc.mag_db, FS)
check('makes one mic sound like another', rms < 0.4 and p['high_g'] > 1, (rms, p))
big = [c for c in mic_comp.correction(a, a)]
check('a mic matched to itself needs nothing', max(abs(x) for x in big) < 1e-9)

# the real window, real button
tmp = tempfile.mkdtemp()
mic_comp.FOLDER = tmp
with open(os.path.join(tmp, 'testmic.csv'), 'w') as f:
    f.write(''.join(f'{x},{y:.2f}\n' for x, y in mic))
app = QApplication([])


class FakeLink:
    def __init__(self):
        self.calls = []

    def set_block(self, ch, name, on, c):
        self.calls.append((ch, name, on, c['peak_f']))

    def set_gain(self, *a):
        pass

    def set_hiz(self, *a):
        pass

    def ensure_patchbay(self, *a, **k):
        pass


w = rc.Main(demo=True)
w.timer.stop()
w.setup_path = os.path.join(tmp, 'setup.json')
L = w.units[0]['link'] = FakeLink()
w.strip.link = L
w.select_unit(0)
w.resize(1460, 950)
w.show()
app.processEvents()
c = w.units[0]['chans'][0]
before = {k: c[k] for k in mic_comp.KEYS}
c['on'].update(low=False, peak=True, high=False)
QTest.mouseClick(w.strip.mic_btn, Qt.LeftButton)
check('click picks the mic', c.get('mic') == 'testmic' and w.strip.mic_btn.text() == 'MIC TESTMIC', w.strip.mic_btn.text())
check('all three bands turned on', c['on']['low'] and c['on']['peak'] and c['on']['high'])
check('the fitted bands reach the box', {x[1] for x in L.calls} == {'low', 'peak', 'high'} and all(x[2] for x in L.calls), L.calls)
check('EQ knobs show the fit', w.strip.k['peak_f'].val == c['peak_f'] and c['peak_f'] != before['peak_f'])
check('the dashed line appears', bool(w.strip.eq.overlay))
check('the status says how good the fit is', 'dB left' in w.rec_info.text(), w.rec_info.text())
check('counts as a change to save', w.save_timer.isActive())
pic = os.path.join(tmp, 'shot.png')
w.grab().save(pic)
check('window still draws with the overlay', os.path.getsize(pic) > 20000)
w.save_timer.stop()
w.save_setup()
s = octa_setup.load(w.setup_path)
check('mic and the old EQ are saved', s['units'][0][0]['mic'] == 'testmic' and s['units'][0][0]['mic_prev']['peak_f'] == before['peak_f'], s['units'][0][0])
L.calls.clear()
QTest.mouseClick(w.strip.mic_btn, Qt.LeftButton)
check('next click is off', 'mic' not in c and w.strip.mic_btn.text() == 'MIC OFF', w.strip.mic_btn.text())
check('off puts the old EQ back', all(c[k] == before[k] for k in mic_comp.KEYS), c)
check('off puts the old on/off back', c['on']['low'] is False and c['on']['peak'] is True and c['on']['high'] is False, c['on'])
check('...on the box too', ('low' in {x[1] for x in L.calls}) and any(x[1] == 'peak' and x[2] for x in L.calls) and any(x[1] == 'low' and not x[2] for x in L.calls), L.calls)
check('dashed line gone', w.strip.eq.overlay is None)
w.set_mic(0, 0, 'no_such_mic')
check('a missing curve changes nothing', 'mic' not in c and 'no_such_mic' in w.rec_info.text(), w.rec_info.text())
w.close()
app.processEvents()
shutil.rmtree(tmp, ignore_errors=True)
print('\n%s' % ('ALL PASS' if not fails else 'FAILED: ' + ', '.join(fails)))
sys.exit(1 if fails else 0)
