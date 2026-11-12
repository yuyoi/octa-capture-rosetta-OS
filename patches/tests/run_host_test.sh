#!/bin/sh
set -e
cd /tmp/octa_ht
gcc -O1 -Wall -fPIC -shared -DHOST_TEST -I. -o liblab.so lab.c strip.c scene.c scene_host.c lab_host.c
gcc -O1 -Wall -DHOST_TEST -I. -o test_scene test_scene_host.c scene.c scene_host.c strip.c
./test_scene
gcc -O1 -g -Wall -DHOST_TEST -fsanitize=address,undefined -I. -o test_fx test_fx_host.c strip.c -lm
./test_fx
gcc -O1 -g -Wall -DHOST_TEST -fsanitize=address,undefined -I. -o test_gate test_gate_host.c strip.c -lm
./test_gate
python3 test_lab_host.py
python3 test_strip_host.py
