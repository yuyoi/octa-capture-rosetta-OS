"""Keeps a Scarlett's mixer inputs in place after a power cycle, through Focusrite Control's local server.

The Scarlett kept its clock and output routing over a restart but dropped the inputs added to its mixer, and the server
gave it a new device number, so every id here is read from what the server announces instead of being remembered.
It only ever adds a named stereo input that is missing, at most 3 tries per power-up, and only when the server says
this client is approved. Approving it stays the user's click in Focusrite Control.
"""
import re
import socket
import subprocess
import threading
import time

NO_WINDOW = 0x08000000
HELLO = '<client-details hostname="Rosetta Octa Control" client-key="rosetta-octa-1"/>'
MODEL = 'Scarlett 6i6'
MAX_TRIES = 3


def frame(x):
    b = x.encode()
    return ('Length=%06X ' % len(b)).encode() + b


def split_frames(buf):
    """-> (complete messages, what is left over)"""
    out = []
    while True:
        m = re.match(rb'Length=([0-9A-Fa-f]{6}) ', buf)
        if not m:
            return out, buf
        n = int(m.group(1), 16)
        if len(buf) < m.end() + n:
            return out, buf
        out.append(buf[m.end():m.end() + n].decode('utf8', 'replace'))
        buf = buf[m.end() + n:]


def parse_arrival(xml):
    """device number, the add-stereo-input item, stereo names -> source ids, and the mixer's input slots"""
    m = re.search(r'<device\b[^>]*?\bid="(\d+)"[^>]*>', xml)
    if not m:
        return None
    model = re.search(r'\bmodel="([^"]*)"', m.group(0))
    add = re.search(r'<add-stereo-input\b[^>]*?\bid="(\d+)"', xml)
    mixer = re.search(r'<mixer\b.*?<mixes>', xml, re.S)
    return dict(devid=m.group(1), model=model.group(1) if model else '', add=add.group(1) if add else None,
                names={n: i for i, n in re.findall(r'<[a-z-]+\s+id="(\d+)"[^>]*?\bstereo-name="([^"]+)"', xml)},
                slots=re.findall(r'<source\s+id="(\d+)"', mixer.group(0)) if mixer else [])


def find_port():
    """the TCP port Focusrite Control's server listens on, or None"""
    run = lambda *a: subprocess.run(a, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=15).stdout
    m = re.search(r'"ControlServer\.exe","(\d+)"', run('tasklist', '/FI', 'IMAGENAME eq ControlServer.exe', '/FO', 'CSV', '/NH'))
    if not m:
        return None
    for ln in run('netstat', '-ano', '-p', 'TCP').splitlines():      # the state word is translated, so match the address
        p = re.match(r'\s*TCP\s+\S+:(\d+)\s+(?:0\.0\.0\.0:0|\[::\]:0)\s+\S+\s+(\d+)\s*$', ln)
        if p and p.group(2) == m.group(1):
            return int(p.group(1))
    return None


class Keeper:
    def __init__(self, inputs, log=print, port_finder=find_port):
        self.inputs, self.log, self.find = list(inputs), log, port_finder
        self.stop = False
        self.status = 'not connected'
        self.th = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.th.start()

    def run(self):
        while not self.stop:
            try:
                port = self.find()
                if port:
                    self.session(port)
                else:
                    self.status = 'Focusrite Control not running'
            except (OSError, subprocess.SubprocessError) as e:
                self.status = 'lost: ' + str(e)[:60]
            for _ in range(10):
                if self.stop:
                    return
                time.sleep(0.5)

    def session(self, port):
        s = socket.create_connection(('127.0.0.1', port), timeout=3)
        s.settimeout(0.25)
        try:
            s.sendall(frame(HELLO))
            self.status = 'connected, no Scarlett on yet'
            self.loop(s)
        finally:
            s.close()

    def loop(self, s):
        buf = b''
        auth, dev, vals, tries, seen_ok = None, None, {}, 0, False
        arrived, next_try, keep = 0.0, 0.0, 0.0
        while not self.stop:
            now = time.time()
            if now > keep:
                s.sendall(frame('<keep-alive/>'))
                keep = now + 1.5
            try:
                d = s.recv(1 << 20)
                if not d:
                    return
                buf += d
            except socket.timeout:
                pass
            msgs, buf = split_frames(buf)
            for t in msgs:
                if '<approval' in t and 'type="response"' in t or ('<approval' in t and auth is None):
                    auth = 'authorised="true"' in t
                if '<device-arrival' in t:
                    a = parse_arrival(t)
                    if a and MODEL in a['model']:
                        dev, vals, tries, seen_ok = a, {}, 0, False
                        arrived, next_try = time.time(), 0.0
                        s.sendall(frame('<device-subscribe devid="%s" subscribe="true"/>' % a['devid']))
                        self.status = 'device %s' % a['devid']
                elif '<device-removal' in t:
                    dev = None
                    self.status = 'device removed'
                for k, v in re.findall(r'<item id="(\d+)" value="([^"]*)"', t):
                    vals[k] = v
            if not dev or auth is not True or not dev['add'] or time.time() - arrived < 2.0 or time.time() < next_try:
                continue
            have = {vals.get(x) for x in dev['slots']}
            missing = [n for n in self.inputs if n in dev['names'] and dev['names'][n] not in have]
            if not missing:
                if not seen_ok:
                    seen_ok = True
                    self.status = 'mixer ok, device %s' % dev['devid']
                    self.log('focusrite: mixer ok (device %s)' % dev['devid'])
                continue
            seen_ok = False
            if tries >= MAX_TRIES:
                self.status = 'could not add ' + missing[0]
                continue
            s.sendall(frame('<set devid="%s"><item id="%s" value="%s"/></set>' % (dev['devid'], dev['add'], dev['names'][missing[0]])))
            self.log('focusrite: added %s back to the mixer (device %s)' % (missing[0], dev['devid']))
            tries += 1
            next_try = time.time() + 2.5
