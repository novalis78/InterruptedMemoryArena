"""Memory-isolating measures for finished chains. Usage: analyze.py RUN [RUN...]  (writes analysis.json per chain)

1. Solve vs keep: was each traveler's plan right at the end of their own session (against the ground truth as of that
   session)? Of those, how many are still right at the end of the chain (against the final ground truth)?
   Solving in-session is table filtering; keeping is memory (carrying changes, not breaking what was right).
2. First wrong session: the first session after which the submitted state (last plan per traveler so far) differs
   from the ground truth as of that session. Chains that go wrong in session 1 say little about memory.
3. Join consistency: for every live join link (i follows j for a slot), compare i's final value with the agent's
   OWN final value for j (not the reference): correct / propagated (wrong, but equal to the agent's own wrong j) /
   inconsistent (differs from the agent's own j). Links to the first traveler are reported apart (that plan is shown
   in every session). Relative constraints ("within 10% of", "rated higher") are not assessed here.
"""
import glob
import json
import sys

import grade

TASKS = {t['id']: t for t in json.load(open('/opt/ima/data/arena_v2.json'))}


def plan_ok(gt_plan, sub):
    if not sub:
        return False
    for gd in gt_plan:
        sd = grade.day(sub, gd['days'])
        if not sd or not all(grade.ok(gd.get(s), sd.get(s), s) for s in grade.SLOTS):
            return False
    return True


def val(plan, d, s):
    return (grade.day(plan, d) or {}).get(s)


def analyze(task, subs):
    names = task['names']
    n = len(names) - 1
    lower = {nm.lower(): i for i, nm in enumerate(names)}
    by_session = {}
    state = {}
    for sb in subs:
        by_session.setdefault(sb.get('session') or 0, []).append(sb)
    states = {}
    for sess in range(1, n + 1):
        for sb in by_session.get(sess, []):
            state[lower.get(sb['name'].lower())] = sb['plan']
        states[sess] = dict(state)
    per_session, first_wrong = {}, None
    for sess in range(1, n + 1):
        gt = task['gt_by_session'][str(sess)]
        good = sum(plan_ok(gt[str(i)], states[sess].get(i)) for i in range(1, sess + 1))
        per_session[sess] = good / sess
        if first_wrong is None and good < sess:
            first_wrong = sess
    solved = [i for i in range(1, n + 1) if plan_ok(task['gt_by_session'][str(i)][str(i)], states[i].get(i))]
    final_state = states[n]
    kept = [i for i in solved if plan_ok(task['final'][str(i)], final_state.get(i))]
    base = {d['days']: d for d in task['base_plan']}
    joins = {'correct': 0, 'propagated': 0, 'inconsistent': 0, 'missing': 0}
    base_joins = {'correct': 0, 'wrong': 0, 'missing': 0}
    for i, d, s, j in task['final_links']:
        mine = val(final_state.get(i), d, s)
        truth = val(task['final'][str(i)], d, s)
        if mine is None:
            (base_joins if j == 0 else joins)['missing'] += 1
            continue
        if j == 0:
            base_joins['correct' if grade.ok(truth, mine, s) else 'wrong'] += 1
            continue
        leader = val(final_state.get(j), d, s)
        if grade.ok(truth, mine, s):
            joins['correct'] += 1
        elif leader is not None and grade.ok(leader, mine, s):
            joins['propagated'] += 1
        else:
            joins['inconsistent'] += 1
    return {'per_session_PS': per_session, 'first_wrong_session': first_wrong, 'sessions': n,
            'solved_in_session': len(solved), 'kept_to_end': len(kept), 'travelers': n,
            'joins': joins, 'joins_to_first_traveler': base_joins}


if __name__ == '__main__':
    for run in sys.argv[1:]:
        for f in sorted(glob.glob(f'/var/lib/ima/runs/{run}/*/result.json')):
            r = json.load(open(f))
            m = r['meta']
            sp = f"/var/lib/ima/chains/{run}/{m['chain']}/submissions.jsonl"
            subs = [json.loads(l) for l in open(sp)] if __import__('os').path.exists(sp) else []
            a = analyze(TASKS[m['task']], subs)
            json.dump(a, open(f.replace('result.json', 'analysis.json'), 'w'), indent=1)
            j = a['joins']
            print(f"{m['chain']:48} solved {a['solved_in_session']}/{a['travelers']} kept {a['kept_to_end']}/{a['solved_in_session']} "
                  f"first-wrong s{a['first_wrong_session'] or '-'} joins ok {j['correct']} prop {j['propagated']} incons {j['inconsistent']} miss {j['missing']}")
