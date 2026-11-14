"""Build InterruptedMemoryArena tasks from MemoryArena's group travel planner.

Interruption types (InterruptBench's taxonomy, aimed at memory across sessions):
  retract  - in the traveler's own session, after the agent has answered: "forget my request for <slot> on day d,
             I'll do what <base> does". Delivered as a second turn of the same conversation.
  revise   - in a later session: an earlier traveler now wants to join someone else for <slot> on day d.
  add      - in a later session: an earlier traveler adds a request they forgot (join someone for a slot they left open).
  undo     - in a later session: an earlier traveler takes back a revision made in an even earlier session.

Joins are live links (stated in the instructions): whoever joined a traveler for a slot follows that traveler's
final choice. Ground truth is recomputed by replaying the events and following the links. Only slots whose
dependents are all joins (recursively) are ever changed, so every new answer is exact without re-solving.
"""
import json
import random
import sys
from collections import defaultdict

import deps

SLOT_WORD = {'breakfast': 'breakfast', 'lunch': 'lunch', 'dinner': 'dinner', 'accommodation': 'accommodation'}
ORDW = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth', 6: 'sixth', 7: 'seventh'}


def build(task, rng):
    names, plans, refs, problems = deps.analyze(task)
    n = len(names) - 1                      # travelers after the base person: sessions 1..n
    base = 0
    days = sorted({d for (d, s) in plans[0]})
    # links[(i,d,s)] = j  (i copies j's choice for that slot)
    links = {}
    dependents = defaultdict(list)          # (j,d,s) -> [(i, kind)]
    for r in refs:
        dependents[(r['of'], r['oday'], r['oslot'])].append((r['who'], r['kind']))
        if r['kind'] == 'join':
            links[(r['who'], r['day'], r['slot'])] = r['of']
    governed = defaultdict(set)              # slots each traveler asked about explicitly
    for i, q in enumerate(task['questions'], start=1):
        for p in deps.paragraphs(q):
            d, s = deps.day_of(p), deps.slot_of(p)
            if d and s:
                governed[i].add((d, s))
            if 'my' in p.lower() and any(w in p.lower() for w in ('than my', 'as my', 'of my', 'to my')):
                governed[i].add(('self-ref', None))

    def closure(key):
        """All (i,d,s) that follow key through joins; None if any dependent along the way is relative."""
        out, stack = [], [key]
        while stack:
            k = stack.pop()
            for (i, kind) in dependents.get(k, []):
                if kind != 'join':
                    return None
                nk = (i, k[1], k[2])
                out.append(nk)
                stack.append(nk)
        return out

    def changeable(i, d, s):
        if i == base or ('self-ref', None) in governed[i]:
            return False
        if s == 'accommodation' and d == days[-1]:
            return False                       # nobody sleeps there on the last day
        return closure((i, d, s)) is not None

    V0 = {(i, d, s): plans[i][(d, s)] for i in range(n + 1) for d in days for s in deps.SLOTS}
    used = set()                               # slots already touched (keep events independent)
    used_sources = set()

    def free(i, d, s):
        if (i, d, s) in used or (i, d, s) in used_sources:
            return False
        cl = closure((i, d, s)) or []
        return not any(k in used or k in used_sources for k in cl)

    events = []
    cands = [(i, d, s) for i in range(1, n + 1) for d in days for s in deps.SLOTS if changeable(i, d, s)]
    rng.shuffle(cands)

    def downstream_after(i, d, s, k):
        return [x for x in (closure((i, d, s)) or []) if x[0] >= k]

    # retract: traveler i asked for (d,s) themselves; value differs from base
    for (i, d, s) in cands:
        if (d, s) in governed[i] and deps.norm(V0[(i, d, s)]) != deps.norm(V0[(0, d, s)]) and free(i, d, s) and i < n:
            events.append(dict(type='retract', session=i, who=i, day=d, slot=s, to=0, turn='followup'))
            used.add((i, d, s)); used.update(closure((i, d, s)))
            break

    # revise (prefer one with a dependent planned after the message arrives), then add, then a revise+undo pair
    def pick_revise(min_gap=1, exclude=()):
        best = None
        for (i, d, s) in cands:
            if i in exclude or not free(i, d, s) or i + min_gap > n:
                continue
            for k in range(i + min_gap, n + 1):
                xs = [x for x in range(0, k) if x != i and free(x, d, s) and (x, d, s) not in (closure((i, d, s)) or [])
                      and deps.norm(V0[(x, d, s)]) not in (deps.norm(V0[(i, d, s)]), deps.norm(V0[(0, d, s)]))]
                if not xs:
                    continue
                score = len(downstream_after(i, d, s, k)) * 2 + len(closure((i, d, s)) or []) + (k - i) * 0.1
                if best is None or score > best[0]:
                    best = (score, i, d, s, k, rng.choice(xs))
        return best

    rv = pick_revise()
    if rv:
        _, i, d, s, k, x = rv
        events.append(dict(type='revise', session=k, who=i, day=d, slot=s, to=x, turn='opening'))
        used.add((i, d, s)); used.update(closure((i, d, s)) or []); used_sources.add((x, d, s))

    for (i, d, s) in cands:
        if (d, s) in governed[i] or not free(i, d, s) or i >= n:
            continue
        if deps.norm(V0[(i, d, s)]) != deps.norm(V0[(0, d, s)]):
            continue
        k = rng.randint(i + 1, n)
        xs = [x for x in range(1, k) if x != i and free(x, d, s) and deps.norm(V0[(x, d, s)]) != deps.norm(V0[(0, d, s)])
              and (x, d, s) not in (closure((i, d, s)) or [])]
        if xs:
            x = rng.choice(xs)
            events.append(dict(type='add', session=k, who=i, day=d, slot=s, to=x, turn='opening'))
            used.add((i, d, s)); used.update(closure((i, d, s)) or []); used_sources.add((x, d, s))
            break

    if n >= 6:
        rv2 = pick_revise(min_gap=1, exclude={e['who'] for e in events})
        if rv2:
            _, i, d, s, k, x = rv2
            if k + 1 <= n:
                k2 = rng.randint(k + 1, n)
                events.append(dict(type='revise', session=k, who=i, day=d, slot=s, to=x, turn='opening', undone_in=k2))
                events.append(dict(type='undo', session=k2, who=i, day=d, slot=s, to=None, turn='opening'))
                used.add((i, d, s)); used.update(closure((i, d, s)) or []); used_sources.add((x, d, s))

    events.sort(key=lambda e: (e['session'], e['turn'] != 'opening'))
    return names, plans, links, days, events, n


def replay(names, plans, links, days, events, n):
    """Final ground truth after all events, plus which slots changed and their stale (original) values."""
    links = dict(links)
    own = {(i, d, s): plans[i][(d, s)] for i in range(n + 1) for d in days for s in deps.SLOTS}
    saved = {}
    for e in events:
        key = (e['who'], e['day'], e['slot'])
        if e['type'] in ('retract', 'revise', 'add'):
            saved.setdefault(key, (links.get(key), own[key]))
            links[key] = e['to']
        elif e['type'] == 'undo':
            prev_link, prev_own = saved[key]
            if prev_link is None:
                links.pop(key, None)
            else:
                links[key] = prev_link
            own[key] = prev_own

    def value(key, seen=()):
        if key in links and key not in seen:
            j = links[key]
            return value((j, key[1], key[2]), seen + (key,))
        return own[key]

    replay.links = links
    final = {}
    for i in range(1, n + 1):
        final[i] = []
        for d in days:
            row = {'days': d}
            for s in ('current_city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation'):
                row[s] = value((i, d, s)) if s in deps.SLOTS else plans[i][(d, s)]
            final[i].append(row)
    changed = []
    for i in range(1, n + 1):
        for d in days:
            for s in deps.SLOTS:
                new, old = value((i, d, s)), plans[i][(d, s)]
                if deps.norm(new) != deps.norm(old):
                    changed.append(dict(who=i, day=d, slot=s, new=new, stale=old))
    return final, changed


def phrase(e, names):
    who, d, s = names[e['who']], e['day'], e['slot']
    when = f"on the {ORDW[d]} day"
    meal = SLOT_WORD[s]
    if e['type'] == 'retract':
        return (f"Wait, one change from me ({who}): forget what I asked for about {meal} {when}. "
                f"For that I'll just do the same as {names[0]}.")
    if e['type'] == 'revise':
        what = f"stay with {names[e['to']]}" if s == 'accommodation' else f"have {meal} with {names[e['to']]}"
        return f"{who} has changed plans: {when}, {who} now wants to {what} instead." if s != 'accommodation' else \
            f"{who} has changed plans: for accommodation {when}, {who} now wants to {what} instead."
    if e['type'] == 'add':
        what = f"stay with {names[e['to']]}" if s == 'accommodation' else f"have {meal} with {names[e['to']]}"
        return f"{who} forgot to mention something: {when}, {who} would like to {what}." if s != 'accommodation' else \
            f"{who} forgot to mention something: for accommodation {when}, {who} would like to {what}."
    if e['type'] == 'undo':
        return (f"{who} has changed their mind again about {meal} {when}: forget the change {who} asked for earlier. "
                f"{who} is going back to the original plan for that.")


def main(path, out, seed=7):
    rng = random.Random(seed)
    rows = [json.loads(l) for l in open(path)]
    tasks = []
    for t in rows:
        names, plans, links, days, events, n = build(t, rng)
        final, changed = replay(names, plans, links, days, events, n)
        final_links = [[k[0], k[1], k[2], j] for k, j in sorted(replay.links.items())]   # before any other replay call
        undo_traps = []
        if any(e['type'] == 'undo' for e in events):
            kept, _ = replay(names, plans, links, days, [e for e in events if e['type'] != 'undo'], n)
            for i in final:
                for fd, kd in zip(final[i], kept[i]):
                    for s in deps.SLOTS:
                        if deps.norm(fd[s]) != deps.norm(kd[s]):
                            undo_traps.append(dict(who=i, day=fd['days'], slot=s, final=fd[s], trap=kd[s]))
        # ground truth after each session (only the travelers seen so far, only the events delivered so far)
        gt_by_session = {}
        for sess in range(1, n + 1):
            part, _ = replay(names, plans, links, days, [e for e in events if e['session'] <= sess], n)
            gt_by_session[sess] = {i: part[i] for i in range(1, sess + 1)}
        _, _, refs, _ = deps.analyze(t)
        relative_refs = [[r['who'], r['day'], r['slot'], r['of'], r['oday'], r['oslot']] for r in refs if r['kind'] == 'relative']
        for e in events:
            e['text'] = phrase(e, names)
        base_plan = [{'days': d, **{s: plans[0][(d, s)] for s in ('current_city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation')}} for d in days]
        tasks.append(dict(id=t['id'], names=names, days=len(days), base_query=t['base_person']['query'], base_plan=base_plan,
                          questions=t['questions'], events=events, final=final, original={i + 1: a for i, a in enumerate(t['answers'])},
                          changed=changed, undo_traps=undo_traps, final_links=final_links, relative_refs=relative_refs,
                          gt_by_session=gt_by_session, n_events=len(events), types=sorted({e['type'] for e in events})))
    json.dump(tasks, open(out, 'w'), indent=1)
    import collections
    print('tasks', len(tasks), 'events per task', collections.Counter(t['n_events'] for t in tasks))
    print('type sets', collections.Counter(tuple(t['types']) for t in tasks).most_common(6))
    print('changed slots per task', collections.Counter(len(t['changed']) for t in tasks).most_common(10))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
