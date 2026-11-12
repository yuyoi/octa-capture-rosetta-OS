"""Small timing experiments on a live box, run from app/do.txt: `probe <unit> <name> [args]`. Reloaded on every call, so an
experiment can change without restarting the app. Read-only on the audio path: nothing here changes what the box plays."""
import os
import re
import statistics
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
PATCHES = os.path.join(HERE, '..', 'patches')
L1_SCRATCH = 0xFF803F80                 # free L1 data (the install DMA scratch, idle afterwards)
SDRAM_SCRATCH = 0x3E0000


def fast_syms():
    out = {}
    for ln in open(os.path.join(PATCHES, 'fast.nm')):
        p = ln.split()
        if len(p) == 3:
            out[p[2]] = int(p[0], 16)
    return out


def rts_after(sym_addr):
    """address of the first RTS at or after a function start, from the build listing: calling it is an empty call"""
    lst = open(os.path.join(PATCHES, 'fast.lst')).read()
    for m in re.finditer(r'^\s*([0-9a-f]+):\s+(?:[0-9a-f]{2} )+\s*RTS;', lst, re.M):
        a = int(m.group(1), 16)
        if a >= sym_addr:
            return a
    raise SystemExit('no RTS found')


def time_call(lab, addr, r0=0, r1=0, r2=0, n=60):
    cyc = [lab.call(addr, r0, r1, r2)[1] for _ in range(n)]
    return min(cyc), statistics.median(cyc)


def calls(lab, strip, log):
    """the filter alone, outside the audio interrupt: struct in fast data memory and in SDRAM, against an empty call"""
    import octa_lab as ol
    s = fast_syms()
    rts = rts_after(s['_bq_run'])
    c = ol.q30(ol.rbj('peak', 44100, 1000.0, 6.0, 1.0))
    empty = time_call(lab, rts)
    log(f'probe: empty call {empty[0]} cycles min, {empty[1]:.0f} median (harness cost)')
    for where, addr in (('fast data memory', L1_SCRATCH), ('SDRAM', SDRAM_SCRATCH)):
        lab.poke(addr, struct.pack('<5i5i', *c, 0, 0, 0, 0, 0), 4)
        for name in ('_bq_run', '_bq_slim', '_bq_run3'):
            t = time_call(lab, s[name], addr, 1 << 26)
            log(f'probe: {name:8s} struct in {where:16s}: {t[0] - empty[0]:4d} cycles min over harness, median {t[1] - empty[1]:.0f}')


TIMES = 0xFF803FE0                      # fast.S: strip cycle sum, samples, Roland-chain cycle sum


def _avg(lab, secs=1.0):
    import time
    rd = lambda: struct.unpack('<3I', lab.peek(TIMES, 12, 4))
    a = rd()
    time.sleep(secs)
    b = rd()
    n = (b[1] - a[1]) & 0xFFFFFFFF
    if not n:
        raise SystemExit('the fast path is not counting samples')
    return ((b[0] - a[0]) & 0xFFFFFFFF) / n, ((b[2] - a[2]) & 0xFFFFFFFF) / n


def inside(lab, strip, log, n='4'):
    """inside the audio interrupt: average cycles of our strip and of Roland's chain per sample, block by block"""
    import octa_fast as F
    import octa_lab as ol
    n = int(n)
    fs = strip.fs
    fast = F.Fast(lab, log)
    fast.install()
    fast.selftest(ol.bq_model, ol.q30, ol.rbj, fs, log)
    cfgs = [('nothing', None)] + [(name, fn) for name, fn in F.PROFILE] + [('full FAT x4', 'full'), ('full SLIM x4', 'slim')]
    try:
        for ch in range(8):
            strip.off(ch, list(F.BLOCKS))
        fast.on()
        base_s, base_r = _avg(lab)
        log(f'inside: nothing on: strip {base_s:.0f} cycles, Roland chain {base_r:.0f} cycles per sample')
        for name, fn in cfgs[1:]:
            if fn in ('full', 'slim'):
                for ch in range(n):
                    F.neutral_chain(strip, ch, ol.rbj, fs, slim=fn == 'slim')
                k = n
            else:
                for ch in range(n):
                    fn(strip, ch, ol.rbj, fs, False)
                k = n
            s, r = _avg(lab)
            log(f'inside: {name:13s} on {k}: strip +{(s - base_s) / k:5.0f} per input, Roland chain {r - base_r:+5.0f} total')
            for ch in range(8):
                strip.off(ch, list(F.BLOCKS))
    finally:
        fast.off()
        for ch in range(8):
            strip.off(ch, list(F.BLOCKS))
    return True                                         # strips were touched: the app puts your chains back


def micro(lab, strip, log):
    """calibrated costs with interrupts off: what each kind of instruction really costs on this box"""
    import octa_fast as F
    import octa_lab as ol
    fast = F.Fast(lab, log)
    fast.install()
    fast.selftest(ol.bq_model, ol.q30, ol.rbj, strip.fs, log)
    s = fast_syms()
    best = lambda *a: min(lab.call(*a)[0] for _ in range(15))
    log(f'micro: 64 NOPs                  {best(s["_mb_nop"]):5d} cycles')
    for name, addr in (('fast data (strip block)', 0xFF8034C0), ('scratchpad', 0xFFB00000), ('SDRAM data', SDRAM_SCRATCH)):
        log(f'micro: 64 loads, {name:24s}{best(s["_mb_ld"], addr):5d} cycles')
    log(f'micro: 32 load+store, fast data     {best(s["_mb_ldst"], L1_SCRATCH):5d} cycles')
    log(f'micro: 32 taken branches, hinted    {best(s["_mb_br"]):5d} cycles')
    log(f'micro: 32 taken branches, unhinted  {best(s["_mb_brnp"]):5d} cycles')
    log(f'micro: 16 empty calls               {best(s["_mb_call"]):5d} cycles')
    c = ol.q30(ol.rbj('peak', 44100, 1000.0, 6.0, 1.0))
    for name, addr in (('fast data', L1_SCRATCH), ('SDRAM', SDRAM_SCRATCH)):
        lab.poke(addr, struct.pack('<5i5i', *c, 0, 0, 0, 0, 0), 4)
        log(f'micro: 8 biquads, struct in {name:9s} {best(s["_mb_bq"], addr):5d} cycles, fast version {best(s["_mb_bqf"], addr):5d}')
    inbuf = 0xFF803FA8
    lab.poke(inbuf, bytes(48), 4)
    for ch in range(8):
        strip.off(ch, list(F.BLOCKS))
    log(f'micro: one strip pass, all off      {best(s["_mb_strip"], inbuf):5d} cycles')
    F.neutral_chain(strip, 0, ol.rbj, strip.fs)
    log(f'micro: one strip pass, 1 full input {best(s["_mb_strip"], inbuf):5d} cycles')
    strip.off(0, list(F.BLOCKS))
    return True


def roland(lab, strip, log):
    """what Roland's own chain costs with its comps and reverb on or off. Your strip chains stay as they are (the timed path
    runs the same code bit for bit); every Roland setting touched is put back exactly."""
    import time
    import mido
    import octa_fast as F
    import octa_lab as ol
    from octa_link import dt1

    def send(addr, val):
        lab.out.send(mido.Message('sysex', data=dt1(addr, [val])))
        time.sleep(0.15)

    comp_was = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)]
    rev_was = lab.call(0x30B8C8, 0x43F7A8, 1, 0)[0]
    log(f'roland: as found: comp on inputs {[ch + 1 for ch in range(8) if comp_was[ch]]}, reverb type {rev_was} (0 = off)')
    fast = F.Fast(lab, log)
    fast.install()
    fast.selftest(ol.bq_model, ol.q30, ol.rbj, strip.fs, log)
    res = {}
    try:
        fast.on()

        def measure(name):
            time.sleep(0.4)
            s, r = _avg(lab, 1.5)
            res[name] = r
            log(f'roland: {name:28s} Roland chain {r:6.0f} cycles per sample ({100 * r / 8444:.1f} %)')

        measure('as found')
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], 0)
        measure('all 8 comps off')
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], 1)
        measure('all 8 comps on')
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], comp_was[ch] & 1)
        send([0x00, 0x04, 0x00, 0x00], 0)
        measure('reverb off, comps as found')
        if rev_was == 0:
            send([0x00, 0x04, 0x00, 0x00], 2)
            measure('reverb ROOM, comps as found')
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], 0)
        send([0x00, 0x04, 0x00, 0x00], 0)
        measure('comps off and reverb off')
    finally:
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], comp_was[ch] & 1)
        send([0x00, 0x04, 0x00, 0x00], rev_was)
        fast.off()
        now_c = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)]
        now_r = lab.call(0x30B8C8, 0x43F7A8, 1, 0)[0]
        log(f'roland: settings put back: {"yes" if now_c == comp_was and now_r == rev_was else "NO, check: " + str((now_c, now_r))}')
    return False


COMP_SITE, COMP_FN, CT = 0xFFA0832A, 0xFFA098EA, 0xFF803F40
ORIG_CALL = bytes.fromhex('00e3e00a')       # CALL 0xffa098ea, as in Roland's stock file (main.bin 0xa08f6)


def call_bytes(site, target):
    off = (target - site) >> 1
    if not -(1 << 23) <= off < (1 << 23):
        raise SystemExit('call target out of range')
    return struct.pack('<HH', 0xE300 | ((off >> 16) & 0xFF), off & 0xFFFF)


def comp_cost(lab, strip, log):
    """time Roland's comp routine inside their chain: point its CALL at our timer, measure, put the original back"""
    import time
    import mido
    import octa_fast as F
    from octa_link import dt1
    s = fast_syms()
    fast = F.Fast(lab, log)
    fast.install()
    up = {}
    for ln in open(os.path.join(PATCHES, 'fast.nm')):
        p = ln.split()
        if len(p) == 3:
            up[p[2]] = int(p[0], 16)

    def l1_bytes(addr, n=4):
        r = lab.call(up['_l1_read'], 0xFF803F00, addr, n)[0]
        if r:
            raise SystemExit(f'reading Roland code failed ({r})')
        return lab.peek(0xFF803F00, n)

    def patch(b):
        lab.poke(0xFF803F04, b)
        r = lab.call(up['_l1_patch_call'], 0xFF803F04)[0]
        if r:
            raise SystemExit(f'patch failed ({r})')
        got = l1_bytes(COMP_SITE)
        if got != b:
            raise SystemExit(f'patch did not land: {got.hex()}')

    def send(addr, val):
        lab.out.send(mido.Message('sysex', data=dt1(addr, [val])))
        time.sleep(0.15)

    now = l1_bytes(COMP_SITE)
    if now != ORIG_CALL:
        raise SystemExit(f'Roland code at {COMP_SITE:#x} is {now.hex()}, not the original call: not touching it')
    comp_was = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)]
    lab.poke(CT, bytes(28), 4)
    try:
        patch(call_bytes(COMP_SITE, s['_comp_timed']))

        def measure(name):
            time.sleep(0.3)
            a = struct.unpack('<7I', lab.peek(CT, 28, 4))
            time.sleep(1.5)
            b = struct.unpack('<7I', lab.peek(CT, 28, 4))
            n = (b[6] - a[6]) & 0xFFFFFFFF
            if not n:
                raise SystemExit('the timer never ran')
            cyc = ((b[3] - a[3]) & 0xFFFFFFFF) / n
            log(f'comp_cost: {name:22s} Roland comp {cyc:6.0f} cycles per sample ({100 * cyc / 8444:.1f} % of the budget), {n} calls')

        measure('as found')
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], 0)
        measure('all 8 comps off')
    finally:
        try:
            patch(ORIG_CALL)
            log('comp_cost: Roland code back to the original, read back and checked')
        finally:
            for ch in range(8):
                send([0x00, 0x05, ch, 0x06], comp_was[ch] & 1)
            log('comp_cost: comp switches put back' if [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)] == comp_was
                else 'comp_cost: COMP SWITCHES DIFFER, check them')
    return False


CAP = 0x3C0000


def comp_delay(lab, strip, log):
    """does Roland's comp delay the signal when it is off? 128 samples of inputs 1-2 before and after it, compared"""
    import time
    import mido
    import octa_fast as F
    from octa_link import dt1
    s = fast_syms()
    fast = F.Fast(lab, log)
    fast.install()
    up = s

    def l1_bytes(addr, n=4):
        if lab.call(up['_l1_read'], 0xFF803F00, addr, n)[0]:
            raise SystemExit('reading Roland code failed')
        return lab.peek(0xFF803F00, n)

    def patch(b):
        lab.poke(0xFF803F04, b)
        if lab.call(up['_l1_patch_call'], 0xFF803F04)[0] or l1_bytes(COMP_SITE) != b:
            raise SystemExit('patch failed')

    def send(addr, val):
        lab.out.send(mido.Message('sysex', data=dt1(addr, [val])))
        time.sleep(0.15)

    def grab():
        lab.poke(CT + 0x28, bytes(4), 4)
        time.sleep(0.2)
        raw = lab.peek(CAP, 128 * 16, 4)
        v = struct.unpack('<512i', raw)
        return [v[i::4] for i in range(4)]             # in0 before, in1 before, in0 after, in1 after

    def lag(before, after):
        out = []
        for k in range(0, 48):
            same = sum(1 for n in range(k, 128) if after[n] == before[n - k])
            out.append((same, k))
        return max(out)

    if l1_bytes(COMP_SITE) != ORIG_CALL:
        raise SystemExit('Roland code is not the original call: not touching it')
    comp_was = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)]
    lab.poke(CT, bytes(44), 4)
    try:
        patch(call_bytes(COMP_SITE, s['_comp_timed']))
        for name, setting in (('comps off', 0), ('comps on', 1)):
            for ch in range(2):
                send([0x00, 0x05, ch, 0x06], setting)
            time.sleep(0.3)
            b0, b1, a0, a1 = grab()
            for ch, (b, a) in enumerate(((b0, a0), (b1, a1))):
                same, k = lag(b, a)
                nz = sum(1 for x in b if x)
                log(f'comp_delay: {name:9s} input {ch + 1}: output == input from {k} samples earlier in {same}/{128 - k} '
                    f'samples (input non-zero in {nz}/128; first values {b[:3]} -> {a[:3]})')
    finally:
        try:
            patch(ORIG_CALL)
            log('comp_delay: Roland code back to the original, checked')
        finally:
            for ch in range(8):
                send([0x00, 0x05, ch, 0x06], comp_was[ch] & 1)
            ok = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)] == comp_was
            log('comp_delay: comp switches put back' if ok else 'comp_delay: COMP SWITCHES DIFFER, check them')
    return False


CM = 0xFF803F70                         # fast.S _comp_maybe: +0 R3, +4 ASTAT, +8 skipped, +0xc ran


def comp_switch_addrs(lab):
    """where Roland keeps each input's comp switch: getter 0x3097e4 reads [[0x41d024 + 4 ch]] + 4 * id, id 10 = COMP"""
    out = []
    for ch in range(8):
        p = struct.unpack('<I', lab.peek(0x41D024 + 4 * ch, 4, 4))[0]
        q = struct.unpack('<I', lab.peek(p, 4, 4))[0]
        out.append(q + 40)
    return out


def comp_skip(lab, strip, log, keep='0'):
    """skip Roland's comp while all 8 switches are off: check the side data, prove the switch logic, time it, put it back"""
    import time
    import mido
    import octa_fast as F
    import octa_lab as ol
    from octa_link import dt1
    s = fast_syms()
    fast = F.Fast(lab, log)
    fast.install()
    fast.selftest(ol.bq_model, ol.q30, ol.rbj, strip.fs, log)

    def l1_bytes(addr, n=4):
        if lab.call(s['_l1_read'], 0xFF803F00, addr, n)[0]:
            raise SystemExit('reading Roland code failed')
        return lab.peek(0xFF803F00, n)

    def patch(b):
        lab.poke(0xFF803F04, b)
        if lab.call(s['_l1_patch_call'], 0xFF803F04)[0] or l1_bytes(COMP_SITE) != b:
            raise SystemExit('patch failed')

    def send(addr, val):
        lab.out.send(mido.Message('sysex', data=dt1(addr, [val])))
        time.sleep(0.15)

    def comps(vals):
        for ch in range(8):
            send([0x00, 0x05, ch, 0x06], vals[ch] & 1)

    if l1_bytes(COMP_SITE) != ORIG_CALL:
        raise SystemExit('Roland code is not the original call: not touching it')
    comp_was = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)]
    addrs = comp_switch_addrs(lab)
    direct = [struct.unpack('<I', lab.peek(a, 4, 4))[0] for a in addrs]
    if direct != comp_was:
        raise SystemExit(f"switch addresses do not read the same as Roland's getter: {direct} vs {comp_was}")
    log(f"comp_skip: switch addresses found and match Roland's own getter: {[hex(a) for a in addrs]}")
    side = lambda: lab.peek(0xFFB00600, 0x100, 4)
    try:
        comps([0] * 8)
        time.sleep(0.5)
        a = side()
        time.sleep(0.5)
        b = side()
        moving = [0xFFB00600 + i for i in range(0, 0x100, 4) if a[i:i + 4] != b[i:i + 4]]
        log(f'comp_skip: comp side area 0xffb00600-0xffb006ff with all comps off: words still changing: {[hex(x) for x in moving] or "none"}')
        lab.poke(CM, bytes(16), 4)
        fast.on()
        time.sleep(0.4)
        before = _avg(lab, 1.5)[1]
        patch(call_bytes(COMP_SITE, s['_comp_maybe']))
        time.sleep(0.4)
        cnt0 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        after = _avg(lab, 1.5)[1]
        cnt1 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        log(f'comp_skip: Roland chain {before:.0f} -> {after:.0f} cycles per sample with the skip, all comps off '
            f'(skipped {cnt1[0] - cnt0[0]}, ran {cnt1[1] - cnt0[1]})')
        a2 = side()
        time.sleep(0.5)
        b2 = side()
        moving2 = [0xFFB00600 + i for i in range(0, 0x100, 4) if a2[i:i + 4] != b2[i:i + 4]]
        log(f'comp_skip: same area WITH the skip: still changing {[hex(x) for x in moving2] or "none"}; '
            f'values {[struct.unpack("<i", b2[x - 0xFFB00600:x - 0xFFB00600 + 4])[0] for x in moving]}')
        if set(moving) - set(moving2):
            log(f'comp_skip: FROZEN by the skip: {[hex(x) for x in sorted(set(moving) - set(moving2))]}')
        send([0x00, 0x05, 2, 0x06], 1)
        time.sleep(0.4)
        cnt0 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        on = _avg(lab, 1.0)[1]
        cnt1 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        log(f'comp_skip: input 3 comp switched on: Roland chain {on:.0f} cycles (skipped {cnt1[0] - cnt0[0]}, ran {cnt1[1] - cnt0[1]}): '
            f'{"their comp runs again, good" if cnt1[1] - cnt0[1] > 1000 and cnt1[0] == cnt0[0] else "UNEXPECTED"}')
        send([0x00, 0x05, 2, 0x06], 0)
        time.sleep(0.4)
        cnt0 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        time.sleep(0.5)
        cnt1 = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
        log(f'comp_skip: switched off again: skipped {cnt1[0] - cnt0[0]}, ran {cnt1[1] - cnt0[1]}')
    finally:
        try:
            patch(ORIG_CALL)
            log('comp_skip: Roland code back to the original, checked')
        finally:
            fast.off()
            comps(comp_was)
            ok = [lab.call(0x3097E4, 0x43F444, 10, ch)[0] for ch in range(8)] == comp_was
            log('comp_skip: comp switches put back' if ok else 'comp_skip: COMP SWITCHES DIFFER, check them')
    return False


def fw(lab, strip, log):
    """after flashing strip8: did the firmware load the fast path and the comp skip, and what do things cost now"""
    import time
    import octa_fast as F
    stamp, state = struct.unpack('<2I', lab.peek(F.FAST_INFO, 8, 4))
    f = F.Fast(lab, log)
    blob = f.img + bytes(-len(f.img) % 4)
    mine = sum(int.from_bytes(blob[i:i + 4], 'little') for i in range(0, len(blob), 4)) & 0xFFFFFFFF
    ptr = f.u32(F.CBPTR)
    log(f'fw: fast path state {state:#x} ({"running" if state & 1 else "NOT running"}, '
        f'{"comp skip in" if state & 0x10 else "comp skip NOT in"}{", Roland call was not original" if state & 0x20 else ""}), '
        f'build stamp {"matches this PC" if stamp == mine else f"{stamp:#x} differs from this PC {mine:#x}"}, '
        f'audio pointer {ptr:#x} ({"fast" if ptr == f.sym["wrap48_fast"] else "normal"})')
    a = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
    s, r = _avg(lab, 1.5)
    b = struct.unpack('<2I', lab.peek(CM + 8, 8, 4))
    log(f'fw: per sample: our strip {s:.0f} cycles, Roland chain {r:.0f} cycles ({100 * (s + r) / 8444:.1f} % together); '
        f'Roland comp skipped {b[0] - a[0]}, ran {b[1] - a[1]} in 1.5 s')
    return False


def regs(lab, strip, log):
    """cache and clock setup: is the data cache on, where are the stacks, what speed is SDRAM"""
    rd = lambda a: struct.unpack('<I', lab.peek(a, 4, 4))[0]
    rd16 = lambda a: struct.unpack('<H', lab.peek(a, 2, 2))[0]
    log(f'probe: IMEM_CONTROL {rd(0xFFE01004):#x}  DMEM_CONTROL {rd(0xFFE00004):#x}  (bit 2/3 = caches on)')
    log(f'probe: PLL_CTL {rd16(0xFFC00000):#x}  PLL_DIV {rd16(0xFFC00004):#x}  EBIU_SDGCTL {rd(0xFFC00A10):#x}')
    log(f'probe: EVT10 (our audio ISR) {rd(0xFFE02028):#x}  IMASK {rd(0xFFE02104):#x}  ILAT {rd(0xFFE0210C):#x}')
    for i in range(16):
        a = rd(0xFFE00100 + 4 * i)
        d = rd(0xFFE00200 + 4 * i)
        if d & 1:
            log(f'probe: DCPLB{i:2d} {a:#010x} data {d:#x} ({"cached" if d & 0x1000 else "not cached"}{", write-back" if d & 0x4000 else ""})')
