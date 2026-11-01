"""Журнал опытов на STUB-стенде для отчёта: команда, код завершения, состояния задач, SHA-256.

    python scripts/run_experiments.py   # пишет evidence/experiments.log

STUB проверяет управление процессом и не является оценкой модели.
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integration_tests.harness import FAULT_LEDGER, FAULT_REPLACE  # noqa: E402

ENV = {**os.environ, 'PYTHONUTF8': '1', 'PREFECT_LOGGING_LEVEL': 'WARNING', 'PREFECT_SERVER_ANALYTICS_ENABLED': 'false',
       'PREFECT_SERVER_UI_ENABLED': 'false'}
LINES = []


def say(text=''):
    print(text, flush=True)
    LINES.append(text)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lab(root, *args, fault=None):
    command = [sys.executable, *(['-c', fault] if fault else ['lab.py']), '--workspace', str(root), *args]
    done = subprocess.run(command, cwd=ROOT, env=ENV, capture_output=True, text=True, encoding='utf-8', errors='replace')
    keep = [x for x in done.stdout.splitlines() if x.startswith(('PREPARED', 'CACHE_HIT', 'PUBLISHED', 'ALREADY', 'RECOVERED', 'ROLLED'))]
    errors = [x for x in done.stderr.splitlines() if x.startswith(('REJECTED', 'RuntimeError', 'OSError'))]
    say(f'$ python lab.py --workspace <ws> {" ".join(args)}{"  [инъекция сбоя в subprocess]" if fault else ""}')
    say(f'  exit={done.returncode}  ' + ' | '.join(x[:150] for x in keep + errors[-1:]))
    pointer = root / 'accepted.json'
    if pointer.exists():
        say(f'  accepted.json: {json.loads(pointer.read_text(encoding="utf-8"))["model_id"]} sha256={sha(pointer)[:16]}')
    attempts = root / 'attempts.jsonl'
    if attempts.exists() and args[0] == 'run':
        last = json.loads(attempts.read_text(encoding='utf-8').splitlines()[-1])
        say(f'  попытка {last["attempt_id"][:8]}: outcome={last["outcome"]} tasks={last["tasks"]}')
    return done


def events(root):
    path = root / 'publications.jsonl'
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
    return [(r['seq'], r['kind'], r['model_id'], 'recovered' if r['recovered'] else '') for r in rows]


def experiment(title):
    say()
    say(f'===== {title}')


def get(port, method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def with_service(root, warmup, action):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, 'service.py', '--workspace', str(root), '--port', str(port), '--warmup-seconds', str(warmup)],
                            cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            try:
                get(port, 'GET', '/health')
                break
            except OSError:
                time.sleep(.05)
        return action(port)
    finally:
        proc.terminate()
        proc.wait(timeout=30)


def wait_ready(port):
    for _ in range(600):
        if get(port, 'GET', '/ready')[0] == 200:
            return
        time.sleep(.05)
    raise SystemExit('Сервис не стал готов')


def served(port):
    wait_ready(port)
    body = get(port, 'POST', '/generate', {'prompt': 'q'})[1]
    return body['model_id'], body['answer'], body['runtime_sha256'][:12]


def main():
    home = tempfile.mkdtemp(prefix='hw8910-exp-')
    ENV['PREFECT_HOME'] = str(Path(home) / 'prefect')
    try:
        say('STUB-опыты: управление процессом; PASS в фикстурах не является оценкой модели.')

        experiment('1. Сбой после подготовки, восстановление в новом процессе, изменение входа')
        root = Path(home) / 'recovery'
        lab(root, 'init')
        lab(root, 'run', '--fail-after-prepare')
        lab(root, 'run')
        (root / 'input.txt').write_text('Новые байты по прежнему пути\n', encoding='utf-8')
        say('# изменены байты input.txt по прежнему пути')
        lab(root, 'run')
        config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        config['max_length'] = 256
        (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        say('# изменён max_length')
        lab(root, 'run')
        say(f'publications: {events(root)}')

        experiment('2. Отрицательные проверки: указатель побайтно неизменен')
        root = Path(home) / 'reject'
        lab(root, 'init')
        report = json.loads((root / 'report-B.json').read_text(encoding='utf-8'))
        report['metrics']['accuracy'] = float('nan')
        (root / 'report-nan.json').write_text(json.dumps(report), encoding='utf-8')
        before = sha(root / 'accepted.json')
        for name in ('report-incomplete.json', 'report-A.json', 'report-reject.json', 'report-nan.json'):
            lab(root, 'run', '--report', name)
        lab(root, 'run', fault=FAULT_REPLACE)
        say(f'SHA-256 до:    {before}')
        say(f'SHA-256 после: {sha(root / "accepted.json")}')
        say(f'publications: {events(root)}')
        say(f'сохранено отказов: {len(list((root / "rejections").glob("*.json")))}')

        experiment('3. Повтор выпуска и сбой между указателем и журналом')
        root = Path(home) / 'crash'
        lab(root, 'init')
        lab(root, 'run', fault=FAULT_LEDGER)
        say(f'publications после сбоя: {events(root)}')
        lab(root, 'run')
        say(f'publications после восстановления: {events(root)}')
        lab(root, 'run')
        say(f'publications после ещё одного повтора: {events(root)}')
        say(f'попыток в attempts.jsonl: {len((root / "attempts.jsonl").read_text(encoding="utf-8").splitlines())}')

        experiment('4. Готовность: прогрев 5 с')
        root = Path(home) / 'ready'
        lab(root, 'init')

        def ready_flow(port):
            say(f'/health {get(port, "GET", "/health")}')
            say(f'/ready  {get(port, "GET", "/ready")}')
            say(f'/generate до готовности: {get(port, "POST", "/generate", {"prompt": "q"})[0]}')
            say(f'/debug {get(port, "GET", "/debug")[1]}')
            wait_ready(port)
            say(f'/ready после прогрева: {get(port, "GET", "/ready")[0]}')
            say(f'/generate после: {get(port, "POST", "/generate", {"prompt": "q"})[0]}')
            for payload in ({'prompt': ''}, {'prompt': 'x', 'max_new_tokens': 0}, {'prompt': 'x', 'max_new_tokens': 100000},
                            {'prompt': 'x', 'max_new_tokens': '4'}):
                say(f'{json.dumps(payload)} -> {get(port, "POST", "/generate", payload)[0]}')
            say(f'/debug {get(port, "GET", "/debug")[1]}')
        with_service(root, 5, ready_flow)

        experiment('5. A → B → перезапуск → откат A → перезапуск')
        root = Path(home) / 'versions'
        lab(root, 'init')

        def publish_under_load(port):
            say(f'работает на: {served(port)}')
            lab(root, 'run')
            say(f'после публикации B, без перезапуска: {served(port)}')
        with_service(root, 0, publish_under_load)
        say(f'после перезапуска: {with_service(root, 0, served)}')
        lab(root, 'rollback', '--to', 'fixture-A')
        say(f'после отката и перезапуска: {with_service(root, 0, served)}')
        say(f'publications: {events(root)}')
    finally:
        shutil.rmtree(home, ignore_errors=True)
    path = ROOT / 'evidence' / 'experiments.log'
    path.parent.mkdir(exist_ok=True)
    path.write_text('\n'.join(LINES) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
