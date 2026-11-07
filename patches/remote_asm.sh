#!/bin/sh
# usage: remote_asm.sh NAME   (in /tmp/octa_p, files NAME_pp.S + NAME.ld)
set -e
B=~/bfin/tools/bin/bfin-elf
N=$1
$B-as -o $N.o ${N}_pp.S
$B-ld -T $N.ld -o $N.elf $N.o
$B-objdump -h $N.elf | awk '$2 ~ /^\./ {print $2, $4, $3}' > $N.sec
while read s vma size; do $B-objcopy -O binary -j $s $N.elf ${N}_${s#.}.bin; done < $N.sec
$B-objdump -d $N.elf > $N.lst
$B-nm $N.elf > $N.nm
