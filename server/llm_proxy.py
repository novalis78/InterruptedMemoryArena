"""Model proxy for InterruptedMemoryArena (127.0.0.1:8402, runs as user ima).

Agents get a dummy per-chain key and http://127.0.0.1:8402/api/v1 as their OpenRouter base URL. The proxy:
  - swaps in the real OpenRouter key (agents never hold it),
  - sets the chain's model, pins its upstream provider and the reasoning effort, the same for every agent,
  - asks OpenRouter for usage and records tokens and cost per chain, session and turn,
  - refuses requests once the chain's or the run's budget is spent.
"""
import asyncio
import json
import os
import time

import aiohttp
from aiohttp import web

STATE = os.environ.get('IMA_STATE', '/var/lib/ima')
UP = 'https://openrouter.ai/api/v1'
KEY = open('/etc/ima/openrouter.key').read().strip()
RUN_CAP = float(os.environ.get('IMA_RUN_CAP', '95'))
CHAIN_CAP = float(os.environ.get('IMA_CHAIN_CAP', '4'))
PIN = {
    'openai/gpt-6.1-terra': {'order': ['openai'], 'allow_fallbacks': False},
    'deepseek/deepseek-v4.1-flash': {'order': ['deepinfra'], 'allow_fallbacks': False},
    'qwen/qwen3.7-flash': {'order': ['alibaba'], 'allow_fallbacks': False},
}
EFFORT = os.environ.get('IMA_REASONING', 'medium')
# A rate-limited upstream (429) is retried here, the same way for every agent, so no harness is scored on its own
# rate-limit handling. Waits in seconds; after the last one the agent gets the 429.
RETRY_429 = (2, 4, 8, 15, 30)
USAGE = f'{STATE}/usage.jsonl'
spent = {'run': 0.0, 'chains': {}}
models_cache = {}


def load_spent():
    if os.path.exists(USAGE):
        for line in open(USAGE):
            try:
                u = json.loads(line)
            except ValueError:
                continue
            c = u.get('cost') or 0
            spent['run'] += c
            spent['chains'][u['chain']] = spent['chains'].get(u['chain'], 0) + c


def chain_for(request):
    auth = request.headers.get('authorization', '')
    tok = auth.removeprefix('Bearer ').strip()
    if not tok.startswith('ima-') or '/' in tok or len(tok) > 100:
        return None
    p = f'{STATE}/keys/{tok}.json'
    if not os.path.exists(p):
        return None
    k = json.load(open(p))
    c = json.load(open(f"{STATE}/chains/{k['token']}.json"))
    return {**k, 'session': c.get('session'), 'turn': c.get('turn')}


def record(c, model_asked, usage, status, ms, stream, proxy_retry=False):
    cost = float((usage or {}).get('cost') or 0)
    spent['run'] += cost
    spent['chains'][c['chain']] = spent['chains'].get(c['chain'], 0) + cost
    det = (usage or {}).get('prompt_tokens_details') or {}
    row = {'t': time.time(), 'chain': c['chain'], 'arm': c.get('arm'), 'model': c['model'], 'asked': model_asked,
           'session': c.get('session'), 'turn': c.get('turn'), 'status': status, 'ms': ms, 'stream': stream,
           'prompt': (usage or {}).get('prompt_tokens'), 'completion': (usage or {}).get('completion_tokens'),
           'cached': det.get('cached_tokens'), 'reasoning': ((usage or {}).get('completion_tokens_details') or {}).get('reasoning_tokens'),
           'cost': cost}
    if proxy_retry:
        row['proxy_retry'] = True   # the agent never saw this answer: the proxy waited and asked again
    with open(USAGE, 'a') as f:
        f.write(json.dumps(row) + '\n')
    if status == 402:   # the account (not the agent) is out of credit: tell the orchestrator to stop the run
        with open(f'{STATE}/HALT', 'w') as f:
            json.dump({'t': time.time(), 'chain': c['chain'], 'why': 'OpenRouter 402 (account credit)'}, f)


def err(code, msg):
    return web.json_response({'error': {'code': code, 'message': msg}}, status=code)


async def key_info(request):
    return web.json_response({'data': {'label': 'ima', 'usage': 0, 'limit': None, 'is_free_tier': False}})


async def models(request):
    path = request.path.removeprefix('/api/v1')
    if path in models_cache and time.time() - models_cache[path][0] < 3600:
        return web.Response(body=models_cache[path][1], content_type='application/json')
    async with request.app['http'].get(UP + path, headers={'authorization': f'Bearer {KEY}'}) as r:
        body = await r.read()
        if r.status == 200:
            models_cache[path] = (time.time(), body)
        return web.Response(body=body, status=r.status, content_type='application/json')


async def chat(request):
    c = chain_for(request)
    if not c:
        return err(401, 'Unknown key.')
    if spent['run'] >= RUN_CAP:
        return err(402, 'The run budget is spent.')
    if spent['chains'].get(c['chain'], 0) >= CHAIN_CAP:
        return err(402, 'This chain has used its budget.')
    try:
        body = await request.json()
    except ValueError:
        return err(400, 'Bad JSON.')
    asked = body.get('model')
    body['model'] = c['model']
    body['provider'] = PIN.get(c['model'], {})
    body['usage'] = {'include': True}
    body['reasoning'] = {'effort': EFFORT}
    for k in ('reasoning_effort', 'models', 'route'):
        body.pop(k, None)
    stream = bool(body.get('stream'))
    if stream:
        body['stream_options'] = {**(body.get('stream_options') or {}), 'include_usage': True}
    t0 = time.time()
    hdr = {'authorization': f'Bearer {KEY}', 'content-type': 'application/json',
           'http-referer': 'https://bootkey.ai', 'x-title': 'bootkey InterruptedMemoryArena'}
    tmo = aiohttp.ClientTimeout(total=1200, sock_read=600)
    for wait in (*RETRY_429, None):
        r = await request.app['http'].post(UP + '/chat/completions', json=body, headers=hdr, timeout=tmo)
        if r.status != 429 or wait is None:
            break
        await r.read()
        r.release()
        record(c, asked, None, 429, int((time.time() - t0) * 1000), stream, proxy_retry=True)
        await asyncio.sleep(wait)
    async with r:
        if not stream or r.status != 200 or 'text/event-stream' not in r.headers.get('content-type', ''):
            data = await r.read()
            usage = None
            try:
                usage = json.loads(data).get('usage')
            except (ValueError, AttributeError):
                pass
            record(c, asked, usage, r.status, int((time.time() - t0) * 1000), False)
            return web.Response(body=data, status=r.status, content_type=r.headers.get('content-type', 'application/json').split(';')[0])
        resp = web.StreamResponse(status=200, headers={'content-type': 'text/event-stream', 'cache-control': 'no-cache'})
        await resp.prepare(request)
        usage, buf = None, b''
        async for chunk in r.content.iter_any():
            await resp.write(chunk)
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                if line.startswith(b'data: {') and b'"usage"' in line:
                    try:
                        u = json.loads(line[6:]).get('usage')
                        if u:
                            usage = u
                    except ValueError:
                        pass
        await resp.write_eof()
        record(c, asked, usage, 200, int((time.time() - t0) * 1000), True)
        return resp


async def other(request):
    return err(404, f'Not available here: {request.method} {request.path}')


async def start(app):
    app['http'] = aiohttp.ClientSession()


async def stop(app):
    await app['http'].close()


def main():
    load_spent()
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    app.router.add_get('/api/v1/key', key_info)
    app.router.add_get('/api/v1/auth/key', key_info)
    app.router.add_get('/api/v1/models', models)
    app.router.add_get('/api/v1/models/{tail:.*}', models)
    app.router.add_post('/api/v1/chat/completions', chat)
    app.router.add_route('*', '/{tail:.*}', other)
    print(f'llm proxy: spent so far ${spent["run"]:.4f} of ${RUN_CAP}', flush=True)
    web.run_app(app, host='127.0.0.1', port=8402, print=None)


if __name__ == '__main__':
    main()
