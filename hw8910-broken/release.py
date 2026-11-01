"""Ядро выпуска: ключ кеша, контракт отчёта, атомарная публикация и журнал.

Только стандартная библиотека: быстрые тесты не требуют Prefect, весов и сети.
"""
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

SHA256 = re.compile(r'[0-9a-f]{64}')
BOUND_HASHES = ('model_sha256', 'protocol_sha256', 'golden_sha256', 'runtime_sha256')
REQUIRED_METRICS = ('accuracy', 'format_rate')
POINTER = 'accepted.json'
LEDGER = 'publications.jsonl'


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def cache_key(data_path: Path, config: dict) -> str:
    return digest(canonical({'data_sha256': file_sha256(data_path), 'config': config}))


def _require_text(value, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name}: нужна непустая строка')


def _require_sha(value, name: str) -> None:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f'{name}: нужен SHA-256 в нижнем регистре')


def validate_release(manifest: dict, report: dict) -> None:
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError('manifest и report должны быть объектами')
    version = manifest.get('schema_version')
    if type(version) is not int or version != 1:
        raise ValueError('schema_version: нужно целое 1')
    _require_text(manifest.get('model_id'), 'model_id')
    _require_text(manifest.get('artifact_uri'), 'artifact_uri')
    for name in BOUND_HASHES:
        _require_sha(manifest.get(name), f'manifest.{name}')
    runtime = manifest.get('runtime')
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError('runtime: нужен непустой объект')
    if digest(canonical(runtime)) != manifest['runtime_sha256']:
        raise ValueError('runtime_sha256 не соответствует runtime: нужна новая оценка')

    if report.get('status') != 'complete':
        raise ValueError(f'Отчёт неполный: status={report.get("status")!r}')
    if report.get('passed') is not True:
        raise ValueError(f'Кандидат отклонён: passed={report.get("passed")!r}')
    _require_text(report.get('evaluation_run_id'), 'evaluation_run_id')
    for name in ('model_id', *BOUND_HASHES):
        if report.get(name) != manifest[name]:
            raise ValueError(f'Отчёт относится к другой версии: различается {name}')
    metrics = report.get('metrics')
    if not isinstance(metrics, dict):
        raise ValueError('metrics: нужен объект')
    for name in REQUIRED_METRICS:
        value = metrics.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f'metrics.{name}: нужно конечное число от 0 до 1, получено {value!r}')


def publish(manifest: dict, report: dict, target: Path) -> None:
    validate_release(manifest, report)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=target.name + '.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def artifact_root(manifest: dict, root: Path) -> Path:
    """Каталог файлов артефакта. Веса вне Git: кеш HF или локальная копия run."""
    uri = manifest['artifact_uri']
    if uri.startswith('hf://'):
        name, revision = uri.removeprefix('hf://').rsplit('@', 1)
        home = Path(os.environ.get('HF_HOME', Path.home() / '.cache' / 'huggingface'))
        return home / 'hub' / ('models--' + name.replace('/', '--')) / 'snapshots' / revision
    return Path(root) / manifest.get('artifact_dir', '.')


def verify_artifact(manifest: dict, root: Path) -> None:
    """Пересчёт хешей реальных файлов перед выпуском и перед загрузкой."""
    expected = manifest.get('file_hashes')
    if not isinstance(expected, dict) or not expected:
        raise ValueError('manifest без закреплённого списка файлов file_hashes')
    base = artifact_root(manifest, root)
    actual = {}
    for name, sha in expected.items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError(f'Недопустимый путь файла артефакта: {name}')
        try:
            actual[name] = file_sha256(base / path)
        except FileNotFoundError as exc:
            raise ValueError(f'Нет файла артефакта: {name}') from exc
        if actual[name] != sha:
            raise ValueError(f'Хеш файла не совпал: {name}')
    if digest(canonical(actual)) != manifest['model_sha256']:
        raise ValueError('model_sha256 не соответствует списку файлов')


def release_id(manifest: dict, report: dict) -> str:
    """Идентичность выпуска: модель, runtime и связанное решение, а не только model_id."""
    identity = {name: manifest[name] for name in ('model_id', *BOUND_HASHES)}
    identity.update(evaluation_run_id=report['evaluation_run_id'], report_sha256=digest(canonical(report)))
    return digest(canonical(identity))


def accepted_document(manifest: dict, report: dict) -> dict:
    return {**{k: v for k, v in manifest.items() if k != 'release'},
            'release': {'release_id': release_id(manifest, report),
                        'evaluation_run_id': report['evaluation_run_id'],
                        'report_sha256': digest(canonical(report))}}


def read_pointer(root: Path) -> dict | None:
    path = Path(root) / POINTER
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def _pointer_id(document: dict | None) -> str | None:
    return (document or {}).get('release', {}).get('release_id')


def read_events(root: Path) -> list:
    """События фактического выпуска. Хвост без \\n — оборванная запись, не событие."""
    path = Path(root) / LEDGER
    if not path.exists():
        return []
    data = path.read_bytes()
    return [json.loads(line) for line in data[:data.rfind(b'\n') + 1].splitlines()]


def _append_event(root: Path, document: dict, kind: str, previous: str | None, recovered: bool = False) -> None:
    path = Path(root) / LEDGER
    if path.exists():
        data = path.read_bytes()
        if not data.endswith(b'\n'):
            os.truncate(path, data.rfind(b'\n') + 1)
    event = {'seq': len(read_events(root)) + 1, 'kind': kind, 'recovered': recovered,
             'release_id': _pointer_id(document), 'previous_release_id': previous,
             'model_id': document['model_id'], 'runtime_sha256': document['runtime_sha256'],
             'evaluation_run_id': document['release']['evaluation_run_id']}
    with path.open('a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n')
        f.flush()
        os.fsync(f.fileno())


def _reconcile(root: Path) -> bool:
    """Указатель обновлён, а журнал нет (сбой между шагами): дописать одно событие."""
    current = read_pointer(root)
    current_id = _pointer_id(current)
    events = read_events(root)
    last_id = events[-1]['release_id'] if events else None
    if current_id is None or current_id == last_id:
        return False
    _append_event(root, current, 'publish', last_id, recovered=True)
    return True


def commit_release(root: Path, manifest: dict, report: dict, kind: str = 'publish', repromote: bool = False) -> str:
    """Один писатель. Порядок: архив → указатель → журнал; повтор доводит выпуск до конца.

    Возвращает published, noop (уже выпущено) или recovered (дописан пропавший журнал).
    """
    root = Path(root)
    validate_release(manifest, report)
    document = accepted_document(manifest, report)
    new_id = document['release']['release_id']
    recovered = _reconcile(root)
    events = read_events(root)
    current_id = _pointer_id(read_pointer(root))
    if current_id == new_id:
        return 'recovered' if recovered else 'noop'
    if kind == 'publish' and not repromote and any(e['release_id'] == new_id for e in events):
        raise ValueError(f'Выпуск {document["model_id"]} уже заменён или откатан; повтор процесса его не вернёт. '
                         'Намеренный возврат: rollback или --repromote')
    archive = root / 'releases' / new_id
    archive.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(archive / 'report.json', report)
    _write_json_atomic(archive / POINTER, document)
    publish(document, report, root / POINTER)
    _append_event(root, document, kind, events[-1]['release_id'] if events else None)
    return 'published'


def rollback(root: Path, model_id: str) -> str:
    """Возврат к ранее принятой версии: её файлы и решение проверяются заново."""
    root = Path(root)
    _reconcile(root)
    current_id = _pointer_id(read_pointer(root))
    for event in reversed(read_events(root)):
        if event['model_id'] == model_id:
            archive = root / 'releases' / event['release_id']
            document = json.loads((archive / POINTER).read_text(encoding='utf-8'))
            report = json.loads((archive / 'report.json').read_text(encoding='utf-8'))
            verify_artifact(document, root)
            if event['release_id'] == current_id:
                return 'noop'
            return commit_release(root, document, report, kind='rollback')
    raise ValueError(f'{model_id} не выпускалась: откат возможен только к принятой версии')


def check_accepted(document: dict, root: Path) -> None:
    """Проверка сервисом при загрузке: решение, связь версий и сами файлы."""
    release = document.get('release') or {}
    if not isinstance(release.get('release_id'), str):
        raise ValueError('accepted.json создан не процессом выпуска')
    path = Path(root) / 'releases' / release['release_id'] / 'report.json'
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ValueError('Нет отчёта, связанного с принятой версией') from exc
    validate_release(document, report)
    if release_id(document, report) != release['release_id']:
        raise ValueError('accepted.json не соответствует сохранённому решению')
    verify_artifact(document, root)


def _write_json_atomic(path: Path, value) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode('utf-8') + b'\n'
    tmp = path.with_name(path.name + f'.{os.getpid()}.part')
    tmp.write_bytes(data)
    os.replace(tmp, path)
