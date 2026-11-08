"""Bench test of octarec's USB playback (no app running). montest.py <t|1> <seconds> [take folder]
t = -50 dBFS tone out of the box on libusbK; 1 = the other box's inputs. A folder also records a take during the test.
Box 1's capture channels 11-12 are its main mix, so what it plays shows up there without a cable."""
import os
import subprocess
import sys
import threading
import time

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'build', 'octarec.exe')
mode, secs = (sys.argv[1] if len(sys.argv) > 1 else 't'), int(sys.argv[2]) if len(sys.argv) > 2 else 8
take = sys.argv[3] if len(sys.argv) > 3 else None
p = subprocess.Popen([EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def rd():
    for ln in p.stdout:
        if ln[:2] not in ('M ', 'S '):
            print(ln.rstrip(), flush=True)


def send(s):
    p.stdin.write(s + '\n')
    p.stdin.flush()


threading.Thread(target=rd, daemon=True).start()
time.sleep(1.5)
print('--- before playback', flush=True)
time.sleep(1.2)
send('mon %s' % mode)
if take:
    os.makedirs(take, exist_ok=True)
    send('rec ' + take)
time.sleep(secs)
if take:
    send('stop')
    time.sleep(2)
send('mon 0')
time.sleep(1.5)
send('quit')
try:
    p.wait(5)
except subprocess.TimeoutExpired:
    p.kill()
    print('had to kill it')
print('exit', p.returncode)
