"""Доказательства тестов: красный прогон на исходном коммите и зелёный на текущем.

Плюс отдельные мутации: в копию исправленного кода по одному возвращается каждый
из семи дефектов, и должен упасть его регрессионный тест. Рабочее дерево не меняется.

    python scripts/red_green.py [--skip-red] [--skip-mutants]
"""
import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence'
ORIGINAL_COMMIT = '72a7c67'
PROJECT_FILES = ['backends.py', 'flow.py', 'hw67_bridge.py', 'lab.py', 'release.py', 'service.py', 'sources.py']

# Дефект → (файл, что заменить, на что, какой тест обязан упасть).
MUTANTS = {
    1: ('release.py', "return digest(canonical({'data_sha256': file_sha256(data_path), 'config': config}))",
        'return digest(str(data_path).encode())',
        'test_process.ProcessTests.test_cli_recovery_uses_cache_and_input_change_invalidates'),
    2: ('release.py', "if report.get('passed') is not True:", "if not report.get('passed'):",
        'test_process.ProcessTests.test_cli_rejected_reports_keep_pointer_bytes'),
    3: ('release.py', '        os.replace(tmp, target)\n',
        "        Path(tmp).unlink()\n        target.write_bytes(data)\n        os.replace(target, target)\n",
        'test_process.ProcessTests.test_cli_disk_error_keeps_pointer_bytes'),
    4: ('release.py', "        return 'recovered' if recovered else 'noop'\n",
        "        _append_event(root, document, kind, current_id)\n        return 'published'\n",
        'test_process.ProcessTests.test_cli_repeat_release_single_event'),
    5: ('service.py', 'if state.ready.is_set():\n            return {\'ready\': True,',
        "if state.backend:\n            return {'ready': True,",
        'test_http.HttpTests.test_http_ready_and_generate_wait_for_warmup'),
    6: ('service.py', "'model_id': backend.loaded_id,\n",
        "'model_id': json.loads((state.root / release.POINTER).read_text(encoding='utf-8'))['model_id'],\n",
        'test_http.HttpTests.test_http_serves_loaded_version_until_restart_then_rollback'),
    7: ('service.py', 'if limit > state.limit:', 'if False:',
        'test_http.HttpTests.test_http_bad_requests_never_reach_backend'),
}


def run_suite(target, label, *selected):
    env = {**os.environ, 'HW_TARGET_DIR': str(target), 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    names = list(selected) and [f'integration_tests.{name}' for name in selected]
    command = [sys.executable, '-m', 'unittest', *names, '-v'] if names else \
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'integration_tests', '-t', '.', '-v']
    started = time.monotonic()
    done = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(f'{label}: exit={done.returncode}, {time.monotonic() - started:.0f} c', flush=True)
    return done


def verdicts(output):
    """Имя теста → ok/FAIL/ERROR по финальным заголовкам и строкам unittest -v."""
    result = {}
    for name, status in re.findall(r'^(test_\w+) \(.*?\)(?:\n.*?)?\.\.\. (ok|FAIL|ERROR)$', output, re.M):
        result[name] = status
    for status, name in re.findall(r'^(FAIL|ERROR): (test_\w+)', output, re.M):
        if result.get(name) != 'ERROR':
            result[name] = 'ERROR' if status == 'ERROR' else 'FAIL'
    return result


def public_tests(target):
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=target, env=env,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def extract_original(tmp):
    archive = subprocess.run(['git', 'archive', '--format=tar', ORIGINAL_COMMIT, '.'], cwd=ROOT, capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(tmp, filter='data')
    return Path(tmp)


def write_log(name, done, header):
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / name).write_text(f'# {header}\n# exit code: {done.returncode}\n\n{done.stdout}\n{done.stderr}', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-red', action='store_true')
    parser.add_argument('--skip-mutants', action='store_true')
    args = parser.parse_args()
    summary_path = EVIDENCE / 'red-green-summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {}

    green = run_suite(ROOT, 'green')
    write_log('integration-green.log', green, 'Интеграционные тесты, исправленная версия')
    write_log('public-green.log', public_tests(ROOT), 'Публичные 13 тестов, исправленная версия')
    summary['green'] = verdicts(green.stderr)
    failed = green.returncode != 0

    if not args.skip_red:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            original = extract_original(tmp)
            red = run_suite(original, 'red')
            write_log('integration-red.log', red, f'Те же интеграционные тесты, исходный коммит {ORIGINAL_COMMIT}')
            write_log('public-red.log', public_tests(original), f'Публичные 13 тестов, исходный коммит {ORIGINAL_COMMIT}')
        summary['red'] = verdicts(red.stderr)
        not_failed = [k for k, v in summary['red'].items() if v != 'FAIL']
        if not_failed or red.returncode == 0:
            print('Не FAIL на исходной версии:', not_failed)
            failed = True

    if not args.skip_mutants:
        summary['mutants'] = {}
        log = []
        for number, (name, old, new, test) in MUTANTS.items():
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                for file in PROJECT_FILES:
                    shutil.copy(ROOT / file, tmp)
                path = Path(tmp) / name
                source = path.read_text(encoding='utf-8')
                if source.count(old) != 1:
                    raise SystemExit(f'Мутация {number}: шаблон найден {source.count(old)} раз')
                path.write_text(source.replace(old, new), encoding='utf-8')
                done = run_suite(tmp, f'mutant-{number}', test)
            status = verdicts(done.stderr).get(test.rsplit('.', 1)[1])
            summary['mutants'][str(number)] = {'test': test, 'exit_code': done.returncode, 'status': status}
            log.append(f'===== Дефект {number}: {name}: {test} exit={done.returncode}\n{done.stderr}\n')
            if done.returncode == 0 or status != 'FAIL':
                print(f'Мутация {number} не обнаружена как FAIL: {status}')
                failed = True
        (EVIDENCE / 'mutants.log').write_text('\n'.join(log), encoding='utf-8')

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
