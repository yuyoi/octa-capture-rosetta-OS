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
import datetime
import math
import os
import shutil
import struct
import sys
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QIcon, QKeySequence, QPixmap, QPolygonF, QRadialGradient, QShortcut)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMenu, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, '..', 'art', 'app')
RECORDINGS = os.environ.get('RD_RECDIR', r'E:\skriv 2026\recordings')   # same folder as the old OCTA REC

NO_FW = "NOT ON THIS BOX'S FIRMWARE: flash test/strip10"     # said on every control the running firmware can't do

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
TOUCH_H = 40                               # every button at least this tall: a fingertip, not a mouse tip
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

def fine_factor(e, x0):
    """Shift = fine; on touch (no Shift) slide the finger sideways off the knob: the further, the finer (down to 1/6)"""
    if e.modifiers() & Qt.ShiftModifier:
        return 0.25
    return max(1 / 6, 1.0 / (1.0 + max(0.0, abs(e.position().x() - x0) - 30) / 50))


class BlueButton(QWidget):
    toggled = Signal(bool)

    def __init__(self, text, checkable=True, checked=False, w=64, h=30, parent=None):
        super().__init__(parent)
        self.text, self.checkable, self.checked = text, checkable, checked
        self.blocked = None                        # a reason: the running firmware can't do this, the key says so
        self.setFixedSize(w, h)
        self.setCursor(Qt.PointingHandCursor)

    def set_blocked(self, why):
        if why != self.blocked:
            self.blocked = why
            self.setToolTip(why or '')
            self.update()

    def mousePressEvent(self, e):
        if self.blocked:
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(e.globalPosition().toPoint(), self.blocked, self)
            return
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
        if self.blocked:                                                   # dead key: dark, NO FW under the name
            p.setBrush(QColor('#3a3d42'))
            p.drawRoundedRect(r, 3.5, 3.5)
            p.setPen(QColor('#8d939b'))
            p.setFont(font(11, True))
            p.drawText(r.adjusted(0, 2, 0, -r.height() / 2 + 2), Qt.AlignCenter, self.text)
            p.setPen(HOT)
            p.setFont(font(9, True))
            p.drawText(r.adjusted(0, r.height() / 2 - 2, 0, -2), Qt.AlignCenter, 'NO FW')
            return
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
        self._drag = (e.position().x(), e.position().y())

    def mouseMoveEvent(self, e):
        if self._drag:                                 # step by step, so the fine factor can change mid-drag
            x0, y_last = self._drag
            self.set_frac(self.frac() + (y_last - e.position().y()) / 180 * fine_factor(e, x0))
            self._drag = (x0, e.position().y())

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
        self.sel = None                          # the band last touched: ringed, its values shown on the screen
        self.overlay = None                      # [(Hz, dB)]: what a mic's curve asks for, drawn dashed behind the EQ

    NODE_R = 12                                  # drawn node radius (touch: was 6)
    GRAB = 46                                    # a press this close (px) to a node takes the nearest one

    def plot(self):
        return QRectF(self.rect()).adjusted(24, 24, -24, -28)

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

    def nearest(self, pos):
        """the band whose node is closest to pos, if within GRAB px (the selected one wins a tie)"""
        best, bd = None, self.GRAB
        for kind, fk, gk in self.NODES:
            d = self.node_pos(kind, fk, gk) - pos
            d = math.hypot(d.x(), d.y()) - (0.5 if kind == self.sel else 0)
            if d < bd:
                best, bd = (kind, fk, gk), d
        return best

    def mousePressEvent(self, e):
        hit = self.nearest(e.position())
        if not hit:
            return
        kind, fk, gk = hit
        self.sel = kind
        self._drag = (fk, gk, self.node_pos(kind, fk, gk) - e.position())   # keep the grab offset: no jump
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        fk, gk, off = self._drag
        pt = e.position() + off
        self.c[fk] = round(self.xf(pt.x()), 1)
        if gk:
            self.c[gk] = round(self.yd(pt.y()) * 2) / 2
        self.edited.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        """wheel over the screen: Q of the PEAK band (touch: the Q knob under the screen)"""
        if self.sel == 'peak' or (self.nearest(e.position()) or ('',))[0] == 'peak':
            self.sel = 'peak'
            q = self.c['peak_q'] * (1.1 if e.angleDelta().y() > 0 else 1 / 1.1)
            self.c['peak_q'] = round(min(8.0, max(0.3, q)), 2)
            self.edited.emit()
            self.update()

    def readout(self, kind):
        c = self.c
        hz = lambda f: f'{f / 1000:.2f} kHz' if f >= 1000 else f'{f:.0f} Hz'
        if kind == 'hpf':
            return f'HPF  {hz(c["hpf_f"])}'
        s = f'{kind.upper()}  {hz(c[kind + "_f"])}  {c[kind + "_g"]:+.1f} dB'
        return s + f'  Q {c["peak_q"]:.2f}' if kind == 'peak' else s

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
        if self.overlay:
            p.setPen(QPen(QColor(255, 196, 90, 200), 1.6, Qt.DashLine))
            p.drawPolyline(QPolygonF([QPointF(self.fx(f), self.dy(max(-18, min(18, d)))) for f, d in self.overlay]))
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
        R = self.NODE_R
        for kind, fk, gk in sorted(self.NODES, key=lambda n: n[0] == self.sel):   # nodes, the selected one on top
            pt = self.node_pos(kind, fk, gk)
            on = self.c['on'][kind]
            sel = kind == self.sel
            if sel:                                                           # halo + ring: this one is grabbed
                halo = QRadialGradient(pt, R * 2.4)
                halo.setColorAt(0, QColor(*INK, 110))
                halo.setColorAt(1, QColor(*INK, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(pt, R * 2.4, R * 2.4)
                p.setPen(QPen(QColor('#ffffff'), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(pt, R + 5, R + 5)
            p.setPen(QPen(LCD_INK, 2.4))
            p.setBrush(LCD_INK if on else LCD_BG)
            p.drawEllipse(pt, R, R)
            p.setFont(font(9, True))
            p.setPen(LCD_BG if on else LCD_INK)
            p.drawText(QRectF(pt.x() - R, pt.y() - R, 2 * R, 2 * R), Qt.AlignCenter, dict(hpf='HP', low='LO', peak='PK', high='HI')[kind])
            p.setFont(font(10, True))
            p.setPen(QColor('#ffffff') if sel else LCD_INK)
            ly = pt.y() - R - 18 if pt.y() - R - 18 > r.top() - 20 else pt.y() + R + 4   # under it at the top edge
            p.drawText(QRectF(pt.x() - 30, ly, 60, 14), Qt.AlignCenter, kind.upper())
        if self.sel:                                                          # the grabbed band's values
            p.setFont(font(12, True))
            t = self.readout(self.sel)
            w = p.fontMetrics().horizontalAdvance(t) + 20
            box = QRectF(r.right() - w, r.top() - 15, w, 22)
            p.setPen(QPen(QColor(*INK, 160), 1))
            p.setBrush(QColor(5, 7, 12, 220))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor('#ffffff'))
            p.drawText(box, Qt.AlignCenter, t)


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


def draw_broadcast(p, cx, cy, color):
    """the 'on air' mark: a dot with two arcs each side"""
    p.save()
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QPointF(cx, cy), 2.4, 2.4)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap))
    for r in (5.5, 9.0):
        rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        p.drawArc(rect, -45 * 16, 90 * 16)
        p.drawArc(rect, 135 * 16, 90 * 16)
    p.restore()


class TrackButton(QWidget):
    """a channel in the rail: picture + number + name; lit blue when selected. Double-click = name + picture,
    right-click = menu (name + picture, send to stream). The broadcast mark shows it is being sent."""
    clicked = Signal()
    edit = Signal()
    menu = Signal()

    def __init__(self, n, parent=None):
        super().__init__(parent)
        self.n, self.name, self.pm, self.checked, self.stream = n, '', QPixmap(), False, False
        self.setFixedSize(150, 52)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('double-click: name + picture. Right-click or HOLD: more (send to stream)')
        self.hold = QTimer(self)                       # touch: press and hold 0.5 s = the right-click menu
        self.hold.setSingleShot(True)
        self.hold.setInterval(500)
        self.hold.timeout.connect(self._held)
        self._p0, self._held_fired = None, False

    def set_track(self, t):
        self.name = t.get('name', '')
        self.pm = icon_pixmap(t.get('icon', ''))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.menu.emit()
        else:
            self.clicked.emit()
            self._p0, self._held_fired = e.position(), False
            self.hold.start()

    def mouseMoveEvent(self, e):
        if self._p0 is not None and (e.position() - self._p0).manhattanLength() > 12:
            self.hold.stop()                           # a finger that slides is not a hold

    def mouseReleaseEvent(self, e):
        self.hold.stop()
        self._p0 = None

    def _held(self):
        self._held_fired = True
        self._p0 = None
        self.menu.emit()

    def mouseDoubleClickEvent(self, e):
        self.hold.stop()
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
        if self.stream:
            draw_broadcast(p, r.right() - 14, r.top() + 13, QColor('#ffffff') if self.checked else LCD_INK)


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
                           'padding:10px 16px;font:bold 13px "Segoe UI";} QPushButton:checked{background:#3a86e8;color:white;}')
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
    stream_toggle = Signal(int)
    stream_settings = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.unit = 0
        self.tracks = load_tracks(0)
        self.stream_name, self.sends = 'Discord', set()
        self.btns, self.meters = [], []
        for i in range(8):
            row = QHBoxLayout()
            row.setSpacing(6)
            b = TrackButton(i)
            b.checked = i == 0
            b.set_track(self.tracks[i])
            b.clicked.connect(lambda k=i: self.pick(k))
            b.edit.connect(lambda k=i: self.edit(k))
            b.menu.connect(lambda k=i: self.show_menu(k))
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

    def build_menu(self, k):
        m = QMenu(self)
        m.setStyleSheet('QMenu{background:#15171a;color:#e6e9ec;border:1px solid #8c9299;font:bold 14px "Segoe UI";}'
                        'QMenu::item{padding:13px 26px;} QMenu::item:selected{background:#2d6bd2;}')
        m.addAction('Name and picture...', lambda: self.edit(k))
        send = m.addAction(f'Send to {self.stream_name}', lambda: self.stream_toggle.emit(k))
        send.setCheckable(True)
        send.setChecked(k in self.sends)
        m.addSeparator()
        m.addAction(f'{self.stream_name} settings...', self.stream_settings.emit)
        return m

    def show_menu(self, k):
        self.build_menu(k).exec(self.btns[k].mapToGlobal(self.btns[k].rect().bottomLeft()))

    def set_stream(self, name, sends):
        self.stream_name, self.sends = name, set(sends)
        for i, b in enumerate(self.btns):
            b.stream = i in self.sends
            b.update()

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


def mic_overlay(c):
    """the dashed line for an input with a mic curve on it, or None"""
    if c.get('mic', 'off') == 'off':
        return None
    import mic_comp
    try:
        return list(zip(mic_comp.GRID, mic_comp.correction(mic_comp.load(c['mic']))))
    except (OSError, ValueError):
        return None


class StripPage(QWidget):
    preset = Signal(str)                              # a strip patch or amp tone by name (strip_patches / amp_presets)
    patches = Signal()                                # open the patch browser
    amp = Signal(str)
    amp_knob = Signal(str, float)                     # amp head knob: key, 0-10
    dirty = Signal()                                  # something worth saving changed
    mic = Signal()                                    # next mic curve for the shown input

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
            b = BlueButton(k.upper(), checked=c['on'][k], w=74, h=TOUCH_H)
            b.toggled.connect(lambda v, kk=k: self.set_on(kk, v))
            blocks.addWidget(b)
            self.blk[k] = b
        blocks.addStretch(1)
        self.patch_btn = QPushButton('PATCH')           # mic + guitar patches for the shown input (tap = browser)
        self.patch_btn.setFixedSize(210, TOUCH_H)
        self.patch_btn.setStyleSheet('QPushButton{color:#62b2ff;background:#070a10;border:2px solid #8c9299;border-radius:4px;'
                                     'font:bold 12px "Segoe UI";text-align:left;padding-left:10px;}'
                                     'QPushButton:pressed{background:#1f4fa8;color:#fff;}')
        self.patch_btn.setToolTip('patches for this input: mic (e935) and guitar, DI chains and amp tones; save your own')
        self.patch_btn.clicked.connect(self.patches.emit)
        blocks.addWidget(self.patch_btn)
        self.amp_btn = QPushButton('AMP OFF')            # click = next amp voice for the shown input
        self.amp_btn.setFixedSize(112, TOUCH_H)
        self.amp_btn.setStyleSheet('QPushButton{color:#c9ced4;background:#2a2d31;border:1px solid #4a4f55;border-radius:4px;'
                                   'font:bold 11px "Segoe UI";}')
        self.amp_btn.clicked.connect(self.next_amp)
        blocks.addWidget(self.amp_btn)
        self.mic_btn = QPushButton('MIC OFF')            # click = next mic curve (app/mics/) for the shown input
        self.mic_btn.setFixedSize(112, TOUCH_H)
        self.mic_btn.setStyleSheet(self.amp_btn.styleSheet())
        self.mic_btn.setToolTip('correct this input for its microphone: the three EQ bands are fitted to the mic\'s curve')
        self.mic_btn.clicked.connect(self.mic.emit)
        blocks.addWidget(self.mic_btn)
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
        from amp_panel import AmpHead                      # in amp mode the EQ screen becomes the amp head
        self.amp_head = AmpHead()
        self.amp_head.knob.connect(self.amp_knob.emit)
        self.amp_head.voice.connect(self.amp.emit)
        self.amp_preset = self.amp_head.preset                  # tone presets (amp_presets.py)
        self.amp_save = self.amp_head.save_preset
        self.amp_delete = self.amp_head.delete_preset
        self.amp_head.hide()
        mid.addWidget(self.amp_head, 1)
        self.gate = None                                   # main window: gate(ch, change) -> allowed by the DSP budget
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
        self.b48 = BlueButton('+48V', checked=c['ph48'], w=74, h=TOUCH_H)
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
        if self.link:
            self.link.set_block(self.ch, block, c['on'][block], dict(c))

    def set_on(self, k, v):
        if v and k != 'gate' and self.gate and not self.gate(self.ch, lambda n: n['on'].__setitem__(k, True)):
            self.blk[k].checked = False
            self.blk[k].update()
            return
        self.chans[self.ch]['on'][k] = v
        self.push(k)
        self.eq.update()
        self.refresh_tube()
        self.patch_edited()
        self.dirty.emit()

    def patch_edited(self):
        c = self.chans[self.ch]
        if c.get('patch') and not c.get('patch_edited'):
            c['patch_edited'] = True
            self.show_patch()

    def show_patch(self):
        c = self.chans[self.ch]
        if c.get('amp', 'off') != 'off':
            name, ed = c.get('amp_preset') or 'AMP ' + c['amp'].upper(), c.get('amp_edited')
        else:
            name, ed = c.get('patch') or '-', c.get('patch_edited')
        self.patch_btn.setText(f'PATCH  {name}' + ('  *' if ed else ''))

    def set_val(self, k, v):
        c = self.chans[self.ch]
        c[k] = v
        if c['on'][KEY_BLOCK[k]]:
            self.push(KEY_BLOCK[k])
        self.eq.update()
        self.refresh_tube()
        self.patch_edited()
        self.dirty.emit()

    def eq_edited(self):
        self.sync_knobs()
        for b in ('hpf', 'low', 'peak', 'high'):
            if self.chans[self.ch]['on'][b]:
                self.push(b)
        self.patch_edited()
        self.dirty.emit()

    def gain_turned(self, v):
        v = round(v * 2) / 2
        self.chans[self.ch]['pre_gain'] = v
        self.chans[self.ch]['gain_known'] = True
        if self.link:
            self.link.set_gain(self.ch, round(v * 2))
        self.dirty.emit()

    def ph48_toggled(self, on):
        self.chans[self.ch]['ph48'] = on
        if self.link:
            self.link.set_48v(self.ch, on)

    def on_preamp(self, ch, raw, ph, hiz=-1):
        if ch != self.ch:
            return
        c = self.chans[ch]
        changed = False
        if self.gain._drag is None:
            changed = c['pre_gain'] != raw / 2 or not c.get('gain_known')      # the front-panel knob counts too
            c['pre_gain'] = raw / 2
            c['gain_known'] = True
            self.gain.val = raw / 2
            self.gain.update()
        c['ph48'] = bool(ph)
        self.b48.checked = bool(ph)
        self.b48.update()
        if hiz >= 0 and c.get('hiz') != bool(hiz):
            c['hiz'] = bool(hiz)
            changed = True
        if changed:
            self.dirty.emit()

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

    def next_amp(self):
        voices = ('off', 'clean', 'crunch', 'lead', 'cranked', 'fuzz')
        cur = self.chans[self.ch].get('amp', 'off')
        self.amp.emit(voices[(voices.index(cur) + 1) % len(voices)])

    def show_chan(self, k):
        self.ch = k
        if self.link:
            self.link.watch = k
        c = self.chans[k]
        self.amp_btn.setText('AMP ' + c.get('amp', 'off').upper())
        amp_on = c.get('amp', 'off') != 'off'
        self.eqp.setVisible(not amp_on)
        self.amp_head.setVisible(amp_on)
        if amp_on:
            self.amp_head.show_channel(c, getattr(self, 'track_name', ''))
        self.mic_btn.setText('MIC ' + c.get('mic', 'off').upper()[:10])
        self.eq.overlay = mic_overlay(c)
        self.gain.val = c['pre_gain']
        self.gain.update()
        self.b48.checked = c['ph48']
        self.b48.update()
        self.eq.c = c
        for key, b in self.blk.items():
            b.checked = c['on'][key]
            b.setVisible(not (amp_on and key in ('low', 'peak', 'high')))     # in amp mode those are the amp's tone knobs
            b.update()
        self.sync_knobs()
        self.show_patch()
        self.eq.update()
        self.refresh_tube()


class PatchBrowser(QDialog):
    """every patch for the shown input: four big group tabs (MIC e935, GUITAR DI, GUITAR AMP tones, YOURS), each a
    4-wide grid of finger-sized keys. One tap loads it straight away and the window stays open, so you can tap
    through them while you play."""
    COLS = (('MIC', 'MIC  ·  e935'), ('GUITAR', 'GUITAR  ·  DI'), ('AMP', 'GUITAR  ·  AMP'), ('USER', 'YOURS'))
    WIDE = 4

    def __init__(self, win):
        super().__init__(win)
        self.win, self.fx_ok, self.btns = win, None, {}
        self.setWindowTitle('Patches')
        self.setStyleSheet(
            'QDialog{background:#0c0d0f;} QLabel{color:#c9ced4;font:bold 12px "Segoe UI";}'
            'QLabel#col{color:#8d939b;font:bold 12px "Segoe UI";letter-spacing:1px;}'
            'QLabel#who{color:#62b2ff;font:bold 15px "Segoe UI";}'
            'QLabel#fw{color:#ff5a4a;font:bold 13px "Segoe UI";border:1px solid #ff5a4a;border-radius:4px;padding:6px;}'
            'QLabel#note{color:#62b2ff;background:#070a10;border:1px solid #2a3a52;border-radius:4px;padding:8px;'
            'font:bold 13px "Segoe UI";}'
            'QPushButton{background:#b4b8bd;color:#24272b;border:2px solid #d6d9dd;border-radius:5px;padding:4px 10px;'
            'font:bold 13px "Segoe UI";text-align:left;}'
            'QPushButton:checked{background:#2a6fe0;color:#fff;border-color:#bfe3ff;}'
            'QPushButton#tool{text-align:center;padding:10px 16px;}'
            'QPushButton#tab{text-align:center;padding:10px 16px;font:bold 14px "Segoe UI";background:#2a2d31;color:#c9ced4;'
            'border-color:#4a4f55;}'
            'QPushButton#tab:checked{background:#2a6fe0;color:#fff;border-color:#bfe3ff;}'
            'QPushButton:disabled{background:#3a3d42;color:#6d737b;border-color:#4a4f55;}')
        root = QVBoxLayout(self)
        root.setSpacing(10)
        self.who = QLabel()
        self.who.setObjectName('who')
        root.addWidget(self.who)
        self.fw = QLabel("GATE, ECHO, REVERB and HARD CLIP in these patches do nothing on this box's firmware:"
                         " flash test/strip10 to hear them. EQ, tube, comp and the amp work now.")
        self.fw.setObjectName('fw')
        self.fw.setWordWrap(True)
        self.fw.hide()
        root.addWidget(self.fw)
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self.tabs, self.pages, self.col, self.tab, self.built = {}, {}, {}, 'MIC', None
        for key, title in self.COLS:
            t = QPushButton(title)
            t.setObjectName('tab')
            t.setCheckable(True)
            t.setMinimumHeight(TOUCH_H + 8)
            t.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            tabs.addWidget(t, 1)
            self.tabs[key] = t
        root.addLayout(tabs)
        for key, _ in self.COLS:
            page = QWidget()
            g = QGridLayout(page)
            g.setContentsMargins(0, 4, 0, 0)
            g.setSpacing(6)
            for i in range(self.WIDE):
                g.setColumnStretch(i, 1)
            root.addWidget(page, 1)
            self.pages[key], self.col[key] = page, g
        self.show_tab('MIC')
        self.note = QLabel()
        self.note.setObjectName('note')
        self.note.setWordWrap(True)
        self.note.setMinimumHeight(54)
        root.addWidget(self.note)
        row = QHBoxLayout()
        self.save_b = QPushButton('SAVE AS YOURS...')
        self.del_b = QPushButton('DELETE')
        close = QPushButton('CLOSE')
        for b in (self.save_b, self.del_b, close):
            b.setObjectName('tool')
            b.setMinimumHeight(TOUCH_H + 4)
        self.save_b.clicked.connect(self.ask_save)
        self.del_b.clicked.connect(self.ask_delete)
        close.clicked.connect(self.close)
        row.addWidget(self.save_b)
        row.addWidget(self.del_b)
        row.addStretch(1)
        row.addWidget(close)
        root.addLayout(row)
        self.resize(1180, 720)

    def show_tab(self, key):
        self.tab = key
        for k, page in self.pages.items():
            page.setVisible(k == key)
            self.tabs[k].setChecked(k == key)

    def open_on_current(self):
        """opening the browser: the tab holding what the input runs now (amp on = AMP; unit 1 starts on guitar)"""
        cur, _ = self.current()
        for col, name, *_ in self.items():
            if name == cur:
                self.show_tab(col)
                return
        self.show_tab('AMP' if self.chan().get('amp', 'off') != 'off' else ('GUITAR' if self.win.cur == 0 else 'MIC'))

    def set_fx_ok(self, ok):
        self.fx_ok = ok
        self.fw.setVisible(ok is False)

    def chan(self):
        return self.win.units[self.win.cur]['chans'][self.win.strip.ch]

    def current(self):
        c = self.chan()
        return (c.get('amp_preset'), c.get('amp_edited')) if c.get('amp', 'off') != 'off' else (c.get('patch'), c.get('patch_edited'))

    def items(self):
        """(column, name, note, uses the gate/FX, is yours)"""
        import amp_presets
        import strip_patches
        out = [(p['group'], p['name'], p['note'], p['gate'] is not None, False) for p in strip_patches.FACTORY]
        out += [('AMP', p['name'], p['note'], True, False) for p in amp_presets.FACTORY]
        out += [('USER', p['name'], p['note'] or 'your patch', p['gate'] is not None, True) for p in strip_patches.load_user()]
        out += [('USER', p['name'], p['note'] or 'your amp tone', True, True) for p in amp_presets.load_user()]
        return out

    def build(self, items):
        """(re)make the keys; only when the list itself changed (saved / deleted one of yours)"""
        for g in self.col.values():
            while g.count():
                w = g.takeAt(0).widget()
                if w:
                    w.hide()                                   # gone now, not at the next event loop turn
                    w.deleteLater()
        self.btns, self.notes, self.mine = {}, {}, set()
        n = dict.fromkeys(self.col, 0)
        for col, name, note, fx, mine in items:
            b = QPushButton(name + ('   + GATE' if fx and col in ('MIC', 'GUITAR') else ''))
            b.setCheckable(True)
            b.setMinimumHeight(TOUCH_H + 4)
            b.setToolTip(note)
            b.clicked.connect(lambda _=False, nm=name: self.pick(nm))
            self.col[col].addWidget(b, n[col] // self.WIDE, n[col] % self.WIDE)
            n[col] += 1
            self.btns[name], self.notes[name] = b, note
            if mine:
                self.mine.add(name)
        if not self.mine:
            t = QLabel('None yet: set an input up the way you like it, then SAVE AS YOURS.')
            t.setObjectName('col')
            self.col['USER'].addWidget(t, 0, 0, 1, self.WIDE)
        for g, k in ((self.col[k], k) for k in self.col):
            g.setRowStretch(g.rowCount(), 1)                   # keys stay at the top, finger height
        for key, title in self.COLS:
            self.tabs[key].setText(f'{title}   {n[key]}')
        self.built = items

    def refresh(self):
        items = self.items()
        if items != self.built:
            self.build(items)
        cur, edited = self.current()
        for name, b in self.btns.items():
            b.setChecked(name == cur)
        tracks = load_tracks(self.win.cur)
        nm = (tracks[self.win.strip.ch].get('name') or '').upper()
        self.who.setText(f'UNIT {self.win.cur + 1}  ·  INPUT {self.win.strip.ch + 1}' + (f'  ·  {nm}' if nm else '')
                         + (f'      now: {cur}' + (' (edited)' if edited else '') if cur else ''))
        self.note.setText(self.notes.get(cur, 'Tap a patch: it loads on this input straight away. The preamp gain stays where it is.'))
        self.del_b.setEnabled(cur in self.mine)

    def pick(self, name):
        self.win.apply_preset(self.win.cur, self.win.strip.ch, name)
        self.refresh()

    def ask_save(self):
        from PySide6.QtWidgets import QInputDialog
        cur, edited = self.current()
        name, ok = QInputDialog.getText(self, 'Save patch', 'Name:', text=cur if cur in self.mine else '')
        if ok and name.strip():
            self.win.save_patch(self.win.cur, self.win.strip.ch, name.strip())
            self.refresh()

    def ask_delete(self):
        cur, _ = self.current()
        if cur in self.mine:
            self.win.delete_patch(cur)
            self.refresh()


class RecButton(QWidget):
    """one tap: red dot + REC; while recording it glows red and shows the time"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on, self.text = False, 'REC'
        self.setFixedSize(132, TOUCH_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('one tap: record every input of both boxes (space bar)')

    def mousePressEvent(self, e):
        self.clicked.emit()

    def set_state(self, on, text):
        self.on, self.text = on, text
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(silver(outer))
        p.drawRoundedRect(outer, 5, 5)
        r = outer.adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self.on:
            g.setColorAt(0, QColor('#ff5a4a'))
            g.setColorAt(1, QColor('#a81a12'))
        else:
            g.setColorAt(0, QColor('#2a2d32'))
            g.setColorAt(1, QColor('#15171a'))
        p.setBrush(g)
        p.drawRoundedRect(r, 3.5, 3.5)
        p.setBrush(QColor('#ffffff') if self.on else QColor('#ff4436'))
        p.drawEllipse(QPointF(r.left() + 16, r.center().y()), 6, 6)
        p.setPen(QColor('#ffffff'))
        p.setFont(font(13, True))
        p.drawText(r.adjusted(30, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text)


class Main(QWidget):
    bench_done = Signal(int)
    mode_changed = Signal(int, str)

    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle('Rosetta Octa Control')
        self.setWindowIcon(QIcon(os.path.join(ART, 'app_icon.ico')))
        self.resize(1460, 950)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), PANEL)
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.engine, self.usb_rate, self.usb_boxes, self.recording, self.take_dir, self.rec_t0 = None, 0, 0, False, '', 0
        self.units = [dict(chans=[default_chan(demo) for _ in range(8)], link=None) for _ in range(2)]
        self.cur = 0
        self.chans = self.units[0]['chans']
        import octa_setup
        self.setup = octa_setup
        self.setup_path = None if demo else octa_setup.PATH   # a demo never reads or writes your settings
        self.rig, self.has_saved = dict(octa_setup.DEFAULT_RIG), False
        self.restoring = [0.0, 0.0]                           # until this time the box's gains are not news
        self.closing = self.engine_dead = self.engine_nobox = False
        self.engine_backoff, self.engine_next, self.fc = 10.0, 0.0, None
        self.box_unit, self.unit_ok = [], set()               # engine box -> unit, and the units we are sure about
        self.octas_seen, self.count_next = 0, 0.0             # boxes Windows has, when the engine found fewer
        self.load_setup()
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
            b = BlueButton(f'UNIT {i + 1}', checked=(i == 0), w=80, h=TOUCH_H)
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
            b = BlueButton(t, checked=(i == 0), w=100, h=TOUCH_H)
            pages.addWidget(b)
            self.pagebtn.append(b)
        pages.addSpacing(18)
        import dsp_budget
        self.dspb = dsp_budget
        self.budget = dsp_budget.Budget()
        self.dsp_bar = dsp_budget.DspBar(360, TOUCH_H)
        self.dsp_bar.clicked.connect(self.show_dsp_tasks)
        pages.addWidget(self.dsp_bar)
        self.dsp_win = None
        pages.addStretch(1)
        self.rec_info = QLabel('')
        self.rec_info.setStyleSheet('color:#c9ced4;font:bold 12px "Segoe UI";')
        pages.addWidget(self.rec_info)
        self.mon = QPushButton('MONITOR OTHER BOX')              # the other box's inputs in this box's outputs, over USB
        self.mon.setCheckable(True)
        self.mon.setStyleSheet('QPushButton{color:#c9ced4;background:#2a2d31;border:1px solid #4a4f55;border-radius:4px;'
                               'padding:6px 12px;min-height:26px;font:bold 11px "Segoe UI";}'
                               'QPushButton:checked{color:#fff;background:#1f6fd0;border-color:#6fb0ff;}')
        self.mon.toggled.connect(self.on_mon)
        pages.addWidget(self.mon)
        self.mode_btn = QPushButton('FAT')                       # FAT = full precision, SLIM = cheaper DSP for more strips
        self.mode_btn.setToolTip('FAT: every filter at full precision. SLIM: cheaper filters where nobody can hear the difference, '
                                 'comp worked out every 8 samples. More strips fit.')
        self.mode_btn.setStyleSheet(self.mon.styleSheet())
        self.mode_btn.clicked.connect(self.toggle_mode)
        pages.addWidget(self.mode_btn)
        self.rec = RecButton()
        self.rec.clicked.connect(self.toggle_rec)
        pages.addWidget(self.rec)
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_rec)
        col.addLayout(pages)
        self.strip = StripPage(self.chans)
        col.addWidget(self.strip, 1)
        body.addLayout(col, 1)
        root.addLayout(body, 1)
        self.rail.picked.connect(self.strip.show_chan)
        self.strip.gate = lambda ch, change: self.dsp_gate(self.cur, ch, change)
        self.dsp_timer = QTimer(self)
        self.dsp_timer.timeout.connect(self.refresh_dsp)
        self.dsp_timer.start(400)
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
                L.status.connect(lambda t, o, k=i: self.usb_rate or self.engine_nobox or self.header.unit_status(k, t, o))
                L.meters.connect(lambda db, gr, k=i: k == self.cur and self.on_link_meters(db, gr))
                L.preamp.connect(lambda ch, g, ph, z, k=i: time.time() >= self.restoring[k] and self.trusted(k)
                                 and self.on_unit_preamp(k, ch, g, ph, z))
                L.flags.connect(lambda fl, k=i: self.on_flags(k, fl))
                L.load.connect(lambda busy, per, k=i: self.budget.update(k, busy, per))
                L.start()
            self.strip.link = self.units[0]['link']
            from octa_engine import Engine
            self.engine = Engine()
            self.engine.ready.connect(self.on_engine_ready)
            self.engine.nobox.connect(lambda: setattr(self, 'engine_nobox', True))
            self.engine.meters.connect(lambda box, db: self.unit_of(box) == self.cur and self.on_meters(db, 0.0))
            self.usb_m = {}
            self.engine.meters.connect(lambda box, db: self.usb_m.__setitem__(self.unit_of(box), db))
            self.engine.started.connect(self.on_rec_started)
            self.engine.done.connect(self.finish_take)
            self.engine.error.connect(self.on_engine_error)
            self.engine.feed_status.connect(self.on_feed_status)
            self.engine.start()
            if self.rig.get('monitor'):                           # it was on when the app last closed
                QTimer.singleShot(3000, lambda: self.mon.setChecked(True))
            self.link_timer = QTimer(self)
            self.link_timer.timeout.connect(self.watch_links)
            self.link_timer.start(2000)
            if self.rig['focusrite_inputs']:
                from focusrite import Keeper
                self.fc = Keeper(self.rig['focusrite_inputs'], log=self.do_log)
                self.fc.start()
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_setup)
        self.strip.dirty.connect(self.touch)
        self.strip.mic.connect(self.next_mic)
        self.strip.amp_knob.connect(lambda key, v: self.set_amp_knob(self.cur, self.strip.ch, key, v))
        self.strip.amp_preset.connect(lambda n: self.load_amp_preset(self.cur, self.strip.ch, n))
        self.strip.amp_save.connect(lambda n: self.save_amp_preset(self.cur, self.strip.ch, n))
        self.strip.amp_delete.connect(self.delete_amp_preset)
        self.rail.stream_toggle.connect(self.toggle_send)
        self.rail.stream_settings.connect(self.stream_dialog)
        self.feed_dev, self.feed_last = None, ''
        self.refresh_stream_marks()
        self.bench_done.connect(self.after_bench)
        self.mode_changed.connect(self.on_mode)
        self.rec_timer = QTimer(self)
        self.rec_timer.timeout.connect(self.rec_tick)
        self.strip.preset.connect(lambda name: self.apply_preset(self.cur, self.strip.ch, name))
        self.strip.patches.connect(self.patch_browser)
        self.patch_win = None
        self.strip.amp.connect(lambda v: self.set_amp(self.cur, self.strip.ch, v))
        self.do_timer = QTimer(self)
        self.do_timer.timeout.connect(self.poll_do)
        if self.setup_path:                                   # a demo never takes the real app's commands
            self.do_timer.start(300)

    def on_link_meters(self, db, gr):
        if not self.usb_rate:
            self.on_meters(db, gr)
            return
        self.strip.gr.level = min(1.0, max(0.0, gr / 20))     # on USB the engine feeds the input meters
        self.strip.gr.update()

    # ---------------- settings that survive a power cut (app/setup.json, octa_setup.py) ----------------
    def load_setup(self):
        d = self.setup_path and self.setup.load(self.setup_path)
        if not d:
            return
        self.rig, self.has_saved = d['rig'], True
        for u, saved in zip(self.units, d['units']):
            for c, s in zip(u['chans'], saved):
                c['on'].update(s.get('on', {}))
                c.update({k: v for k, v in s.items() if k != 'on'})

    def touch(self):
        if self.setup_path:
            self.save_timer.start(1500)

    def save_setup(self):
        if not self.setup_path:
            return
        try:
            self.setup.save(dict(units=[u['chans'] for u in self.units], rig=self.rig), self.setup_path)
            self.has_saved = True
        except OSError as e:
            self.rec_info.setText('could not save settings: ' + str(e)[:60])

    def restore_unit(self, k):
        """a box that came up with an empty strip gets everything back; +48V stays the user's button"""
        u, L = self.units[k], self.units[k]['link']
        self.restoring[k] = time.time() + 3.0                 # don't take its boot-up gains for your settings
        for ch, c in enumerate(u['chans']):
            for b in ('hpf', 'low', 'peak', 'high', 'tube', 'comp', 'gate'):
                if c['on'].get(b):
                    L.set_block(ch, b, True, dict(c))
            if c.get('gain_known'):
                L.set_gain(ch, round(c['pre_gain'] * 2))
            if ch < 2 and 'hiz' in c:
                L.set_hiz(ch, c['hiz'])
            if c.get('amp', 'off') != 'off':
                self.push_amp(k, ch)
        self.do_log(f'unit {k + 1}: restored after power-up')
        if not self.recording:
            self.rec_info.setText(f'UNIT {k + 1}: your settings are back')

    def apply_routes(self, k):
        L = self.units[k]['link']
        for pair, wire in self.rig['patchbay'].get(str(k), {}).items():
            L.ensure_patchbay(int(pair), wire, lambda p, w, u=k: self.do_log(f'unit {u + 1}: output pair {p} set back to {w}'))

    def on_engine_error(self, t):
        if self.engine_nobox and 'has stopped' in t:          # it only stopped because there was no box to talk to
            return
        self.rec_info.setText('ENGINE: ' + t)
        if not self.closing and ('disconnected' in t or 'has stopped' in t):
            self.engine_dead = True

    def watch_links(self):
        """power-cycled boxes leave the engine holding dead USB handles: start a new engine, gently, until they answer"""
        if self.closing or self.recording:
            return
        now = time.time()
        if self.engine_nobox:                                 # rack off (or boxes on another driver): keep looking
            if now >= self.engine_next:
                self.engine_next = now + 8.0
                self.header.units.clear()
                self.header.set_status('WAITING FOR THE BOXES: switch them on', False)
                self.rec_info.setText('no box on USB yet')
                self.engine.restart()
            return
        if self.usb_boxes < 2 and now >= self.count_next:     # a box that was still booting when the engine looked
            self.count_next = now + 20.0
            import threading
            threading.Thread(target=self.count_octas, daemon=True).start()
        if self.octas_seen > self.usb_boxes and now >= self.engine_next:
            self.octas_seen = 0
            self.engine_next = now + 20.0
            self.do_log('engine: Windows has more boxes than the engine found, looking again')
            self.engine.restart()
            return
        late = [i for i in self.unit_ok
                if not getattr(self.units[i]['link'], 'busy', False) and now - self.units[i]['link'].last_ok > 10]
        if not late and not self.engine_dead:
            self.engine_backoff = 10.0
            return
        if now < self.engine_next:
            return
        self.engine_next = now + self.engine_backoff
        self.engine_backoff = min(60.0, self.engine_backoff * 2)
        self.engine_dead = False
        self.do_log(f'engine: restarting (boxes not answering: {[i + 1 for i in late]})')
        self.header.set_status('RECONNECTING...', False)
        self.engine.restart()

    def count_octas(self):
        """how many OCTA-CAPTUREs Windows has right now (runs in a thread)"""
        import subprocess
        try:
            ps = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
            r = subprocess.run([ps, '-NoProfile', '-Command',
                                "@(Get-PnpDevice -PresentOnly -InstanceId 'USB\\VID_0582&PID_0120\\*' -ErrorAction SilentlyContinue).Count"],
                               capture_output=True, text=True, timeout=20, creationflags=0x08000000)
            self.octas_seen = int(r.stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    # ---------------- send to stream (dual/src/win/feed.h): ticked inputs, after their strips, into one playback device ----------------
    def stream(self):
        return self.rig.setdefault('stream', self.setup.clean_stream(None))

    def refresh_stream_marks(self):
        st = self.stream()
        self.rail.set_stream(st['name'], st['sends'].get(str(self.cur), []))

    def toggle_send(self, k):
        st = self.stream()
        sends = set(st['sends'].get(str(self.cur), []))
        sends ^= {k}
        if sends:
            st['sends'][str(self.cur)] = sorted(sends)
        else:
            st['sends'].pop(str(self.cur), None)
        self.refresh_stream_marks()
        self.save_setup()
        self.apply_stream()
        on = k in sends
        self.do_log(f'stream: unit {self.cur + 1} input {k + 1} {"sent to" if on else "no longer sent to"} {st["name"]}')
        if not self.recording:
            self.rec_info.setText(f'{self.rail.label(k)} {"-> " + st["name"] if on else "not sent to " + st["name"]}')

    def apply_stream(self):
        """tell the engine which inputs of which box go out, and start or stop the sending"""
        if not self.engine or not self.usb_rate:
            return
        st = self.stream()
        anything = False
        for b, u in enumerate(self.box_unit):
            sends = set(st['sends'].get(str(u), [])) if u is not None else set()
            self.engine.fgain(b, [1.0 if c in sends else 0.0 for c in range(10)])
            anything |= bool(sends)
        want = st['device'] if anything else None
        if want != self.feed_dev:
            self.feed_dev = want
            self.engine.feed(want)

    def on_feed_status(self, ln):
        st = self.stream()
        self.feed_last = ln
        a = ln.split()
        if a[0] == 'F' or a[1] in ('underrun', 'fix'):
            return                                    # numbers and single events, read with `stream status`
        if a[1] == 'nodevice':
            self.feed_dev = None
            msg = f'{st["name"]}: no playback device called "{st["device"]}". Install VB-Cable, or pick one in {st["name"]} settings'
        elif a[1] == 'on':
            msg = f'{st["name"]}: sending to {" ".join(a[4:])}'
        elif a[1] == 'error':
            self.feed_dev = None
            msg = f'{st["name"]}: {" ".join(a[2:])}'
        else:
            msg = f'{st["name"]}: stopped'
        self.do_log('stream: ' + msg)
        if not self.recording:
            self.rec_info.setText(msg)

    def stream_dialog(self):
        st = self.stream()
        d = QDialog(self)
        d.setWindowTitle('Send to stream')
        d.setStyleSheet('QDialog{background:#111316;} QLabel{color:#e6e9ec;font:bold 12px "Segoe UI";}'
                        'QLineEdit,QComboBox{background:#05070c;color:#62b2ff;border:2px solid #8c9299;border-radius:5px;'
                        'padding:6px;font:bold 14px "Segoe UI";} QPushButton{background:#b4b8bd;color:#24272b;'
                        'border:2px solid #d6d9dd;border-radius:5px;padding:10px 16px;font:bold 13px "Segoe UI";}')
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel('NAME (shown in the menu)'))
        name = QLineEdit(st['name'])
        name.setMaxLength(24)
        lay.addWidget(name)
        lay.addWidget(QLabel('SEND TO THIS PLAYBACK DEVICE (its other end is the mic you pick in the app)'))
        dev = QComboBox()
        dev.setEditable(True)
        for n in playback_devices():
            dev.addItem(n)
        dev.setCurrentText(st['device'])
        lay.addWidget(dev)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton('OK')
        ok.clicked.connect(d.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self._stream_dialog = (d, name, dev)                  # tests reach in here
        if d.exec():
            self.set_stream_route(name.text(), dev.currentText())

    def set_stream_route(self, name, device):
        st = self.stream()
        clean = self.setup.clean_stream(dict(name=name, device=device, sends=st['sends']))
        st['name'], st['device'] = clean['name'], clean['device']
        self.refresh_stream_marks()
        self.save_setup()
        self.feed_dev = None if self.feed_dev else self.feed_dev   # new device: restart the sending
        self.apply_stream()

    # ---------------- patches (strip_patches.py + amp_presets.py) ----------------
    def apply_preset(self, unit, ch, name):
        """a strip patch (amp off) or an amp tone, by name, onto one input"""
        import amp_presets
        import strip_patches
        p = strip_patches.find(name)
        if not p:
            if amp_presets.find(name):
                self.load_amp_preset(unit, ch, name)
                if self.patch_win:
                    self.patch_win.refresh()
            return
        c = self.units[unit]['chans'][ch]

        def change(n):
            n['amp'] = 'off'
            n['on'].update({b: b in p['on'] for b in strip_patches.BLOCKS})
            n['on']['gate'] = p['gate'] is not None
        if not self.dsp_gate(unit, ch, change):
            return
        was_amp = c.get('amp', 'off') != 'off'
        strip_patches.apply(c, p)
        c.pop('patch_edited', None)
        L = self.units[unit]['link']
        if L:
            if was_amp:                                        # a strip patch takes the input out of amp mode
                L.set_amp(ch, 'off')
                self.push_fx(unit, ch)
            for b in strip_patches.BLOCKS:
                L.set_block(ch, b, c['on'][b], dict(c))
        self.push_gate(unit, ch)
        tracks = load_tracks(unit)
        if p.get('track') and not tracks[ch].get('name'):     # an unnamed input takes the patch's name + picture
            tracks[ch] = dict(name=p['track'][0], icon=p['track'][1])
            save_tracks(tracks, unit)
            if unit == self.cur:
                self.rail.set_unit(unit)
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.do_log(f'unit {unit + 1} input {ch + 1}: patch {p["name"]}')
        self.retitle()
        self.touch()
        if self.patch_win:
            self.patch_win.refresh()

    def save_patch(self, unit, ch, name):
        """what the input runs now, as one of yours: a strip patch, or an amp tone when the amp is on"""
        import strip_patches
        c = self.units[unit]['chans'][ch]
        if c.get('amp', 'off') != 'off':
            self.save_amp_preset(unit, ch, name)
            return
        p = strip_patches.from_chan(c, name)
        if strip_patches.find(p['name'], user=[]):
            p['name'] = (p['name'] + ' (MINE)')[:24]          # factory patches stay as they are
        user = [u for u in strip_patches.load_user() if u['name'] != p['name']] + [p]
        try:
            strip_patches.save_user(user)
        except OSError as e:
            self.rec_info.setText('could not save the patch: ' + str(e)[:60])
            return
        c['patch'] = p['name']
        c.pop('patch_edited', None)
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.rec_info.setText(f'patch saved: {p["name"]}')
        self.touch()

    def delete_patch(self, name):
        import strip_patches
        user = strip_patches.load_user()
        keep = [u for u in user if u['name'] != name]
        if len(keep) != len(user):
            strip_patches.save_user(keep)
            self.rec_info.setText(f'patch deleted: {name}')
        else:
            self.delete_amp_preset(name)
        for u in self.units:                                   # inputs running it keep the sound, lose the name
            for c in u['chans']:
                for k, e in (('patch', 'patch_edited'), ('amp_preset', 'amp_edited')):
                    if c.get(k) == name:
                        c.pop(k, None)
                        c.pop(e, None)
        self.strip.show_chan(self.strip.ch)
        self.touch()

    def patch_browser(self):
        if self.patch_win is None:
            self.patch_win = PatchBrowser(self)
        self.patch_win.set_fx_ok(getattr(self.units[self.cur]['link'], 'fx_ok', None))
        self.patch_win.refresh()
        self.patch_win.open_on_current()
        self.patch_win.show()
        self.patch_win.raise_()

    def set_amp(self, unit, ch, voice):
        """one amp per box: turning it on here takes it off the box's other inputs in this app's state"""
        import amp_panel
        u = self.units[unit]
        if voice != 'off' and u['chans'][ch].get('amp', 'off') == 'off':
            def change(n):
                n['amp'] = voice
                n['on'].update(tube=False, comp=False)
            if not self.dsp_gate(unit, ch, change):
                return
        for k, c in enumerate(u['chans']):
            if k != ch and c.get('amp', 'off') != 'off':
                c['amp'] = 'off'
                self.push_amp(unit, k)
        c = u['chans'][ch]
        was = c.get('amp', 'off')
        c['amp'] = voice
        if voice != 'off':
            if c.get('patch'):                                # the strip patch under it no longer runs as saved
                c['patch_edited'] = True
            c['on'].update(tube=False, comp=False)
            if voice != was:                                  # a channel brings its own gain, presence and master
                c.update(amp_panel.voice_defaults(voice))
            c.pop('amp_tweaks', None)                         # a plain channel, not a tone preset any more
            c.pop('amp_preset', None)
            c.pop('amp_edited', None)
        self.push_amp(unit, ch)
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.touch()

    def set_amp_knob(self, unit, ch, key, v):
        c = self.units[unit]['chans'][ch]
        if c.get('amp', 'off') == 'off':
            return
        if key in ('amp_echo', 'amp_rev', 'amp_gate') and v > 0 and c.get(key, 0) <= 0:
            if not self.dsp_gate(unit, ch, lambda n: n.__setitem__(key, v)):
                if unit == self.cur:
                    self.strip.show_chan(self.strip.ch)        # the knob goes back to OFF
                return
        c[key] = v
        if c.get('amp_preset'):
            c['amp_edited'] = True
        if key in ('amp_echo', 'amp_time', 'amp_rev'):
            self.push_fx(unit, ch)
        elif key == 'amp_gate':
            self.push_gate(unit, ch)
        else:
            self.push_amp(unit, ch, tone_only=key in ('amp_bass', 'amp_mid'))   # TREBLE moves MASTER too (treble_makeup)
        if key == 'amp_hard' and unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.touch()

    def load_amp_preset(self, unit, ch, name):
        """a tone preset onto one input: voice, knobs, HARD, the amp's insides (one amp per box, as set_amp)"""
        import amp_presets
        p = amp_presets.find(name)
        if not p:
            return
        u = self.units[unit]
        c = u['chans'][ch]
        if c.get('amp', 'off') == 'off':
            def change(n):
                n['amp'] = p['voice']
                n['on'].update(tube=False, comp=False)
            if not self.dsp_gate(unit, ch, change):
                return
        for k, o in enumerate(u['chans']):
            if k != ch and o.get('amp', 'off') != 'off':
                o['amp'] = 'off'
                self.push_amp(unit, k)
        amp_presets.apply(c, p)
        self.push_amp(unit, ch)
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.do_log(f'unit {unit + 1} input {ch + 1}: tone {name}')
        self.touch()

    def save_amp_preset(self, unit, ch, name):
        import amp_presets
        c = self.units[unit]['chans'][ch]
        if c.get('amp', 'off') == 'off':
            return
        p = amp_presets.from_chan(c, name)
        if any(f['name'] == p['name'] for f in amp_presets.FACTORY):
            p['name'] = (p['name'] + ' (MINE)')[:24]          # factory tones stay as they are
        user = [u for u in amp_presets.load_user() if u['name'] != p['name']] + [p]
        try:
            amp_presets.save_user(user)
        except OSError as e:
            self.rec_info.setText('could not save the tone: ' + str(e)[:60])
            return
        c['amp_preset'] = p['name']
        c.pop('amp_edited', None)
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.rec_info.setText(f'tone saved: {p["name"]}')
        self.touch()

    def delete_amp_preset(self, name):
        import amp_presets
        user = amp_presets.load_user()
        keep = [u for u in user if u['name'] != name]
        if len(keep) != len(user):
            amp_presets.save_user(keep)
            self.rec_info.setText(f'tone deleted: {name}')

    def push_gate(self, unit, ch):
        """in amp mode the amp head's GATE knob runs the input's gate; otherwise the strip's GATE button does"""
        import amp_panel
        L = self.units[unit]['link']
        if not L:
            return
        c = self.units[unit]['chans'][ch]
        if c.get('amp', 'off') != 'off':
            thr = amp_panel.gate_db(c.get('amp_gate', 0.0))
            L.set_block(ch, 'gate', thr is not None, dict(c, gate_thr=thr if thr is not None else -45.0))
        else:
            L.set_block(ch, 'gate', bool(c['on'].get('gate')), dict(c))

    def push_fx(self, unit, ch):
        """the box's echo + reverb follow the input's amp: on with it, off with it"""
        import amp_panel
        L = self.units[unit]['link']
        if not L:
            return
        c = self.units[unit]['chans'][ch]
        L.set_fx(ch, amp_panel.fx_params(c) if c.get('amp', 'off') != 'off' else None)

    def push_amp(self, unit, ch, tone_only=False):
        """send an input's amp: the amp block, and low/peak/high as its BASS/MIDDLE/TREBLE. Amp off: the input's own EQ back."""
        import amp_panel
        L = self.units[unit]['link']
        if not L:
            return
        c = self.units[unit]['chans'][ch]
        voice = c.get('amp', 'off')
        if voice == 'off':
            if not tone_only:
                L.set_amp(ch, 'off')
                self.push_gate(unit, ch)
                if not any(o.get('amp', 'off') != 'off' for o in self.units[unit]['chans']):
                    L.set_fx(ch, None)
            for b in ('low', 'peak', 'high'):
                L.set_block(ch, b, c['on'][b], dict(c))
            return
        if not tone_only:
            d = amp_panel.voice_defaults(voice)
            L.set_amp(ch, voice, amp_panel.amp_db('amp_gain', c.get('amp_gain', d['amp_gain'])),
                      amp_panel.amp_db('amp_master', c.get('amp_master', d['amp_master'])) + amp_panel.treble_makeup(c),
                      amp_panel.amp_db('amp_pres', c.get('amp_pres', d['amp_pres'])),
                      bool(c.get('amp_hard', d['amp_hard'])), c.get('amp_tweaks'))
        tone = dict(c, **amp_panel.tone_override(c))
        for b in ('low', 'peak', 'high'):
            L.set_block(ch, b, True, tone)
        if not tone_only:
            self.push_fx(unit, ch)
            self.push_gate(unit, ch)

    # ---------------- mic compensation (app/mic_comp.py, curves in app/mics/) ----------------
    def next_mic(self):
        import mic_comp
        c = self.units[self.cur]['chans'][self.strip.ch]
        order = ['off'] + mic_comp.names()
        if len(order) == 1:
            self.rec_info.setText('no mic curves yet: add one to app/mics/')
            return
        now = c.get('mic', 'off')
        self.set_mic(self.cur, self.strip.ch, order[(order.index(now) + 1) % len(order)] if now in order else 'off')

    def set_mic(self, unit, ch, name, target=None):
        """fit the input's low/peak/high to a mic's curve; 'off' puts the EQ back as it was"""
        import mic_comp
        keys = mic_comp.KEYS
        c = self.units[unit]['chans'][ch]
        if name == 'off':
            prev = c.pop('mic_prev', None)
            c.pop('mic', None)
            if prev:
                c.update({k: v for k, v in prev.items() if k != 'on'})
                c['on'].update(prev.get('on', {}))
            note = 'mic correction off'
        else:
            try:
                r = mic_comp.solve(name, rbj, mag_db, target, FS)
            except (OSError, ValueError) as e:
                self.rec_info.setText(f'mic curve {name}: {e}'[:70])
                return
            if 'mic_prev' not in c:
                c['mic_prev'] = {k: c[k] for k in keys}
                c['mic_prev']['on'] = {b: c['on'][b] for b in ('low', 'peak', 'high')}
            c.update(r['params'])
            c['on'].update(low=True, peak=True, high=True)
            c['mic'] = name
            note = f'{name.upper()}: {r["was_rms"]:.1f} dB of colour fitted, {r["left_rms"]:.1f} dB left (worst {r["worst"]:.1f})'
        L = self.units[unit]['link']
        if L:
            for b in ('low', 'peak', 'high'):
                L.set_block(ch, b, c['on'][b], dict(c))
        if not self.recording:
            self.rec_info.setText(note)
        self.do_log(f'mic unit {unit + 1} input {ch + 1}: {note}')
        if unit == self.cur:
            self.strip.show_chan(self.strip.ch)
        self.touch()

    # ---------------- fast-memory A/B (app/octa_fast.py): live only, a power cycle undoes it ----------------
    def run_bench(self, unit, what='bench'):
        L = self.units[unit]['link']
        if self.recording or not L or not L.lab or L.fast_on:
            self.do_log(f'bench unit {unit + 1}: not now (recording, no link, or the fast path is already on)')
            return

        def go():
            import octa_fast
            from octa_lab import bq_model, q30, rbj as lab_rbj
            log = lambda s: self.do_log(f'unit {unit + 1}: {s}')
            L.busy = True
            try:
                (octa_fast.profile if what == 'profile' else octa_fast.bench)(L.lab, L.strip, lab_rbj, L.fs, bq_model, q30, log)
                log(f'{what} finished, back on the normal path')
            except octa_fast.Refused as e:
                log(f'bench refused, nothing changed: {e}')
            except BaseException as e:
                log(f'bench failed: {e}')
                raise
            finally:
                L.busy = False
                L.last_ok = time.time()
                self.bench_done.emit(unit)
        L.send(('bench', unit), go)

    def toggle_mode(self):
        unit = self.cur
        L = self.units[unit]['link']
        if self.recording or not L or not getattr(L, 'lab', None):
            self.rec_info.setText('FAT/SLIM needs a connected box, and not during a take')
            return
        want = 'fat' if L.mode == 'slim' else 'slim'
        if want == 'fat':
            chans = self.units[unit]['chans']
            extra = self.dspb.model_cycles(chans, False) - self.dspb.model_cycles(chans, True)
            ok, pred = self.budget.allow(unit, chans, True, extra)
            if not ok:
                self.dsp_refused(unit, pred)
                return

        def go():
            import octa_fast
            from octa_lab import bq_model, q30, rbj as lab_rbj
            try:
                if want == 'slim' and not L.fast_on:
                    f = octa_fast.Fast(L.lab, self.do_log)
                    if f.in_firmware():
                        self.do_log(f'unit {unit + 1}: fast path already running from the firmware')
                        L.fast, L.fast_on = f, True
                        L.mode = want
                        self.mode_changed.emit(unit, want)
                        return
                    f.install()
                    f.selftest(bq_model, q30, lab_rbj, L.fs, self.do_log)
                    f.on()
                    L.fast, L.fast_on = f, True
                L.mode = want
                self.mode_changed.emit(unit, want)
            except octa_fast.Refused as e:
                self.do_log(f'unit {unit + 1}: SLIM refused, staying FAT: {e}')
                self.mode_changed.emit(unit, 'refused')
        L.send(('mode', unit), go)

    def on_mode(self, unit, mode):
        if mode == 'refused':
            self.rec_info.setText('SLIM refused: see do.out')
            return
        if unit == self.cur:
            self.mode_btn.setText(mode.upper())
        L = self.units[unit]['link']
        for ch, c in enumerate(self.units[unit]['chans']):
            for b in ('peak', 'high', 'comp'):
                if c['on'][b]:
                    L.set_block(ch, b, True, dict(c))
        self.do_log(f'unit {unit + 1}: mode {mode}')
        if not self.recording:
            self.rec_info.setText(f'UNIT {unit + 1}: {mode.upper()} mode')

    def run_probe(self, unit, name, args):
        L = self.units[unit]['link']
        if self.recording or not L or not L.lab:
            self.do_log(f'probe unit {unit + 1}: not now')
            return

        def go():
            import importlib
            log = lambda s: self.do_log(f'unit {unit + 1}: {s}')
            try:
                import octa_probe
                importlib.reload(octa_probe)
            except Exception as e:                        # a broken probe file must say so, not vanish
                log(f'probe file does not load: {e}')
                return
            L.busy = True
            touched = True
            try:
                touched = getattr(octa_probe, name)(L.lab, L.strip, log, *args)
            except BaseException as e:
                log(f'probe {name} failed: {e}')
                if not isinstance(e, Exception):
                    raise
            finally:
                L.busy = False
                L.last_ok = time.time()
                if touched:
                    self.bench_done.emit(unit)
        L.send(('probe', unit, name), go)

    def after_bench(self, unit):
        self.restore_unit(unit)
        self.do_log(f'unit {unit + 1}: your chains are back after the bench')

    def do_log(self, s):
        import threading
        lock = self.__dict__.setdefault('_log_lock', threading.Lock())   # two box threads write here: keep lines whole
        name = 'do.out' if self.setup_path else 'do_demo.out'                # a demo doesn't write in the real app's log
        with lock, open(os.path.join(HERE, name), 'a', encoding='utf-8') as f:
            f.write(s + '\n')

    def poll_do(self):
        """app/do.txt: commands Claude drops in while the app runs (it owns the USB link, so nothing else can talk
        to the boxes): preset <unit> <input> <PATCH or TONE NAME>, whoami"""
        p = os.path.join(HERE, 'do.txt')
        if not os.path.exists(p):
            return
        with open(p, encoding='utf-8') as f:
            lines = f.read().splitlines()
        os.remove(p)
        for ln in lines:
            a = ln.split()
            if a[:1] == ['preset'] and len(a) >= 4:
                import strip_patches
                name = ' '.join(a[3:])
                name = strip_patches.OLD_NAMES.get(name, name.upper())
                self.apply_preset(int(a[1]) - 1, int(a[2]) - 1, name)
                self.do_log('ok ' + ln)
            elif a[:1] == ['whoami']:
                for i, u in enumerate(self.units[:self.usb_boxes]):
                    L = u['link']
                    if not L or not L.lab:
                        self.do_log(f'unit {i + 1}: link not up')
                        continue

                    def ask(k=i, L=L):
                        v = struct.unpack('<I', L.lab.peek(0x401554, 4, 4))[0]
                        g = L.lab.call(0x3097e4, 0x43f444, 0, 0)[0]
                        self.do_log(f'unit {k + 1}: Channel page = {"Roland (has Hi-Z)" if v == 0x310418 else "knob"}, '
                                    f'input 1 gain {g / 2} dB, strip flags ch1 {L.strip.flags(0)}')
                    L.send(('who', i), ask)
            elif a[:1] == ['call'] and len(a) == 6:                  # call <unit> <addr> <r0> <r1> <r2> (hex): firmware getter
                L = self.units[int(a[1]) - 1]['link']

                def do_call(L=L, x=tuple(a)):
                    self.do_log(' '.join(x) + ' -> %#x' % L.lab.call(*[int(v, 16) for v in x[2:]])[0])
                L.send(('do', ln), do_call)
            elif a[:1] == ['peek'] and len(a) == 4:                  # peek <unit> <addr> <bytes> (hex)
                L = self.units[int(a[1]) - 1]['link']

                def do_peek(L=L, x=tuple(a)):
                    self.do_log(' '.join(x) + ' -> ' + L.lab.peek(int(x[2], 16), int(x[3], 16), 4).hex())
                L.send(('do', ln), do_peek)
            elif a[:1] == ['shot'] and len(a) == 2:                  # shot <path.png>: picture of this window
                self.grab().save(a[1])
                self.do_log('ok ' + ln)
            elif a[:1] == ['rec']:                                   # same as tapping the red button
                self.toggle_rec()
                self.do_log(f'rec -> recording={self.recording} {self.take_dir}')
            elif a[:1] == ['set'] and len(a) == 5:                  # set <unit> <input> <key> <value>: one strip knob, saved
                u, ch, key, v = int(a[1]) - 1, int(a[2]) - 1, a[3], float(a[4])
                c = self.units[u]['chans'][ch]
                lo, hi = self.setup.RANGES.get(key, (None, None))
                if key in KEY_BLOCK and lo is not None:
                    c[key] = min(hi, max(lo, v))
                    L = self.units[u]['link']
                    if L and c['on'][KEY_BLOCK[key]]:
                        L.set_block(ch, KEY_BLOCK[key], True, dict(c))
                    if u == self.cur and ch == self.strip.ch:
                        self.strip.show_chan(ch)
                    self.touch()
                    self.do_log(f'ok {ln} -> {c[key]}')
                else:
                    self.do_log(f'set: unknown key {key}')
            elif a[:1] == ['tone'] and len(a) >= 4:                # tone <unit> <input> <PRESET NAME>
                self.load_amp_preset(int(a[1]) - 1, int(a[2]) - 1, ' '.join(a[3:]).upper())
            elif a[:1] == ['amp'] and len(a) == 4:                   # amp <unit> <input> <off|clean|crunch|lead|cranked|fuzz>
                self.set_amp(int(a[1]) - 1, int(a[2]) - 1, a[3])
                self.do_log('ok ' + ln)
            elif a[:1] == ['mon'] and len(a) == 2 and self.engine:
                self.mon.setChecked(a[1] == '1')
                self.do_log('ok ' + ln)
            elif a[:1] == ['mic'] and len(a) in (4, 5):               # mic <unit> <input> <name|off> [target mic]
                self.set_mic(int(a[1]) - 1, int(a[2]) - 1, a[3], a[4] if len(a) == 5 else None)
            elif a[:1] == ['bench'] and len(a) == 2:                  # bench <unit>: fast-memory load test, ramps and stops itself
                self.run_bench(int(a[1]) - 1)
            elif a[:1] == ['probe'] and len(a) >= 3:                  # probe <unit> <experiment> [args]: app/octa_probe.py
                self.run_probe(int(a[1]) - 1, a[2], a[3:])
            elif a[:1] == ['profile'] and len(a) == 2:                # profile <unit>: cycles per block, old path / FAT / SLIM
                self.run_bench(int(a[1]) - 1, 'profile')
            elif a[:1] == ['stream']:                                 # stream status | stream send <unit> <input> | stream route <name>|<device>
                if a[1:2] == ['send'] and len(a) == 4:
                    keep = self.cur
                    self.cur = int(a[2]) - 1
                    self.toggle_send(int(a[3]) - 1)
                    self.cur = keep
                    self.refresh_stream_marks()
                elif a[1:2] == ['route']:
                    nm, _, dv = ' '.join(a[2:]).partition('|')
                    self.set_stream_route(nm, dv)
                st = self.stream()
                self.do_log(f'stream: {st["name"]} -> "{st["device"]}", sends {st["sends"]}, engine device {self.feed_dev}, '
                            f'last: {self.feed_last}')
            elif a[:1] == ['setup']:                                  # what would come back after a power cut
                for i, u in enumerate(self.units):
                    on = [k + 1 for k, c in enumerate(u['chans'])
                          if any(c['on'][b] for b in ('hpf', 'low', 'peak', 'high', 'tube', 'comp'))]
                    g = {k + 1: c['pre_gain'] for k, c in enumerate(u['chans']) if c.get('gain_known')}
                    self.do_log(f'unit {i + 1}: strips on {on}, gains {g}, routes {self.rig["patchbay"].get(str(i), {})}')
                self.do_log('focusrite: ' + (self.fc.status if self.fc else 'not kept'))
            elif a[:1] == ['state'] and len(a) == 2:
                self.do_state(int(a[1]) - 1)
            elif a[:1] == ['dt1'] and len(a) == 4:                    # dt1 <unit> <addr hex, 4 bytes> <data hex>
                L = self.units[int(a[1]) - 1]['link']
                L.send(('do', ln), lambda L=L, x=tuple(a): L._dt1(bytes.fromhex(x[2]), bytes.fromhex(x[3])))
                self.do_log('ok ' + ln)

    def do_state(self, unit):
        """everything between a jack and the headphones, for one box: input peaks over 1 s (at the ADC and on USB,
        which is after the strip), preamp, direct mix A-D, patchbay"""
        import mido
        L = self.units[unit]['link']
        if not L or not L.lab:
            self.do_log(f'unit {unit + 1}: link not up')
            return

        got = {}

        def dump():                                 # the firmware only answers the bulk RQ1, as a run of DT1s
            L.lab.out.send(mido.Message('sysex', data=[0x41, 0x10, 0, 0, 0x4D, 0x11, 1, 0, 0, 0, 0, 0, 8, 0x7A, 0x7D]))
            end = time.time() + 2.0
            while time.time() < end:
                for m in L.lab.inp.iter_pending():
                    d = tuple(m.data)
                    if d[:6] == (0x41, 0x10, 0, 0, 0x4D, 0x12):
                        got[d[6:10]] = list(d[10:-1])
                time.sleep(0.005)

        def rq(addr, size):
            return got.get(tuple(addr))

        def nibs(v):
            return 'none' if v is None else '%06x' % sum(x << (4 * (len(v) - 1 - k)) for k, x in enumerate(v))

        def ask():
            adc, usb = [-120.0] * 8, [-120.0] * 10
            for _ in range(40):
                v = struct.unpack('<8I', L.lab.peek(0x43ed10, 32, 4))
                adc = [max(p, 20 * math.log10(x / (1 << 27)) if x > 0 else -120.0) for p, x in zip(adc, v)]
                usb = [max(p, x) for p, x in zip(usb, self.usb_m.get(unit, usb))]
                time.sleep(0.025)
            log = lambda s: self.do_log(f'unit {unit + 1}: {s}')
            dump()
            log(f'dump: {len(got)} values')
            log('adc peak dB ' + ' '.join('%.0f' % x for x in adc))
            log('usb peak dB ' + ' '.join('%.0f' % x for x in usb))
            for c in (0, 1):
                log(f'input {c + 1} 48V {rq([0, 5, c, 0], 1)} hiz {rq([0, 5, c, 3], 1)} sens {rq([0, 5, c, 4], 1)}')
            log(f'strip flags ch1 {L.strip.flags(0)}')
            for m in range(4):
                log(f'mix {"ABCD"[m]} in1 mute {rq([0, 6, m << 4, 3], 1)} solo {rq([0, 6, m << 4, 2], 1)} '
                    f'fader {nibs(rq([0, 6, m << 4, 8], 6))}, master L {nibs(rq([0, 8, m << 4, 1], 6))} '
                    f'R {nibs(rq([0, 8, (m << 4) | 1, 1], 6))}')
            log('mix A solos ' + ' '.join(str(rq([0, 6, i, 2], 1)) for i in range(10)))
            log('mix A mutes ' + ' '.join(str(rq([0, 6, i, 3], 1)) for i in range(10)))
            log('output sources (0-3 = mix A-D, 4+ = DAW) ' + ' '.join(str(rq([0, 3, 0, p], 1)) for p in range(5)))
        L.send(('state', unit), ask)

    # ---------------- one-tap recorder (boxes on WinUSB, see octa_engine.py) ----------------
    def free_text(self):
        try:
            free = shutil.disk_usage(RECORDINGS).free
        except OSError:
            return 'recordings folder missing'
        mins = free / (self.usb_rate * 3 * 20) / 60
        return f'{free / 1e9:.1f} GB free, about {mins:.0f} min of 20 tracks'

    def map_boxes(self, serials):
        """engine box -> unit, by USB serial. A box we can't place is None: it is left completely alone."""
        known = self.rig.setdefault('serials', {})
        if not serials:                                       # old engine without serials: only a full pair is certain
            return [0, 1][:len(serials) or self.usb_boxes] if self.usb_boxes == 2 else [None] * self.usb_boxes
        units = [known.get(s) for s in serials]
        free = [u for u in (0, 1) if u not in units]
        unknown = [i for i, u in enumerate(units) if u is None]
        if len(serials) == 2 and len(unknown) == 2:          # first time with both on: they come sorted by serial
            units = [0, 1]
        elif len(serials) == 2 and len(unknown) == 1 and len(free) == 1:
            units[unknown[0]] = free[0]                       # a new box next to a known one takes the free place
        else:
            return units
        for s, u in zip(serials, units):
            if known.get(s) != u:
                for old in [k for k, v in known.items() if v == u]:   # one serial per unit: a swapped-in box replaces the old one
                    del known[old]
                known[s] = u
                self.do_log(f'box {s} is unit {u + 1} from now on')
        self.save_setup()
        return units

    def unit_of(self, box):
        return self.box_unit[box] if 0 <= box < len(self.box_unit) and self.box_unit[box] is not None else -1

    def trusted(self, k):
        """on our USB engine, only boxes placed by serial; on Roland's driver there's nothing to mix up"""
        return not self.usb_rate or k in self.unit_ok

    def on_engine_ready(self, boxes, rate, serials=()):
        self.usb_rate, self.usb_boxes = rate, boxes
        self.engine_dead = self.engine_nobox = False
        self.engine_backoff = 10.0
        self.box_unit = self.map_boxes(list(serials))
        self.unit_ok = {u for u in self.box_unit if u is not None}
        for i, u in enumerate(self.units):            # the lab + Roland messages now go through the USB engine
            if not u['link']:
                continue
            if i in self.unit_ok:
                u['link'].transport = (lambda k=self.box_unit.index(i): self.engine.ports(k))
                u['link'].paused = False
                u['link'].last_ok = time.time()
            else:
                u['link'].paused = True
        self.feed_dev = None                          # a new engine starts with nothing sending
        self.apply_stream()
        for b, u in enumerate(self.box_unit):
            if u is None:
                self.do_log(f'box {list(serials)[b] if b < len(serials) else b + 1}: not sure which unit this is, left alone')
        os.makedirs(RECORDINGS, exist_ok=True)
        self.header.units.clear()
        self.header.set_status(f'USB DIRECT   {boxes} box{"es" if boxes > 1 else ""}   {rate / 1000:g} kHz', True)
        self.rec_info.setText(self.free_text())

    def toggle_rec(self):
        if not self.usb_rate:
            self.rec_info.setText('REC needs the boxes on our USB driver')
            return
        if self.recording:
            self.rec.set_state(True, 'SAVING')
            self.engine.stop()
            self.rec_timer.stop()
            return
        if shutil.disk_usage(RECORDINGS).free < 200e6:
            self.rec_info.setText('disk is full: free some space first')
            return
        self.take_dir = os.path.join(RECORDINGS, datetime.datetime.now().strftime('take %Y-%m-%d %H-%M-%S'))
        os.makedirs(self.take_dir, exist_ok=True)
        self.recording = True
        self.engine.rec(self.take_dir)

    def on_rec_started(self):
        self.rec_t0 = time.time()
        self.rec.set_state(True, '0:00')
        self.rec_info.setText('recording every input')
        self.rec_timer.start(250)

    def rec_tick(self):
        s = int(time.time() - self.rec_t0)
        self.rec.set_state(True, f'{s // 60}:{s % 60:02d}')

    def track_filename(self, box, inp):
        """Vocal.wav from the input's name; unnamed inputs stay findable"""
        if inp > 8:
            base = f'box{box} digital {"L" if inp == 9 else "R"}'
        else:
            u = self.unit_of(box - 1)
            t = load_tracks(u if u >= 0 else box - 1)[inp - 1].get('name', '').strip()
            base = ''.join(ch for ch in t if ch.isalnum() or ch in ' -_').strip() or f'box{box} in{inp:02d}'
        name, k = base + '.wav', 2
        while os.path.exists(os.path.join(self.take_dir, name)):
            name, k = f'{base} {k}.wav', k + 1
        return name

    def finish_take(self, secs, dropouts, ppm, kept):
        if not self.recording:
            return
        self.recording = False
        self.rec_timer.stop()
        for box, inp in kept:
            src = os.path.join(self.take_dir, f'b{box}_in{inp:02d}.wav')
            if os.path.exists(src):
                os.replace(src, os.path.join(self.take_dir, self.track_filename(box, inp)))
        self.rec.set_state(False, 'REC')
        if not kept:
            try:
                os.rmdir(self.take_dir)
            except OSError:
                pass
            self.rec_info.setText('nothing above -60 dB: take deleted')
            return
        warn = f'   {dropouts} DROPOUTS' if dropouts else ''
        self.rec_info.setText(f'{len(kept)} tracks, {int(secs) // 60}:{int(secs) % 60:02d} saved in '
                              f'{os.path.basename(self.take_dir)}{warn}')

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
        self.refresh_stream_marks()
        self.strip.show_chan(self.strip.ch)
        self.mode_btn.setText(getattr(u['link'], 'mode', 'fat').upper())
        for m in self.rail.meters:
            m.level = 0
            m.update()

    def on_flags(self, k, fl):
        if not self.trusted(k):                               # never read a box we can't name into your settings, or back
            self.do_log(f'unit {k + 1}: not sure which box this is, left alone')
            return
        self.apply_routes(k)
        if self.has_saved and not any(fl):                    # empty strip = fresh power-up
            self.restore_unit(k)
            if k == self.cur:
                self.strip.show_chan(self.strip.ch)
            return
        for ch, on in enumerate(fl):
            amp = self.units[k]['chans'][ch].get('amp', 'off') != 'off'
            for b in ('hpf', 'low', 'peak', 'high', 'tube', 'comp'):
                if amp and b in ('low', 'peak', 'high'):          # those are the amp's BASS/MIDDLE/TREBLE now, not your EQ
                    continue
                self.units[k]['chans'][ch]['on'][b] = b in on
        if k == self.cur:
            self.strip.show_chan(self.strip.ch)

    def on_mon(self, on):
        """MONITOR OTHER BOX: the other box's inputs in this box's outputs, over USB. Saved, so a restart keeps it
        (2026-11-11: restarts switched it off and the mic went missing from the monitors)"""
        if self.engine:
            self.engine.monitor(on)
        if bool(self.rig.get('monitor')) != on:
            self.rig['monitor'] = on
            self.touch()

    def on_unit_preamp(self, k, ch, raw, ph, hiz=-1):
        """a box's preamp as read from it: the shown box goes through the page; the other one is saved too, or its gain
        is lost at the next power cut (2026-11-11: unit 2's 44 dB came back as 0)"""
        if k == self.cur:
            self.strip.on_preamp(ch, raw, ph, hiz)
            return
        c = self.units[k]['chans'][ch]
        changed = c.get('pre_gain') != raw / 2 or not c.get('gain_known')
        c['pre_gain'], c['gain_known'], c['ph48'] = raw / 2, True, bool(ph)
        if hiz >= 0 and c.get('hiz') != bool(hiz):
            c['hiz'] = bool(hiz)
            changed = True
        if changed:
            self.touch()

    # ---------------- DSP budget ----------------
    def dsp_slim(self, unit):
        L = self.units[unit]['link']
        return getattr(L, 'mode', 'fat') == 'slim'

    def dsp_now(self, unit):
        return self.budget.now(unit, self.units[unit]['chans'], self.dsp_slim(unit))

    def dsp_gate(self, unit, ch, change):
        """may this change to an input be made? No = the bar says so and the task manager opens"""
        c = self.units[unit]['chans'][ch]
        slim = self.dsp_slim(unit)
        delta = self.dspb.delta_for(c, change, slim)
        ok, pred = self.budget.allow(unit, self.units[unit]['chans'], slim, delta)
        if not ok:
            self.dsp_refused(unit, pred)
        return ok

    def dsp_refused(self, unit, pred):
        if unit != self.cur:
            self.select_unit(unit)
        self.dsp_bar.refuse(pred)
        self.rec_info.setText(f'DSP FULL on unit {unit + 1}: that would be {pred:.0f} %. Remove something.')
        self.do_log(f'unit {unit + 1}: refused, DSP would be {pred:.1f} % (limit {self.dspb.LIMIT:.0f})')
        self.show_dsp_tasks()

    def refresh_dsp(self):
        pct, live = self.dsp_now(self.cur)
        self.dsp_bar.set_load(pct, live, self.cur)
        ok = getattr(self.units[self.cur]['link'], 'fx_ok', None)
        self.strip.blk['gate'].set_blocked(NO_FW if ok is False else None)
        if self.patch_win:
            self.patch_win.set_fx_ok(ok)
        if self.strip.amp_head.fx_ok != ok:
            self.strip.amp_head.fx_ok = ok
            for key in ('amp_gate', 'amp_echo', 'amp_time', 'amp_rev'):   # knobs that do nothing on this box: gone
                self.strip.amp_head.knobs[key].setVisible(ok is not False)
            self.strip.amp_head.update()

    def show_dsp_tasks(self):
        if self.dsp_win is None:
            self.dsp_win = self.dspb.DspTasks(self)
        self.dsp_win.show()
        self.dsp_win.rebuild()
        self.dsp_win.raise_()

    def dsp_tasks(self, unit):
        """[(ch, block, who, cycles, what)] for the task manager"""
        tracks = load_tracks(unit)
        slim = self.dsp_slim(unit)
        out = []
        for ch, c in enumerate(self.units[unit]['chans']):
            name = (tracks[ch].get('name') or '').upper()
            who = f'IN {ch + 1}' + (f'  {name}' if name else '')
            for block, cyc in self.dspb.tasks(c, slim):
                if block == 'input':
                    continue
                what = self.dspb.NAMES[block] + (f' · {c["amp"].upper()}' if block == 'amp' else '')
                out.append((ch, block, who, cyc + (self.dspb.COST['input'] if len(self.dspb.tasks(c, slim)) == 2 else 0), what))
        return out

    def dsp_kill(self, unit, ch, block):
        """the task manager's X: that block off now, same as its button"""
        c = self.units[unit]['chans'][ch]
        if block == 'amp':
            self.set_amp(unit, ch, 'off')
        elif block in ('echo', 'reverb'):                       # the amp's FX knob to OFF
            c['amp_echo' if block == 'echo' else 'amp_rev'] = 0.0
            self.push_fx(unit, ch)
            if unit == self.cur:
                self.strip.show_chan(self.strip.ch)
            self.touch()
        elif block == 'gate' and c.get('amp', 'off') != 'off':  # the amp head's GATE knob to OFF
            c['amp_gate'] = 0.0
            self.push_gate(unit, ch)
            if unit == self.cur:
                self.strip.show_chan(self.strip.ch)
            self.touch()
        else:
            c['on'][block] = False
            L = self.units[unit]['link']
            if L:
                L.set_block(ch, block, False, dict(c))
            if unit == self.cur:
                self.strip.show_chan(self.strip.ch)
            self.touch()
        self.do_log(f'unit {unit + 1}: DSP task killed: input {ch + 1} {block}')
        self.refresh_dsp()
        if self.dsp_win and self.dsp_win.isVisible():
            QTimer.singleShot(0, self.dsp_win.rebuild)

    def retitle(self):
        self.strip.track_name = self.rail.label(self.strip.ch)
        if not self.strip.amp_head.isHidden():
            self.strip.amp_head.name = self.strip.track_name
            self.strip.amp_head.update()
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
        self.closing = True
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_setup()
        if self.fc:
            self.fc.stop = True
        for u in self.units:
            if u['link']:
                u['link'].stop = True
        if self.engine:
            if self.recording:                        # never lose a take to the X button
                self.engine.stop()
                if self.engine.finished.wait(60) and self.engine.result:
                    self.finish_take(*self.engine.result)
            self.engine.quit()
        super().closeEvent(e)

    def demo_tick(self):
        self.t += 1
        for i, m in enumerate(self.rail.meters):
            m.level = max(0.0, 0.55 + 0.3 * math.sin(self.t * 0.13 + i * 1.7) - (0.5 if i > 3 else 0))
            m.update()
        self.strip.gr.level = max(0.0, 0.3 + 0.2 * math.sin(self.t * 0.2))
        self.strip.gr.update()


def playback_devices():
    """names of Windows playback devices (WASAPI), for the stream settings list; empty if they can't be listed"""
    try:
        import sounddevice as sd
        apis = [i for i, h in enumerate(sd.query_hostapis()) if 'WASAPI' in h['name']]
        return sorted({d['name'] for d in sd.query_devices() if d['max_output_channels'] > 0 and d['hostapi'] in apis})
    except Exception:
        return []


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
