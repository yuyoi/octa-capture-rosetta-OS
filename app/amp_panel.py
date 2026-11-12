"""The Rosetta amp head: what the EQ screen turns into when an input is in amp mode.

A modern head in the app's own OCTA-CAPTURE look: dark anodised chassis with a brushed-silver edge, a wide-spaced
silver ROSETTA wordmark, a dark-glass display in blue ink (channel, last knob moved, its real value), a glass window
where the app's tube art glows brighter with GAIN between vent slots, lit channel keys, and the app's knob (black rubber
cap, brushed skirt, blue dot ring) numbered 0-10. All QPainter, scales with the window.

Knob positions (0-10) are what is saved; amp_db() turns them into what the box's amp block and tone stack get.
"""
import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
TUBES = os.path.join(HERE, '..', 'art', 'app', 'tube')

VOICES = ('clean', 'crunch', 'lead', 'cranked', 'fuzz')
KNOBS = (('amp_gain', 'GAIN'), ('amp_bass', 'BASS'), ('amp_mid', 'MIDDLE'), ('amp_treble', 'TREBLE'),
         ('amp_pres', 'PRESENCE'), ('amp_master', 'MASTER'), ('amp_gate', 'GATE'), ('amp_echo', 'ECHO'),
         ('amp_time', 'TIME'), ('amp_rev', 'REVERB'))
FX_KNOBS = ('amp_gate', 'amp_echo', 'amp_time', 'amp_rev')
FX_DEFAULT = dict(amp_gate=0.0, amp_echo=0.0, amp_time=6.0, amp_rev=0.0)


def gate_db(v):
    """GATE knob: 0 = off, 1-10 = -70..-25 dBFS (the input level it opens at; the Les Paul hisses at about -53)"""
    return None if v <= 0 else -75.0 + 5.0 * min(10.0, v)


def echo_ms(v):
    """TIME knob 0-10 -> 60..1200 ms, even steps by ear (log)"""
    return 60.0 * 20 ** (max(0.0, min(10.0, v)) / 10)


def fx_params(c):
    """what the box's echo + reverb get for an input in amp mode, or None (both off)"""
    e, r = c.get('amp_echo', 0.0), c.get('amp_rev', 0.0)
    if e <= 0 and r <= 0:
        return None
    tw = c.get('amp_tweaks') or {}
    return dict(echo_ms=echo_ms(c.get('amp_time', 6.0)), echo_fb=tw.get('echo_fb', 0.35), echo_mix=0.07 * e,
                echo_tone=tw.get('echo_tone', 3500.0), rev_mix=0.06 * r, rev_size=tw.get('rev_size', 0.5),
                rev_damp=tw.get('rev_damp', 0.5))
TONE = dict(amp_bass=('low', 150.0, None), amp_mid=('peak', 650.0, 0.8), amp_treble=('high', 2800.0, None))
VOICE_GAIN = dict(clean=12.0, crunch=26.0, lead=40.0, cranked=24.0, fuzz=46.0)          # tools/octa_lab.py AMP_VOICES
VOICE_PRES = dict(clean=2.0, crunch=3.0, lead=4.0, cranked=2.0, fuzz=1.0)
VOICE_MASTER = dict(clean=-3.0, crunch=-3.0, lead=-4.0, cranked=-3.0, fuzz=-8.0)
FUZZ_MID = (1000.0, 0.6)                # the big-box fuzz scoop sits higher and wider than an amp's mids

# the app's palette (rosetta_control.py)
TEXT = QColor('#c9ced4')
GREY_T = QColor('#8d939b')
BLUE = QColor('#2d6bd2')
BLUE_LIT = QColor('#62b2ff')
LCD_BG = QColor('#070a10')
LCD_INK = QColor('#62b2ff')


def amp_db(key, v):
    """knob 0-10 -> dB for the box"""
    v = max(0.0, min(10.0, v))
    if key == 'amp_gain':
        return 4.6 * v                          # 0-46 dB, the amp block's range
    if key in TONE:
        return (v - 5.0) * 2.4                  # -12 .. +12 dB, 5 = flat
    if key == 'amp_pres':
        return -3.0 + 1.2 * v                   # -3 .. +9 dB cone presence
    if key == 'amp_master':
        return -30.0 + 3.6 * v                  # -30 .. +6 dB after the speaker
    raise KeyError(key)


def voice_defaults(voice):
    """knob positions that give a voice's own settings (tone knobs at noon)"""
    d = dict(amp_gain=round(VOICE_GAIN[voice] / 4.6, 1), amp_pres=round((VOICE_PRES[voice] + 3.0) / 1.2, 1),
             amp_master=round((VOICE_MASTER[voice] + 30.0) / 3.6, 1), amp_hard=1.0 if voice == 'fuzz' else 0.0)
    if voice == 'fuzz':
        d['amp_mid'] = 2.5                      # starts scooped, -6 dB at 1 kHz
    return d


def tone_override(c):
    """the strip block settings that make low/peak/high act as the amp's BASS/MIDDLE/TREBLE"""
    out = {}
    for key, (block, f, q) in TONE.items():
        g = amp_db(key, c.get(key, 5.0))
        if block == 'low':
            out.update(low_f=f, low_g=g)
        elif block == 'peak':
            if c.get('amp') == 'fuzz':
                f, q = FUZZ_MID
            tw = c.get('amp_tweaks') or {}
            f, q = tw.get('mid_f', f), tw.get('mid_q', q)
            out.update(peak_f=f, peak_g=g, peak_q=q)
        elif g > 0:                     # a high-shelf boost needs a coefficient > 2 (no room in the box's Q30): the same
            out.update(high_f=f, high_g=-g, high_kind='low')   # curve exactly = low-shelf cut + that much more level,
        else:                           # which treble_makeup() puts on MASTER (checked 0.0000 dB apart, 20 Hz-20 kHz)
            out.update(high_f=f, high_g=g)
    return out


def treble_makeup(c):
    """dB MASTER carries for a TREBLE boost (tone_override sends it as a low-shelf cut)"""
    return max(0.0, amp_db('amp_treble', c.get('amp_treble', 5.0)))


def _font(px, bold=True, spacing=104):
    f = QFont('Segoe UI')
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    f.setLetterSpacing(QFont.PercentageSpacing, spacing)
    return f


def _silver(r):
    g = QLinearGradient(r.topLeft(), r.bottomLeft())
    for t, c in ((0, '#ffffff'), (0.45, '#c9ced4'), (0.55, '#9aa0a8'), (1, '#e6e9ec')):
        g.setColorAt(t, QColor(c))
    return g


def _fine(e, x0):
    """Shift = fine; on touch slide the finger sideways off the knob: the further, the finer (down to 1/6)"""
    if e.modifiers() & Qt.ShiftModifier:
        return 0.25
    return max(1 / 6, 1.0 / (1.0 + max(0.0, abs(e.position().x() - x0) - 30) / 50))


class AmpKnob(QWidget):
    """the app's knob, amp-scaled: blue dot ring with 0-10 printed round it, value under it"""
    changed = Signal(float)

    def __init__(self, label, value=5.0, parent=None):
        super().__init__(parent)
        self.label, self.val = label, value
        self.default = value
        self.readout = ''
        self._drag = None
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip(f'{label}: drag up/down (slide sideways or Shift = fine), wheel, double-tap = default')

    def set_value(self, v, emit=True):
        v = round(max(0.0, min(10.0, v)), 1)
        if v != self.val:
            self.val = v
            self.update()
            if emit:
                self.changed.emit(v)

    def mousePressEvent(self, e):
        self._drag = (e.position().x(), e.position().y(), self.val)

    def mouseMoveEvent(self, e):
        if self._drag:                                 # exact value kept here, the knob shows it rounded to 0.1
            x0, y_last, v = self._drag
            v = max(0.0, min(10.0, v + (y_last - e.position().y()) / 18.0 * _fine(e, x0)))
            self._drag = (x0, e.position().y(), v)
            self.set_value(v)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.set_value(self.default)

    def wheelEvent(self, e):
        self.set_value(self.val + (0.5 if e.angleDelta().y() > 0 else -0.5))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        R = min(w * 0.30, (h - 110) * 0.5)
        cx, cy = w / 2, h / 2 + 4                              # label, ring and value as one block, centred
        a0, sweep, f = 225.0, -270.0, self.val / 10.0
        p.setFont(_font(13, True, 112))
        p.setPen(QColor('#e6e9ec'))
        p.drawText(QRectF(0, cy - R - 58, w, 18), Qt.AlignCenter, self.label)
        # LED dot ring: 41 dots, lit ones blue with a soft glow, the 11 numbered ones bigger
        for i in range(41):
            t = i / 40
            a = math.radians(a0 + sweep * t)
            rr = R + 8
            lit = t <= f + 1e-6
            x, y = cx + rr * math.cos(a), cy - rr * math.sin(a)
            p.setPen(Qt.NoPen)
            if lit:
                p.setBrush(QColor(98, 178, 255, 60))
                p.drawEllipse(QPointF(x, y), 4.2, 4.2)
            p.setBrush(BLUE_LIT if lit else QColor(225, 228, 232, 110))
            d = 2.1 if i % 4 == 0 else 1.3
            p.drawEllipse(QPointF(x, y), d, d)
        p.setFont(_font(max(9, R * 0.24), True))
        for i in range(11):
            a = math.radians(a0 + sweep * i / 10)
            rr = R + 21
            pt = QPointF(cx + rr * math.cos(a), cy - rr * math.sin(a))
            p.setPen(BLUE_LIT if i / 10 <= f + 1e-6 else GREY_T)
            p.drawText(QRectF(pt.x() - 10, pt.y() - 8, 20, 16), Qt.AlignCenter, str(i))
        # shadow, brushed skirt, black rubber cap
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawEllipse(QPointF(cx + 2, cy + 3), R, R)
        sk = QConicalGradient(QPointF(cx, cy), 30)
        for t, c in ((0, '#f2f4f6'), (0.15, '#80868d'), (0.3, '#dcdfe2'), (0.45, '#6f757c'), (0.6, '#eef0f2'),
                     (0.75, '#868c93'), (0.9, '#d2d6da'), (1, '#f2f4f6')):
            sk.setColorAt(t, QColor(c))
        p.setBrush(sk)
        p.drawEllipse(QPointF(cx, cy), R, R)
        cap = QRadialGradient(QPointF(cx - R * 0.3, cy - R * 0.35), R * 1.1)
        cap.setColorAt(0, QColor('#4a4e54'))
        cap.setColorAt(0.45, QColor('#1d1f23'))
        cap.setColorAt(1, QColor('#0a0b0d'))
        p.setBrush(cap)
        p.setPen(QPen(QColor(0, 0, 0, 200), 1))
        p.drawEllipse(QPointF(cx, cy), R * 0.86, R * 0.86)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        for i in range(32):                                     # rubber grip ridges
            a = math.radians(i * 360 / 32)
            p.drawLine(QPointF(cx + R * 0.74 * math.cos(a), cy - R * 0.74 * math.sin(a)),
                       QPointF(cx + R * 0.86 * math.cos(a), cy - R * 0.86 * math.sin(a)))
        a = math.radians(a0 + sweep * f)
        p.setPen(QPen(QColor('#f4f6f8'), max(2.4, R * 0.07), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx + R * 0.25 * math.cos(a), cy - R * 0.25 * math.sin(a)),
                   QPointF(cx + R * 0.80 * math.cos(a), cy - R * 0.80 * math.sin(a)))
        p.setFont(_font(12, True))
        p.setPen(TEXT)
        p.drawText(QRectF(0, cy + R + 26, w, 18), Qt.AlignCenter, self.readout or f'{self.val:.1f}')


class ChannelKey(QWidget):
    """a channel key: the app's button, with an LED that lights blue on the chosen channel"""
    clicked = Signal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.text, self.lit = text, False
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self.lit:
            g.setColorAt(0, QColor('#3f86f0'))
            g.setColorAt(1, QColor('#1f4fa8'))
            p.setPen(QPen(BLUE_LIT, 1.4))
        else:
            g.setColorAt(0, QColor('#2a2d32'))
            g.setColorAt(1, QColor('#17191c'))
            p.setPen(QPen(QColor('#5a6068'), 1.2))
        p.setBrush(g)
        p.drawRoundedRect(r, 5, 5)
        lx, ly = r.left() + 14, r.center().y()                  # LED
        if self.lit:
            halo = QRadialGradient(QPointF(lx, ly), 9)
            halo.setColorAt(0, QColor(160, 215, 255, 200))
            halo.setColorAt(1, QColor(160, 215, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(lx, ly), 9, 9)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(QColor('#d8f0ff') if self.lit else QColor('#30343a'))
        p.drawEllipse(QPointF(lx, ly), 3.6, 3.6)
        p.setPen(QColor('#ffffff') if self.lit else TEXT)
        p.setFont(_font(min(13, r.height() * 0.38), True, 110))
        p.drawText(r.adjusted(16, 0, 0, 0), Qt.AlignCenter, self.text)


class AmpHead(QWidget):
    """the amp head. Signals: knob(key, 0-10), voice(name)"""
    knob = Signal(str, float)
    voice = Signal(str)
    preset = Signal(str)                                        # load this preset by name
    save_preset = Signal(str)                                   # save what runs now under this name
    delete_preset = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cur_voice, self.name, self.last, self.hard = 'crunch', '', 'amp_gain', False
        self.preset_name, self.edited = '', False
        self.fx_ok = None                                       # False: the box's firmware has no gate/echo/reverb
        self.setToolTip('Click the screen for tone presets')
        self.tubes = [QPixmap(os.path.join(TUBES, 'tube_glow_%03d.png' % k)) for k in (0, 25, 50, 75, 100)]
        self.tube_bg = self.tubes[0].toImage().pixelColor(3, 3) if not self.tubes[0].isNull() else LCD_BG
        self.knobs = {}
        for key, label in KNOBS:
            k = AmpKnob(label, 5.0, self)
            k.changed.connect(lambda v, kk=key: self._turned(kk, v))
            self.knobs[key] = k
        self.chans = {}
        for v in VOICES:
            b = ChannelKey(v.upper(), self)
            b.clicked.connect(lambda vv=v: self.voice.emit(vv))
            self.chans[v] = b
        self.hard_key = ChannelKey('HARD CLIP', self)
        self.hard_key.setToolTip('the gain stages clip hard (sharp corner, more edge) instead of the tube curve')
        self.hard_key.clicked.connect(self._toggle_hard)
        self.setMinimumSize(560, 380)
        self._readouts()

    def _toggle_hard(self):
        self.hard = not self.hard
        if self.preset_name:
            self.edited = True
        self.hard_key.lit = self.hard
        self.hard_key.update()
        self.update()
        self.knob.emit('amp_hard', 1.0 if self.hard else 0.0)

    def mousePressEvent(self, e):
        r, head, screen, window, keys, face = self._rects()
        if screen.contains(e.position()):
            self.preset_menu(e.globalPosition().toPoint())

    def preset_menu(self, at):
        """factory tones, yours, save, delete"""
        from PySide6.QtWidgets import QInputDialog, QMenu
        import amp_presets
        m = QMenu(self)
        m.setStyleSheet('QMenu{background:#0b0f16;color:#c9ced4;border:1px solid #2d6bd2;font:bold 14px "Segoe UI";}'
                        'QMenu::item{padding:13px 28px;}QMenu::item:selected{background:#1f4fa8;color:#fff;}'
                        'QMenu::separator{height:1px;background:#2a3a52;margin:4px 8px;}')
        t = m.addAction('FACTORY TONES')
        t.setEnabled(False)
        for p in amp_presets.FACTORY:
            a = m.addAction(p['name'])
            a.setToolTip(p['note'])
            a.triggered.connect(lambda _=False, n=p['name']: self.preset.emit(n))
        user = amp_presets.load_user()
        m.addSeparator()
        t = m.addAction('YOUR TONES' if user else 'YOUR TONES (none yet)')
        t.setEnabled(False)
        for p in user:
            a = m.addAction(p['name'])
            a.triggered.connect(lambda _=False, n=p['name']: self.preset.emit(n))
        m.addSeparator()
        sv = m.addAction('SAVE THIS TONE...')
        sv.triggered.connect(self._ask_save)
        if user:
            dm = m.addMenu('DELETE ONE OF YOURS')
            dm.setStyleSheet(m.styleSheet())
            for p in user:
                a = dm.addAction(p['name'])
                a.triggered.connect(lambda _=False, n=p['name']: self.delete_preset.emit(n))
        m.setToolTipsVisible(True)
        m.exec(at)

    def _ask_save(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, 'Save tone', 'Name:', text=self.preset_name if self.edited else '')
        if ok and name.strip():
            self.save_preset.emit(name.strip())

    def _turned(self, key, v):
        self.last = key
        if self.preset_name and not self.edited:
            self.edited = True
        self._readouts()
        self.update()
        self.knob.emit(key, v)

    def _readouts(self):
        for key, k in self.knobs.items():
            if key == 'amp_time':
                k.readout = f'{echo_ms(k.val):.0f} ms'
            elif key == 'amp_gate':
                k.readout = 'OFF' if k.val <= 0 else f'{gate_db(k.val):.0f} dB'
            elif key in FX_KNOBS:
                k.readout = 'OFF' if k.val <= 0 else f'{k.val * 10:.0f} %'
            elif key == 'amp_gain':
                k.readout = f'{amp_db(key, k.val):.0f} dB'
            else:
                k.readout = f'{amp_db(key, k.val):+.1f} dB'
            k.update()

    def show_channel(self, c, name=''):
        """take an input's saved amp settings"""
        self.cur_voice, self.name = c.get('amp', 'crunch'), name
        defaults = dict(FX_DEFAULT, **(voice_defaults(self.cur_voice) if self.cur_voice in VOICES else {}))
        for key, k in self.knobs.items():
            k.default = defaults.get(key, 5.0)
            k.set_value(c.get(key, k.default), emit=False)
        for v, b in self.chans.items():
            b.lit = v == self.cur_voice
            b.update()
        self.hard = bool(c.get('amp_hard', defaults.get('amp_hard', 0)))
        self.preset_name, self.edited = c.get('amp_preset', ''), bool(c.get('amp_edited'))
        self.hard_key.lit = self.hard
        self.hard_key.update()
        self._readouts()
        self.update()

    # geometry: chassis, header, display row (screen + tube window), keys, control face
    def _rects(self):
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        H = r.height()
        head = QRectF(r.left() + 20, r.top() + 12, r.width() - 40, H * 0.11)
        row = QRectF(r.left() + 20, head.bottom() + 6, r.width() - 40, H * 0.30)
        screen = QRectF(row.left(), row.top(), row.width() * 0.36, row.height())
        window = QRectF(screen.right() + 16, row.top(), row.right() - screen.right() - 16, row.height())
        keys = QRectF(r.left() + 20, row.bottom() + 12, r.width() - 40, max(40.0, H * 0.075))   # 40 px: finger size
        face = QRectF(r.left() + 12, keys.bottom() + 12, r.width() - 24, r.bottom() - keys.bottom() - 24)
        return r, head, screen, window, keys, face

    def resizeEvent(self, e):
        r, head, screen, window, keys, face = self._rects()
        gap = 26
        kw = (face.width() - gap) / len(self.knobs)
        for i, (key, k) in enumerate(self.knobs.items()):
            x = face.left() + i * kw + (gap if key in FX_KNOBS else 0)
            k.setGeometry(int(x), int(face.top() + 10), int(kw), int(face.height() - 16))
        keyset = list(self.chans.values()) + [self.hard_key]
        n = len(keyset)
        gap = 24                                                # the clip key sits apart from the channels
        bw = (keys.width() - (n - 2) * 8 - gap) / n
        x = keys.left()
        for i, b in enumerate(keyset):
            if b is self.hard_key:
                x += gap - 8
            b.setGeometry(int(x), int(keys.top()), int(bw), int(keys.height()))
            x += bw + 8

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r, head, screen, window, keys, face = self._rects()
        # chassis: dark anodised body, brushed-silver edge, flush hex screws
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor('#1d2024'))
        g.setColorAt(1, QColor('#0c0d0f'))
        p.setPen(Qt.NoPen)
        p.setBrush(_silver(r))
        p.drawRoundedRect(r, 12, 12)
        p.setBrush(g)
        p.drawRoundedRect(r.adjusted(3, 3, -3, -3), 10, 10)
        for sx, sy in ((r.left() + 12, r.top() + 12), (r.right() - 12, r.top() + 12),
                       (r.left() + 12, r.bottom() - 12), (r.right() - 12, r.bottom() - 12)):
            p.setBrush(QColor('#3a3e44'))
            p.setPen(QPen(QColor(0, 0, 0, 180), 1))
            p.drawEllipse(QPointF(sx, sy), 4.2, 4.2)
            hexp = QPainterPath()
            for i in range(6):
                a = math.radians(30 + 60 * i)
                pt = QPointF(sx + 2.2 * math.cos(a), sy + 2.2 * math.sin(a))
                hexp.moveTo(pt) if i == 0 else hexp.lineTo(pt)
            hexp.closeSubpath()
            p.fillPath(hexp, QColor('#111316'))
        # header: ROSETTA wordmark, model line, power LED
        wm = QPainterPath()
        wm.addText(QPointF(head.left(), head.center().y() + head.height() * 0.2), _font(head.height() * 0.52, True, 130), 'ROSETTA')
        p.fillPath(wm, _silver(QRectF(0, head.top() + head.height() * 0.15, 10, head.height() * 0.6)))
        x = head.left() + wm.boundingRect().width() + 18
        p.setFont(_font(head.height() * 0.22, True, 120))
        p.setPen(GREY_T)
        p.drawText(QRectF(x, head.top(), 500, head.height()), Qt.AlignLeft | Qt.AlignVCenter, 'AMP  ·  OCTA-CAPTURE  ·  ROSETTA OS')
        lx, ly = head.right() - 6, head.center().y()
        p.setFont(_font(head.height() * 0.2, True, 120))
        p.drawText(QRectF(lx - 90, head.top(), 76, head.height()), Qt.AlignRight | Qt.AlignVCenter, 'POWER')
        halo = QRadialGradient(QPointF(lx, ly), 12)
        halo.setColorAt(0, QColor(98, 178, 255, 170))
        halo.setColorAt(1, QColor(98, 178, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(lx, ly), 12, 12)
        p.setBrush(QColor('#cfeaff'))
        p.drawEllipse(QPointF(lx, ly), 4, 4)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))            # hairline under the header
        p.drawLine(QPointF(head.left(), head.bottom() + 2), QPointF(head.right(), head.bottom() + 2))
        self._screen(p, screen)
        self._window(p, window)
        # control face: brushed dark aluminium
        fg = QLinearGradient(face.topLeft(), face.bottomLeft())
        fg.setColorAt(0, QColor('#2a2d32'))
        fg.setColorAt(0.08, QColor('#1c1e22'))
        fg.setColorAt(1, QColor('#111215'))
        p.setPen(QPen(QColor(220, 224, 228, 60), 1))
        p.setBrush(fg)
        p.drawRoundedRect(face, 8, 8)
        p.setPen(QPen(QColor(255, 255, 255, 7), 1))
        for i in range(2, int(face.height()) - 2, 3):           # brushed lines, fixed to the face
            p.drawLine(QPointF(face.left() + 6, face.top() + i), QPointF(face.right() - 6, face.top() + i))
        fx0 = self.knobs['amp_gate'].geometry().left() - 13      # FX section: a seam and its label
        p.setPen(QPen(QColor(98, 178, 255, 70), 1.2))
        p.drawLine(QPointF(fx0, face.top() + 14), QPointF(fx0, face.bottom() - 14))
        p.setFont(_font(11, True, 140))
        p.setPen(QColor(98, 178, 255, 150))
        p.drawText(QRectF(fx0 + 8, face.bottom() - 26, 300, 18), Qt.AlignLeft | Qt.AlignVCenter, 'ROSETTA FX')
        if self.fx_ok is False:                                 # say it where the knobs are: they do nothing on this box
            box = QRectF(fx0 + 6, face.top() + 6, face.right() - fx0 - 12, face.height() - 12)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 10, 14, 215))
            p.drawRoundedRect(box, 6, 6)
            p.setPen(QPen(QColor('#ff5a6e'), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 6, 6)
            p.setFont(_font(16, True, 112))
            p.setPen(QColor('#ff8a9a'))
            p.drawText(box.adjusted(10, 0, -10, -box.height() * 0.25), Qt.AlignCenter | Qt.TextWordWrap,
                       "GATE · ECHO · REVERB\nNOT ON THIS BOX'S FIRMWARE")
            p.setFont(_font(12, True, 108))
            p.setPen(TEXT)
            p.drawText(box.adjusted(10, box.height() * 0.5, -10, 0), Qt.AlignCenter | Qt.TextWordWrap,
                       'Flash test/strip10 to hear them.\nHARD CLIP needs it too.')

    def _bezel(self, p, rect):
        """the app's screen: thin silver bezel round dark glass"""
        p.setPen(Qt.NoPen)
        p.setBrush(_silver(rect))
        p.drawRoundedRect(rect, 8, 8)
        p.setBrush(QColor('#05070b'))
        p.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 6, 6)

    def _screen(self, p, s):
        self._bezel(p, s)
        g = s.adjusted(6, 6, -6, -6)
        p.setPen(Qt.NoPen)
        p.setBrush(LCD_BG)
        p.drawRoundedRect(g, 5, 5)
        pad = g.height() * 0.08
        f = _font(g.height() * 0.11, True, 115)
        p.setFont(f)
        top_row = QRectF(g.left() + pad * 1.5, g.top() + pad, g.width() - pad * 3, g.height() * 0.16)
        room = top_row.width()
        if self.hard:                                           # HARD tag, boxed, right end of the top row
            tw = QFontMetrics(f).horizontalAdvance('HARD') + 16
            tag = QRectF(top_row.right() - tw, top_row.top() + 1, tw, top_row.height() - 2)
            p.setPen(QPen(LCD_INK, 1.4))
            p.setBrush(QColor(98, 178, 255, 40))
            p.drawRoundedRect(tag, 3, 3)
            p.setPen(LCD_INK)
            p.drawText(tag, Qt.AlignCenter, 'HARD')
            room -= tw + 10
        p.setPen(QColor(98, 178, 255, 150))
        name = ('IN ' + self.name) if self.name else 'AMP'
        p.drawText(QRectF(top_row.left(), top_row.top(), room, top_row.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(name, Qt.ElideRight, int(room)))
        big = (self.preset_name + (' *' if self.edited else '')) if self.preset_name else self.cur_voice.upper()
        px = g.height() * 0.30
        while px > 8 and QFontMetrics(_font(px, True, 112)).horizontalAdvance(big) > g.width() - pad * 3:
            px -= 1
        p.setFont(_font(px, True, 112))
        p.setPen(LCD_INK)
        p.drawText(QRectF(g.left() + pad * 1.5, g.top() + g.height() * 0.22, g.width(), g.height() * 0.38),
                   Qt.AlignLeft | Qt.AlignVCenter, big)
        if self.preset_name:                                    # the channel it's built on, small, under the name
            p.setFont(_font(g.height() * 0.085, True, 115))
            p.setPen(QColor(98, 178, 255, 140))
            p.drawText(QRectF(g.left() + pad * 1.5, g.top() + g.height() * 0.52, g.width(), g.height() * 0.1),
                       Qt.AlignLeft | Qt.AlignVCenter, self.cur_voice.upper() + ' CHANNEL  ·  CLICK FOR TONES')
        k = self.knobs[self.last]                               # the last knob moved, with its real value
        p.setFont(_font(g.height() * 0.12, True, 115))
        p.setPen(QColor(98, 178, 255, 200))
        y = g.top() + g.height() * 0.64
        p.drawText(QRectF(g.left() + pad * 1.5, y, g.width() * 0.5, g.height() * 0.16), Qt.AlignLeft | Qt.AlignVCenter, k.label)
        p.drawText(QRectF(g.left(), y, g.width() - pad * 1.5, g.height() * 0.16), Qt.AlignRight | Qt.AlignVCenter,
                   f'{k.val:.1f}   {k.readout}')
        bar = QRectF(g.left() + pad * 1.5, g.bottom() - pad - g.height() * 0.07, g.width() - pad * 3, g.height() * 0.07)
        p.setBrush(QColor(98, 178, 255, 30))
        p.drawRoundedRect(bar, 2, 2)
        n = 40                                                  # segmented level bar like a meter
        sw = bar.width() / n
        for i in range(int(round(k.val / 10 * n))):
            p.setBrush(LCD_INK)
            p.drawRect(QRectF(bar.left() + i * sw + 1, bar.top() + 1, sw - 2, bar.height() - 2))

    def _window(self, p, w):
        # vent slots both sides, the glass window in the middle
        side = w.height() - 12
        win = QRectF(w.center().x() - side * 1.05, w.top(), side * 2.1, w.height())
        p.setPen(Qt.NoPen)
        for left, right in ((w.left(), win.left() - 14), (win.right() + 14, w.right())):
            if right - left < 20:
                continue
            rows = 9
            sh = w.height() / rows
            for i in range(rows):
                sl = QRectF(left, w.top() + i * sh + sh * 0.3, right - left, sh * 0.4)
                p.setBrush(QColor('#050607'))
                p.drawRoundedRect(sl, sl.height() / 2, sl.height() / 2)
                p.setBrush(QColor(255, 255, 255, 14))
                p.drawRoundedRect(QRectF(sl.left(), sl.bottom(), sl.width(), 1), 0.5, 0.5)
        self._bezel(p, win)
        gl = win.adjusted(6, 6, -6, -6)
        p.setBrush(self.tube_bg)
        p.drawRoundedRect(gl, 5, 5)
        p.save()
        p.setClipRect(gl)
        p.setCompositionMode(QPainter.CompositionMode_Lighten)
        glow = self.knobs['amp_gain'].val / 10.0
        ts = gl.height() * 1.02
        for i in range(3):
            cx = gl.center().x() + (i - 1) * ts * 0.6
            tr = QRectF(cx - ts / 2, gl.center().y() - ts / 2 + ts * 0.02, ts, ts)
            x = 0.25 + 0.75 * glow
            k = min(3, int(x * 4))
            for idx, op in ((k, 1 - (x * 4 - k)), (k + 1, x * 4 - k)):
                pm = self.tubes[min(idx, 4)]
                if not pm.isNull() and op > 0.02:
                    p.setOpacity(op)
                    p.drawPixmap(tr, pm, QRectF(pm.rect()))
            p.setOpacity(1.0)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        sheen = QLinearGradient(gl.topLeft(), gl.bottomRight())        # glass reflection
        sheen.setColorAt(0, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.35, QColor(255, 255, 255, 0))
        p.fillRect(gl, sheen)
        p.restore()
