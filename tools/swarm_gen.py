"""Tiny SwarmUI client (newpc:7801, both 3060s) for the Rosetta Control app art.
python -I tools/swarm_gen.py OUTDIR NAME "prompt" [--model M] [--n 4] [--w 1024] [--h 1024] [--steps S] [--cfg C] [--seed N] [--neg "..."]
Writes OUTDIR/NAME_<seed>.png"""
import argparse
import json
import os
import urllib.parse
import urllib.request

HOST = 'http://newpc.local:7801'


def post(path, body):
    req = urllib.request.Request(HOST + path, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('name')
    ap.add_argument('prompt')
    ap.add_argument('--model', default='flux1-schnell-Q8_0.gguf')
    ap.add_argument('--neg', default='')
    ap.add_argument('--n', type=int, default=4)
    ap.add_argument('--w', type=int, default=1024)
    ap.add_argument('--h', type=int, default=1024)
    ap.add_argument('--steps', type=int, default=None)
    ap.add_argument('--cfg', type=float, default=None)
    ap.add_argument('--seed', type=int, default=-1)
    a = ap.parse_args()
    flux = 'flux' in a.model.lower()
    steps = a.steps or (4 if flux else 28)
    cfg = a.cfg if a.cfg is not None else (1.0 if flux else 5.0)
    sid = post('/API/GetNewSession', {})['session_id']
    body = {'session_id': sid, 'images': a.n, 'prompt': a.prompt, 'negativeprompt': a.neg, 'model': a.model,
            'width': a.w, 'height': a.h, 'steps': steps, 'cfgscale': cfg, 'seed': a.seed}
    r = post('/API/GenerateText2Image', body)
    if 'error' in r:
        raise SystemExit(r['error'])
    os.makedirs(a.outdir, exist_ok=True)
    for i, p in enumerate(r['images']):
        data = urllib.request.urlopen(HOST + '/' + urllib.parse.quote(p), timeout=120).read()
        out = os.path.join(a.outdir, f'{a.name}_{i}.png')
        open(out, 'wb').write(data)
        print(out)


if __name__ == '__main__':
    main()
