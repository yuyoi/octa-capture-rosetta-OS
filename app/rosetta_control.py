"""Rosetta Octa Control: the PC editor for the OCTA-CAPTURE running Rosetta OS (replaces Roland's Control Panel).
Look: modelled on the unit's front panel: black, silver outlines, a bit of grey, blue buttons, the violet-on-white LCD.

v1 = live STRIP page (app/octa_link.py): the EQ curve you drag, silver knobs, the blue tube that glows with DRIVE,
COMP with the unit's real GR, preamp GAIN + 48V (Roland's own DT1), the 8 real input meters.
Blocks start as the unit has them (read on connect); a block's settings are sent when you turn it on or touch it.
MIXER / ROUTING / REVERB / SYSTEM pages: next (re/CTRL_PROTOCOL.md).

python -I app/rosetta_control.py            run (connects to the OCTA; close Roland's Control Panel first)
python -I app/rosetta_control.py --demo     no device, fake meters
python -I app/rosetta_control.py --shot F  render one demo frame offscreen to F (PNG) and quit
"""
import math
import os
import sys

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QIcon, QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, '..', 'art', 'app')

# ---------------- palette (OCTA-CAPTURE front panel) ----------------
PANEL = QColor('#0c0d0f')
PANEL2 = QColor('#15171a')
GREY = QColor('#2c2f34')
GREY_T = QColor('#8d939b')
TEXT = QColor('#c9ced4')
BLUE = QColor('#2d6bd2')
BLUE_LIT = QColor('#62b2ff')
LCD_BG = QColor('#070a10')                 # dark glass (user: no white screens)
LCD_INK = QColor('#62b2ff')
INK = (98, 178, 255)
HOT = QColor('#ff5a4a')
FS = 48000.0


def silver(rect, vertical=True):
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft() if vertical else rect.topRight())
    for t, c in ((0, '#f4f6f8'), (0.18, '#b9bec4'), (0.45, '#e2e5e8'), (0.62, '#8c9299'), (1, '#d6d9dd')):
        g.setColorAt(t, QColor(c))
    return g


def font(px, bold=False):
    f = QFont('Segoe UI')
    f.setPixelSize(px)
    f.setBold(bold)
    f.setLetterSpacing(QFont.PercentageSpacing, 104)
    return f


# ---------------- DSP maths for the curve (same RBJ cookbook as the unit, tools/octa_lab.py rbj) ----------------
def rbj(kind, fs, f, db=0.0, q=0.7071):
    w = 2 * math.pi * f / fs
    cw, sw = math.cos(w), math.sin(w)
    al = sw / (2 * q)
    A = 10 ** (db / 40)
    if kind == 'hpf':
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + al, -2 * cw, 1 - al]
    elif kind == 'peak':
        b = [1 + al * A, -2 * cw, 1 - al * A]
        a = [1 + al / A, -2 * cw, 1 - al / A]
    else:
        s2 = 2 * math.sqrt(A) * al
        sg = 1 if kind == 'low' else -1
        b = [A * ((A + 1) - sg * (A - 1) * cw + s2), sg * 2 * A * ((A - 1) - sg * (A + 1) * cw),
             A * ((A + 1) - sg * (A - 1) * cw - s2)]
        a = [(A + 1) + sg * (A - 1) * cw + s2, -sg * 2 * ((A - 1) + sg * (A + 1) * cw), (A + 1) + sg * (A - 1) * cw - s2]
    return [x / a[0] for x in b], [1.0, a[1] / a[0], a[2] / a[0]]


def mag_db(stages, f):
    z = complex(math.cos(2 * math.pi * f / FS), -math.sin(2 * math.pi * f / FS))
    h = 1
    for b, a in stages:
        h *= (b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)
    return 20 * math.log10(max(abs(h), 1e-9))


# ---------------- channel state (mirrors patches/page.c tables) ----------------
KEY_BLOCK = dict(hpf_f='hpf', low_f='low', low_g='low', peak_f='peak', peak_g='peak', peak_q='peak', high_f='high',
                 high_g='high', drive='tube', bias='tube', tube_out='tube', thresh='comp', ratio='comp', attack='comp',
                 release='comp', makeup='comp')


def default_chan(demo=False):
    return dict(on={k: demo and k != 'gate' for k in ('hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gate')},
                pre_gain=17.0, ph48=False,
                gain=34.0, hpf_f=80.0, low_f=120.0, low_g=2.0, peak_f=2500.0, peak_g=-3.0, peak_q=1.2, high_f=8000.0,
                high_g=3.5, drive=9.0, bias=0.2, tube_out=0.0, thresh=-18.0, ratio=4.0, attack=10.0, release=150.0,
                makeup=2.0)


# ---------------- widgets ----------------
class Panel(QWidget):
    """black plate with a silver outline + an engraved title, like the unit's grouped sections"""

    def __init__(self, title='', parent=None):
        super().__init__(parent)
        self.title = title

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        path = QPainterPath()
        path.addRoundedRect(r, 9, 9)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor('#191b1f'))
        g.setColorAt(1, QColor('#0e0f11'))
        p.fillPath(path, g)
        p.setPen(QPen(QBrush(silver(r)), 2))
        p.drawPath(path)
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.drawRoundedRect(r.adjusted(2.5, 2.5, -2.5, -2.5), 7, 7)
        if self.title:
            p.setFont(font(12, True))
            p.setPen(QColor('#eef0f2'))
            tr = QRectF(r.left() + 14, r.top() + 6, r.width() - 28, 18)
            w = p.fontMetrics().horizontalAdvance(self.title)
            p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, self.title)
            p.setPen(QPen(QColor(220, 224, 228, 70), 1))
            p.drawLine(QPointF(tr.left() + w + 8, tr.center().y()), QPointF(tr.right(), tr.center().y()))

class BlueButton(QWidget):
    toggled = Signal(bool)

    def __init__(self, text, checkable=True, checked=False, w=64, h=30, parent=None):
        super().__init__(parent)
        self.text, self.checkable, self.checked = text, checkable, checked
        self.setFixedSize(w, h)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if self.checkable:
            self.checked = not self.checked
            self.toggled.emit(self.checked)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.checked:                                                   # backlight bleeding out of the bezel
            glow = QRadialGradient(r.center(), r.width() * 0.8)
            glow.setColorAt(0, QColor(80, 160, 255, 110))
            glow.setColorAt(1, QColor(80, 160, 255, 0))
            p.fillRect(self.rect(), glow)
        p.setPen(Qt.NoPen)                                                 # silver bezel
        p.setBrush(silver(QRectF(self.rect())))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 5, 5)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self.checked:
            g.setColorAt(0, QColor('#bfe3ff'))
            g.setColorAt(0.35, QColor('#5aa9ff'))
            g.setColorAt(1, QColor('#2a6fe0'))
        else:
            g.setColorAt(0, QColor('#d9dcdf'))
            g.setColorAt(0.5, QColor('#b4b8bd'))
            g.setColorAt(1, QColor('#959aa0'))
        p.setBrush(g)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.drawRoundedRect(r, 3.5, 3.5)
        p.setPen(QColor('#ffffff') if self.checked else QColor('#24272b'))
        p.setFont(font(12, True))
        p.drawText(r, Qt.AlignCenter, self.text)

class Knob(QWidget):
    """the unit's knob: black rubber cap on a brushed-silver skirt, white pointer + dot scale (lit part blue)"""
    changed = Signal(float)

    def __init__(self, label, lo, hi, val, fmt='{:.1f}', unit='', log=False, size=64, parent=None):
        super().__init__(parent)
        self.label, self.lo, self.hi, self.val, self.fmt, self.unit, self.log = label, lo, hi, val, fmt, unit, log
        self.s = size
        self.setFixedSize(size + 30, size + 50)
        self._drag = None
        self.setCursor(Qt.SizeVerCursor)

    def frac(self):
        if self.log:
            return math.log(self.val / self.lo) / math.log(self.hi / self.lo)
        return (self.val - self.lo) / (self.hi - self.lo)

    def set_frac(self, f):
        f = min(1.0, max(0.0, f))
        self.val = self.lo * (self.hi / self.lo) ** f if self.log else self.lo + f * (self.hi - self.lo)
        self.changed.emit(self.val)
        self.update()

    def mousePressEvent(self, e):
        self._drag = (e.position().y(), self.frac())

    def mouseMoveEvent(self, e):
        if self._drag:
            y0, f0 = self._drag
            fine = 0.25 if e.modifiers() & Qt.ShiftModifier else 1.0
            self.set_frac(f0 + (y0 - e.position().y()) / 180 * fine)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        self.set_frac(self.frac() + (1 if e.angleDelta().y() > 0 else -1) / 100)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy, R = self.width() / 2, 22 + self.s / 2, self.s / 2
        a0, sweep = 225, -270
        f = self.frac()
        # dot scale (white, like the MAIN OUT scale), lit part blue
        for i in range(21):
            t = i / 20
            a = math.radians(a0 + sweep * t)
            rr = R + 6
            lit = t <= f + 1e-6
            p.setPen(Qt.NoPen)
            p.setBrush(BLUE_LIT if lit else QColor(225, 228, 232, 150))
            d = 1.9 if (i % 5 == 0 or lit) else 1.3
            p.drawEllipse(QPointF(cx + rr * math.cos(a), cy - rr * math.sin(a)), d, d)
        # shadow, brushed silver skirt, black rubber cap
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 160))
        p.drawEllipse(QPointF(cx + 1.5, cy + 2.5), R - 1, R - 1)
        skirt = QConicalGradient(QPointF(cx, cy), 30)
        for t, c in ((0, '#f2f4f6'), (0.15, '#80868d'), (0.3, '#dcdfe2'), (0.45, '#6f757c'), (0.6, '#eef0f2'),
                     (0.75, '#868c93'), (0.9, '#d2d6da'), (1, '#f2f4f6')):
            skirt.setColorAt(t, QColor(c))
        p.setBrush(skirt)
        p.drawEllipse(QPointF(cx, cy), R - 1, R - 1)
        cap = QRadialGradient(QPointF(cx - R * 0.3, cy - R * 0.35), R * 1.1)
        cap.setColorAt(0, QColor('#4a4e54'))
        cap.setColorAt(0.45, QColor('#1d1f23'))
        cap.setColorAt(1, QColor('#0a0b0d'))
        p.setBrush(cap)
        p.setPen(QPen(QColor(0, 0, 0, 200), 1))
        p.drawEllipse(QPointF(cx, cy), R - 5, R - 5)
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))                               # rubber grip ridges
        for i in range(24):
            a = math.radians(i * 15)
            p.drawLine(QPointF(cx + (R - 9) * math.cos(a), cy - (R - 9) * math.sin(a)),
                       QPointF(cx + (R - 5.5) * math.cos(a), cy - (R - 5.5) * math.sin(a)))
        a = math.radians(a0 + sweep * f)                                      # white pointer line
        p.setPen(QPen(QColor('#f4f6f8'), 2.4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx + (R * 0.25) * math.cos(a), cy - (R * 0.25) * math.sin(a)),
                   QPointF(cx + (R - 7) * math.cos(a), cy - (R - 7) * math.sin(a)))
        # label + value
        p.setFont(font(11, True))
        p.setPen(QColor('#e6e9ec'))
        p.drawText(QRectF(0, 0, self.width(), 12), Qt.AlignCenter, self.label)
        p.setFont(font(12, True))
        p.setPen(TEXT)
        v = self.fmt.format(self.val) + self.unit
        p.drawText(QRectF(0, self.height() - 18, self.width(), 16), Qt.AlignCenter, v)


class LcdEq(QWidget):
    """the EQ curve on a dark glass screen in a silver bezel, blue ink; drag the nodes"""
    edited = Signal()

    NODES = [('hpf', 'hpf_f', None), ('low', 'low_f', 'low_g'), ('peak', 'peak_f', 'peak_g'), ('high', 'high_f', 'high_g')]

    def __init__(self, chan, parent=None):
        super().__init__(parent)
        self.c = chan
        self.setMinimumSize(520, 230)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._drag = None

    def plot(self):
        return QRectF(self.rect()).adjusted(16, 14, -16, -26)

    def fx(self, f):
        r = self.plot()
        return r.left() + r.width() * math.log(f / 20) / math.log(1000)

    def xf(self, x):
        r = self.plot()
        return 20 * 1000 ** min(1, max(0, (x - r.left()) / r.width()))

    def dy(self, db):
        r = self.plot()
        return r.center().y() - db / 18 * (r.height() / 2)

    def yd(self, y):
        r = self.plot()
        return max(-15, min(15, (r.center().y() - y) / (r.height() / 2) * 18))

    def stages(self):
        c, on, s = self.c, self.c['on'], []
        if on['hpf']:
            s.append(rbj('hpf', FS, c['hpf_f']))
        if on['low']:
            s.append(rbj('low', FS, c['low_f'], c['low_g']))
        if on['peak']:
            s.append(rbj('peak', FS, c['peak_f'], c['peak_g'], c['peak_q']))
        if on['high']:
            s.append(rbj('high', FS, c['high_f'], c['high_g']))
        return s

    def node_pos(self, kind, fk, gk):
        return QPointF(self.fx(self.c[fk]), self.dy(self.c[gk] if gk else 0) if gk else self.dy(-3))

    def mousePressEvent(self, e):
        for kind, fk, gk in self.NODES:
            if (self.node_pos(kind, fk, gk) - e.position()).manhattanLength() < 14:
                self._drag = (fk, gk)
                return

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        fk, gk = self._drag
        self.c[fk] = round(self.xf(e.position().x()), 1)
        if gk:
            self.c[gk] = round(self.yd(e.position().y()) * 2) / 2
        self.edited.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QBrush(silver(outer)), 3))                              # silver bezel
        p.setBrush(QColor('#202327'))
        p.drawRoundedRect(outer, 8, 8)
        glass = outer.adjusted(7, 7, -7, -7)
        g = QLinearGradient(glass.topLeft(), glass.bottomLeft())
        g.setColorAt(0, QColor('#0d1320'))
        g.setColorAt(1, QColor('#05070c'))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(glass, 4, 4)
        r = self.plot()
        p.setFont(font(10, True))
        for db in (-12, -6, 0, 6, 12):                                        # grid
            y = self.dy(db)
            p.setPen(QPen(QColor(*INK, 90 if db == 0 else 34), 1, Qt.SolidLine if db == 0 else Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(*INK, 150))
            p.drawText(QRectF(r.left() + 2, y - 12, 40, 12), Qt.AlignLeft, f'{db:+d}' if db else '0')
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self.fx(f)
            p.setPen(QPen(QColor(*INK, 34), 1, Qt.DotLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(*INK, 150))
            p.drawText(QRectF(x - 20, r.bottom() + 4, 40, 14), Qt.AlignCenter, f'{f // 1000}K' if f >= 1000 else str(f))
        st = self.stages()
        n = 240
        pts = []
        for i in range(n + 1):
            f = 20 * 1000 ** (i / n)
            pts.append(QPointF(self.fx(f), self.dy(max(-18, min(18, mag_db(st, f))))))
        y0 = self.dy(0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(*INK, 46))
        for a, b in zip(pts, pts[1:]):                                        # fill between curve and 0 dB
            p.drawPolygon(QPolygonF([QPointF(a.x(), y0), a, b, QPointF(b.x(), y0)]))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(LCD_INK, 2.6))
        p.drawPolyline(QPolygonF(pts))
        for kind, fk, gk in self.NODES:                                       # nodes
            pt = self.node_pos(kind, fk, gk)
            on = self.c['on'][kind]
            p.setPen(QPen(LCD_INK, 2))
            p.setBrush(LCD_INK if on else LCD_BG)
            p.drawEllipse(pt, 6, 6)
            p.setPen(LCD_INK)
            p.drawText(QRectF(pt.x() - 30, pt.y() - 24, 60, 14), Qt.AlignCenter, kind.upper())


class Meter(QWidget):
    """segmented vertical meter in the unit's blue, top segments hot; down=True for gain reduction"""

    def __init__(self, w=12, down=False, parent=None):
        super().__init__(parent)
        self.level, self.down = 0.0, down
        self.setFixedWidth(w)
        self.setMinimumHeight(60)

    def paintEvent(self, e):
        p = QPainter(self)
        r = QRectF(self.rect())
        p.fillRect(r, QColor('#07080a'))
        n = max(8, int(r.height() // 5))
        lit = round(self.level * n)
        for i in range(n):
            y = (i * 5 + 1) if self.down else (r.height() - (i + 1) * 5 + 1)
            on = i < lit
            hot = (not self.down) and i >= n - max(2, n // 10)
            c = (HOT if hot else (QColor('#ffb14a') if self.down else BLUE_LIT)) if on else QColor('#1a1d22')
            p.fillRect(QRectF(1, y, r.width() - 2, 3), c)


class TubeView(QWidget):
    """the blue GBA tube: art/app/tube/tube_glow_*.png, brightness follows DRIVE, dark when TUBE is off"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps = []
        for k in (0, 25, 50, 75, 100):
            fn = os.path.join(ART, 'tube', 'tube_glow_%03d.png' % k)
            self.steps.append(QPixmap(fn) if os.path.exists(fn) else QPixmap())
        self.glow = 1.0
        self.setFixedSize(190, 210)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QBrush(silver(r)), 2))
        p.setBrush(QColor('#05070a'))
        p.drawRoundedRect(r, 8, 8)
        x = self.glow * 4
        i = min(3, int(x))
        t = x - i
        inner = r.adjusted(6, 6, -6, -6)
        for idx, op in ((i, 1 - t), (i + 1, t)):
            pm = self.steps[min(idx, 4)]
            if not pm.isNull() and op > 0.01:
                p.setOpacity(op)
                side = min(inner.width(), inner.height())
                p.drawPixmap(QRectF(inner.center().x() - side / 2, inner.center().y() - side / 2, side, side), pm,
                             QRectF(pm.rect()))
        p.setOpacity(1)


# ---------------- track names + pictures (saved in app/tracks.json, not in git) ----------------
ICONS = ['vocal', 'guitar', 'bass', 'acoustic', 'synth', 'kick', 'snare', 'cymbal', 'sampler', 'amp']
TRACKS = os.path.join(HERE, 'tracks.json')


def _all_tracks():
    import json
    try:
        t = json.load(open(TRACKS, encoding='utf-8'))
        return {'0': t} if isinstance(t, list) else dict(t)
    except Exception:
        return {}


def load_tracks(unit=0):
    t = _all_tracks().get(str(unit))
    if isinstance(t, list) and len(t) == 8:
        return t
    return [dict(name='', icon='') for _ in range(8)]


def save_tracks(t, unit=0):
    import json
    a = _all_tracks()
    a[str(unit)] = t
    try:
        json.dump(a, open(TRACKS, 'w', encoding='utf-8'), indent=1)
    except Exception:
        pass


def icon_pixmap(icon):
    """built-in name -> art/app/icons/NAME.png, else a path to the user's own picture"""
    if not icon:
        return QPixmap()
    fn = os.path.join(ART, 'icons', icon + '.png') if icon in ICONS else icon
    return QPixmap(fn) if os.path.exists(fn) else QPixmap()


def rounded_pix(p, pm, rect, radius=6):
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path)
    side = min(pm.width(), pm.height())
    src = QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side)
    p.drawPixmap(rect, pm, src)
    p.restore()


class TrackButton(QWidget):
    """a channel in the rail: picture + number + name; lit blue when selected. Double-click / right-click = edit."""
    clicked = Signal()
    edit = Signal()

    def __init__(self, n, parent=None):
        super().__init__(parent)
        self.n, self.name, self.pm, self.checked = n, '', QPixmap(), False
        self.setFixedSize(150, 52)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('double-click or right-click: name + picture')

    def set_track(self, t):
        self.name = t.get('name', '')
        self.pm = icon_pixmap(t.get('icon', ''))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.edit.emit()
        else:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, e):
        self.edit.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.checked:
            glow = QRadialGradient(outer.center(), outer.width() * 0.7)
            glow.setColorAt(0, QColor(80, 160, 255, 90))
            glow.setColorAt(1, QColor(80, 160, 255, 0))
            p.fillRect(self.rect(), glow)
        p.setPen(Qt.NoPen)
        p.setBrush(silver(outer))
        p.drawRoundedRect(outer, 6, 6)
        r = outer.adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self.checked:
            g.setColorAt(0, QColor('#2d6bd2'))
            g.setColorAt(1, QColor('#173f86'))
        else:
            g.setColorAt(0, QColor('#1d2025'))
            g.setColorAt(1, QColor('#111316'))
        p.setBrush(g)
        p.drawRoundedRect(r, 4.5, 4.5)
        pic = QRectF(r.left() + 4, r.top() + 4, r.height() - 8, r.height() - 8)
        if not self.pm.isNull():
            rounded_pix(p, self.pm, pic, 5)
        else:
            p.setBrush(QColor('#0a0b0d'))
            p.drawRoundedRect(pic, 5, 5)
        p.setPen(QColor('#ffffff') if self.checked else QColor('#e6e9ec'))
        p.setFont(font(15, True))
        tx = pic.right() + 8
        if self.name:
            p.drawText(QRectF(tx, r.top() + 3, r.right() - tx - 4, 20), Qt.AlignLeft | Qt.AlignVCenter, str(self.n + 1))
            p.setFont(font(12, True))
            p.setPen(QColor('#cfe6ff') if self.checked else GREY_T)
            el = p.fontMetrics().elidedText(self.name.upper(), Qt.ElideRight, int(r.right() - tx - 4))
            p.drawText(QRectF(tx, r.top() + 23, r.right() - tx - 4, 18), Qt.AlignLeft | Qt.AlignVCenter, el)
        else:
            p.drawText(QRectF(tx, r.top(), r.right() - tx - 4, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       str(self.n + 1))


class TrackDialog(QDialog):
    """name + picture for one input: built-in stickers, your own picture, or none"""

    def __init__(self, n, t, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Input {n + 1}')
        self.icon = t.get('icon', '')
        self.setStyleSheet('QDialog{background:#111316;} QLabel{color:#e6e9ec;font:bold 12px "Segoe UI";}'
                           'QLineEdit{background:#05070c;color:#62b2ff;border:2px solid #8c9299;border-radius:5px;'
                           'padding:6px;font:bold 15px "Segoe UI";}'
                           'QPushButton{background:#b4b8bd;color:#24272b;border:2px solid #d6d9dd;border-radius:5px;'
                           'padding:6px 12px;font:bold 12px "Segoe UI";} QPushButton:checked{background:#3a86e8;color:white;}')
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f'INPUT {n + 1}  NAME'))
        self.edit = QLineEdit(t.get('name', ''))
        self.edit.setMaxLength(24)
        self.edit.setPlaceholderText('VOCAL, GUITAR L, KICK ...')
        lay.addWidget(self.edit)
        lay.addWidget(QLabel('PICTURE'))
        grid = QGridLayout()
        self.btns = {}
        for i, k in enumerate(ICONS):
            b = QPushButton()
            b.setCheckable(True)
            b.setIcon(QIcon(icon_pixmap(k)))
            b.setIconSize(QSize(56, 56))
            b.setFixedSize(72, 72)
            b.setToolTip(k)
            b.clicked.connect(lambda _=False, kk=k: self.choose(kk))
            grid.addWidget(b, i // 5, i % 5)
            self.btns[k] = b
        lay.addLayout(grid)
        row = QHBoxLayout()
        own = QPushButton('OWN PICTURE...')
        own.clicked.connect(self.own)
        none = QPushButton('NO PICTURE')
        none.clicked.connect(lambda: self.choose(''))
        row.addWidget(own)
        row.addWidget(none)
        row.addStretch(1)
        ok = QPushButton('OK')
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self.choose(self.icon)

    def choose(self, k):
        self.icon = k
        for kk, b in self.btns.items():
            b.setChecked(kk == k)

    def own(self):
        fn, _ = QFileDialog.getOpenFileName(self, 'Picture for this input', '', 'Images (*.png *.jpg *.jpeg *.gif *.bmp)')
        if fn:
            self.choose(fn)

    def result_track(self):
        return dict(name=self.edit.text().strip(), icon=self.icon)


class ChannelRail(QWidget):
    picked = Signal(int)
    renamed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.unit = 0
        self.tracks = load_tracks(0)
        self.btns, self.meters = [], []
        for i in range(8):
            row = QHBoxLayout()
            row.setSpacing(6)
            b = TrackButton(i)
            b.checked = i == 0
            b.set_track(self.tracks[i])
            b.clicked.connect(lambda k=i: self.pick(k))
            b.edit.connect(lambda k=i: self.edit(k))
            m = Meter(10)
            m.setFixedHeight(52)
            row.addWidget(b)
            row.addWidget(m)
            lay.addLayout(row)
            self.btns.append(b)
            self.meters.append(m)
        lay.addStretch(1)

    def pick(self, k):
        for i, b in enumerate(self.btns):
            b.checked = i == k
            b.update()
        self.picked.emit(k)

    def edit(self, k):
        d = TrackDialog(k, self.tracks[k], self)
        if d.exec():
            self.tracks[k] = d.result_track()
            self.btns[k].set_track(self.tracks[k])
            save_tracks(self.tracks, self.unit)
            self.renamed.emit()

    def set_unit(self, u):
        self.unit = u
        self.tracks = load_tracks(u)
        for i, b in enumerate(self.btns):
            b.set_track(self.tracks[i])
        self.renamed.emit()

    def label(self, k):
        n = self.tracks[k].get('name', '')
        return f'{k + 1}  {n.upper()}' if n else f'INPUT {k + 1}'


class Header(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.text, self.online = 'OFFLINE DEMO', False
        self.units = {}

    def set_status(self, text, online):
        self.text, self.online = text, online
        self.update()

    def unit_status(self, u, text, online):
        self.units[u] = (text, online)
        parts = [f'UNIT {k + 1}: {v[0]}' for k, v in sorted(self.units.items())]
        self.set_status('   '.join(parts), any(v[1] for v in self.units.values()))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        g.setColorAt(0, QColor('#1b1d21'))
        g.setColorAt(1, QColor('#0b0c0e'))
        p.fillRect(r, g)
        p.setPen(QPen(QBrush(silver(r, vertical=False)), 2))
        p.drawLine(QPointF(0, r.bottom() - 1), QPointF(r.right(), r.bottom() - 1))
        f = font(24, True)
        f.setLetterSpacing(QFont.PercentageSpacing, 118)
        tp = QPainterPath()
        tp.addText(QPointF(20, 38), f, 'ROSETTA OCTA CONTROL')
        p.fillPath(tp, QBrush(silver(QRectF(0, 14, 10, 28))))
        p.setFont(font(12, True))
        p.setPen(GREY_T)
        p.drawText(QRectF(410, 22, 300, 20), Qt.AlignLeft | Qt.AlignVCenter, 'OCTA-CAPTURE  ·  ROSETTA OS')
        p.setPen(Qt.NoPen)                                                     # status LED
        p.setBrush(BLUE_LIT if self.online else QColor('#ffb14a'))
        p.drawEllipse(QPointF(r.right() - 560, 30), 5, 5)
        p.setPen(TEXT)
        p.drawText(QRectF(r.right() - 548, 20, 540, 20), Qt.AlignLeft | Qt.AlignVCenter, self.text)


class StripPage(QWidget):
    def __init__(self, chans, parent=None):
        super().__init__(parent)
        self.chans, self.ch, self.link = chans, 0, None
        c = chans[0]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        # block row
        blocks = QHBoxLayout()
        blocks.setSpacing(8)
        self.blk = {}
        for k in ('hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gate'):
            b = BlueButton(k.upper(), checked=c['on'][k], w=74, h=30)
            b.toggled.connect(lambda v, kk=k: self.set_on(kk, v))
            blocks.addWidget(b)
            self.blk[k] = b
        blocks.addStretch(1)
        root.addLayout(blocks)
        mid = QHBoxLayout()
        mid.setSpacing(10)
        # EQ panel
        eqp = self.eqp = Panel('EQ')
        ev = QVBoxLayout(eqp)
        ev.setContentsMargins(14, 30, 14, 10)
        self.eq = LcdEq(c)
        self.eq.edited.connect(self.eq_edited)
        ev.addWidget(self.eq, 1)
        kr = QHBoxLayout()
        self.k = {}

        def knob(key, *a, **kw):
            w = Knob(*a, **kw)
            w.changed.connect(lambda v, kk=key: self.set_val(kk, v))
            self.k[key] = w
            return w
        for key, args in (('hpf_f', ('HPF', 20, 600, c['hpf_f'], '{:.0f}', ' Hz', True)),
                          ('low_f', ('LOW', 30, 1000, c['low_f'], '{:.0f}', ' Hz', True)),
                          ('low_g', ('LOW GAIN', -15, 15, c['low_g'], '{:+.1f}', ' dB')),
                          ('peak_f', ('PEAK', 100, 12000, c['peak_f'], '{:.0f}', ' Hz', True)),
                          ('peak_g', ('PEAK GAIN', -15, 15, c['peak_g'], '{:+.1f}', ' dB')),
                          ('peak_q', ('Q', 0.3, 8, c['peak_q'], '{:.2f}', '', True)),
                          ('high_f', ('HIGH', 1000, 16000, c['high_f'], '{:.0f}', ' Hz', True)),
                          ('high_g', ('HIGH GAIN', -15, 15, c['high_g'], '{:+.1f}', ' dB'))):
            kr.addWidget(knob(key, *args, size=52))
        ev.addLayout(kr)
        mid.addWidget(eqp, 1)
        # right column: preamp + tube + comp
        right = QVBoxLayout()
        right.setSpacing(10)
        pp = Panel('PREAMP')
        pl = QHBoxLayout(pp)
        pl.setContentsMargins(14, 26, 14, 8)
        self.gain = Knob('GAIN', 0, 50, c['pre_gain'], '{:.1f}', ' dB', size=52)
        self.gain.changed.connect(self.gain_turned)
        pl.addWidget(self.gain)
        pv = QVBoxLayout()
        self.b48 = BlueButton('+48V', checked=c['ph48'], w=74, h=30)
        self.b48.toggled.connect(self.ph48_toggled)
        pv.addWidget(self.b48)
        self.inlab = QLabel('IN  --')
        self.inlab.setStyleSheet('color:#c9ced4;font:bold 13px "Segoe UI";')
        pv.addWidget(self.inlab)
        pl.addLayout(pv)
        pl.addStretch(1)
        right.addWidget(pp)
        tp = Panel('TUBE')
        tl = QHBoxLayout(tp)
        tl.setContentsMargins(14, 30, 14, 12)
        self.tube = TubeView()
        tl.addWidget(self.tube)
        tk = QVBoxLayout()
        tk.addWidget(knob('drive', 'DRIVE', 0, 24, c['drive'], '{:.1f}', ' dB', size=52))
        tk.addWidget(knob('bias', 'BIAS', 0, 0.5, c['bias'], '{:.2f}', '', size=52))
        tl.addLayout(tk)
        right.addWidget(tp)
        cp = Panel('COMP')
        cl = QHBoxLayout(cp)
        cl.setContentsMargins(14, 30, 14, 12)
        grid = QGridLayout()
        grid.setSpacing(2)
        for i, (key, args) in enumerate((('thresh', ('THRESH', -60, 0, c['thresh'], '{:.1f}', ' dB')),
                                         ('ratio', ('RATIO', 1, 20, c['ratio'], '{:.1f}', ':1', True)),
                                         ('attack', ('ATTACK', 0.1, 100, c['attack'], '{:.1f}', ' ms', True)),
                                         ('release', ('RELEASE', 10, 2000, c['release'], '{:.0f}', ' ms', True)),
                                         ('makeup', ('MAKEUP', 0, 24, c['makeup'], '{:.1f}', ' dB')))):
            grid.addWidget(knob(key, *args, size=46), i // 3, i % 3)
        gw = QVBoxLayout()
        gw.addLayout(grid)
        gw.addStretch(1)
        cl.addLayout(gw)
        gv = QVBoxLayout()
        lab = QLabel('GR')
        lab.setStyleSheet('color:#8d939b;font:bold 11px "Segoe UI";')
        gv.addWidget(lab, 0, Qt.AlignHCenter)
        self.gr = Meter(14, down=True)
        gv.addWidget(self.gr, 1)
        cl.addLayout(gv)
        right.addWidget(cp)
        mid.addLayout(right)
        root.addLayout(mid, 1)
        self.refresh_tube()

    def push(self, block):
        c = self.chans[self.ch]
        if self.link and block != 'gate':
            self.link.set_block(self.ch, block, c['on'][block], dict(c))

    def set_on(self, k, v):
        self.chans[self.ch]['on'][k] = v
        self.push(k)
        self.eq.update()
        self.refresh_tube()

    def set_val(self, k, v):
        c = self.chans[self.ch]
        c[k] = v
        if c['on'][KEY_BLOCK[k]]:
            self.push(KEY_BLOCK[k])
        self.eq.update()
        self.refresh_tube()

    def eq_edited(self):
        self.sync_knobs()
        for b in ('hpf', 'low', 'peak', 'high'):
            if self.chans[self.ch]['on'][b]:
                self.push(b)

    def gain_turned(self, v):
        v = round(v * 2) / 2
        self.chans[self.ch]['pre_gain'] = v
        if self.link:
            self.link.set_gain(self.ch, round(v * 2))

    def ph48_toggled(self, on):
        self.chans[self.ch]['ph48'] = on
        if self.link:
            self.link.set_48v(self.ch, on)

    def on_preamp(self, ch, raw, ph):
        if ch != self.ch:
            return
        c = self.chans[ch]
        if self.gain._drag is None:
            c['pre_gain'] = raw / 2
            self.gain.val = raw / 2
            self.gain.update()
        c['ph48'] = bool(ph)
        self.b48.checked = bool(ph)
        self.b48.update()

    def on_flags(self, fl):
        for ch, on in enumerate(fl):
            for b in ('hpf', 'low', 'peak', 'high', 'tube', 'comp'):
                self.chans[ch]['on'][b] = b in on
        self.show_chan(self.ch)

    def sync_knobs(self):
        for key, w in self.k.items():
            w.val = self.chans[self.ch][key]
            w.update()

    def refresh_tube(self):
        c = self.chans[self.ch]
        self.tube.glow = (0.35 + 0.65 * c['drive'] / 24) if c['on']['tube'] else 0.0
        self.tube.update()

    def show_chan(self, k):
        self.ch = k
        if self.link:
            self.link.watch = k
        c = self.chans[k]
        self.gain.val = c['pre_gain']
        self.gain.update()
        self.b48.checked = c['ph48']
        self.b48.update()
        self.eq.c = c
        for key, b in self.blk.items():
            b.checked = c['on'][key]
            b.update()
        self.sync_knobs()
        self.eq.update()
        self.refresh_tube()


class Main(QWidget):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle('Rosetta Octa Control')
        self.setWindowIcon(QIcon(os.path.join(ART, 'app_icon.ico')))
        self.resize(1460, 950)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), PANEL)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.units = [dict(chans=[default_chan(demo) for _ in range(8)], link=None) for _ in range(2)]
        self.cur = 0
        self.chans = self.units[0]['chans']
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)
        self.header = Header()
        root.addWidget(self.header)
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        railp = Panel('INPUT')
        rl = QVBoxLayout(railp)
        rl.setContentsMargins(12, 30, 12, 12)
        ub = QHBoxLayout()
        ub.setSpacing(6)
        self.unitbtn = []
        for i in range(2):
            b = BlueButton(f'UNIT {i + 1}', checked=(i == 0), w=80, h=30)
            b.toggled.connect(lambda _, k=i: self.select_unit(k))
            ub.addWidget(b)
            self.unitbtn.append(b)
        rl.addLayout(ub)
        self.rail = ChannelRail()
        rl.addWidget(self.rail)
        railp.setFixedWidth(196)
        body.addWidget(railp)
        col = QVBoxLayout()
        col.setSpacing(10)
        pages = QHBoxLayout()
        pages.setSpacing(8)
        self.pagebtn = []
        for i, t in enumerate(('STRIP', 'MIXER', 'ROUTING', 'REVERB', 'SYSTEM')):
            b = BlueButton(t, checked=(i == 0), w=100, h=32)
            pages.addWidget(b)
            self.pagebtn.append(b)
        pages.addStretch(1)
        col.addLayout(pages)
        self.strip = StripPage(self.chans)
        col.addWidget(self.strip, 1)
        body.addLayout(col, 1)
        root.addLayout(body, 1)
        self.rail.picked.connect(self.strip.show_chan)
        self.rail.picked.connect(lambda k: self.retitle())
        self.rail.renamed.connect(self.retitle)
        self.retitle()
        self.t = 0
        if demo:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.demo_tick)
            self.timer.start(50)
            self.demo_tick()
        else:
            from octa_link import Link
            for i, u in enumerate(self.units):
                L = u['link'] = Link(i)
                L.status.connect(lambda t, o, k=i: self.header.unit_status(k, t, o))
                L.meters.connect(lambda db, gr, k=i: k == self.cur and self.on_meters(db, gr))
                L.preamp.connect(lambda ch, g, ph, k=i: k == self.cur and self.strip.on_preamp(ch, g, ph))
                L.flags.connect(lambda fl, k=i: self.on_flags(k, fl))
                L.start()
            self.strip.link = self.units[0]['link']

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        p.fillRect(r, PANEL)
        for k, w in ((0, 7), (7, 2)):                                     # chamfered rim: bright outer, dark inner step
            rr = r.adjusted(k + w / 2, k + w / 2, -k - w / 2, -k - w / 2)
            p.setPen(QPen(QBrush(silver(rr)) if k == 0 else QColor(0, 0, 0, 180), w))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rr, 12 - k, 12 - k)

    def select_unit(self, k):
        self.cur = k
        for i, b in enumerate(self.unitbtn):
            b.checked = i == k
            b.update()
        u = self.units[k]
        self.chans = u['chans']
        self.strip.chans = u['chans']
        self.strip.link = u['link']
        self.rail.set_unit(k)
        self.strip.show_chan(self.strip.ch)
        for m in self.rail.meters:
            m.level = 0
            m.update()

    def on_flags(self, k, fl):
        for ch, on in enumerate(fl):
            for b in ('hpf', 'low', 'peak', 'high', 'tube', 'comp'):
                self.units[k]['chans'][ch]['on'][b] = b in on
        if k == self.cur:
            self.strip.show_chan(self.strip.ch)

    def retitle(self):
        self.strip.eqp.title = f'EQ   ·   UNIT {self.cur + 1}   ·   ' + self.rail.label(self.strip.ch)
        self.strip.eqp.update()

    def on_meters(self, db, gr):
        for i, m in enumerate(self.rail.meters):
            m.level = min(1.0, max(0.0, (db[i] + 60) / 60))
            m.update()
        self.strip.gr.level = min(1.0, max(0.0, gr / 20))
        self.strip.gr.update()
        d = db[self.strip.ch]
        self.strip.inlab.setText('IN  %s' % ('%.1f dB' % d if d > -100 else '--'))

    def closeEvent(self, e):
        for u in self.units:
            if u['link']:
                u['link'].stop = True
        super().closeEvent(e)

    def demo_tick(self):
        self.t += 1
        for i, m in enumerate(self.rail.meters):
            m.level = max(0.0, 0.55 + 0.3 * math.sin(self.t * 0.13 + i * 1.7) - (0.5 if i > 3 else 0))
            m.update()
        self.strip.gr.level = max(0.0, 0.3 + 0.2 * math.sin(self.t * 0.2))
        self.strip.gr.update()


def main():
    sys.path.insert(0, HERE)
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Rosetta.OctaControl')
        except Exception:
            pass
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Rosetta Octa Control')
    app.setWindowIcon(QIcon(os.path.join(ART, 'app_icon.ico')))
    w = Main(demo='--demo' in sys.argv or '--shot' in sys.argv)
    if '--shot' in sys.argv:
        out = sys.argv[sys.argv.index('--shot') + 1]
        w.show()
        app.processEvents()
        w.grab().save(out)
        print('wrote', out)
        return
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
