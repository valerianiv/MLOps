"""Опыты с настоящей моделью через настоящий HTTP: smoke, сверка с оценкой, замер.

    python scripts/real_api.py smoke   --workspace .lab-real --hw67 ../mlops26-hw67
    python scripts/real_api.py replay  --workspace .lab-real --hw67 ../mlops26-hw67
    python scripts/real_api.py loadtest --workspace .lab-real --hw67 ../mlops26-hw67

Нужны HF_HOME со снимком базы и зависимости extra model. Результаты пишутся в evidence/real.
"""
import argparse
import csv
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evidence' / 'real'
ADAPTER_CHECK_ROWS = 3


def call(port, method, path, payload=None, timeout=900):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def eval_rows(hw67):
    protocol = json.loads((hw67 / 'evaluation/params.json').read_text(encoding='utf-8'))
    read = lambda name: [json.loads(x) for x in (hw67 / name).read_text(encoding='utf-8').splitlines() if x.strip()]
    return read(protocol['golden']), read(protocol['behavioral']), protocol


class Api:
    def __init__(self, workspace, warmup=0.0):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen([sys.executable, str(ROOT / 'service.py'), '--workspace', str(workspace),
                                      '--port', str(self.port), '--backend', 'transformers',
                                      '--warmup-seconds', str(warmup)],
                                     cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'})

    def wait_alive(self, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return call(self.port, 'GET', '/health')
            except OSError:
                time.sleep(.2)
        raise SystemExit('Сервис не начал отвечать')

    def wait_ready(self, timeout=600):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, body = call(self.port, 'GET', '/ready')
            if status == 200:
                return body
            if body.get('error'):
                raise SystemExit(f'Загрузка не удалась: {body["error"]}')
            time.sleep(.5)
        raise SystemExit('Сервис не стал готов')

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=60)


def smoke(args):
    golden, _, protocol = eval_rows(args.hw67)
    saved = {o['id']: o for o in json.loads((args.hw67 / 'metrics/baseline-outputs.json').read_text(encoding='utf-8'))}
    api = Api(args.workspace, warmup=2)
    log = {'accepted_sha256': __import__('hashlib').sha256((args.workspace / 'accepted.json').read_bytes()).hexdigest()}
    try:
        started = time.monotonic()
        log['health_at_start'] = api.wait_alive()
        log['ready_during_load'] = call(api.port, 'GET', '/ready')
        log['generate_during_load'] = call(api.port, 'POST', '/generate', {'prompt': golden[0]['input']})
        log['debug_route_status'] = call(api.port, 'GET', '/debug')[0]
        log['ready_after_load'] = api.wait_ready()
        log['load_and_warmup_seconds'] = time.monotonic() - started
        negative = {}
        for name, payload in {'empty_prompt': {'prompt': ''}, 'limit_zero': {'prompt': 'x', 'max_new_tokens': 0},
                              'limit_100000': {'prompt': 'x', 'max_new_tokens': 100000},
                              'limit_string': {'prompt': 'x', 'max_new_tokens': '4'},
                              'unknown_setting': {'prompt': 'x', 'temperature': 2.0},
                              'too_long': {'prompt': 'x ' * 3000}}.items():
            t0 = time.perf_counter()
            status, body = call(api.port, 'POST', '/generate', payload)
            negative[name] = {'status': status, 'client_seconds': time.perf_counter() - t0}
        log['bad_requests'] = negative
        row = golden[0]
        status, body = call(api.port, 'POST', '/generate', {'prompt': row['input']})
        log['real_response'] = {'prompt_id': row['id'], 'status': status, **body,
                                'saved_eval_raw': saved[row['id']]['raw'],
                                'matches_saved_eval': body.get('answer') == saved[row['id']]['raw']}
        log['protocol_limit'] = protocol['max_new_tokens']
    finally:
        api.stop()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'smoke.json').write_text(json.dumps(log, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(log, ensure_ascii=False, indent=2))
    if status != 200 or any(not 400 <= n['status'] < 500 for n in negative.values()) or log['ready_during_load'][0] != 503:
        sys.exit(1)


def replay(args):
    """Runtime API = runtime оценки: все сохранённые ответы воспроизводятся через HTTP."""
    golden, behavioral, _ = eval_rows(args.hw67)
    saved = {o['id']: o for o in json.loads((args.hw67 / 'metrics/baseline-outputs.json').read_text(encoding='utf-8'))}
    api = Api(args.workspace)
    rows = []
    try:
        api.wait_alive()
        ready = api.wait_ready()
        for row in golden + behavioral:
            status, body = call(api.port, 'POST', '/generate', {'prompt': row['input']})
            rows.append({'id': row['id'], 'status': status, 'answer': body.get('answer'), 'saved': saved[row['id']]['raw'],
                         'match': body.get('answer') == saved[row['id']]['raw'], 'output_tokens': body.get('output_tokens'),
                         'generation_seconds': body.get('generation_seconds'), 'model_id': body.get('model_id')})
            print(rows[-1]['id'], rows[-1]['match'], flush=True)
    finally:
        api.stop()
    summary = {'n': len(rows), 'matched': sum(r['match'] for r in rows), 'errors': sum(r['status'] != 200 for r in rows),
               'model_id': ready['model_id'], 'runtime_sha256': ready['runtime_sha256'], 'rows': rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'replay-baseline.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print({k: summary[k] for k in ('n', 'matched', 'errors')})
    check_adapter_loader(args)


def check_adapter_loader(args):
    """Загрузчик adapter для сценария смены версии. Кандидат отклонён и в API не выпускается."""
    sys.path.insert(0, str(ROOT))
    import backends
    import release
    manifest = json.loads((args.workspace / 'manifests/candidate.json').read_text(encoding='utf-8'))
    release.verify_artifact(manifest, args.workspace)
    backend = backends.TransformersBackend(manifest, args.workspace)
    golden, _, _ = eval_rows(args.hw67)
    saved = {o['id']: o for o in json.loads((args.hw67 / 'metrics/candidate-outputs.json').read_text(encoding='utf-8'))}
    rows = []
    for row in golden[:ADAPTER_CHECK_ROWS]:
        answer = backend.generate(row['input'], manifest['runtime']['generation']['max_new_tokens'])['answer']
        rows.append({'id': row['id'], 'answer': answer, 'saved': saved[row['id']]['raw'], 'match': answer == saved[row['id']]['raw']})
    result = {'model_id': manifest['model_id'], 'note': 'Проверка загрузчика, не выпуск: кандидат отклонён gate', 'rows': rows}
    (OUT / 'adapter-loader-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(result)


def run_batch(port, mode, concurrency, rows, limit, out):
    """Клиентская очередь заданий: параллельность = число потоков-клиентов."""
    pending = list(rows)
    lock = threading.Lock()
    records = []

    def worker():
        while True:
            with lock:
                if not pending:
                    return
                row = pending.pop(0)
            started_at = time.time()
            t0 = time.perf_counter()
            record = {'mode': mode, 'concurrency': concurrency, 'id': row['id'], 'started_at': started_at, 'status': None,
                      'error': None, 'client_seconds': None, 'queue_seconds': None, 'generation_seconds': None,
                      'output_tokens': None, 'model_id': None, 'request_id': None}
            try:
                status, body = call(port, 'POST', '/generate', {'prompt': row['input'], 'max_new_tokens': limit}, timeout=600)
                record['status'] = status
                if status == 200:
                    record.update({k: body[k] for k in ('queue_seconds', 'generation_seconds', 'output_tokens', 'model_id', 'request_id')})
                else:
                    record['error'] = body
            except Exception as exc:
                record['error'] = f'{type(exc).__name__}: {exc}'
            record['client_seconds'] = time.perf_counter() - t0
            with lock:
                records.append(record)
                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                out.flush()

    started = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return records, time.perf_counter() - started


def summarize(records, elapsed, concurrency):
    ok = [r for r in records if r['status'] == 200]
    failed = [r for r in records if r['status'] != 200]
    client = [r['client_seconds'] for r in ok]
    return {'concurrency': concurrency, 'n': len(records), 'success': len(ok), 'errors': len(failed),
            'batch_seconds': round(elapsed, 3), 'success_rps': round(len(ok) / elapsed, 4),
            'latency_mean_s': round(statistics.mean(client), 3) if client else None,
            'latency_max_s': round(max(client), 3) if client else None,
            'queue_mean_s': round(statistics.mean(r['queue_seconds'] for r in ok), 3) if ok else None,
            'generation_mean_s': round(statistics.mean(r['generation_seconds'] for r in ok), 3) if ok else None,
            'output_tokens_total': sum(r['output_tokens'] for r in ok),
            'error_latency_max_s': round(max(r['client_seconds'] for r in failed), 3) if failed else None}


def loadtest(args):
    golden, _, protocol = eval_rows(args.hw67)
    rows = golden[:args.requests]
    limit = protocol['max_new_tokens']
    OUT.mkdir(parents=True, exist_ok=True)
    api = Api(args.workspace)
    table = []
    try:
        api.wait_alive()
        api.wait_ready()
        # Прогрев завершён внутри сервиса; один запрос вне журнала выравнивает кеши CPU перед замером.
        call(api.port, 'POST', '/generate', {'prompt': rows[0]['input'], 'max_new_tokens': limit})
        with (OUT / 'load-attempts.jsonl').open('w', encoding='utf-8') as out:
            for concurrency in (1, 2):
                records, elapsed = run_batch(api.port, f'c{concurrency}', concurrency, rows, limit, out)
                table.append(summarize(records, elapsed, concurrency))
    finally:
        api.stop()
    with (OUT / 'load-summary.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print(json.dumps(table, ensure_ascii=False, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['smoke', 'replay', 'loadtest', 'adapter'])
    p.add_argument('--workspace', type=Path, default=ROOT / '.lab-real')
    p.add_argument('--hw67', type=Path, default=ROOT.parent / 'mlops26-hw67')
    p.add_argument('--requests', type=int, default=8)
    args = p.parse_args()
    args.workspace = args.workspace.resolve()
    args.hw67 = args.hw67.resolve()
    {'smoke': smoke, 'replay': replay, 'loadtest': loadtest, 'adapter': check_adapter_loader}[args.command](args)


if __name__ == '__main__':
    main()
