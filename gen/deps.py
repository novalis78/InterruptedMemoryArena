"""Parse MemoryArena group-travel requests into per-slot dependencies.

For every traveler request paragraph we find the slot it governs (day, meal/accommodation) and which earlier
travelers it refers to. A reference is a "join" when the traveler simply copies that person's choice (verified
against the ground truth: same value). Everything else ("20% more than Emma's", "rated lower than Eric's") is
"relative": its answer would have to be re-solved against the database if the referenced value changed.
"""
import json
import re

ORD = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7}
SLOTS = ('breakfast', 'lunch', 'dinner', 'accommodation')
DAY_RE = re.compile(r'\b(first|second|third|fourth|fifth|sixth|seventh)[- ]day\b|\bday (\d)\b|\bthe (first|second|third|fourth|fifth|sixth|seventh) day\b', re.I)
SLOT_RE = re.compile(r'breakfast|lunch|dinner|accommodation|stay|room|share accommodation|place to stay', re.I)
COMPARE_RE = re.compile(r'%|\$|more|less|higher|lower|within|cost|price|rated|rating|room type|rule|cheaper|pricier|different|same .* rule', re.I)


def norm(v):
    return str(v or '-').strip().lower()


def day_of(text):
    m = DAY_RE.search(text)
    if not m:
        return None
    if m.group(1):
        return ORD[m.group(1).lower()]
    if m.group(2):
        return int(m.group(2))
    return ORD[m.group(3).lower()]


def slot_of(text):
    t = text.lower()
    for s in ('breakfast', 'lunch', 'dinner'):
        if s in t:
            return s
    if re.search(r'accommodation|stay|room|share', t):
        return 'accommodation'
    return None


def names_of(task):
    names = [task['base_person']['name']]
    for q in task['questions']:
        first = q.split('\n', 1)[0].strip()
        m = re.match(r'I am ([A-Z][\w\'-]+)', first)
        names.append(m.group(1) if m else None)
    return names


def plans_of(task):
    """plans[i] -> {(day, slot): value}; i=0 is the base person."""
    out = []
    for p in [task['base_person']['daily_plans']] + list(task['answers']):
        plan = p['daily_plans'] if isinstance(p, dict) else p
        out.append({(d['days'], s): d.get(s, '-') for d in plan for s in SLOTS + ('transportation', 'attraction', 'current_city')})
    return out


def paragraphs(q):
    lines = [x.strip() for x in q.split('\n') if x.strip()]
    return lines[2:] if len(lines) > 2 else []   # drop "I am X." and "I'm joining ..."


def analyze(task):
    names = names_of(task)
    plans = plans_of(task)
    refs = []        # dicts: who(i), day, slot, of(j), oday, oslot, kind, text
    problems = []
    for i, q in enumerate(task['questions'], start=1):
        for para in paragraphs(q):
            gday, gslot = day_of(para), slot_of(para)
            mentioned = [j for j, n in enumerate(names[:i]) if n and re.search(r'\b' + re.escape(n) + r'\b', para)]
            if not mentioned:
                continue
            if gday is None or gslot is None:
                problems.append((i, para))
                continue
            for j in mentioned:
                n = names[j]
                # explicit "<Name>'s <ord>-day <slot>" / "<Name>'s <slot> on day N"
                m = re.search(re.escape(n) + r"'s ([^.;,]{0,60})", para)
                oday, oslot = gday, gslot
                if m:
                    tail = m.group(1)
                    d2, s2 = day_of(tail), slot_of(tail)
                    if d2:
                        oday = d2
                    if s2:
                        oslot = s2
                same = norm(plans[i].get((gday, gslot))) == norm(plans[j].get((oday, oslot)))
                kind = 'join' if (len(mentioned) == 1 and same and (oday, oslot) == (gday, gslot) and not COMPARE_RE.search(para)) else 'relative'
                refs.append(dict(who=i, day=gday, slot=gslot, of=j, oday=oday, oslot=oslot, kind=kind, text=para))
    return names, plans, refs, problems


if __name__ == '__main__':
    import sys, collections
    rows = [json.loads(l) for l in open(sys.argv[1])]
    c = collections.Counter()
    probs = 0
    for t in rows:
        _, _, refs, problems = analyze(t)
        probs += len(problems)
        for r in refs:
            c[r['kind']] += 1
    print(c, 'unparsed paragraphs with names:', probs)
