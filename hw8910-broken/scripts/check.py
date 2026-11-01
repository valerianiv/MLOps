"""Локальный эквивалент CI: быстрые проверки без весов, torch и сети.

    python scripts/check.py                      # код завершения 0 только при успехе
    python scripts/check.py --demo-break         # намеренная поломка в копии: ожидается не 0

Этапы: компиляция → публичные 13 тестов → собственные интеграционные тесты.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['backends.py', 'flow.py', 'hw67_bridge.py', 'lab.py', 'release.py', 'service.py', 'sources.py']
BREAK_FILE, BREAK_FROM, BREAK_TO = 'release.py', "if report.get('passed') is not True:", "if not report.get('passed'):"


def stages(root):
    python = sys.executable
    yield 'compile', [python, '-m', 'compileall', '-q', *SOURCES, 'tests', 'integration_tests', 'scripts']
    yield 'public-tests', [python, '-m', 'unittest', 'discover', '-s', 'tests', '-v']
    yield 'integration-tests', [python, '-m', 'unittest', 'discover', '-s', 'integration_tests', '-t', '.', '-v']


def check(root):
    env = {**os.environ, 'HW_TARGET_DIR': str(root), 'PYTHONUTF8': '1'}
    for name, command in stages(root):
        print(f'=== {name}: {" ".join(command[1:])}', flush=True)
        code = subprocess.run(command, cwd=root, env=env).returncode
        print(f'=== {name}: exit {code}', flush=True)
        if code:
            return code
    return 0


def demo_break():
    """Рабочее дерево не меняется: поломка живёт только во временной копии."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        for name in SOURCES:
            shutil.copy(ROOT / name, tmp)
        for name in ('tests', 'integration_tests', 'scripts'):
            shutil.copytree(ROOT / name, tmp / name, ignore=shutil.ignore_patterns('__pycache__'))
        path = tmp / BREAK_FILE
        source = path.read_text(encoding='utf-8')
        if source.count(BREAK_FROM) != 1:
            raise SystemExit('Шаблон поломки не найден')
        path.write_text(source.replace(BREAK_FROM, BREAK_TO), encoding='utf-8')
        print(f'Намеренная поломка в копии: {BREAK_FILE}: {BREAK_FROM} -> {BREAK_TO}', flush=True)
        return check(tmp)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--demo-break', action='store_true')
    args = p.parse_args()
    sys.exit(demo_break() if args.demo_break else check(ROOT))


if __name__ == '__main__':
    main()
