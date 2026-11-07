#!/bin/sh
set -e
cd /tmp/octa_ht
gcc -O1 -Wall -fPIC -shared -DHOST_TEST -I. -o liblab.so lab.c lab_host.c
python3 test_lab_host.py
