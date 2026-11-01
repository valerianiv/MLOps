"""Prefect-flow выпуска. Между задачами идут описания: пути, версии и хеши, а не веса.

подготовка → получение/проверка артефакта → допуск → публикация
"""
import json
import time
from pathlib import Path

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run

import release
import sources


def _input_key(context, parameters):
    return release.cache_key(Path(parameters['data_path']), parameters['config'])


def _is_transient(task_, task_run, state):
    """Повторяем только сетевые/файловые сбои чтения; ValueError — окончательный отказ."""
    try:
        state.result()
    except OSError:
        return True
    except Exception:
        return False
    return False


@task(name='prepare-input', cache_key_fn=_input_key, persist_result=True, result_serializer='json')
def prepare_input(spec: dict, data_path: str, config: dict) -> dict:
    get_run_logger().info('Подготовка выполняется: %s', Path(data_path).name)
    return sources.load(spec).prepare(Path(data_path), config)


@task(name='fetch-artifact', retries=2, retry_delay_seconds=1, retry_condition_fn=_is_transient)
def fetch_artifact(spec: dict, manifest_ref: str) -> dict:
    root = Path(spec['root'])
    path = sources.load(spec).fetch(manifest_ref)
    manifest = sources.read_json(path)
    release.verify_artifact(manifest, root)
    get_run_logger().info('Файлы артефакта проверены: %s', manifest['model_id'])
    return {'manifest_path': str(path), 'model_id': manifest['model_id'], 'artifact_uri': manifest['artifact_uri'],
            'model_sha256': manifest['model_sha256'], 'runtime_sha256': manifest['runtime_sha256']}


@task(name='evaluate-gate')
def evaluate_gate(spec: dict, artifact: dict, report_ref: str, attempt_id: str) -> dict:
    """Сохраняет release-report.json при любом решении и только затем проверяет контракт."""
    root = Path(spec['root'])
    manifest = sources.read_json(artifact['manifest_path'])
    report = sources.load(spec).gate(manifest, report_ref)
    sources.write_json(root / 'release-report.json', report)
    try:
        release.validate_release(manifest, report)
    except ValueError as exc:
        sources.write_json(root / 'rejections' / f'{attempt_id}.json', {'reason': str(exc), 'report': report})
        reason = report.get('reason') if isinstance(report, dict) else None
        raise ValueError(f'{exc}: {reason}' if reason else str(exc)) from exc
    return {'report_path': str(root / 'release-report.json'), 'passed': True,
            'evaluation_run_id': report['evaluation_run_id'], 'release_id': release.release_id(manifest, report)}


@task(name='publish-release')
def publish_release(spec: dict, artifact: dict, decision: dict, repromote: bool = False) -> dict:
    root = Path(spec['root'])
    manifest = sources.read_json(artifact['manifest_path'])
    report = sources.read_json(decision['report_path'])
    outcome = release.commit_release(root, manifest, report, repromote=repromote)
    return {'outcome': outcome, 'release_id': decision['release_id'], 'model_id': artifact['model_id']}


def _pointer_sha(root):
    path = root / release.POINTER
    return release.file_sha256(path) if path.exists() else None


def _log_attempt(root, entry):
    """Журнал попыток отделён от publications.jsonl: повторы и отказы видны здесь."""
    with (root / 'attempts.jsonl').open('a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')


@flow(name='model-release')
def release_flow(spec: dict, manifest_ref: str, report_ref: str | None = None,
                 fail_after_prepare: bool = False, repromote: bool = False) -> dict:
    root = Path(spec['root'])
    source = sources.load(spec)
    attempt_id = flow_run.id
    entry = {'attempt_id': attempt_id, 'started_at': time.time(), 'manifest': manifest_ref, 'report': report_ref,
             'accepted_sha256_before': _pointer_sha(root), 'tasks': {}, 'outcome': 'failed'}
    try:
        data_path, config = source.prepare_inputs()
        prepared_state = prepare_input(spec, str(data_path), config, return_state=True)
        prepared = prepared_state.result()
        cached = prepared_state.name == 'Cached'
        entry['tasks']['prepare-input'] = prepared_state.name
        entry['input_sha256'] = prepared['input_sha256']
        print('CACHE_HIT' if cached else 'PREPARED', prepared['input_sha256'], flush=True)
        if fail_after_prepare:
            raise RuntimeError('Инъекция сбоя после подготовки')
        artifact = fetch_artifact(spec, manifest_ref)
        entry['tasks']['fetch-artifact'] = 'Completed'
        entry['model_id'] = artifact['model_id']
        decision = evaluate_gate(spec, artifact, report_ref, attempt_id)
        entry['tasks']['evaluate-gate'] = 'Completed'
        entry['release_id'] = decision['release_id']
        published = publish_release(spec, artifact, decision, repromote)
        entry['tasks']['publish-release'] = 'Completed'
        entry['outcome'] = published['outcome']
        return {**published, 'attempt_id': attempt_id, 'cached': cached}
    except ValueError as exc:
        entry.update(outcome='rejected', error=str(exc))
        raise
    except Exception as exc:
        entry['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        entry.update(finished_at=time.time(), accepted_sha256_after=_pointer_sha(root))
        _log_attempt(root, entry)
