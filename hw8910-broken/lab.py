"""CLI процесса выпуска: синтетический стенд и реальный выпуск из ДЗ 6+7.

init создаёт стенд STUB ONLY. run запускает Prefect-flow в отдельном процессе:
для опытов берите новый --workspace. Команды прототипа сохранены для общих тестов.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import release


def canonical(value):
    return release.canonical(value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def initialize(root):
    if root.exists():
        raise FileExistsError('Рабочий каталог уже существует. Выберите новый --workspace.')
    root.mkdir(parents=True)
    runtime = {'base_revision': 'fixture-only', 'tokenizer_sha256': 'a' * 64,
               'template_sha256': 'b' * 64, 'generation': {'max_new_tokens': 32, 'do_sample': False}, 'dtype': 'float32'}
    for version in ['A', 'B']:
        artifact = root / f'model-{version}.txt'
        artifact.write_text(f'STUB ONLY — version {version}\n', encoding='utf-8')
        files = {artifact.name: digest(artifact.read_bytes())}
        m = {'schema_version': 1, 'model_id': f'fixture-{version}',
             'artifact_uri': artifact.name, 'model_sha256': digest(canonical(files)),
             'protocol_sha256': 'd' * 64, 'golden_sha256': 'e' * 64,
             'runtime_sha256': digest(canonical(runtime)), 'runtime': runtime, 'file_hashes': files}
        report = {k: m[k] for k in ['model_id', 'model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256']}
        report.update(status='complete', passed=True, evaluation_run_id=f'fixture-eval-{version}',
                      metrics={'accuracy': .8, 'format_rate': 1.0}, synthetic=True)
        write(root / f'manifest-{version}.json', m)
        write(root / f'report-{version}.json', report)
    release.commit_release(root, read(root / 'manifest-A.json'), read(root / 'report-A.json'))
    report = read(root / 'report-B.json')
    report['status'] = 'running'
    report['passed'] = 'false'
    write(root / 'report-incomplete.json', report)
    report = read(root / 'report-B.json')
    report['passed'] = False
    write(root / 'report-reject.json', report)
    write(root / 'config.json', {'tokenizer_revision': 'r1', 'template_sha256': 'b' * 64, 'max_length': 128, 'code_revision': 'prepare-v1'})
    (root / 'input.txt').write_text('Первый пример\nВторой пример\n', encoding='utf-8')
    print('Создан синтетический стенд:', root)


def run(root, args):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Состояние Prefect и сохранённые результаты живут в рабочем каталоге и переживают процесс.
    os.environ.setdefault('PREFECT_HOME', str(root / '.prefect'))
    os.environ['PREFECT_LOCAL_STORAGE_PATH'] = str(root / 'prefect-results')
    os.environ.setdefault('PREFECT_LOGGING_LEVEL', 'INFO')
    from flow import release_flow
    if args.hw67:
        spec = {'kind': 'hw67', 'root': str(root), 'hw67': str(args.hw67.resolve())}
        manifest_ref, report_ref = args.role, args.role
    else:
        spec = {'kind': 'stub', 'root': str(root)}
        manifest_ref, report_ref = args.manifest, args.report
    result = release_flow(spec, manifest_ref, report_ref, args.fail_after_prepare, args.repromote)
    label = {'published': 'PUBLISHED', 'noop': 'ALREADY_PUBLISHED', 'recovered': 'RECOVERED'}[result['outcome']]
    print(label, result['model_id'], 'release_id=' + result['release_id'], 'attempt=' + result['attempt_id'], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, default=Path('.lab'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    run_parser = sub.add_parser('run')
    run_parser.add_argument('--report', default='report-B.json')
    run_parser.add_argument('--manifest', default='manifest-B.json')
    run_parser.add_argument('--fail-after-prepare', action='store_true')
    run_parser.add_argument('--repromote', action='store_true', help='намеренно вернуть ранее заменённый выпуск')
    run_parser.add_argument('--hw67', type=Path, help='каталог mlops26-hw67: реальный выпуск вместо стенда')
    run_parser.add_argument('--role', choices=['baseline', 'candidate'], default='candidate')
    back = sub.add_parser('rollback')
    back.add_argument('--to', required=True, help='model_id ранее принятой версии')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(args.workspace)
        elif args.command == 'rollback':
            outcome = release.rollback(args.workspace, args.to)
            print('ROLLED_BACK' if outcome == 'published' else 'ALREADY_PUBLISHED', args.to, flush=True)
        else:
            run(args.workspace, args)
    except ValueError as exc:
        sys.exit(f'REJECTED: {exc}')


if __name__ == '__main__':
    main()
