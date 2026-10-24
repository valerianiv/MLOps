"""score и quality_gate для ДЗ 6+7. Контракт: EVALUATION.md и tests/test_eval.py.

score      — строгий scorer меток: каждый вопрос golden остаётся в знаменателе.
quality_gate — допуск кандидата: совместимость протокола, целостность отчётов,
               нулевой допуск регрессии (общая метрика, формат, критические срезы),
               все behavioral-кейсы кандидата пройдены.
"""
import json
import math

METRIC_FIELDS = ('n', 'correct', 'valid', 'errors', 'accuracy', 'format_rate')
ITEM_FIELDS = ('id', 'raw', 'parsed', 'valid', 'correct', 'error', 'slices')
MIN_GOLDEN = 50
MIN_SLICES = 3
MIN_BEHAVIORAL = 3
TOLERANCE = 1e-12


# ---------------------------------------------------------------- score

def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate key')
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f'{name} не является строгим JSON')


def parse_answer(raw, choices):
    """Строгий разбор: JSON-объект ровно с одним строковым полем answer из choices.

    Возвращает метку или None. Markdown-обёртки, лишние поля, числа, null,
    свободный текст и метки вне choices — невалидный ответ, а не «почти верный».
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {'answer'}:
        return None
    answer = value['answer']
    if not isinstance(answer, str) or answer not in choices:
        return None
    return answer


def _metrics(items):
    n = len(items)
    correct = sum(1 for i in items if i['correct'])
    valid = sum(1 for i in items if i['valid'])
    errors = sum(1 for i in items if i['error'] is not None)
    return dict(n=n, correct=correct, valid=valid, errors=errors,
                accuracy=correct / n, format_rate=valid / n)


def score(rows, outputs):
    """overall, slices и per_item. Сопоставление по id, порядок outputs не важен."""
    rows, outputs = list(rows), list(outputs)
    if not rows:
        raise ValueError('Пустой набор вопросов')
    row_ids = [r.get('id') for r in rows]
    if any(not isinstance(i, str) or not i for i in row_ids) or len(set(row_ids)) != len(row_ids):
        raise ValueError('id вопросов должны быть непустыми строками без дублей')
    out_ids = [o.get('id') for o in outputs]
    if len(set(out_ids)) != len(out_ids):
        raise ValueError('Дублирующиеся id в ответах')
    if set(out_ids) != set(row_ids):
        missing = sorted(set(row_ids) - set(out_ids))[:5]
        extra = sorted(set(out_ids) - set(row_ids), key=str)[:5]
        raise ValueError(f'id ответов не совпадают с вопросами: нет {missing}, лишние {extra}')
    by_id = {o['id']: o for o in outputs}

    per_item = []
    for row in rows:
        choices, slices = row.get('choices'), row.get('slices')
        if not isinstance(choices, list) or not choices or row.get('expected') not in choices:
            raise ValueError(f'{row["id"]}: нужны choices и expected из них')
        if not isinstance(slices, list) or not slices or any(not isinstance(s, str) or not s for s in slices):
            raise ValueError(f'{row["id"]}: нужны именованные slices')
        out = by_id[row['id']]
        error = out.get('error')
        error = None if error in (None, '') else str(error)
        raw = out.get('raw')
        raw = '' if raw is None else str(raw)
        # Ошибка генерации — ответ не засчитывается, даже если какой-то текст успел появиться.
        parsed = None if error is not None else parse_answer(raw, choices)
        valid = parsed is not None
        per_item.append(dict(id=row['id'], raw=raw, parsed=parsed, valid=valid,
                             correct=valid and parsed == row['expected'], error=error,
                             slices=list(dict.fromkeys(slices))))

    names = sorted({s for item in per_item for s in item['slices']})
    slices = {name: _metrics([i for i in per_item if name in i['slices']]) for name in names}
    return dict(overall=_metrics(per_item), slices=slices, per_item=per_item)


# ---------------------------------------------------------------- quality_gate

def _count(value, what):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{what}: ожидается неотрицательное целое, получено {value!r}')
    return value


def _rate(value, what):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{what}: метрика отсутствует или не конечна ({value!r})')
    return float(value)


def _check_metrics(m, where):
    if not isinstance(m, dict) or any(k not in m for k in METRIC_FIELDS):
        raise ValueError(f'{where}: нет полей {METRIC_FIELDS}')
    n = _count(m['n'], f'{where}.n')
    if n == 0:
        raise ValueError(f'{where}: пустой набор (n=0)')
    for k in ('correct', 'valid', 'errors'):
        if _count(m[k], f'{where}.{k}') > n:
            raise ValueError(f'{where}.{k} > n')
    if m['correct'] > m['valid']:
        raise ValueError(f'{where}: correct > valid')
    if abs(_rate(m['accuracy'], f'{where}.accuracy') - m['correct'] / n) > TOLERANCE:
        raise ValueError(f'{where}: accuracy не соответствует correct/n')
    if abs(_rate(m['format_rate'], f'{where}.format_rate') - m['valid'] / n) > TOLERANCE:
        raise ValueError(f'{where}: format_rate не соответствует valid/n')
    return m


def _check_items(items, where):
    if not isinstance(items, list) or not items:
        raise ValueError(f'{where}: нет per_item')
    for item in items:
        if not isinstance(item, dict) or any(k not in item for k in ITEM_FIELDS):
            raise ValueError(f'{where}: в per_item нет полей {ITEM_FIELDS}')
        if not isinstance(item['id'], str) or not item['id']:
            raise ValueError(f'{where}: пустой id')
        if not isinstance(item['valid'], bool) or not isinstance(item['correct'], bool):
            raise ValueError(f'{where}.{item["id"]}: valid/correct должны быть bool')
        if item['valid'] != (item['parsed'] is not None) or (item['correct'] and not item['valid']):
            raise ValueError(f'{where}.{item["id"]}: valid/correct противоречат parsed')
        if item['error'] is not None and item['valid']:
            raise ValueError(f'{where}.{item["id"]}: ответ с ошибкой генерации не может быть валиден')
        if not isinstance(item['slices'], list) or not item['slices']:
            raise ValueError(f'{where}.{item["id"]}: нет slices')
    ids = [i['id'] for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError(f'{where}: дубли id в per_item')


def _check_scored(report, where, min_items):
    """Отчёт score: структура, конечные метрики, counts == пересчёт по per_item."""
    if not isinstance(report, dict) or any(k not in report for k in ('overall', 'slices', 'per_item')):
        raise ValueError(f'{where}: отчёт неполный (нужны overall, slices, per_item)')
    items = report['per_item']
    _check_items(items, where)
    if len(items) < min_items:
        raise ValueError(f'{where}: {len(items)} вопросов, минимум {min_items}')
    overall = _check_metrics(report['overall'], f'{where}.overall')
    if any(overall[k] != v for k, v in _counts(items).items()):
        raise ValueError(f'{where}.overall: counts не соответствуют per_item')
    slices = report['slices']
    if not isinstance(slices, dict):
        raise ValueError(f'{where}: slices должен быть словарём')
    names = {s for i in items for s in i['slices']}
    if set(slices) != names:
        raise ValueError(f'{where}: набор срезов не совпадает с per_item')
    for name, m in slices.items():
        _check_metrics(m, f'{where}.slices.{name}')
        members = [i for i in items if name in i['slices']]
        if any(m[k] != v for k, v in _counts(members).items()):
            raise ValueError(f'{where}.slices.{name}: counts не соответствуют per_item')
    return report


def _counts(items):
    return dict(n=len(items), correct=sum(i['correct'] for i in items),
                valid=sum(i['valid'] for i in items), errors=sum(i['error'] is not None for i in items))


def _check_report(report, role):
    if not isinstance(report, dict):
        raise ValueError(f'{role}: отчёт отсутствует')
    if report.get('status') != 'complete':
        raise ValueError(f'{role}: status={report.get("status")!r}, нужен complete')
    if not isinstance(report.get('model'), dict) or not report['model']:
        raise ValueError(f'{role}: не указана конкретная модель')
    for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
        if not isinstance(report.get(key), str) or not report[key]:
            raise ValueError(f'{role}: нет {key}')
    _check_scored(report, role, MIN_GOLDEN)
    if len(report['slices']) < MIN_SLICES:
        raise ValueError(f'{role}: срезов {len(report["slices"])}, минимум {MIN_SLICES}')
    if report['overall']['errors']:
        raise ValueError(f'{role}: ошибок генерации {report["overall"]["errors"]}')
    if not isinstance(report.get('behavioral'), dict):
        raise ValueError(f'{role}: нет behavioral-отчёта')
    return report


def quality_gate(baseline, candidate, critical_slices):
    """{passed: True, ...} или ValueError. Допуск регрессии — ноль."""
    _check_report(baseline, 'baseline')
    _check_report(candidate, 'candidate')

    # 1. Один протокол, один golden, один baseline.
    for key in ('protocol_sha256', 'golden_sha256', 'baseline_spec_sha256'):
        if baseline[key] != candidate[key]:
            raise ValueError(f'Несовместимые отчёты: различается {key}')

    # 2. Одинаковые вопросы, срезы и размеры групп.
    a = {i['id']: i for i in baseline['per_item']}
    b = {i['id']: i for i in candidate['per_item']}
    if set(a) != set(b):
        raise ValueError('Разные id вопросов в baseline и candidate')
    if any(sorted(a[i]['slices']) != sorted(b[i]['slices']) for i in a):
        raise ValueError('Разная разметка срезов у одних и тех же id')
    if {k: v['n'] for k, v in baseline['slices'].items()} != {k: v['n'] for k, v in candidate['slices'].items()}:
        raise ValueError('Различаются имена или размеры срезов')

    critical = list(critical_slices or [])
    if not critical:
        raise ValueError('Не задан ни один критический срез')
    missing = [s for s in critical if s not in candidate['slices']]
    if missing:
        raise ValueError(f'Критический срез отсутствует: {missing}')

    # 3. Нулевой допуск регрессии. Сравниваем counts при равном n — без плавающей точки.
    bo, co = baseline['overall'], candidate['overall']
    if co['correct'] < bo['correct']:
        raise ValueError(f'Регрессия accuracy: {co["correct"]}/{co["n"]} < {bo["correct"]}/{bo["n"]}')
    if co['valid'] < bo['valid']:
        raise ValueError(f'Регрессия format_rate: {co["valid"]}/{co["n"]} < {bo["valid"]}/{bo["n"]}')
    critical_report = {}
    for name in critical:
        bs, cs = baseline['slices'][name], candidate['slices'][name]
        if cs['correct'] < bs['correct']:
            raise ValueError(f'Регрессия в критическом срезе {name}: '
                             f'{cs["correct"]}/{cs["n"]} < {bs["correct"]}/{bs["n"]}')
        critical_report[name] = dict(n=cs['n'], baseline_correct=bs['correct'],
                                     candidate_correct=cs['correct'],
                                     baseline_accuracy=bs['accuracy'], candidate_accuracy=cs['accuracy'])

    # 4. Все behavioral-кейсы кандидата пройдены (их минимум три, counts сверены с per_item).
    for role, report in (('baseline', baseline), ('candidate', candidate)):
        _check_scored(report['behavioral'], f'{role}.behavioral', MIN_BEHAVIORAL)
    if {i['id'] for i in baseline['behavioral']['per_item']} != {i['id'] for i in candidate['behavioral']['per_item']}:
        raise ValueError('Разные behavioral-кейсы в baseline и candidate')
    beh = candidate['behavioral']
    failed = [i['id'] for i in beh['per_item'] if not i['correct']]
    if failed or beh['overall']['correct'] != beh['overall']['n']:
        raise ValueError(f'Behavioral не пройден: {failed or beh["overall"]}')

    return dict(
        passed=True,
        max_regression=0,
        protocol_sha256=candidate['protocol_sha256'],
        golden_sha256=candidate['golden_sha256'],
        baseline_spec_sha256=candidate['baseline_spec_sha256'],
        baseline_model=baseline['model'],
        candidate_model=candidate['model'],
        overall=dict(n=co['n'],
                     baseline=dict(correct=bo['correct'], valid=bo['valid'], accuracy=bo['accuracy'], format_rate=bo['format_rate']),
                     candidate=dict(correct=co['correct'], valid=co['valid'], accuracy=co['accuracy'], format_rate=co['format_rate'])),
        critical_slices=critical_report,
        behavioral=dict(n=beh['overall']['n'], passed=beh['overall']['correct']),
        improved=sorted(i for i in a if not a[i]['correct'] and b[i]['correct']),
        regressed=sorted(i for i in a if a[i]['correct'] and not b[i]['correct']),
    )
