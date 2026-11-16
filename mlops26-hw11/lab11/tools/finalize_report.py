"""Finalize the measured report and package the local lab, without rerunning experiments.

Run from ~/lab11: .venv/bin/python /path/to/finalize_report.py
Only the current lab directory is written. No Docker/Kubernetes commands are run.
"""
import argparse
import hashlib
import html
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def request_rows(folder):
    return [row for file in sorted(folder.glob('client-*.jsonl')) for row in read_lines(file)]


def quantile(values, p=.95):
    values=sorted(v for v in values if v is not None)
    if not values:
        return None
    pos=(len(values)-1)*p
    low=int(pos)
    return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(pos-low)


def summary(rows):
    good=[r for r in rows if r.get('outcome')=='completed']
    return {'planned':len(rows),'completed':len(good),
            'outcomes':dict(Counter(r.get('outcome') for r in rows)),
            'p95_ttfc':quantile([r.get('ttfc') for r in good]),
            'fail_fraction':1-len(good)/len(rows) if rows else None,
            'versions':dict(Counter(r.get('version') for r in rows))}


def number(value):
    return 'нет данных' if value is None else f'{value:.4f}'


def table(headers,rows):
    return '<table><tr>'+''.join('<th>'+html.escape(str(h))+'</th>' for h in headers)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'


def prom_scalar(document,key):
    response=document.get(key,{})
    values=response.get('data',{}).get('result',[])
    return float(values[0]['value'][1]) if response.get('status')=='success' and values else None


def finalize(root):
    root=root.resolve()
    if not (root/'tools/report.py').is_file() or not (root/'results').is_dir():
        raise SystemExit('Run from the working lab directory containing tools/report.py and results/.')
    sys.path.insert(0,str(root))
    from tools import report
    report.ROOT=root
    report.RESULTS=root/'results'
    report.main()
    checks=[dict(row,phase=file.parent.name) for file in sorted((root/'results').glob('*/checks.jsonl')) for row in read_lines(file)]
    warnings=[]
    passed=sum(c.get('passed') is True for c in checks)
    failed=[c for c in checks if c.get('passed') is not True]
    if len(checks)!=15 or failed:
        warnings.append(f'Expected 15 passing checks; found {passed} PASS out of {len(checks)}.')
    before=summary(request_rows(root/'archive/A-before-timeout-fix/rollout-A-B'))
    after=summary(request_rows(root/'results/A/rollout-A-B'))
    if not before['planned']:
        warnings.append('Original rollout data missing from archive/A-before-timeout-fix/.')
    if after['planned']!=100 or after['completed']!=100:
        warnings.append('Repeated rollout did not record exactly 100 completed requests.')
    text=['<section id="measured-conclusions"><h2>Итог по фактическим данным</h2>',
          f'<p>Проверки актуальных прогонов: {passed} PASS из {len(checks)}. Исходный неуспешный опыт хранится отдельно в archive/A-before-timeout-fix и не исключается из доказательств.</p>',
          '<h3>Rollout: исходный отказ и повтор</h3>',
          table(['Прогон','Запросов','Успешно','Исходы','Версии'],[
              ['До: очередь 30 с',before['planned'],before['completed'],before['outcomes'],before['versions']],
              ['После: очередь 65 с',after['planned'],after['completed'],after['outcomes'],after['versions']]]),
          '<p>В исходной конфигурации одна реплика одновременно обрабатывала один запрос; длительный поток занимал её примерно 40 с, а ожидание очереди ограничивалось 30 с. Получены HTTP 503 queue_timeout. Для опыта A очередь увеличена до 65 с при клиентском тайм-ауте 70 с и termination grace period 90 с. Длина потока и расписание нагрузки сохранены. Успех повтора подтверждается полными клиентскими результатами, а не только наличием B. Увеличение ожидания снижает отказы этой нагрузки ценой возможного роста задержки; оно не увеличивает вычислительную ёмкость.</p>',
          '<h3>Выбор маршрутизации</h3>']
    comparisons=[]
    for traffic in ['uniform','hot']:
        for policy in ['api','api-sticky']:
            for repeat in range(1,4):
                rows=request_rows(root/f'results/B/{traffic}-{policy}-{repeat}')
                s=summary(rows)
                if not rows:
                    warnings.append(f'Missing B run: {traffic}-{policy}-{repeat}')
                comparisons.append([traffic,policy,repeat,s['planned'],s['completed'],number(s['p95_ttfc']),number(s['fail_fraction'])])
    text += [table(['Трафик','Service','Повтор','Всего','Успешно','p95 TTFC, с','Доля неуспеха'],comparisons),
       '<p>Для горячего клиента сравниваются все три повтора по задержке и отказам. Выбран обычный Service как исправление постоянной ClientIP-привязки для данного сценария. Для равномерного трафика привязка может давать низкую задержку при удачном распределении клиентов и локальности кеша, но также создаёт перекос при назначении нескольких клиентов одной реплике. Поэтому вывод не переносится на любую нагрузку. Выброс второго uniform/api-прогона нужно рассматривать вместе с CPU/очередями и трассой, а не удалять из результатов.</p>',
       '<h3>Сверка клиента и Prometheus</h3>']
    reconcile_path=root/'results/B/hot-api-1/reconciliation.json'
    reconciliation=None
    if reconcile_path.is_file():
        reconciliation=json.loads(reconcile_path.read_text(encoding='utf-8'))
        client=reconciliation.get('client_p95')
        server=prom_scalar(reconciliation,'prometheus_p95')
        observations=prom_scalar(reconciliation,'prometheus_observations')
        diff=server-client if server is not None and client is not None else None
        text += [table(['Величина','Значение'],[
            ['Завершено по клиенту',reconciliation.get('client_completed')],
            ['p95 TTFC клиента, с',number(client)],['p95 по Histogram, с',number(server)],
            ['Разница оценок p95, с',number(diff)],['Оценка increase(count)',number(observations)]]),
            '<p>histogram_quantile интерполирует внутри бакета, а increase экстраполирует прирост счётчика к границам диапазона, поэтому оценка количества может быть дробной. Это не число дополнительных фактических запросов. Клиент включает сетевую задержку; сервер измеряет другой участок обработки. Эти свойства объясняют возможное расхождение, но совпадение набора событий нельзя доказать одной близостью агрегатов. Сохраняются исходные запросы, терминальные журналы, границы времени и JSON сверки.</p>',
            '<p>Первичные источники: <a href="https://prometheus.io/docs/prometheus/latest/querying/functions/#increase">increase</a> и <a href="https://prometheus.io/docs/practices/histograms/">Histogram</a>.</p>',
            '<pre>'+html.escape(json.dumps(reconciliation.get('promql',{}),ensure_ascii=False,indent=2))+'</pre>']
        if server is None or observations is None:
            warnings.append('Reconciliation has no usable Prometheus values.')
    else:
        warnings.append('Missing reconciliation.json for B/hot-api-1.')
        text.append('<p class="missing">Сверка Prometheus не найдена.</p>')
    figures=[f'results/figures/{name}' for name in ['01-rollout.svg','02-hot-pod.svg','03-policies.svg','04-hpa.svg']]
    if not all((root/name).is_file() for name in figures):
        warnings.append('One or more of the four required figures are missing.')
    text += ['<h3>Оставшееся оформление</h3><p>Перед сдачей заполнить ФИО, группу, ссылку на свой репозиторий и коммит. По видеолекции указать компанию, таймкод и первичную публикацию, затем связать кейс с ревью. Публикация репозитория выполняется автором работы. Наличие 15 PASS не заменяет эти пункты.</p>']
    if warnings:
        text.append('<p class="missing">'+html.escape(' '.join(warnings))+'</p>')
    text.append('</section>')
    document=(root/'report.html').read_text(encoding='utf-8')
    (root/'report.html').write_text(document.replace('</html>','\n'.join(text)+'\n</html>'),encoding='utf-8')
    status={'checks':len(checks),'passed':passed,'failed':failed,'rollout_before':before,'rollout_after':after,
            'warnings':warnings,'remaining_editorial_items':['author and group','repository and commit','lecture company, timestamp and primary source']}
    (root/'submission-status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    target=root/'tools/finalize_report.py'
    if Path(__file__).resolve()!=target.resolve():
        shutil.copyfile(__file__,target)
    dirs=['app','artifacts','assignment','chart','infra','monitoring','tools','tests','reference','scripts','results','evidence','archive']
    names=['Dockerfile','requirements.in','requirements.lock','README.md','PROVENANCE.md','REVIEW.md','START_HERE.txt',
           '.dockerignore','.gitignore','.gitattributes','report.html','submission-status.json','run.log','run-A-repeat.log']
    files=[]
    for name in dirs:
        if (root/name).is_dir():
            files += [p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'
                      and not p.name.endswith('.bak') and '.before-' not in p.name]
    files += [root/name for name in names if (root/name).is_file()]
    files=sorted(set(files))
    manifest=[{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    (root/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    with ZipFile(root/'lab11-ready.zip','w',ZIP_DEFLATED) as archive:
        for p in files+[root/'MANIFEST.json']:
            archive.write(p,'lab11/'+p.relative_to(root).as_posix())
    print(json.dumps(status,ensure_ascii=False,indent=2))
    print('Written: report.html, submission-status.json, lab11-ready.zip')
    print('Remaining: author/group, own repository/commit, lecture case. Keep the archived original failure.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    finalize(parser.parse_args().root)
