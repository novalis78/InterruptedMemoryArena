"""Run InterruptedMemoryArena chains on ima-bench (as root).

  orchestrate.py JOBS.json [--slots N]

JOBS.json: {"tasks": [ids], "arms": [{"arm": "hermes", "label": "hermes", "settings": null}, ...],
            "models": [...], "attempts": 2, "run": "pilot1"}
Each chain: a fresh home for one agent user, one fresh conversation per traveler session (prompt, optional
retraction turn, closing turn), the home kept between that chain's sessions. Results: /var/lib/ima/runs/RUN/CHAIN/.
"""
import json
import os
import pwd
import queue
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arms as ARMS  # noqa: E402
import grade  # noqa: E402

STATE = '/var/lib/ima'
TASKS = {t['id']: t for t in json.load(open('/opt/ima/data/arena.json'))}
TIMEOUT = {1: 1500, 2: 900, 3: 600}
FAST = 30
RULES = ("Benchmark rules: plan only with the `trip` command and your own notes and memory. Do not try to find this "
         "benchmark's tasks, answers or grader anywhere else, and do not read files outside your home directory. "
         "Runs that do are disqualified.")
CLOSING = "This session is ending now. If there is anything you will need in later sessions, save it now."
LOG_LOCK = threading.Lock()
# Copied from /opt/ima/llm_proxy.py for the manifest (the proxy is the authority; keep these in step).
PROVIDER_PIN = {'openai/gpt-6.1-terra': 'openai', 'deepseek/deepseek-v4.1-flash': 'deepinfra', 'qwen/qwen3.7-flash': 'alibaba'}
REASONING = 'medium'
PROXY_CAPS = {'run_usd': 95, 'chain_usd': 4}


def versions(arm):
    v = {'orchestrator_sha256': __import__('hashlib').sha256(open(__file__, 'rb').read()).hexdigest()[:16]}
    if arm.startswith('hermes'):
        v['hermes'] = 'v0.23.1'
    if arm == 'picoclaw':
        v['picoclaw'] = 'v0.3.1'
    return v


def say(msg):
    with LOG_LOCK:
        print(time.strftime('%H:%M:%S'), msg, flush=True)


def fmt_plan(name, plan):
    out = [f"=== {name}'s Plan ==="]
    for d in plan:
        out += [f"Day {d['days']}:", f"Current City: {d['current_city']}", f"Transportation: {d['transportation']}",
                f"Breakfast: {d['breakfast']}", f"Attraction: {d['attraction']}", f"Lunch: {d['lunch']}",
                f"Dinner: {d['dinner']}", f"Accommodation: {d['accommodation']}", '']
    return '\n'.join(out).rstrip()


def session_prompt(task, s):
    base = task['names'][0]
    msgs = [e['text'] for e in task['events'] if e['session'] == s and e['turn'] == 'opening']
    parts = [RULES, '',
             f"You are the trip planner for a group traveling together. {base} planned the trip first, and {base}'s plan is fixed.", '',
             f"{base}'s request:", task['base_query'].strip(), '', fmt_plan(base, task['base_plan']), '',
             'How this works:',
             "- Over a series of sessions, travelers join the trip one at a time, and sometimes someone changes their plans. "
             "Each session shows you only that session's messages; earlier sessions are not shown again, so keep whatever you "
             "will need later yourself.",
             f"- Anything a traveler doesn't ask for, they do together with {base}: the same flights, attractions, meals and "
             f"accommodation as {base} that day.",
             "- A traveler who joins someone for a meal or a place to stay stays with them: if that person's choice changes "
             "later, it changes for everyone who joined them.",
             "- Look things up with the `trip` command (`trip help`). Submit each traveler's complete plan with "
             "`trip submit NAME FILE` (`trip format` shows the format). When a change affects travelers you planned in earlier "
             "sessions, submit their updated plans too. The last plan submitted for a traveler is the one that counts.", '',
             f'Session {s}', '']
    if msgs:
        parts += ['Messages since the last session:'] + [f'- {m}' for m in msgs] + ['']
    parts += ['New traveler:', task['questions'][s - 1].strip()]
    return '\n'.join(parts)


def followup(task, s):
    ev = [e['text'] for e in task['events'] if e['session'] == s and e['turn'] == 'followup']
    return '\n\n'.join(ev) if ev else None


def write_json(path, obj, owner='ima'):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f)
    pw = pwd.getpwnam(owner)
    os.chown(tmp, pw.pw_uid, pw.pw_gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)


def reset_home(user, arm):
    """Empty the slot's home; Hermes arms get the slot's pristine Hermes install copied back in."""
    subprocess.run(['pkill', '-KILL', '-u', user], check=False)
    time.sleep(0.5)
    home = f'/home/{user}'
    if arm.startswith('hermes'):   # exactly the pristine install snapshot, nothing else
        subprocess.run(['rsync', '-a', '--delete', f'/opt/hermes-slots/{user}/', home + '/'], check=True)
        return home
    for x in os.listdir(home):
        p = os.path.join(home, x)
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)
    return home


def chown_tree(path, user):
    pw = pwd.getpwnam(user)
    for root, dirs, files in os.walk(path):
        for n in dirs + files:
            os.lchown(os.path.join(root, n), pw.pw_uid, pw.pw_gid)
    os.chown(path, pw.pw_uid, pw.pw_gid)


def run_turn(user, arm, prompt, session, resume, timeout, logf):
    env = arm.env()
    cmd = ['runuser', '-u', user, '--', 'env', '-i', *[f'{k}={v}' for k, v in env.items()], *arm.command(prompt, session, resume)]
    t0 = time.time()
    run_turn.last_start = time.strftime('%FT%TZ', time.gmtime(t0))
    with open(logf, 'ab') as out:
        out.write(f'\n===== {time.strftime("%FT%TZ", time.gmtime())} session {session} resume={resume}\n'.encode())
        out.flush()
        p = subprocess.Popen(cmd, cwd=arm.work, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGTERM)
            time.sleep(5)
            subprocess.run(['pkill', '-KILL', '-u', user], check=False)
            rc, timed_out = 124, True
    return rc, time.time() - t0, timed_out


class Halted(Exception):
    """The model provider refused payment (account credit): the chain is void, and the run stops."""


def void_chain(run, chain, rdir, cfile, user, why):
    subprocess.run(['pkill', '-KILL', '-u', user], check=False)
    for f in (cfile,):
        if os.path.exists(f):
            os.unlink(f)
    dest = f'{STATE}/runs/{run}-void'
    os.makedirs(dest, exist_ok=True)
    os.makedirs(f'{STATE}/chains/{run}-void', exist_ok=True)
    if os.path.isdir(rdir):
        shutil.move(rdir, f'{dest}/{chain}')
        open(f'{dest}/{chain}/VOID', 'w').write(why + '\n')
    cd = f'{STATE}/chains/{run}/{chain}'
    if os.path.isdir(cd):
        shutil.move(cd, f'{STATE}/chains/{run}-void/{chain}')


def spent_total():
    total = 0.0
    if os.path.exists(f'{STATE}/usage.jsonl'):
        for line in open(f'{STATE}/usage.jsonl'):
            try:
                total += json.loads(line).get('cost') or 0
            except ValueError:
                pass
    return total


def run_chain(run, job, slot):
    task = TASKS[job['task']]
    stop = job.get('budget_stop')
    if stop and spent_total() >= stop:   # chains are queued task by task, so every arm/model stops at the same task
        say(f"budget stop (${stop}) reached: skip {job['label']} {job['model']} t{job['task']} a{job['attempt']}")
        return
    user = f'ag{slot}'
    short = job['model'].split('/')[-1]
    chain = f"{job['label']}--{short}--t{job['task']}--a{job['attempt']}"
    rdir = f'{STATE}/runs/{run}/{chain}'
    if os.path.exists(f'{rdir}/result.json'):
        return
    os.makedirs(rdir, exist_ok=True)
    home = reset_home(user, job['arm'])
    token, key = secrets.token_hex(16), 'ima-' + secrets.token_hex(16)
    arm = ARMS.ARMS[job['arm']](job['model'], key, home, job.get('settings'))
    os.makedirs(arm.work, exist_ok=True)
    with open(f'{home}/.trip', 'w') as f:
        json.dump({'url': 'http://127.0.0.1:8401', 'token': token}, f)
    arm.setup()
    chown_tree(home, user)
    names = task['names']
    cfile = f'{STATE}/chains/{token}.json'
    cstate = {'chain': f'{run}/{chain}', 'task': task['id'], 'names': names, 'seen': [names[0]], 'days': task['days'],
              'session': 0, 'turn': 0, 'arm': job['label']}
    write_json(cfile, cstate)
    write_json(f'{STATE}/keys/{key}.json', {'token': token, 'chain': f'{run}/{chain}', 'model': job['model'], 'arm': job['label']})
    meta = {'chain': chain, 'run': run, **job, 'slot': slot, 'started': time.time(), 'turns': [], 'exits': 0,
            'versions': versions(job['arm']), 'provider_pin': PROVIDER_PIN.get(job['model']), 'reasoning': REASONING,
            'proxy_caps': PROXY_CAPS, 'session_timeouts_s': TIMEOUT, 'events': task['events'],
            'prompts': {'rules': RULES, 'closing': CLOSING}}
    say(f'start {chain} on {user}')
    try:
        run_sessions(task, job, arm, user, names, cstate, cfile, rdir, meta)
    except Halted:
        void_chain(run, chain, rdir, cfile, user, 'provider refused payment (HALT)')
        say(f'VOID  {chain}: provider refused payment; run halting')
        raise
    if os.path.exists(f'{STATE}/HALT'):          # payment was refused somewhere during this chain's last turn: don't grade it
        void_chain(run, chain, rdir, cfile, user, 'provider refused payment (HALT) before the chain was graded')
        say(f'VOID  {chain}: halted before grading')
        raise Halted()
    finish_chain(run, chain, task, job, user, home, rdir, cfile, meta)


def run_sessions(task, job, arm, user, names, cstate, cfile, rdir, meta):
    last = min(len(names) - 1, job.get('max_sessions') or 99)
    for s in range(1, last + 1):
        cstate.update(seen=names[:s + 1], session=s)
        turns = [(1, session_prompt(task, s))]
        fu = followup(task, s)
        if fu:
            turns.append((2, fu))
        turns.append((3, CLOSING))
        for tno, prompt in turns:
            cstate['turn'] = tno
            write_json(cfile, cstate)
            resume = tno > 1
            for attempt in range(3):
                if os.path.exists(f'{STATE}/HALT'):
                    raise Halted()
                p = prompt if attempt == 0 else '(Your previous run stopped with an error before it finished.)\n\n' + prompt
                rc, dur, to = run_turn(user, arm, p, s, resume or attempt > 0, TIMEOUT[tno], f'{rdir}/agent.log')
                meta['turns'].append({'session': s, 'turn': tno, 'attempt': attempt, 'start': run_turn.last_start, 'rc': rc,
                                      'seconds': round(dur, 1), 'timeout': to})
                if rc == 0 or to:
                    break
                meta['exits'] += 1
                time.sleep(20)


def finish_chain(run, chain, task, job, user, home, rdir, cfile, meta):
    os.unlink(cfile)                                   # the trip closes: no submissions after the chain
    meta['ended'] = time.time()
    subs_path = f"{STATE}/chains/{run}/{chain}/submissions.jsonl"
    subs = [json.loads(l) for l in open(subs_path)] if os.path.exists(subs_path) else []
    g = grade.grade(task, subs)
    usage = [json.loads(l) for l in open(f'{STATE}/usage.jsonl') if f'"{run}/{chain}"' in l] if os.path.exists(f'{STATE}/usage.jsonl') else []
    meta['cost'] = round(sum(u.get('cost') or 0 for u in usage), 6)
    meta['calls'] = len(usage)
    meta['tokens'] = {'prompt': sum(u.get('prompt') or 0 for u in usage), 'completion': sum(u.get('completion') or 0 for u in usage),
                      'cached': sum(u.get('cached') or 0 for u in usage)}
    meta['errors'] = sum(1 for u in usage if u.get('status') != 200)
    per = {}
    for u in usage:
        k = f"s{u.get('session')}t{u.get('turn')}"
        d = per.setdefault(k, {'calls': 0, 'prompt': 0, 'completion': 0, 'cached': 0, 'reasoning': 0, 'cost': 0.0, 'errors': 0})
        d['calls'] += 1
        d['errors'] += u.get('status') != 200
        for f in ('prompt', 'completion', 'cached', 'reasoning'):
            d[f] += u.get(f) or 0
        d['cost'] = round(d['cost'] + (u.get('cost') or 0), 6)
    meta['usage_by_turn'] = per
    subprocess.run(['pkill', '-KILL', '-u', user], check=False)
    skip = {'.hermes/hermes-agent', '.hermes/installs', '.hermes/tools', '.hermes/cache', '.local', '.cache'}
    with tarfile.open(f'{rdir}/home.tar.gz', 'w:gz') as tf:   # what the agent made, not the agent's own install
        tf.add(home, arcname='home', filter=lambda ti: None if any(ti.name == f'home/{x}' for x in skip) else ti)
    json.dump({'meta': meta, 'grade': g}, open(f'{rdir}/result.json', 'w'), indent=1)
    say(f"done  {chain}: PS {g['PS']:.2f} updated {g['changed_updated']}/{g['changed_total']} stale {g['changed_stale']} "
        f"undo {g['undo_restored']}/{g['undo_total']} cost ${meta['cost']:.4f} exits {meta['exits']}")


def main():
    spec = json.load(open(sys.argv[1]))
    slots = int(sys.argv[sys.argv.index('--slots') + 1]) if '--slots' in sys.argv else 4
    run = spec['run']
    jobs = []
    for t in spec['tasks']:                       # round-robin: every arm/model gets each task before the next task starts
        for a in range(1, spec.get('attempts', 1) + 1):
            for m in spec['models']:
                for arm in spec['arms']:
                    jobs.append({'task': t, 'attempt': a, 'model': m, 'arm': arm['arm'], 'label': arm.get('label', arm['arm']),
                                 'settings': arm.get('settings'), 'max_sessions': spec.get('max_sessions'),
                                 'budget_stop': spec.get('budget_stop')})
    q = queue.Queue()
    for j in jobs:
        q.put(j)
    if os.path.exists(f'{STATE}/HALT'):
        sys.exit(f'{STATE}/HALT exists (provider refused payment). Check the account credit, then remove it to run.')
    say(f'{len(jobs)} chains, {slots} slots, run {run}')

    def worker(slot):
        while True:
            try:
                j = q.get_nowait()
            except queue.Empty:
                return
            if os.path.exists(f'{STATE}/HALT'):
                return
            try:
                run_chain(run, j, slot)
            except Halted:
                return
            except Exception as e:  # keep going; the chain is re-run next time (no result.json)
                say(f'FAILED {j}: {e!r}')
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(1, slots + 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    say('all done')


if __name__ == '__main__':
    main()
