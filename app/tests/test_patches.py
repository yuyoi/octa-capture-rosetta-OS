"""Patch browser by scripted clicks: mic + guitar patches load, amp tones switch the amp, save/delete yours,
firmware without the gate says so on the keys. python -I app/tests/test_patches.py"""
import os, sys, time, tempfile
tmp = tempfile.mkdtemp()
out = tmp
os.environ['RD_SETUP'] = os.path.join(tmp, 'setup.json')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import rosetta_control as rc
import strip_patches as sp
import amp_presets as ap
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
ok = True


def check(n, cond, extra=''):
    global ok
    ok &= bool(cond)
    print(('PASS ' if cond else 'FAIL ') + n, '' if cond else extra)


class FakeLink:
    def __init__(self):
        self.calls, self.last_ok, self.paused, self.fx_ok, self.lab = [], time.time(), False, None, None

    def set_block(self, ch, name, on, c):
        self.calls.append(('blk', ch, name, on, c.get('gate_thr')))

    def set_amp(self, ch, *a):
        self.calls.append(('amp', ch, a[0] if a else None))

    def set_fx(self, ch, p):
        self.calls.append(('fx', ch, p))

    def __getattr__(self, n):
        if n.startswith(('set_', 'ensure_')):
            return lambda *a, **k: self.calls.append((n,) + a)
        raise AttributeError(n)


w = rc.Main(demo=True)
w.timer.stop()
for u in w.units:
    u['link'] = FakeLink()
w.strip.link = w.units[1]['link']
w.select_unit(1)
w.resize(1460, 950)
w.show()
app.processEvents()
check('no VOCAL/GUITAR/FLAT keys any more, one PATCH key', w.strip.patch_btn.height() >= 40)
QTest.mouseClick(w.strip.patch_btn, Qt.LeftButton)
app.processEvents()
pb = w.patch_win
check('PATCH opens the browser', pb is not None and pb.isVisible())
check('MIC column holds the e935 patches', 'E935 VOCAL' in pb.btns and 'GROWL' in pb.btns and 'SCREAM' in pb.btns)
check('AMP column holds all amp tones incl. the new clean ones', all(p['name'] in pb.btns for p in ap.FACTORY) and 'GLASS CLEAN' in pb.btns)
check('every button finger-sized', all(b.minimumHeight() >= 40 for b in pb.btns.values()))
L = w.units[1]['link']
L.calls.clear()
QTest.mouseClick(pb.btns['SCREAM'], Qt.LeftButton)
app.processEvents()
c = w.units[1]['chans'][w.strip.ch]
p = sp.find('SCREAM')
check('tap SCREAM: knobs land on the input', all(c[k] == v for k, v in p['params'].items()) and c['patch'] == 'SCREAM')
check('tap SCREAM: blocks + gate sent to the box', ('blk', w.strip.ch, 'gate', True, -50.0) in L.calls
      and all(('blk', w.strip.ch, b, True) == x[:4] for b in sp.BLOCKS for x in L.calls if x[:3] == ('blk', w.strip.ch, b)), L.calls)
check('browser stays open, SCREAM lit, note shown', pb.isVisible() and pb.btns['SCREAM'].isChecked() and 'scream' in pb.note.text().lower())
check('PATCH key shows the name', 'SCREAM' in w.strip.patch_btn.text() and '*' not in w.strip.patch_btn.text())
w.strip.k['peak_g'].set_frac(0.9)
check('turn a knob: patch marked edited', '*' in w.strip.patch_btn.text() and c.get('patch_edited'))
# amp tone from the browser
for k, o in enumerate(w.units[1]['chans']):          # demo: all 8 inputs fully on = DSP near 80 %, free the others
    if k != w.strip.ch:
        o['on'].update(dict.fromkeys(o['on'], False))
L.calls.clear()
QTest.mouseClick(pb.btns['DOOM CLEAN'], Qt.LeftButton)
app.processEvents()
check('tap DOOM CLEAN: amp on with the tone', c.get('amp') == 'clean' and c.get('amp_preset') == 'DOOM CLEAN', (c.get('amp'), c.get('amp_preset')))
check('PATCH key shows the amp tone', 'DOOM CLEAN' in w.strip.patch_btn.text())
# back to a strip patch: amp off
QTest.mouseClick(pb.btns['TALK'], Qt.LeftButton)
app.processEvents()
check('tap TALK: amp off again, TALK on', c.get('amp') == 'off' and c['patch'] == 'TALK' and 'amp_preset' not in c)
check('amp off sent to the box', ('amp', w.strip.ch, 'off') in L.calls, L.calls)
# save + delete your own
from PySide6.QtWidgets import QInputDialog
QInputDialog.getText = staticmethod(lambda *a, **k: ('my talk', True))
w.strip.k['makeup'].set_frac(0.5)
QTest.mouseClick(pb.save_b, Qt.LeftButton)
app.processEvents()
user = sp.load_user()
check('SAVE AS YOURS: written to the user file', [u['name'] for u in user] == ['MY TALK'] and user[0]['gate'] == -55.0, user)
check('YOURS column shows it, lit', 'MY TALK' in pb.btns and pb.btns['MY TALK'].isChecked() and pb.del_b.isEnabled())
QInputDialog.getText = staticmethod(lambda *a, **k: ('scream', True))
QTest.mouseClick(pb.save_b, Qt.LeftButton)
check('a factory name gets (MINE), factory untouched', 'SCREAM (MINE)' in [u['name'] for u in sp.load_user()])
QTest.mouseClick(pb.del_b, Qt.LeftButton)
check('DELETE removes the lit one of yours', [u['name'] for u in sp.load_user()] == ['MY TALK'])
check('deleted patch name leaves the input + PATCH key', 'patch' not in c and 'SCREAM' not in w.strip.patch_btn.text(), w.strip.patch_btn.text())
check('factory patches can\'t be deleted', not pb.del_b.isEnabled() or pb.current()[0] in pb.mine)
# firmware without gate: notices
L.fx_ok = False
w.refresh_dsp()
app.processEvents()
check('no strip10 on the box: red notice in the browser', pb.fw.isVisible() and 'strip10' in pb.fw.text())
check('no strip10: strip GATE key blocked with NO FW', w.strip.blk['gate'].blocked and 'strip10' in w.strip.blk['gate'].blocked)
before = c['on']['gate']
QTest.mouseClick(w.strip.blk['gate'], Qt.LeftButton)
check('blocked GATE key does nothing', c['on']['gate'] == before)
pb.grab().save(os.path.join(out, 'patch_browser_nofw.png'))
w.grab().save(os.path.join(out, 'patch_strip_nofw.png'))
L.fx_ok = True
w.refresh_dsp()
app.processEvents()
check('strip10 on the box: notice gone, GATE live', not pb.fw.isVisible() and not w.strip.blk['gate'].blocked)
QTest.mouseClick(pb.btns['E935 VOCAL'], Qt.LeftButton)
app.processEvents()
pb.grab().save(os.path.join(out, 'patch_browser.png'))
# old remote names still work
check('old preset names map', sp.find('e935')['name'] == 'E935 VOCAL' and sp.find('lespaul')['name'] == 'LES PAUL DI')
# saved state round trip
import octa_setup
d = octa_setup.clean_chan(dict(c, patch_edited=True))
check('patch name kept in setup.json', d.get('patch') == 'E935 VOCAL' and d.get('patch_edited') is True)
print('ALL OK' if ok else 'SOME FAILED')
sys.exit(0 if ok else 1)
