"""Link to dual/build/octarec.exe, the USB recording engine (boxes on WinUSB, no Roland driver).
One reader thread turns the engine's lines into Qt signals; the app sends rec / stop / quit."""
import collections
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, '..', 'dual', 'build', 'octarec.exe')


class _Msg:
    type = 'sysex'

    def __init__(self, data):
        self.data = data


class _Out:
    """looks like a mido output port to tools/octa_lab.py, sends through the engine"""

    def __init__(self, engine, box):
        self.engine, self.box = engine, box

    def send(self, msg):
        self.engine.sysex(self.box, [0xF0] + list(msg.data) + [0xF7])

    def close(self):
        pass


class _In:
    """looks like a mido input port: complete SysEx messages the engine received from this box"""

    def __init__(self):
        self.q = collections.deque(maxlen=512)

    def iter_pending(self):
        while self.q:
            yield _Msg(self.q.popleft())

    def close(self):
        pass


class Engine(QObject):
    ready = Signal(int, int, list)      # boxes, sample rate, USB serial of each box (engine order)
    nobox = Signal()                    # no box on WinUSB: the Roland path applies
    meters = Signal(int, list)          # box, 10 x peak dBFS
    started = Signal()
    done = Signal(float, int, float, list)   # seconds, dropouts, box 2 ppm, [(box, input), ...]
    error = Signal(str)
    feed_status = Signal(str)           # send to stream: 'FEED on <rate> <ch> <device>', 'FEED nodevice', 'F <stats>', ...

    def __init__(self):
        super().__init__()
        self.p = None
        self.gen = 0                    # which engine process we are listening to, so an old one's exit is not news
        self.result = None
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.inq = [_In(), _In()]

    def ports(self, box):
        return _Out(self, box), self.inq[box]

    def sysex(self, box, data):
        self._send('sx %d %s' % (box, bytes(data).hex()))

    def start(self):
        if not os.path.exists(EXE):
            self.error.emit('octarec.exe is not built')
            return
        self.p = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                  creationflags=0x08000000)     # CREATE_NO_WINDOW
        threading.Thread(target=self._read, args=(self.p, self.gen), daemon=True).start()

    def restart(self):
        """the boxes came back from a power cycle: the old engine's USB handles are dead, so start a new one"""
        threading.Thread(target=self._restart, daemon=True).start()

    def _restart(self):
        self.gen += 1
        old = self.p
        if old:
            try:
                self._send('quit')
                old.wait(3)
            except (subprocess.TimeoutExpired, OSError):
                old.kill()
                old.wait(3)
        for q in self.inq:
            q.q.clear()
        self.start()

    def _send(self, s):
        try:
            with self.lock:
                self.p.stdin.write(s + '\n')
                self.p.stdin.flush()
        except (OSError, ValueError, AttributeError):
            self.error.emit('the recording engine has stopped')

    def rec(self, folder):
        self.finished.clear()
        self.result = None
        self._send('rec ' + folder)

    def stop(self):
        self._send('stop')

    def monitor(self, on):
        """the other box's inputs out of OUT 1-2 of the box on libusbK"""
        self._send('mon 1' if on else 'mon 0')

    def feed(self, device):
        """send the inputs set with fgain to the playback device whose name contains `device` (None = stop)"""
        self._send('feed off' if not device else 'feed ' + device)

    def fgain(self, box, gains):
        self._send('fgain %d %s' % (box, ' '.join('%.4f' % g for g in gains)))

    def quit(self):
        if self.p:
            self._send('quit')

    def _read(self, p, gen):
        for ln in p.stdout:
            if gen != self.gen:
                return
            a = ln.split()
            if not a:
                continue
            if a[0] == 'M':
                self.meters.emit(int(a[1]), [float(x) for x in a[2:]])
            elif a[0] == 'S':
                self.inq[int(a[1])].q.append(tuple(bytes.fromhex(a[2])[1:-1]))
            elif a[0] == 'READY':
                self.ready.emit(int(a[1]), int(a[2]), a[3:])
            elif a[0] == 'NOBOX':
                self.nobox.emit()
            elif a[0] == 'REC':
                self.started.emit()
            elif a[0] == 'DONE':
                kept = [tuple(int(v) for v in x.split(':')) for x in a[4:]]
                self.result = (float(a[1]), int(a[2]), float(a[3]), kept)
                self.finished.set()
                self.done.emit(*self.result)
            elif a[0] in ('FEED', 'F'):
                self.feed_status.emit(ln.strip())
            elif a[0] == 'ERR':
                self.error.emit(' '.join(a[1:]))
        if gen == self.gen:
            self.error.emit('the recording engine has stopped')
