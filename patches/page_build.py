"""Build the STRIP page upload blob on newpc: page.c + strip.h -> page.bin (at 0x3B0000), page.nm, page.lst.
usage: python -I page_build.py"""
import subprocess
import sys

SSH = r'C:\Windows\System32\OpenSSH\ssh.exe'
SCP = r'C:\Windows\System32\OpenSSH\scp.exe'
H = 'wizard@newpc.local'
CFLAGS = ('-O2 -mcpu=bf527-any -mcsync-anomaly -mspecld-anomaly -fno-branch-count-reg -ffreestanding -fno-builtin '
          '-nostdlib -fno-common -Wall')
subprocess.run([SSH, H, 'rm -rf /tmp/octa_pg && mkdir -p /tmp/octa_pg'], check=True)
subprocess.run([SCP, '-q', 'page.c', 'strip.h', 'page.ld', H + ':/tmp/octa_pg/'], check=True)
cmd = (f'cd /tmp/octa_pg && B=~/bfin/tools/bin/bfin-elf && $B-gcc {CFLAGS} -c page.c -o page.o && '
       '$B-ld -T page.ld -o page.elf page.o $(dirname $($B-gcc -print-libgcc-file-name))/libgcc.a && '
       '$B-objcopy -O binary -j .page page.elf page.bin && $B-nm -n page.elf > page.nm && $B-objdump -d page.elf > page.lst')
r = subprocess.run([SSH, H, cmd], capture_output=True, text=True)
if r.returncode:
    sys.exit(r.stdout + r.stderr)
if r.stderr.strip():
    print(r.stderr.strip())
for f in ('page.bin', 'page.nm', 'page.lst'):
    subprocess.run([SCP, '-q', f'{H}:/tmp/octa_pg/{f}', f], check=True)
import os
print(f'page.bin {os.path.getsize("page.bin")} bytes at 0x3b0000')
