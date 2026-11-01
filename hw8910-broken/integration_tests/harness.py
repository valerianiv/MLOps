"""Единый адаптер запуска для двух версий проекта.

HW_TARGET_DIR указывает на проверяемую версию: по умолчанию текущую, для красного
прогона — на распакованный исходный коммит. Тесты общаются с проектом только через
CLI lab.py/service.py, настоящие процессы, файловую систему и HTTP.
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

TARGET = Path(os.environ.get('HW_TARGET_DIR', Path(__file__).resolve().parents[1])).resolve()
# Одна база Prefect на весь прогон: схема создаётся один раз, результаты остаются в workspace.
_prefect_home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
ENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1',
       'PREFECT_HOME': _prefect_home.name, 'PREFECT_LOGGING_LEVEL': 'WARNING',
       'PREFECT_SERVER_ANALYTICS_ENABLED': 'false', 'PREFECT_SERVER_UI_ENABLED': 'false'}

# Сбой внутри процесса выпуска: настоящий subprocess, подменена одна системная операция.
FAULT_LEDGER = """
import pathlib, sys
original = pathlib.Path.open
def failing_open(self, mode='r', *args, **kwargs):
    if self.name == 'publications.jsonl' and 'a' in mode:
        raise OSError('Инъекция сбоя между указателем и журналом')
    return original(self, mode, *args, **kwargs)
pathlib.Path.open = failing_open
import lab
lab.main()
"""
FAULT_REPLACE = """
import os, pathlib, sys
original = os.replace
def failing_replace(src, dst, *args, **kwargs):
    # Именно указатель: архивная копия releases/<id>/accepted.json идёт другим шагом.
    if pathlib.PurePath(dst).name == 'accepted.json' and 'releases' not in pathlib.PurePath(dst).parts:
        raise OSError('Инъекция ошибки диска при замене')
    return original(src, dst, *args, **kwargs)
os.replace = failing_replace
import lab
lab.main()
"""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def request(port, method, path, payload=None, raw=None):
    data = raw if raw is not None else (None if payload is None else json.dumps(payload).encode())
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class Service:
    def __init__(self, workspace, warmup=0.0):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, 'service.py', '--workspace', str(workspace), '--port', str(self.port),
             '--warmup-seconds', str(warmup)], cwd=TARGET, env=ENV,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def __enter__(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f'Сервис завершился с кодом {self.proc.returncode}')
            try:
                if request(self.port, 'GET', '/health')[0] == 200:
                    return self
            except OSError:
                time.sleep(.05)
        raise AssertionError('Сервис не начал отвечать на /health')

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=30)

    def get(self, path):
        return request(self.port, 'GET', path)

    def post(self, payload=None, raw=None):
        return request(self.port, 'POST', '/generate', payload, raw)

    def wait_ready(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.get('/ready')[0] == 200:
                return
            time.sleep(.05)
        raise AssertionError('Сервис не стал готов')


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / 'lab'
        self.init(self.root)
        self.pointer = self.root / 'accepted.json'

    def init(self, root):
        done = self.lab('init', workspace=root)
        self.assertEqual(done.returncode, 0, done.stderr)

    def lab(self, *args, workspace=None, fault=None):
        workspace = workspace or self.root
        command = ['-c', fault] if fault else ['lab.py']
        return subprocess.run([sys.executable, *command, '--workspace', str(workspace), *args], cwd=TARGET,
                              env=ENV, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)

    def accepted_id(self):
        return json.loads(self.pointer.read_text(encoding='utf-8'))['model_id']

    def events(self, model_id):
        path = self.root / 'publications.jsonl'
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        return [row for row in rows if row['model_id'] == model_id]
