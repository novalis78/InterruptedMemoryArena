"""Summarize a run: per arm x model. Usage: summarize.py RUN [RUN...]"""
import glob
import json
import re
import sys
from collections import defaultdict

FORBIDDEN = re.compile(r'/opt/ima|/var/lib/ima|/etc/ima|memoryarena|huggingface|ZexueHe|interruptbench', re.I)

rows = defaultdict(list)
for run in sys.argv[1:]:
    for f in glob.glob(f'/var/lib/ima/runs/{run}/*/result.json'):
        r = json.load(open(f))
        m, g = r['meta'], r['grade']
        log = open(f.replace('result.json', 'agent.log'), errors='replace').read()
        hits = sorted(set(x.group(0) for x in FORBIDDEN.finditer(log)))
        rows[(m['label'], m['model'].split('/')[-1])].append((m, g, hits))

print(f"{'arm':16} {'model':22} {'n':>2} {'PS':>5} {'MA_PS':>5} {'SPS':>5} {'SR':>4} {'upd':>7} {'stale':>7} {'undo':>7} {'undoSt':>6} {'subm':>6} {'cost':>8} {'$/chain':>7} {'min':>5} {'exits':>5}")
for (arm, model), rs in sorted(rows.items()):
    n = len(rs)
    s = lambda k: sum(g[k] for _, g, _ in rs)
    ct, ut = s('changed_total'), s('undo_total')
    cost = sum(m['cost'] for m, _, _ in rs)
    mins = sum((m['ended'] - m['started']) / 60 for m, _, _ in rs) / n
    print(f"{arm:16} {model:22} {n:>2} {s('PS')/n:5.2f} {s('MA_PS')/n:5.2f} {s('SPS')/n:5.2f} {s('SR')/n:4.2f} "
          f"{s('changed_updated'):>3}/{ct:<3} {s('changed_stale'):>3}/{ct:<3} {s('undo_restored'):>3}/{ut:<3} {s('undo_stale'):>6} "
          f"{s('submitted'):>3}/{s('travelers'):<2} {cost:8.4f} {cost/n:7.4f} {mins:5.1f} {sum(m['exits'] for m, _, _ in rs):>5}")
    for m, g, hits in rs:
        if hits:
            print(f"   audit {m['chain']}: {hits}")
