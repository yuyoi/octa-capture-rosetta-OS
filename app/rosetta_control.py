"""Rosetta Control: the PC editor for the OCTA-CAPTURE running Rosetta OS (replaces Roland's Control Panel).
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

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget)

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
LCD_BG = QColor('#e8ebf0')
LCD_INK = QColor('#4a3a8e')
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
            p.setPen(GREY_T)
            p.drawText(QRectF(r.left() + 14, r.top() + 6, r.width() - 28, 18), Qt.AlignLeft | Qt.AlignVCenter, self.title)


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
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.checked:                                                   # lit: soft blue glow behind
            glow = QRadialGradient(r.center(), r.width() * 0.75)
            glow.setColorAt(0, QColor(98, 178, 255, 90))
            glow.setColorAt(1, QColor(98, 178, 255, 0))
            p.fillRect(self.rect(), glow)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self.checked:
            g.setColorAt(0, QColor('#7cc0ff'))
            g.setColorAt(0.5, QColor('#3a86e8'))
            g.setColorAt(1, QColor('#2459b8'))
        else:
            g.setColorAt(0, QColor('#33507a'))
            g.setColorAt(0.5, QColor('#22395c'))
            g.setColorAt(1, QColor('#182a45'))
        p.setBrush(g)
        p.setPen(QPen(QBrush(silver(r)), 1.6))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor('#ffffff') if self.checked else QColor('#9fb4d4'))
        p.setFont(font(12, True))
        p.drawText(r, Qt.AlignCenter, self.text)


class Knob(QWidget):
    """silver knurled knob: conical sheen, grip notches, blue value arc + tick ring, value under it"""
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
        # tick ring
        for i in range(21):
            t = i / 20
            a = math.radians(a0 + sweep * t)
            r0, r1 = R + 4, R + (8 if i % 5 == 0 else 6)
            p.setPen(QPen(BLUE_LIT if t <= f + 1e-6 else QColor('#3b4048'), 1.6 if t <= f + 1e-6 else 1.2))
            p.drawLine(QPointF(cx + r0 * math.cos(a), cy - r0 * math.sin(a)),
                       QPointF(cx + r1 * math.cos(a), cy - r1 * math.sin(a)))
        # value arc
        arc = QRectF(cx - R - 2, cy - R - 2, 2 * R + 4, 2 * R + 4)
        p.setPen(QPen(QColor(98, 178, 255, 200), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(arc, int(a0 * 16), int(sweep * f * 16))
        # shadow + knurled skirt
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 150))
        p.drawEllipse(QPointF(cx + 1.5, cy + 2.5), R - 2, R - 2)
        skirt = QConicalGradient(QPointF(cx, cy), 0)
        for t, c in ((0, '#e9ecef'), (0.12, '#7d838a'), (0.25, '#d7dadd'), (0.4, '#6c7279'), (0.55, '#f1f3f5'),
                     (0.7, '#80868d'), (0.85, '#cfd3d7'), (1, '#e9ecef')):
            skirt.setColorAt(t, QColor(c))
        p.setBrush(skirt)
        p.drawEllipse(QPointF(cx, cy), R - 3, R - 3)
        p.setPen(QPen(QColor(40, 44, 50, 170), 1))                          # grip notches
        for i in range(36):
            a = math.radians(i * 10)
            p.drawLine(QPointF(cx + (R - 7) * math.cos(a), cy - (R - 7) * math.sin(a)),
                       QPointF(cx + (R - 3.5) * math.cos(a), cy - (R - 3.5) * math.sin(a)))
        cap = QRadialGradient(QPointF(cx - R * 0.25, cy - R * 0.3), R)       # brushed cap
        cap.setColorAt(0, QColor('#ffffff'))
        cap.setColorAt(0.5, QColor('#c3c8cd'))
        cap.setColorAt(1, QColor('#7a8087'))
        p.setBrush(cap)
        p.setPen(QPen(QColor('#5d636a'), 1))
        p.drawEllipse(QPointF(cx, cy), R - 9, R - 9)
        a = math.radians(a0 + sweep * f)                                      # pointer
        p.setPen(QPen(QColor('#17191c'), 2.6, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx + (R * 0.18) * math.cos(a), cy - (R * 0.18) * math.sin(a)),
                   QPointF(cx + (R - 11) * math.cos(a), cy - (R - 11) * math.sin(a)))
        # label + value
        p.setFont(font(11, True))
        p.setPen(GREY_T)
        p.drawText(QRectF(0, 0, self.width(), 12), Qt.AlignCenter, self.label)
        p.setFont(font(12, True))
        p.setPen(TEXT)
        v = self.fmt.format(self.val) + self.unit
        p.drawText(QRectF(0, self.height() - 18, self.width(), 16), Qt.AlignCenter, v)


class LcdEq(QWidget):
    """the EQ curve on a screen styled like the unit's LCD (violet ink on white glass, silver bezel); drag the nodes"""
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
        g.setColorAt(0, QColor('#f3f5f8'))
        g.setColorAt(1, QColor('#dde1e8'))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(glass, 4, 4)
        r = self.plot()
        p.setFont(font(10, True))
        for db in (-12, -6, 0, 6, 12):                                        # grid
            y = self.dy(db)
            p.setPen(QPen(QColor(74, 58, 142, 90 if db == 0 else 40), 1, Qt.SolidLine if db == 0 else Qt.DotLine))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(74, 58, 142, 150))
            p.drawText(QRectF(r.left() + 2, y - 12, 40, 12), Qt.AlignLeft, f'{db:+d}' if db else '0')
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self.fx(f)
            p.setPen(QPen(QColor(74, 58, 142, 40), 1, Qt.DotLine))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QColor(74, 58, 142, 150))
            p.drawText(QRectF(x - 20, r.bottom() + 4, 40, 14), Qt.AlignCenter, f'{f // 1000}K' if f >= 1000 else str(f))
        st = self.stages()
        n = 240
        pts = []
        for i in range(n + 1):
            f = 20 * 1000 ** (i / n)
            pts.append(QPointF(self.fx(f), self.dy(max(-18, min(18, mag_db(st, f))))))
        y0 = self.dy(0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(74, 58, 142, 38))
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


class ChannelRail(QWidget):
    picked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.btns, self.meters = [], []
        for i in range(8):
            row = QHBoxLayout()
            b = BlueButton(str(i + 1), checked=(i == 0), w=46, h=40)
            b.toggled.connect(lambda _, k=i: self.pick(k))
            m = Meter(10)
            m.setFixedHeight(40)
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


class Header(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(58)
        self.text, self.online = 'OFFLINE DEMO', False

    def set_status(self, text, online):
        self.text, self.online = text, online
        self.update()

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
        tp.addText(QPointF(20, 38), f, 'ROSETTA CONTROL')
        p.fillPath(tp, QBrush(silver(QRectF(0, 14, 10, 28))))
        p.setFont(font(12, True))
        p.setPen(GREY_T)
        p.drawText(QRectF(320, 22, 300, 20), Qt.AlignLeft | Qt.AlignVCenter, 'OCTA-CAPTURE  ·  ROSETTA OS')
        p.setPen(Qt.NoPen)                                                     # status LED
        p.setBrush(BLUE_LIT if self.online else QColor('#ffb14a'))
        p.drawEllipse(QPointF(r.right() - 330, 30), 5, 5)
        p.setPen(TEXT)
        p.drawText(QRectF(r.right() - 318, 20, 310, 20), Qt.AlignLeft | Qt.AlignVCenter, self.text)


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
        eqp = Panel('EQ')
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
        self.setWindowTitle('Rosetta Control')
        self.resize(1360, 900)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), PANEL)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.chans = [default_chan(demo) for _ in range(8)]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = Header()
        root.addWidget(self.header)
        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        railp = Panel('INPUT')
        rl = QVBoxLayout(railp)
        rl.setContentsMargins(12, 30, 12, 12)
        self.rail = ChannelRail()
        rl.addWidget(self.rail)
        railp.setFixedWidth(96)
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
        self.t = 0
        if demo:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.demo_tick)
            self.timer.start(50)
            self.demo_tick()
        else:
            from octa_link import Link
            self.link = Link()
            self.strip.link = self.link
            self.link.status.connect(self.header.set_status)
            self.link.meters.connect(self.on_meters)
            self.link.preamp.connect(self.strip.on_preamp)
            self.link.flags.connect(self.strip.on_flags)
            self.link.start()

    def on_meters(self, db, gr):
        for i, m in enumerate(self.rail.meters):
            m.level = min(1.0, max(0.0, (db[i] + 60) / 60))
            m.update()
        self.strip.gr.level = min(1.0, max(0.0, gr / 20))
        self.strip.gr.update()
        d = db[self.strip.ch]
        self.strip.inlab.setText('IN  %s' % ('%.1f dB' % d if d > -100 else '--'))

    def closeEvent(self, e):
        if hasattr(self, 'link'):
            self.link.stop = True
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
    app = QApplication(sys.argv[:1])
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
