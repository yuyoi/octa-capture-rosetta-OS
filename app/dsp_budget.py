"""DSP budget: how much of each box's audio time is used, a sci-fi bar for it, and a task manager to kill blocks live.

The box measures itself: its audio interrupt keeps busy and period counters (lab VARS +0xC / +8), the same meter `bench`
stops at 80 %. That live number is what the bar shows. Before anything new is switched on, the app adds what that block
costs (measured on unit 1 on 2026-11-10/11, cycles per sample, fast path) and refuses if the total would pass LIMIT. With
no box online the bar shows the same cost table added up (marked EST).
"""
import copy
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

LIMIT = 80.0                            # % of a sample period: past this the box is too close to dropping audio
PERIOD = 8444                           # cycles per sample at 44.1 kHz (372.4 MHz core), used until the box reports its own
ROLAND = 1208                           # Roland's mixer/router with their comps skipped and reverb off (measured 2026-11-10)
CORE = 114                              # our strip's pass with nothing on (measured)
COST = dict(input=40, hpf=75, low=75, peak=75, high=75, tube=105, comp=210, amp=520,   # FAT; full input = 655 (677 measured)
            echo=90, reverb=260, gate=30)                                               # NOT measured yet (SDRAM buffers)
SLIM_COST = dict(peak=70, high=70, comp=100)                                          # SLIM full input ~535 (550 measured)
BLOCKS = ('hpf', 'low', 'peak', 'high', 'tube', 'comp')
NAMES = dict(hpf='HPF', low='LOW EQ', peak='PEAK EQ', high='HIGH EQ', tube='TUBE', comp='COMP', amp='AMP',
             echo='ECHO', reverb='REVERB', gate='GATE')

BLUE_LIT = QColor('#62b2ff')
CYAN = QColor('#38e8ff')
TEXT = QColor('#c9ced4')
GREY_T = QColor('#8d939b')
RED = QColor('#ff3b5c')
AMBER = QColor('#ffb14a')


def block_cost(b, slim=False):
    return SLIM_COST.get(b, COST[b]) if slim else COST[b]


def tasks(c, slim=False):
    """what runs on one input: [(block, cycles)]. An amp counts its tone stack (low/peak/high) as part of it."""
    amp = c.get('amp', 'off') != 'off'
    out = []
    for b in BLOCKS:
        if c['on'].get(b) and not (amp and b in ('low', 'peak', 'high')):
            out.append((b, block_cost(b, slim)))
    if amp:
        out.append(('amp', COST['amp'] + sum(block_cost(b, slim) for b in ('low', 'peak', 'high'))))
        if c.get('amp_echo', 0) > 0:
            out.append(('echo', COST['echo']))
        if c.get('amp_rev', 0) > 0:
            out.append(('reverb', COST['reverb']))
    elif c['on'].get('fx') and c.get('vfx'):                  # a strip patch's own echo + reverb
        if c['vfx'].get('echo_mix', 0) > 0 and c['vfx'].get('echo_ms', 0) > 0:
            out.append(('echo', COST['echo']))
        if c['vfx'].get('rev_mix', 0) > 0:
            out.append(('reverb', COST['reverb']))
    if (amp and c.get('amp_gate', 0) > 0) or (not amp and c['on'].get('gate')):
        out.append(('gate', COST['gate']))
    if out:
        out.append(('input', COST['input']))
    return out


def chan_cost(c, slim=False):
    return sum(v for _, v in tasks(c, slim))


def model_cycles(chans, slim=False):
    return ROLAND + CORE + sum(chan_cost(c, slim) for c in chans)


class Budget:
    """live load per unit (median of the last readings) + the model, and the yes/no for adding something"""

    def __init__(self):
        self.live = {}                  # unit -> (time, [pct...], period)

    def update(self, unit, busy, period):
        if not period or not busy:
            return
        t, vals, _ = self.live.get(unit, (0, [], period))
        vals = (vals + [100.0 * busy / period])[-10:]
        self.live[unit] = (time.time(), vals, period)

    def period(self, unit):
        v = self.live.get(unit)
        return v[2] if v else PERIOD

    def now(self, unit, chans, slim=False):
        """(percent, live?)"""
        v = self.live.get(unit)
        if v and time.time() - v[0] < 3.0 and v[1]:
            s = sorted(v[1])
            return s[len(s) // 2], True
        return 100.0 * model_cycles(chans, slim) / PERIOD, False

    def allow(self, unit, chans, slim, delta_cycles):
        """(ok, percent it would be)"""
        pct, _ = self.now(unit, chans, slim)
        pred = pct + 100.0 * delta_cycles / self.period(unit)
        return delta_cycles <= 0 or pred <= LIMIT, pred


def delta_for(c, change, slim=False):
    """cycles a change to one input adds: change(copy_of_c) edits the copy"""
    n = copy.deepcopy(c)
    change(n)
    return chan_cost(n, slim) - chan_cost(c, slim)


def _font(px, bold=True, spacing=108):
    f = QFont('Segoe UI')
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    f.setLetterSpacing(QFont.PercentageSpacing, spacing)
    return f


def _cell_colour(t):
    """colour of the energy at position t (0-1 of the whole bar)"""
    stops = ((0.0, (56, 232, 255)), (0.45, (70, 150, 255)), (0.65, (150, 105, 255)), (0.78, (255, 120, 200)),
             (LIMIT / 100, (255, 177, 74)), (1.0, (255, 59, 92)))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return QColor(*[round(a + (b - a) * k) for a, b in zip(c0, c1)])
    return QColor(*stops[-1][1])


class DspBar(QWidget):
    """the budget bar: slanted energy cells filling a chamfered glass capsule, limit notch, big percent"""
    clicked = Signal()

    def __init__(self, w=360, h=34, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.pct, self.live, self.unit = 0.0, False, 0
        self.refused_until, self.refused_pred = 0.0, 0.0
        self.phase = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('DSP used on this box (live from the box, EST = worked out while offline). Click: DSP tasks')
        self.anim = QTimer(self)
        self.anim.timeout.connect(self._tick)

    def set_load(self, pct, live, unit):
        self.pct, self.live, self.unit = pct, live, unit
        self._anim()
        self.update()

    def refuse(self, pred):
        self.refused_until, self.refused_pred = time.time() + 2.5, pred
        self._anim()
        self.update()

    def _anim(self):
        want = self.pct > LIMIT - 12 or time.time() < self.refused_until
        if want and not self.anim.isActive():
            self.anim.start(50)
        elif not want and self.anim.isActive():
            self.anim.stop()
            self.phase = 0.0

    def _tick(self):
        self.phase += 0.05 * 2 * math.pi * 0.8             # 0.8 Hz breathing, brightness only, nothing moves
        self._anim()
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def capsule(self):
        r = QRectF(self.rect())
        return QRectF(r.left() + 56, r.top() + 4, r.width() - 56 - 64, r.height() - 8)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        cap = self.capsule()
        refused = time.time() < self.refused_until
        pulse = 0.5 + 0.5 * math.sin(self.phase)
        # left: DSP / UNIT n
        p.setFont(_font(r.height() * 0.38, True, 120))
        p.setPen(RED if refused else BLUE_LIT)
        p.drawText(QRectF(r.left(), r.top() + 1, 52, r.height() * 0.55), Qt.AlignLeft | Qt.AlignVCenter, 'DSP')
        p.setFont(_font(r.height() * 0.24, True, 110))
        p.setPen(GREY_T)
        p.drawText(QRectF(r.left(), r.top() + r.height() * 0.52, 56, r.height() * 0.42), Qt.AlignLeft | Qt.AlignVCenter,
                   f'UNIT {self.unit + 1}')
        # chamfered glass capsule
        ch = cap.height() * 0.35
        poly = QPolygonF([QPointF(cap.left() + ch, cap.top()), QPointF(cap.right(), cap.top()),
                          QPointF(cap.right(), cap.bottom() - ch), QPointF(cap.right() - ch, cap.bottom()),
                          QPointF(cap.left(), cap.bottom()), QPointF(cap.left(), cap.top() + ch)])
        path = QPainterPath()
        path.addPolygon(poly)
        path.closeSubpath()
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        g.setColorAt(0, QColor('#0b1018'))
        g.setColorAt(1, QColor('#04060a'))
        p.fillPath(path, g)
        # cells
        inner = cap.adjusted(5, 4, -5, -4)
        n = 40
        cw = inner.width() / n
        slant = inner.height() * 0.45
        p.save()
        p.setClipPath(path)
        for i in range(n):
            t0, t1 = i / n, (i + 1) / n
            x = inner.left() + i * cw
            cell = QPolygonF([QPointF(x + slant * 0.5 + 1, inner.top()), QPointF(x + cw + slant * 0.5 - 1, inner.top()),
                              QPointF(x + cw - slant * 0.5 - 1, inner.bottom()), QPointF(x - slant * 0.5 + 1, inner.bottom())])
            fill = min(1.0, max(0.0, (self.pct / 100 - t0) / (t1 - t0)))
            ghost = refused and t0 * 100 < self.refused_pred and t1 * 100 > self.pct
            if fill > 0:
                col = _cell_colour((t0 + t1) / 2)
                cg = QLinearGradient(QPointF(0, inner.top()), QPointF(0, inner.bottom()))
                hi = QColor(col).lighter(150)
                hi.setAlpha(round(255 * fill))
                lo = QColor(col)
                lo.setAlpha(round(200 * fill))
                cg.setColorAt(0, hi)
                cg.setColorAt(0.5, lo)
                cg.setColorAt(1, QColor(col.red() // 3, col.green() // 3, col.blue() // 3, round(220 * fill)))
                p.setPen(Qt.NoPen)
                p.setBrush(cg)
                p.drawPolygon(cell)
            elif ghost:
                gc = QColor(RED)
                gc.setAlpha(round(70 + 90 * pulse))
                p.setPen(Qt.NoPen)
                p.setBrush(gc)
                p.drawPolygon(cell)
            else:
                over = t0 * 100 >= LIMIT
                oc = QColor(RED if over else BLUE_LIT)
                oc.setAlpha(45 if over else 22)
                p.setPen(QPen(oc, 1))
                p.setBrush(Qt.NoBrush)
                p.drawPolygon(cell)
        # energy front: a glow where the fill ends
        if self.pct > 0.5:
            fx = inner.left() + inner.width() * min(1.0, self.pct / 100)
            front = QRadialGradient(QPointF(fx, inner.center().y()), inner.height() * 1.4)
            fc = _cell_colour(min(1.0, self.pct / 100))
            front.setColorAt(0, QColor(fc.red(), fc.green(), fc.blue(), round(150 + 60 * pulse)))
            front.setColorAt(1, QColor(fc.red(), fc.green(), fc.blue(), 0))
            p.fillRect(QRectF(fx - inner.height() * 1.4, cap.top(), inner.height() * 2.8, cap.height()), front)
        # glass sheen
        sh = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        sh.setColorAt(0, QColor(255, 255, 255, 28))
        sh.setColorAt(0.45, QColor(255, 255, 255, 0))
        p.fillRect(cap, sh)
        if refused:                                            # the message, over a darkened capsule
            p.fillPath(path, QColor(4, 6, 10, 170))
            msg = 'FULL · REMOVE SOMETHING'
            px = cap.height() * 0.46
            while px > 7 and QFontMetrics(_font(px, True, 112)).horizontalAdvance(msg) > cap.width() - 2 * ch - 8:
                px -= 0.5
            p.setFont(_font(px, True, 112))
            p.setPen(QColor(255, 140, 158))
            p.drawText(cap, Qt.AlignCenter, msg)
        p.restore()
        # frame + limit notch
        fc = QColor(RED) if refused else (QColor(AMBER) if self.pct > LIMIT - 12 else QColor(98, 178, 255))
        fc.setAlpha(round(150 + 100 * pulse) if (refused or self.pct > LIMIT - 12) else 170)
        p.setPen(QPen(fc, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        lx = inner.left() + inner.width() * LIMIT / 100
        if not refused:                                        # the message sits where the line would be
            p.setPen(QPen(AMBER, 1.6))
            p.drawLine(QPointF(lx, cap.top() - 3), QPointF(lx, cap.bottom() + 3))
        p.setPen(Qt.NoPen)
        p.setBrush(AMBER)
        p.drawPolygon(QPolygonF([QPointF(lx - 4, cap.top() - 4), QPointF(lx + 4, cap.top() - 4), QPointF(lx, cap.top() + 1)]))
        # right: percent + LIVE/EST
        col = RED if refused or self.pct > LIMIT else (AMBER if self.pct > LIMIT - 12 else CYAN)
        p.setFont(_font(r.height() * 0.46, True, 104))
        p.setPen(col)
        shown = self.refused_pred if refused else self.pct
        p.drawText(QRectF(cap.right() + 6, r.top(), 58, r.height() * 0.66), Qt.AlignRight | Qt.AlignVCenter, f'{shown:.0f}%')
        p.setFont(_font(r.height() * 0.22, True, 120))
        p.setPen(GREY_T)
        p.drawText(QRectF(cap.right() + 6, r.top() + r.height() * 0.6, 58, r.height() * 0.38), Qt.AlignRight | Qt.AlignVCenter,
                   'LIVE' if self.live else 'EST')


class KillButton(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self.hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('switch this off now')

    def enterEvent(self, e):
        self.hover = True
        self.update()

    def leaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        c = RED if self.hover else QColor(255, 59, 92, 150)
        p.setPen(QPen(c, 1.4))
        p.setBrush(QColor(255, 59, 92, 60 if self.hover else 18))
        p.drawEllipse(r)
        p.setPen(QPen(QColor('#ffd6de') if self.hover else c, 2, Qt.SolidLine, Qt.RoundCap))
        k = r.width() * 0.26
        p.drawLine(QPointF(r.center().x() - k, r.center().y() - k), QPointF(r.center().x() + k, r.center().y() + k))
        p.drawLine(QPointF(r.center().x() + k, r.center().y() - k), QPointF(r.center().x() - k, r.center().y() + k))


class TaskRow(QWidget):
    """one running thing: input, block, cost bar, cycles + %, and an X (none for the box's own system parts)"""
    kill = Signal()

    def __init__(self, who, what, cycles, period, killable=True, parent=None):
        super().__init__(parent)
        self.who, self.what, self.cycles, self.period = who, what, cycles, period
        self.setFixedHeight(50)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addStretch(1)
        if killable:
            b = KillButton()
            b.clicked.connect(self.kill.emit)
            lay.addWidget(b)
        else:
            lock = QLabel('SYSTEM')
            lock.setStyleSheet('color:#5f6670;font:bold 9px "Segoe UI";letter-spacing:1px;')
            lay.addWidget(lock)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        g = QLinearGradient(r.topLeft(), r.topRight())
        g.setColorAt(0, QColor(98, 178, 255, 22))
        g.setColorAt(1, QColor(98, 178, 255, 4))
        p.setPen(QPen(QColor(98, 178, 255, 40), 1))
        p.setBrush(g)
        p.drawRoundedRect(r, 4, 4)
        frac = self.cycles / self.period
        acc = _cell_colour(min(1.0, frac * 5))                 # small jobs cyan, heavy ones violet/pink
        p.setPen(Qt.NoPen)
        p.setBrush(acc)
        p.drawRoundedRect(QRectF(r.left(), r.top(), 3, r.height()), 1.5, 1.5)
        p.setFont(_font(12, True))
        p.setPen(TEXT)
        p.drawText(QRectF(r.left() + 12, r.top(), 150, r.height()), Qt.AlignLeft | Qt.AlignVCenter, self.who)
        p.setPen(BLUE_LIT)
        p.drawText(QRectF(r.left() + 166, r.top(), 150, r.height()), Qt.AlignLeft | Qt.AlignVCenter, self.what)
        bar = QRectF(r.left() + 320, r.center().y() - 3, 120, 6)  # cost, full width = 15 % of the box
        p.setBrush(QColor(98, 178, 255, 25))
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(acc)
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(1.0, frac / 0.15), bar.height()), 3, 3)
        p.setFont(_font(11, True, 104))
        p.setPen(GREY_T)
        p.drawText(QRectF(bar.right() + 10, r.top(), 140, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   f'{self.cycles:>4.0f} cyc   {100 * frac:4.1f} %')


class DspTasks(QWidget):
    """the DSP task manager: per box its bar and everything running, heaviest first, each with an X"""

    def __init__(self, main):
        super().__init__(None, Qt.Tool)
        self.main = main
        self.setWindowTitle('DSP TASKS')
        self.setStyleSheet('background:#0b0d10;')
        self.resize(720, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        title = QLabel('DSP TASKS')
        title.setStyleSheet('color:#e6e9ec;font:bold 18px "Segoe UI";letter-spacing:4px;')
        outer.addWidget(title)
        hint = QLabel('Everything running on each box, heaviest first. X switches it off now (saved, like the buttons).')
        hint.setStyleSheet('color:#8d939b;font:bold 11px "Segoe UI";')
        outer.addWidget(hint)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setStyleSheet('QScrollArea{border:none;background:#0b0d10;}'
                         'QScrollBar:vertical{background:#0b0d10;width:10px;margin:0;}'
                         'QScrollBar::handle:vertical{background:#2a3a52;border-radius:4px;min-height:30px;}'
                         'QScrollBar::handle:vertical:hover{background:#3f6aa8;}'
                         'QScrollBar::add-line,QScrollBar::sub-line{height:0;}'
                         'QScrollBar::add-page,QScrollBar::sub-page{background:none;}')
        self.body = QWidget()
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(0, 8, 0, 0)
        self.lay.setSpacing(4)
        sc.setWidget(self.body)
        outer.addWidget(sc, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_bars)
        self.bars = {}

    def showEvent(self, e):
        self.rebuild()
        self.timer.start(500)

    def hideEvent(self, e):
        self.timer.stop()

    def rebuild(self):
        while self.lay.count():
            w = self.lay.takeAt(0).widget()
            if w:
                w.hide()                                       # gone now, not after the next event loop pass
                w.setParent(None)
                w.deleteLater()
        self.bars = {}
        for unit in range(len(self.main.units)):
            per = self.main.budget.period(unit)
            head = QWidget()
            hl = QHBoxLayout(head)
            hl.setContentsMargins(0, 10, 0, 4)
            bar = DspBar(420, 34)
            bar.clicked.connect(lambda: None)
            hl.addWidget(bar)
            hl.addStretch(1)
            self.bars[unit] = bar
            self.lay.addWidget(head)
            rows = self.main.dsp_tasks(unit)
            rows.sort(key=lambda t: -t[3])
            for ch, block, who, cycles, what in rows:
                row = TaskRow(who, what, cycles, per)
                row.kill.connect(lambda u=unit, c=ch, b=block: self.main.dsp_kill(u, c, b))
                self.lay.addWidget(row)
            self.lay.addWidget(TaskRow('ROLAND', 'MIXER + ROUTING', ROLAND, per, killable=False))
            self.lay.addWidget(TaskRow('ROSETTA', 'STRIP CORE', CORE, per, killable=False))
        self.lay.addStretch(1)
        self.refresh_bars()

    def refresh_bars(self):
        for unit, bar in self.bars.items():
            pct, live = self.main.dsp_now(unit)
            bar.set_load(pct, live, unit)
