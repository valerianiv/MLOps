"""HTTP-сервис принятой версии на FastAPI. Только localhost.

Модель загружается один раз при старте из accepted.json. Изменение указателя само
по себе не меняет модель в памяти: новая версия вступает в силу после перезапуска.
Backend внедряется: StubBackend для тестов, TransformersBackend для настоящей модели.
"""
import argparse
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

import backends
import release

MAX_BODY_BYTES = 65536
MAX_PROMPT_CHARS = 4000
MAX_INPUT_TOKENS = 1024


class GenerateRequest(BaseModel):
    """Настройки генерации вне запроса фиксирует протокол оценки: лишние поля — 4xx."""
    model_config = ConfigDict(extra='forbid')
    prompt: StrictStr = Field(max_length=MAX_PROMPT_CHARS)
    max_new_tokens: StrictInt | None = Field(default=None, ge=1)

    @field_validator('prompt')
    @classmethod
    def prompt_not_blank(cls, value):
        if not value.strip():
            raise ValueError('prompt пуст')
        return value


class ServiceState:
    def __init__(self, root, backend_factory, warmup_seconds, max_concurrency, max_queue, queue_timeout):
        self.root = Path(root)
        self.backend_factory = backend_factory
        self.warmup_seconds = warmup_seconds
        self.ready = threading.Event()
        self.backend = None
        self.limit = None
        self.error = None
        self.slots = threading.BoundedSemaphore(max_concurrency)
        self.max_queue = max_queue
        self.queue_timeout = queue_timeout
        self.waiting = 0
        self.waiting_lock = threading.Lock()

    def load(self):
        """Готовность появляется только после проверки файлов, загрузки весов и прогрева."""
        try:
            document = json.loads((self.root / release.POINTER).read_text(encoding='utf-8'))
            release.check_accepted(document, self.root)
            self.backend = self.backend_factory(document, self.root)
            self.backend.warmup()
            time.sleep(self.warmup_seconds)
            self.limit = document['runtime']['generation']['max_new_tokens']
            self.ready.set()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'

    def acquire_slot(self):
        """Ограниченная очередь: вычисление не больше max_concurrency, ожидание измеряется."""
        started = time.perf_counter()
        with self.waiting_lock:
            if self.waiting >= self.max_queue:
                raise HTTPException(429, 'Очередь генерации заполнена')
            self.waiting += 1
        try:
            if not self.slots.acquire(timeout=self.queue_timeout):
                raise HTTPException(503, 'Истекло ожидание слота генерации')
        finally:
            with self.waiting_lock:
                self.waiting -= 1
        return time.perf_counter() - started


def create_app(root, backend_factory, warmup_seconds=0.0, max_concurrency=1, max_queue=8, queue_timeout=300.0, debug=False):
    state = ServiceState(root, backend_factory, warmup_seconds, max_concurrency, max_queue, queue_timeout)

    @asynccontextmanager
    async def lifespan(app):
        threading.Thread(target=state.load, daemon=True).start()
        yield

    app = FastAPI(title='model-release-service', lifespan=lifespan)
    app.state.service = state

    @app.middleware('http')
    async def limit_body(request: Request, call_next):
        size = request.headers.get('content-length', '0')
        if not size.isdigit() or int(size) > MAX_BODY_BYTES:
            return JSONResponse({'detail': 'Тело запроса слишком велико'}, status_code=413)
        return await call_next(request)

    @app.get('/health')
    def health():
        return {'alive': True}

    @app.get('/ready')
    def ready():
        if state.ready.is_set():
            return {'ready': True, 'model_id': state.backend.loaded_id, 'runtime_sha256': state.backend.runtime_sha256}
        return JSONResponse({'ready': False, 'error': state.error}, status_code=503)

    @app.post('/generate')
    def generate(body: GenerateRequest):
        started = time.perf_counter()
        if not state.ready.is_set():
            raise HTTPException(503, 'Модель ещё не готова', headers={'Retry-After': '1'})
        backend = state.backend
        limit = state.limit if body.max_new_tokens is None else body.max_new_tokens
        # Диапазон 1..лимит протокола: при greedy меньший лимит лишь обрезает допущенный ответ.
        if limit > state.limit:
            raise HTTPException(422, f'max_new_tokens вне диапазона 1..{state.limit}')
        if backend.count_input_tokens(body.prompt) > MAX_INPUT_TOKENS:
            raise HTTPException(422, f'Вход длиннее {MAX_INPUT_TOKENS} токенов')
        request_id = str(uuid.uuid4())
        queue_seconds = state.acquire_slot()
        generation_started = time.perf_counter()
        try:
            result = backend.generate(body.prompt, limit)
        except Exception as exc:
            raise HTTPException(500, f'Ошибка генерации, request_id={request_id}') from exc
        finally:
            state.slots.release()
        finished = time.perf_counter()
        # Версия берётся из загруженного backend, а не из файла указателя.
        return {**result, 'request_id': request_id, 'model_id': backend.loaded_id,
                'runtime_sha256': backend.runtime_sha256, 'backend': backend.name,
                'queue_seconds': queue_seconds, 'generation_seconds': finished - generation_started,
                'total_seconds': finished - started}

    if debug:
        @app.get('/debug')
        def debug_info():
            backend = state.backend
            return {'loaded_id': backend.loaded_id if backend else None, 'calls': backend.calls if backend else 0,
                    'warmup_finished': state.ready.is_set()}

    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, default=Path('.lab'))
    p.add_argument('--port', type=int, default=8091)
    p.add_argument('--warmup-seconds', type=float, default=5)
    p.add_argument('--backend', choices=sorted(backends.FACTORIES), default='stub')
    p.add_argument('--max-concurrency', type=int, default=1)
    p.add_argument('--max-queue', type=int, default=8)
    args = p.parse_args()
    # /debug есть только у тестового stub: реальный API не раскрывает внутреннее состояние.
    app = create_app(args.workspace, backends.FACTORIES[args.backend], args.warmup_seconds,
                     args.max_concurrency, args.max_queue, debug=args.backend == 'stub')
    label = 'STUB ONLY' if args.backend == 'stub' else args.backend
    print(f'{label}: http://127.0.0.1:{args.port}', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
