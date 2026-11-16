"""CPU simulator extending the release/readiness/queue concepts of HW8–10.

Streaming work is real CPU work; chunks are NOT model tokens. One worker per Pod.
"""
import asyncio
import hashlib
import json
import os
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

BUCKETS = (.01, .025, .05, .1, .2, .4, .8, 1.6, 3.2, 6.4, 12.8, 25.6, 51.2, 102.4)
ttfc = Histogram('lab_ttfc_seconds', 'First content chunk, successful responses', buckets=BUCKETS)
duration = Histogram('lab_duration_seconds', 'Full successful response', buckets=BUCKETS)
outcomes = Counter('lab_requests', 'Terminal request outcomes', ['outcome'])
running = Gauge('lab_running', 'Computations in progress')
waiting = Gauge('lab_waiting', 'Requests waiting for a slot')
ready_metric = Gauge('lab_ready', 'Backend is warm and accepting requests')
cache_count = Counter('lab_cache', 'Simulated prefix cache', ['result'])
VERSION = os.getenv('MODEL_VERSION', 'A')
POD = os.getenv('POD_NAME', 'local')
WARMUP = float(os.getenv('WARMUP_SECONDS', '20'))
CAPACITY = int(os.getenv('CONCURRENCY', '1'))
MAX_QUEUE = int(os.getenv('MAX_QUEUE', '32'))
CACHE_SIZE = int(os.getenv('CACHE_SIZE', '32'))
HIT = int(os.getenv('HIT_ITERATIONS', '15000'))
MISS = int(os.getenv('MISS_ITERATIONS', '180000'))
QUEUE_TIMEOUT = float(os.getenv('QUEUE_TIMEOUT', '30'))
BROKEN = os.getenv('BROKEN_PROBES', 'false').lower() == 'true'
NEVER = os.getenv('NEVER_READY', 'false').lower() == 'true'
cache = OrderedDict()
warm = False
draining = False
queued = 0
loaded_hash = ''
slots = asyncio.Semaphore(CAPACITY)


def log(event, **fields):
    print(json.dumps(dict(event=event, wall=time.time(), pod=POD, version=VERSION,
                         backend_mode='simulated', **fields), ensure_ascii=False), flush=True)


async def load():
    global warm, loaded_hash
    try:
        root = Path(os.getenv('ARTIFACT_DIR', 'artifacts'))
        expected = json.loads((root / 'sha256.json').read_text())[VERSION]
        blob = (root / (VERSION + '.json')).read_bytes()
        loaded_hash = hashlib.sha256(blob).hexdigest()
        if loaded_hash != expected:
            raise ValueError('artifact hash mismatch')
        await asyncio.to_thread(cpu, MISS)
        await asyncio.sleep(WARMUP)
        if not NEVER:
            warm = True
            ready_metric.set(1)
            log('warmup_complete', artifact_sha256=loaded_hash)
    except Exception as exc:
        log('load_failed', error=str(exc))


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(load())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    rid=request.headers.get('x-request-id','unknown')[:120]
    outcomes.labels('validation').inc()
    log('terminal', request_id=rid, outcome='validation', http_status=422)
    return JSONResponse(dict(error='validation', request_id=rid, **meta()),status_code=422)


class Generate(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    history: list[str] = Field(default_factory=list, max_length=20)
    chunks: int = Field(default=8, ge=1, le=40)
    chunk_delay: float = Field(default=.03, ge=0, le=1)
    work: int = Field(default=1, ge=1, le=8)
    fail_after: int | None = Field(default=None, ge=1, le=39)


def meta():
    return dict(pod=POD, version=VERSION, artifact_sha256=loaded_hash, backend_mode='simulated')


@app.get('/health')
async def health():
    return {'alive': True}


@app.get('/startup')
@app.get('/ready')
async def ready():
    ok = (warm or BROKEN) and not draining
    return JSONResponse(dict(ready=ok, warm=warm, **meta()), status_code=200 if ok else 503)


@app.get('/drain')
async def drain():
    global draining
    draining = True
    ready_metric.set(0)
    log('drain_started')
    # Endpoint propagation before kubelet sends SIGTERM to uvicorn.
    await asyncio.sleep(8)
    return {'draining': True}


@app.get('/metrics')
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get('/state')
async def state():
    return dict(warm=warm, draining=draining, waiting=queued, cache_entries=len(cache), **meta())


@app.post('/debug/crash')
async def crash():
    """Local lab fault injection: exit without running drain/finalizers."""
    log('forced_process_exit')
    asyncio.get_running_loop().call_later(.05, os._exit, 137)
    return {'crash_scheduled': True}


def cpu(iterations):
    return hashlib.pbkdf2_hmac('sha256', b'lab11-simulated', b'fixed-salt', iterations)


@app.post('/stream')
async def stream(body: Generate, request: Request):
    global queued
    start = time.perf_counter()
    rid = request.headers.get('x-request-id', os.urandom(8).hex())[:120]
    info = dict(request_id=rid, client_ip=request.client.host, **meta())
    log('received', request_id=rid, client_ip=request.client.host, warm=warm)

    def reject(code, reason):
        outcomes.labels(reason).inc()
        log('terminal', request_id=rid, outcome=reason, http_status=code)
        return JSONResponse(dict(error=reason, **info), status_code=code)

    if not warm or draining:
        return reject(503, 'unready' if not warm else 'draining')
    if queued >= MAX_QUEUE:
        return reject(429, 'rejected')
    queued += 1
    waiting.inc()
    try:
        await asyncio.wait_for(slots.acquire(), QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        return reject(503, 'queue_timeout')
    finally:
        queued -= 1
        waiting.dec()
    queue_seconds = time.perf_counter() - start
    running.inc()

    async def chunks():
        first = None
        count = 0
        outcome = 'aborted'
        # Prefix locality is modeled explicitly. Cache stores no conversation state.
        key = hashlib.sha256((body.prompt.split('|')[0] + str(body.history)).encode()).hexdigest()
        hit = key in cache
        cache_count.labels('hit' if hit else 'miss').inc()
        try:
            await asyncio.to_thread(cpu, (HIT if hit else MISS) * body.work)
            cache[key] = True
            cache.move_to_end(key)
            while len(cache) > CACHE_SIZE:
                cache.popitem(last=False)
            for i in range(body.chunks):
                if await request.is_disconnected():
                    return
                await asyncio.to_thread(cpu, HIT * body.work)
                await asyncio.sleep(body.chunk_delay)
                now = time.perf_counter()
                if first is None:
                    first = now - start
                count += 1
                payload = dict(type='chunk', index=i, text=f'simulated-{i}',
                               context=body.history, queue_seconds=queue_seconds,
                               cache_hit=hit, **info)
                yield (json.dumps(payload, ensure_ascii=False) + '\n').encode()
                if body.fail_after == count:
                    # Explicit failure-injection control, used only by lab checks.
                    log('injected_stream_failure', request_id=rid)
                    return
            outcome = 'completed'
            elapsed = time.perf_counter() - start
            ttfc.observe(first)
            duration.observe(elapsed)
            yield (json.dumps(dict(type='done', chunks=count, **info)) + '\n').encode()
        finally:
            outcomes.labels(outcome).inc()
            running.dec()
            slots.release()
            log('terminal', request_id=rid, outcome=outcome, queue_seconds=queue_seconds,
                duration=time.perf_counter() - start, ttfc=first, chunks=count, cache_hit=hit)

    return StreamingResponse(chunks(), media_type='application/x-ndjson',
                             headers={'X-Request-ID': rid, 'X-Pod': POD, 'X-Model-Version': VERSION})


@app.post('/generate')
async def generate(body: Generate, request: Request):
    """Keep the previous single-response entrypoint alongside streaming."""
    response = await stream(body, request)
    if isinstance(response, JSONResponse):
        return response
    parts=[]
    last={}
    async for blob in response.body_iterator:
        item=json.loads(blob)
        last=item
        if item['type']=='chunk':
            parts.append(item['text'])
    return dict(answer=' '.join(parts), complete=last.get('type')=='done', **meta())


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.getenv('PORT', '8080')),
                timeout_graceful_shutdown=70, access_log=False)
