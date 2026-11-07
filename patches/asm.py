"""Assemble a patch .S on newpc (bfin-elf binutils at ~/bfin/tools/bin).

usage: python -I asm.py NAME   (NAME.S + NAME.ld here) -> NAME_<section>.bin, NAME.sec (section vma size), NAME.lst
Folds `SYM & 0xffff` / `SYM >> 16` of .set constants into numbers first (gas makes unsupported relocs otherwise).
For a full 32-bit constant use `R1.L = SYM; R1.H = SYM;` (gas takes the low/high half itself).
"""
import os
import re
import subprocess
import sys

name = sys.argv[1]
src = open(name + '.S', encoding='utf8').read()
sets = {m.group(1): int(m.group(2), 0) for m in re.finditer(r'^\s*\.set\s+(\w+)\s*,\s*(\w+)', src, re.M)}


def fold(m):
    if m.group(1) not in sets:
        return m.group(0)
    v = sets[m.group(1)]
    return hex(v & 0xffff) if m.group(2) == '&' else hex((v >> 16) & 0xffff)


open(name + '_pp.S', 'w', encoding='utf8').write(re.sub(r'\b(\w+)\s*(&|>>)\s*(?:0xffff|16)\b', fold, src))
SSH = r'C:\Windows\System32\OpenSSH\ssh.exe'
SCP = r'C:\Windows\System32\OpenSSH\scp.exe'
H = 'wizard@newpc.local'
subprocess.run([SSH, H, 'mkdir -p /tmp/octa_p'], check=True)
subprocess.run([SCP, '-q', name + '_pp.S', name + '.ld', 'remote_asm.sh', H + ':/tmp/octa_p/'], check=True)
r = subprocess.run([SSH, H, f'cd /tmp/octa_p && sh remote_asm.sh {name}'], capture_output=True, text=True)
if r.returncode:
    sys.exit(r.stdout + r.stderr)
for f in (name + '.sec', name + '.lst', name + '.nm'):
    subprocess.run([SCP, '-q', f'{H}:/tmp/octa_p/{f}', f], check=True)
for line in open(name + '.sec'):
    sec, vma, size = line.split()
    b = f'{name}_{sec[1:]}.bin'
    subprocess.run([SCP, '-q', f'{H}:/tmp/octa_p/{b}', b], check=True)
    print(f'{sec:8s} {int(vma, 16):#x} {os.path.getsize(b)} bytes')
