"""Grade one InterruptedMemoryArena chain.

Scores follow MemoryArena's travel metrics (fuzzy name match, threshold 0.7):
  PS   traveler full pass: every field of the traveler's final plan matches
  SPS  constraint-slot rate: slots where the traveler differs from the first traveler, averaged per chain
  SR   chain success: every traveler passes
plus the interruption measures:
  changed slots (the answer moved because of an interruption): updated / stale (old answer) / other
  undo traps (a revision that was taken back): restored / stale (kept the withdrawn revision) / other
"""
import difflib
import json
import re
import sys

SIM_TH = 0.7
SLOTS = ['current_city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation']


def sim(a, b):
    a, b = str(a or '-').strip().lower(), str(b or '-').strip().lower()
    if a == '-' and b == '-':
        return 1.0
    m = min(len(a), len(b))
    return difflib.SequenceMatcher(None, a[:m], b[:m]).ratio() if m else 0.0


def ma_ok(exp, got):
    """MemoryArena's own match (prefix-truncated ratio >= 0.7); lenient: a shared ", City" suffix can carry it."""
    return sim(exp, got) >= SIM_TH


def _n(v):
    return ' '.join(str(v or '-').lower().split()).strip(' ;.')


def _name(v):
    """The place's name: no ", City" suffix, no punctuation, single spaces."""
    v = _n(v)
    v = re.sub(r',\s*[^,]*$', '', v) if ',' in v else v
    return ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', v).split())


def _same(a, b):
    return a == b or (abs(len(a) - len(b)) <= 2 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.95)


def _transport(v):
    """What the transportation is, not how it is written up: the flight numbers for flights; the mode and the
    route for driving and taxis. Duration, distance and cost are details of the same leg."""
    flights = re.findall(r'f\d{5,}', v)
    if flights:
        return 'flight:' + ','.join(flights)
    m = re.match(r'(self-driving|taxi)\b[^,]*,\s*from\s+(.+?)\s+to\s+(.+?)\s*(,|$)', v)
    if m:
        return f'{m.group(1)}:{m.group(2)}>{m.group(3)}'
    return ' '.join(re.sub(r'[^a-z0-9: ]+', ' ', v).split())


def ok(exp, got, slot=None):
    """Strict match used for our scores: the same place (spelling slips of a character or two allowed)."""
    e, g = _n(exp), _n(got)
    if e in ('-', '') or g in ('-', ''):
        return e in ('-', '') and g in ('-', '')
    if slot == 'transportation':
        return _transport(e) == _transport(g)
    if slot == 'current_city':
        return _same(' '.join(re.sub(r'[^a-z0-9: ]+', ' ', e).split()), ' '.join(re.sub(r'[^a-z0-9: ]+', ' ', g).split()))
    if slot == 'attraction' or ';' in e:
        es = sorted(_name(x) for x in e.split(';') if x.strip())
        gs = sorted(_name(x) for x in g.split(';') if x.strip())
        return len(es) == len(gs) and all(_same(a, b) for a, b in zip(es, gs))
    return _same(_name(e), _name(g))


def day(plan, d):
    for x in plan or []:
        if x.get('days') == d:
            return x
    return None


def grade(task, submissions):
    names = task['names']
    base = {d['days']: d for d in task['base_plan']}
    last = {}
    for s in submissions:
        last[s['name'].lower()] = s['plan']
    per, slot_rates = [], []
    for i_str, plan in task['final'].items():
        i = int(i_str)
        sub = last.get(names[i].lower())
        full, ma_full, cons, cons_ok = True, True, 0, 0
        for gd in plan:
            sd = day(sub, gd['days'])
            for s in SLOTS:
                good = bool(sd) and ok(gd.get(s), sd.get(s), s)
                ma_full &= bool(sd) and ma_ok(gd.get(s), sd.get(s))
                full &= good
                if sim(base[gd['days']].get(s), gd.get(s)) < SIM_TH:
                    cons += 1
                    cons_ok += good
        per.append({'traveler': names[i], 'submitted': sub is not None, 'pass': full, 'ma_pass': ma_full, 'constraint_slots': cons, 'constraint_ok': cons_ok})
        if cons:
            slot_rates.append(cons_ok / cons)
    changed = []
    for c in task['changed']:
        sub = last.get(names[c['who']].lower())
        got = (day(sub, c['day']) or {}).get(c['slot'])
        verdict = 'missing' if got is None else 'updated' if ok(c['new'], got, c['slot']) else 'stale' if ok(c['stale'], got, c['slot']) else 'other'
        changed.append({**c, 'traveler': names[c['who']], 'got': got, 'verdict': verdict})
    traps = []
    for c in task.get('undo_traps', []):
        sub = last.get(names[c['who']].lower())
        got = (day(sub, c['day']) or {}).get(c['slot'])
        verdict = 'missing' if got is None else 'restored' if ok(c['final'], got, c['slot']) else 'stale' if ok(c['trap'], got, c['slot']) else 'other'
        traps.append({**c, 'traveler': names[c['who']], 'got': got, 'verdict': verdict})
    n = len(per)
    return {
        'PS': sum(p['pass'] for p in per) / n if n else 0,
        'SPS': sum(slot_rates) / len(slot_rates) if slot_rates else 0,
        'SR': float(all(p['pass'] for p in per)),
        'MA_PS': sum(p['ma_pass'] for p in per) / n if n else 0,
        'submitted': sum(p['submitted'] for p in per), 'travelers': n,
        'changed_updated': sum(c['verdict'] == 'updated' for c in changed), 'changed_stale': sum(c['verdict'] == 'stale' for c in changed),
        'changed_total': len(changed),
        'undo_restored': sum(t['verdict'] == 'restored' for t in traps), 'undo_stale': sum(t['verdict'] == 'stale' for t in traps),
        'undo_total': len(traps),
        'per_traveler': per, 'changed': changed, 'undo_traps': traps,
    }


if __name__ == '__main__':
    tasks = {t['id']: t for t in json.load(open(sys.argv[1]))}
    subs = [json.loads(l) for l in open(sys.argv[3])]
    print(json.dumps(grade(tasks[int(sys.argv[2])], subs), indent=1))
