#!/bin/sh
# usage: remote_build.sh NAME  (in /tmp/octa_p: NAME_pp.S, NAME.ld, *.c, *.h, *.inc)
set -e
B=~/bfin/tools/bin/bfin-elf
N=$1
OBJS="$N.o"
for c in *.c; do
  [ -f "$c" ] || continue
  $B-gcc -O2 -mcpu=bf527-any -mcsync-anomaly -mspecld-anomaly -fno-branch-count-reg -ffreestanding -fno-builtin -nostdlib -fno-common -Wall -c "$c" -o "${c%.c}.o"
  OBJS="$OBJS ${c%.c}.o"
done
$B-as -o $N.o ${N}_pp.S
$B-ld -T $N.ld -Map $N.map -o $N.elf $OBJS $(dirname $($B-gcc -print-libgcc-file-name))/libgcc.a
$B-objdump -h $N.elf | awk '$2 ~ /^\./ && $2 != ".comment" {print $2, $4, $3}' > $N.sec
while read s vma size; do $B-objcopy -O binary -j $s $N.elf ${N}_${s#.}.bin; done < $N.sec
$B-objdump -d $N.elf > $N.lst
$B-nm -n $N.elf > $N.nm
