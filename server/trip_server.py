"""trip server: the travel tools and the plan inbox for InterruptedMemoryArena (127.0.0.1:8401, runs as user ima).

The agents only ever see tool results and "saved" receipts. Answers never leave this process's directory.
Chains are registered by the orchestrator as /var/lib/ima/chains/<token>.json.
"""
import csv
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA = os.environ.get('IMA_DATA', '/opt/ima/data/travel_db')
STATE = os.environ.get('IMA_STATE', '/var/lib/ima')
LOCK = threading.Lock()


def load(path, cols):
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            row = {c: (r.get(c) or '').strip() for c in cols}
            if all(row[c] for c in cols):          # like pandas dropna() on the selected columns
                rows.append(row)
    return rows


def num(v):
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except ValueError:
        return v


RESTAURANTS = load(f'{DATA}/restaurants/clean_restaurant_2022.csv', ['Name', 'Average Cost', 'Cuisines', 'Aggregate Rating', 'City'])
HOTELS = load(f'{DATA}/accommodations/clean_accommodations_2022.csv',
              ['NAME', 'price', 'room type', 'house_rules', 'minimum nights', 'maximum occupancy', 'review rate number', 'city'])
ATTRACTIONS = load(f'{DATA}/attractions/attractions.csv', ['Name', 'Address', 'City'])


def table(rows, cols, heads):
    if not rows:
        return None
    vals = [[num(r[c]) if c not in ('Name', 'NAME', 'Cuisines', 'house_rules', 'Address', 'room type') else r[c] for c in cols] for r in rows]
    w = [max(len(h), *(len(v[i]) for v in vals)) for i, h in enumerate(heads)]
    out = ['  '.join(h.ljust(w[i]) for i, h in enumerate(heads)).rstrip()]
    out += ['  '.join(v[i].ljust(w[i]) for i in range(len(heads))).rstrip() for v in vals]
    return '\n'.join(out)


def tool(name, args):
    city = (args.get('city') or '').strip()
    if name == 'restaurants':
        t = table([r for r in RESTAURANTS if r['City'] == city], ['Name', 'Average Cost', 'Cuisines', 'Aggregate Rating'],
                  ['Name', 'Average Cost ($/person)', 'Cuisines', 'Rating'])
        return t or f'There is no restaurant in {city!r}. (City names are exact, e.g. "Rockford".)'
    if name == 'hotels':
        t = table([r for r in HOTELS if r['city'] == city],
                  ['NAME', 'price', 'room type', 'house_rules', 'minimum nights', 'maximum occupancy', 'review rate number'],
                  ['Name', 'Price/night', 'Room type', 'House rules', 'Min nights', 'Max occupancy', 'Rating'])
        return t or f'There is no accommodation in {city!r}. (City names are exact, e.g. "Rockford".)'
    if name == 'attractions':
        t = table([r for r in ATTRACTIONS if r['City'] == city], ['Name', 'Address'], ['Name', 'Address'])
        return t or f'There is no attraction in {city!r}.'
    return f'Unknown tool {name!r}.'


FIELDS = ['current city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation']
KEYS = {'current city': 'current_city', 'transportation': 'transportation', 'breakfast': 'breakfast', 'attraction': 'attraction',
        'attractions': 'attraction', 'lunch': 'lunch', 'dinner': 'dinner', 'accommodation': 'accommodation'}


def parse_plan(text):
    days, cur = {}, None
    for raw in text.splitlines():
        line = raw.strip().strip('*').strip()
        if not line or line.startswith('==='):
            continue
        m = re.match(r'^day\s*(\d+)\s*:?\s*$', line, re.I)
        if m:
            cur = int(m.group(1))
            days[cur] = {'days': cur}
            continue
        m = re.match(r'^([A-Za-z ]+?)\s*:\s*(.*)$', line)
        if m and cur is not None and m.group(1).strip().lower() in KEYS:
            days[cur][KEYS[m.group(1).strip().lower()]] = m.group(2).strip()
    return [days[d] for d in sorted(days)]


def chain(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', token or ''):
        return None
    p = f'{STATE}/chains/{token}.json'
    return json.load(open(p)) if os.path.exists(p) else None


def log(c, kind, payload):
    d = f"{STATE}/chains/{c['chain']}"
    os.makedirs(d, exist_ok=True)
    with LOCK, open(f'{d}/{kind}.jsonl', 'a') as f:
        f.write(json.dumps({'t': time.time(), 'session': c.get('session'), 'turn': c.get('turn'), **payload}) + '\n')


class H(BaseHTTPRequestHandler):
    def _send(self, code, text):
        b = text.encode()
        self.send_response(code)
        self.send_header('content-type', 'text/plain; charset=utf-8')
        self.send_header('content-length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('content-length') or 0)) or b'{}')
        except ValueError:
            return self._send(400, 'Bad request.')
        c = chain(self.headers.get('x-trip-token', ''))
        if not c:
            return self._send(403, 'This trip is not open.')
        if self.path == '/tool':
            out = tool(body.get('tool', ''), body.get('args') or {})
            log(c, 'tools', {'tool': body.get('tool'), 'args': body.get('args'), 'bytes': len(out)})
            return self._send(200, out)
        if self.path == '/submit':
            name = (body.get('name') or '').strip()
            names = c['names']                   # names[0] is the first traveler (fixed), then everyone seen so far
            if name.lower() == names[0].lower():
                return self._send(400, f"{names[0]}'s plan is already fixed; you don't submit it.")
            match = [n for n in c['seen'] if n.lower() == name.lower()]
            if not match:
                return self._send(400, f'No traveler named {name!r} has joined this trip yet.')
            plan = parse_plan(body.get('plan') or '')
            want = c['days']
            problems = []
            if len(plan) != want or [d['days'] for d in plan] != list(range(1, want + 1)):
                problems.append(f'the trip has {want} days (Day 1 to Day {want}); found {len(plan)} day block(s)')
            for d in plan:
                miss = [k for k in ('current_city', 'transportation', 'breakfast', 'attraction', 'lunch', 'dinner', 'accommodation') if k not in d]
                if miss:
                    problems.append(f"Day {d['days']} is missing " + ', '.join(m.replace('_', ' ').title() for m in miss))
            if problems:
                log(c, 'rejected', {'name': match[0], 'problems': problems})
                return self._send(400, 'Not saved: ' + '; '.join(problems) + '. Run `trip format` to see the format.')
            log(c, 'submissions', {'name': match[0], 'plan': plan})
            return self._send(200, f"Saved {match[0]}'s plan ({want} days). The last plan submitted for a traveler is the one that counts.")
        return self._send(404, 'Unknown request.')


if __name__ == '__main__':
    print(f'trip server: {len(RESTAURANTS)} restaurants, {len(HOTELS)} hotels, {len(ATTRACTIONS)} attractions', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8401), H).serve_forever()
