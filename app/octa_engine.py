"""Link to dual/build/octarec.exe, the USB recording engine (boxes on WinUSB, no Roland driver).
One reader thread turns the engine's lines into Qt signals; the app sends rec / stop / quit."""
import os
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, '..', 'dual', 'build', 'octarec.exe')


class Engine(QObject):
    ready = Signal(int, int)            # boxes, sample rate
    nobox = Signal()                    # no box on WinUSB: the Roland path applies
    meters = Signal(int, list)          # box, 10 x peak dBFS
    started = Signal()
    done = Signal(float, int, float, list)   # seconds, dropouts, box 2 ppm, [(box, input), ...]
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.p = None
        self.result = None
        self.finished = threading.Event()

    def start(self):
        if not os.path.exists(EXE):
            self.error.emit('octarec.exe is not built')
            return
        self.p = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                  creationflags=0x08000000)     # CREATE_NO_WINDOW
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, s):
        try:
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

    def quit(self):
        if self.p:
            self._send('quit')

    def _read(self):
        for ln in self.p.stdout:
            a = ln.split()
            if not a:
                continue
            if a[0] == 'M':
                self.meters.emit(int(a[1]), [float(x) for x in a[2:]])
            elif a[0] == 'READY':
                self.ready.emit(int(a[1]), int(a[2]))
            elif a[0] == 'NOBOX':
                self.nobox.emit()
            elif a[0] == 'REC':
                self.started.emit()
            elif a[0] == 'DONE':
                kept = [tuple(int(v) for v in x.split(':')) for x in a[4:]]
                self.result = (float(a[1]), int(a[2]), float(a[3]), kept)
                self.finished.set()
                self.done.emit(*self.result)
            elif a[0] == 'ERR':
                self.error.emit(' '.join(a[1:]))
        self.error.emit('the recording engine has stopped')
