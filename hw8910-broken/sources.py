"""Откуда flow берёт вход, артефакт и решение: синтетический стенд или ДЗ 6+7.

Stub — инженерная проверка управления, не оценка. Hw67 читает готовые runs и отчёты:
чтение результата обучения не является повторным обучением, sweep не запускается.
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import release

BRIDGE = Path(__file__).with_name('hw67_bridge.py')
BASE_FILES = ('config.json', 'generation_config.json', 'merges.txt', 'model.safetensors',
              'tokenizer.json', 'tokenizer_config.json', 'vocab.json')
TOKENIZER_FILES = ('merges.txt', 'tokenizer.json', 'tokenizer_config.json', 'vocab.json')
MAX_PREPARED_TOKENS = 1024
# Хеши файлов закреплены отдельно от манифеста: иначе проверка сверяла бы файлы сами с собой.
PINS = Path(__file__).with_name('model-pins.json')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


class StubSource:
    def __init__(self, spec):
        self.root = Path(spec['root'])

    def prepare_inputs(self):
        return self.root / 'input.txt', read_json(self.root / 'config.json')

    def prepare(self, data_path, config):
        payload = Path(data_path).read_bytes()
        return {'input_sha256': release.digest(payload), 'rows': len(payload.splitlines()), 'config': config}

    def fetch(self, manifest_ref):
        return self.root / manifest_ref

    def gate(self, manifest, report_ref):
        path = self.root / report_ref
        report = read_json(path)
        if isinstance(report, dict):
            report['source_report'] = {'path': report_ref, 'sha256': release.file_sha256(path)}
            report['decision_basis'] = 'STUB: синтетический отчёт стенда, не оценка модели'
        return report


class Hw67Source:
    """Реальный выпуск: Qwen3-0.6B из кеша HF, adapter из MLflow, gate из ДЗ 6+7."""

    def __init__(self, spec):
        self.root = Path(spec['root'])
        self.hw67 = Path(spec['hw67']).resolve()
        self.mlflow = os.environ.get('HW67_MLFLOW_URL', 'http://127.0.0.1:5006')

    def bridge(self, command):
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        done = subprocess.run([sys.executable, '-X', 'utf8', str(BRIDGE), command], cwd=self.hw67, env=env,
                              capture_output=True, text=True, encoding='utf-8')
        if done.returncode:
            raise ValueError(f'hw67_bridge {command}: {done.stderr.strip()[-800:]}')
        return json.loads(done.stdout)

    def base_snapshot(self):
        spec = read_json(self.hw67 / 'evaluation/baseline.json')
        probe = {'artifact_uri': f'hf://{spec["name"]}@{spec["revision"]}'}
        snapshot = release.artifact_root(probe, self.root)
        if not snapshot.is_dir():
            raise ValueError(f'Нет снимка базы {probe["artifact_uri"]} в HF_HOME; задайте HF_HOME')
        return spec, snapshot

    def tokenizer_identity(self, directory):
        files = [p for p in TOKENIZER_FILES if (directory / p).exists()]
        tokenizer_sha = release.digest(release.canonical({p: release.file_sha256(directory / p) for p in files}))
        template = (directory / 'chat_template.jinja')
        text = (template.read_text(encoding='utf-8') if template.exists()
                else read_json(directory / 'tokenizer_config.json')['chat_template'])
        return tokenizer_sha, release.digest(text.encode('utf-8'))

    def prepare_inputs(self):
        spec, snapshot = self.base_snapshot()
        protocol = read_json(self.hw67 / 'evaluation/params.json')
        _, template_sha = self.tokenizer_identity(snapshot)
        config = {'tokenizer_revision': spec['revision'], 'template_sha256': template_sha,
                  'max_length': MAX_PREPARED_TOKENS, 'code_revision': release.file_sha256(Path(__file__)),
                  'behavioral_sha256': release.file_sha256(self.hw67 / protocol['behavioral']),
                  'system_sha256': release.digest(protocol['system'].encode('utf-8'))}
        return self.hw67 / protocol['golden'], config

    def prepare(self, data_path, config):
        """Проверка схемы golden/behavioral и токенизация тем же шаблоном, что в оценке."""
        from transformers import AutoTokenizer
        checked = self.bridge('protocol')
        _, snapshot = self.base_snapshot()
        protocol = checked['config']
        tokenizer = AutoTokenizer.from_pretrained(snapshot, local_files_only=True)
        rows = [json.loads(line) for name in (protocol['golden'], protocol['behavioral'])
                for line in (self.hw67 / name).read_text(encoding='utf-8').splitlines() if line.strip()]
        lengths = []
        for row in rows:
            text = tokenizer.apply_chat_template(
                [{'role': 'system', 'content': protocol['system']}, {'role': 'user', 'content': row['input']}],
                tokenize=False, add_generation_prompt=True, enable_thinking=protocol['enable_thinking'])
            lengths.append(len(tokenizer(text)['input_ids']))
        if max(lengths) > config['max_length']:
            raise ValueError(f'Вход длиннее max_length: {max(lengths)} > {config["max_length"]}')
        return {'input_sha256': release.file_sha256(data_path), 'rows': len(rows), 'max_tokens': max(lengths),
                'protocol_sha256': checked['protocol_sha256'], 'config': config}

    def protocol_dtype(self):
        text = (self.hw67 / 'params.yaml').read_text(encoding='utf-8')
        return re.search(r'^\s*dtype:\s*(\w+)', text, re.M).group(1)

    def check_pins(self, model_id, files):
        pins = read_json(os.environ.get('HW_MODEL_PINS', PINS))
        if pins.get(model_id) != files:
            raise ValueError(f'Файлы {model_id} не совпали с закреплёнными хешами {PINS.name}')

    def runtime(self, tokenizer_dir, spec):
        protocol = read_json(self.hw67 / 'evaluation/params.json')
        tokenizer_sha, template_sha = self.tokenizer_identity(tokenizer_dir)
        return {'base_name': spec['name'], 'base_revision': spec['revision'], 'tokenizer_sha256': tokenizer_sha,
                'template_sha256': template_sha, 'dtype': self.protocol_dtype(),
                'generation': {'system': protocol['system'], 'do_sample': False, 'seed': protocol['seed'],
                               'max_new_tokens': protocol['max_new_tokens'],
                               'enable_thinking': protocol['enable_thinking']}}

    def _mlflow_get(self, endpoint, **params):
        url = f'{self.mlflow}{endpoint}?{urllib.parse.urlencode(params)}'
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()

    def _download_adapter(self, run_id, target):
        """Скачать пакет adapter из MLflow Artifacts; метаданные run проверяются отдельно."""
        run = json.loads(self._mlflow_get('/api/2.0/mlflow/runs/get', run_id=run_id))['run']
        if run['info']['status'] != 'FINISHED':
            raise ValueError(f'Training run {run_id} не завершён')
        prefix = 'model/artifacts/adapter'
        listing = json.loads(self._mlflow_get('/api/2.0/mlflow/artifacts/list', run_id=run_id, path=prefix))
        names = []
        for item in listing.get('files', []):
            if item.get('is_dir'):
                continue
            name = item['path'].removeprefix(prefix + '/')
            names.append(name)
            path = target / name
            if not path.exists() or path.stat().st_size != int(item['file_size']):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self._mlflow_get('/get-artifact', path=item['path'], run_uuid=run_id))
        if not {'adapter_config.json', 'adapter_model.safetensors'} <= set(names):
            raise ValueError('В артефакте run нет весов adapter')
        return sorted(names), run

    def fetch(self, role):
        checked = self.bridge('protocol')
        spec, snapshot = self.base_snapshot()
        if role == 'baseline':
            directory, names = snapshot, BASE_FILES
            manifest = {'kind': 'base', 'model_id': f'{spec["name"]}@{spec["revision"]}',
                        'artifact_uri': f'hf://{spec["name"]}@{spec["revision"]}',
                        'provenance': {'source': 'закреплённая база ДЗ 6+7, evaluation/baseline.json'}}
        elif role == 'candidate':
            model = read_json(self.hw67 / 'metrics/candidate-model.json')
            version = json.loads(self._mlflow_get('/api/2.0/mlflow/model-versions/get', name=model['name'],
                                                  version=model['version']))['model_version']
            if version['run_id'] != model['run_id'] or version['status'] != 'READY':
                raise ValueError('Версия реестра не соответствует training run')
            directory = self.root / 'artifacts' / f'{model["name"]}-{model["version"]}'
            names, run = self._download_adapter(model['run_id'], directory)
            manifest = {'kind': 'adapter', 'model_id': f'{model["name"]}/{model["version"]}',
                        'artifact_uri': model['uri'], 'artifact_dir': directory.relative_to(self.root).as_posix(),
                        'provenance': {'training_run_id': model['run_id'],
                                       'note': 'чтение готового run ДЗ 6+7, не повторное обучение',
                                       'code_sha256': next(t['value'] for t in run['data']['tags'] if t['key'] == 'code_sha256')}}
        else:
            raise ValueError(f'Неизвестная роль {role}')
        files = {name: release.file_sha256(directory / name) for name in names}
        self.check_pins(manifest['model_id'], files)
        runtime = self.runtime(directory if role == 'candidate' else snapshot, spec)
        manifest.update(schema_version=1, file_hashes=files, model_sha256=release.digest(release.canonical(files)),
                        protocol_sha256=checked['protocol_sha256'], golden_sha256=checked['golden_sha256'],
                        runtime=runtime, runtime_sha256=release.digest(release.canonical(runtime)))
        path = self.root / 'manifests' / f'{role}.json'
        write_json(path, manifest)
        return path

    def gate(self, manifest, role):
        """Вызывает quality_gate ДЗ 6+7 и пересчёт по сырым ответам; PASS здесь не вписывается."""
        decision = self.bridge(f'gate-{role}')
        report = {'status': 'complete', 'passed': decision['passed'], 'reason': decision.get('reason'),
                  'decision_basis': decision['decision_basis'], 'evaluation_run_id': decision['evaluation_run_id'],
                  'model_id': manifest['model_id'], 'metrics': decision['metrics'], 'slices': decision['slices'],
                  'behavioral': decision['behavioral'], 'gate_result': decision.get('gate_result'),
                  'evidence': decision['evidence'],
                  **{k: manifest[k] for k in ('model_sha256', 'runtime_sha256')},
                  'protocol_sha256': decision['protocol_sha256'], 'golden_sha256': decision['golden_sha256']}
        return report


def load(spec):
    return {'stub': StubSource, 'hw67': Hw67Source}[spec['kind']](spec)
