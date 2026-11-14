"""Re-grade finished chains with the current grade.py (keeps the old grade as grade_v1 the first time). Usage: regrade.py RUN..."""
import glob
import json
import sys

import grade

TASKS = {t['id']: t for t in json.load(open('/opt/ima/data/arena.json'))}
for run in sys.argv[1:]:
    for f in sorted(glob.glob(f'/var/lib/ima/runs/{run}/*/result.json')):
        r = json.load(open(f))
        m = r['meta']
        sp = f"/var/lib/ima/chains/{run}/{m['chain']}/submissions.jsonl"
        subs = [json.loads(l) for l in open(sp)] if __import__('os').path.exists(sp) else []
        new = grade.grade(TASKS[m['task']], subs)
        r.setdefault('grade_v1', r['grade'])
        if r['grade']['PS'] != new['PS']:
            print(f"{m['chain']}: PS {r['grade']['PS']:.2f} -> {new['PS']:.2f}")
        r['grade'] = new
        r['grader_sha256'] = __import__('hashlib').sha256(open(grade.__file__, 'rb').read()).hexdigest()[:16]
        json.dump(r, open(f, 'w'), indent=1)
