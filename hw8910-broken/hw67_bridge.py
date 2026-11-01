"""Мост к оценке ДЗ 6+7: запускается с cwd=mlops26-hw67, пишет JSON в stdout.

Правило допуска и пересчёт метрик не копируются: вызываются исходные inputs,
current_reports и quality_gate. MLflow здесь не нужен: реестр проверяет sources.py.
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import evaluation.cli as cli  # noqa: E402
from evaluation import solution  # noqa: E402
from evaluation.contract import fingerprint, read_json  # noqa: E402

cli.candidate_model = lambda: read_json(cli.CANDIDATE)


def evidence(*names):
    return {name: {'path': f'mlops26-hw67/{name}', 'sha256': hashlib.sha256(Path(name).read_bytes()).hexdigest()}
            for name in names}


def protocol():
    config, rows, _, baseline, proof, protocol_hash = cli.inputs()
    return {'config': config, 'protocol_sha256': protocol_hash, 'golden_sha256': fingerprint(rows),
            'baseline_spec_sha256': fingerprint(baseline), 'leakage': proof}


def summary(report, decision):
    return {'evaluation_run_id': report['evaluation_run_id'], 'protocol_sha256': report['protocol_sha256'],
            'golden_sha256': report['golden_sha256'],
            'metrics': {k: report['overall'][k] for k in ('accuracy', 'format_rate', 'n', 'correct', 'valid', 'errors')},
            'slices': {k: {f: v[f] for f in ('n', 'correct', 'accuracy')} for k, v in report['slices'].items()},
            'behavioral': report['behavioral']['overall'],
            'evidence': evidence('metrics/baseline-eval.json', 'metrics/candidate-eval.json',
                                 'metrics/baseline-outputs.json', 'metrics/candidate-outputs.json',
                                 'evaluation/params.json', 'evaluation/baseline.json'),
            **decision}


def gate(role):
    baseline, candidate = cli.current_reports()
    critical = read_json('evaluation/params.json')['critical_slices']
    if role == 'baseline':
        # Baseline — закреплённая точка отсчёта: сравнивать её не с чем, поэтому проверяется
        # полнота и согласованность оценки, а не «лучше предыдущей». Behavioral указан как есть.
        solution._check_report(baseline, 'baseline')
        return summary(baseline, {'passed': True, 'decision_basis':
                                  'baseline-reference: полная оценка ДЗ 6+7 пересчитана по сырым ответам, ошибок нет; '
                                  'quality_gate сравнивает кандидата с этой версией и к ней самой не применяется'})
    try:
        result = solution.quality_gate(baseline, candidate, critical)
    except ValueError as exc:
        return summary(candidate, {'passed': False, 'reason': str(exc), 'decision_basis': 'quality_gate ДЗ 6+7'})
    return summary(candidate, {'passed': result['passed'] is True, 'gate_result': result,
                               'decision_basis': 'quality_gate ДЗ 6+7'})


if __name__ == '__main__':
    command = sys.argv[1]
    result = protocol() if command == 'protocol' else gate(command.removeprefix('gate-'))
    json.dump(result, sys.stdout, ensure_ascii=False)
