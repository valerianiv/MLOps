"""Build an honest HTML report and SVG charts from local raw evidence only."""
import csv
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from tools.lab import ROOT, RESULTS, summarize, percentile


def esc(value):
    return html.escape(str(value))


def lines(path):
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def chart(file, title, series, xlabel, ylabel):
    """Small standalone SVG, each series is (legend, [(x,y),...]). No fabricated data."""
    all_points=[(x,y) for _,points in series for x,y in points if math.isfinite(x) and math.isfinite(y)]
    if not all_points:
        return '<p class="missing">Нет измерений для графика: '+esc(title)+'</p>'
    xmin=min(x for x,y in all_points); xmax=max(x for x,y in all_points)
    ymin=0; ymax=max(y for x,y in all_points)*1.1 or 1
    width=960; height=410
    plotw=750; ploth=270
    colors=['#2563eb','#dc2626','#059669','#9333ea','#d97706','#0891b2','#be185d','#475569']
    svg=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"><rect width="960" height="410" fill="white"/>',
         f'<text x="55" y="25" font-size="18">{esc(title)}</text>',
         '<path d="M70 50 V320 H820" fill="none" stroke="#334155"/>']
    for i in range(6):
        y=320-i/5*ploth; value=i/5*ymax
        x=70+i/5*plotw; xv=xmin+i/5*(xmax-xmin)
        svg.append(f'<path d="M70 {y} H820" stroke="#e2e8f0"/><text x="5" y="{y+4}" font-size="12">{value:.3g}</text>')
        svg.append(f'<text x="{x-10}" y="340" font-size="12">{xv:.3g}</text>')
    svg += [f'<text x="380" y="370" font-size="14">{esc(xlabel)}</text>',
            f'<text x="5" y="43" font-size="13">{esc(ylabel)}</text>']
    for i,(label,points) in enumerate(series):
        points=[(x,y) for x,y in points if math.isfinite(x) and math.isfinite(y)]
        if not points: continue
        color=colors[i%len(colors)]
        xy=' '.join(f'{70+(x-xmin)/max(xmax-xmin,1)*plotw:.1f},{320-y/ymax*ploth:.1f}' for x,y in points)
        svg.append(f'<polyline points="{xy}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x,y in points:
            svg.append(f'<circle cx="{70+(x-xmin)/max(xmax-xmin,1)*plotw:.1f}" cy="{320-y/ymax*ploth:.1f}" r="2" fill="{color}"/>')
        svg.append(f'<text x="830" y="{58+18*i}" font-size="10" fill="{color}">{esc(label[:20])}</text>')
    svg.append('</svg>')
    output=RESULTS/'figures'/file
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(''.join(svg),encoding='utf-8')
    return f'<img src="results/figures/{file}" alt="{esc(title)}">'


def table(headers, rows):
    return '<table><tr>'+''.join('<th>'+esc(x)+'</th>' for x in headers)+'</tr>'+''.join(
        '<tr>'+''.join('<td>'+esc(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'


def main():
    RESULTS.mkdir(exist_ok=True)
    groups={}
    all_rows=[]
    for folder in sorted(RESULTS.glob('*/*')):
        if not folder.is_dir(): continue
        files=sorted(folder.glob('client-*.jsonl'))
        if not files: continue
        rows=[dict(row,run=folder.relative_to(RESULTS).as_posix()) for file in files for row in lines(file)]
        groups[folder.relative_to(RESULTS).as_posix()]=rows
        all_rows.extend(rows)
    fields=sorted(set(key for row in all_rows for key in row))
    if fields:
        with (RESULTS/'requests.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
            for row in all_rows:
                writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
    checks=[dict(c,phase=p.parent.name) for p in sorted(RESULTS.glob('*/checks.jsonl')) for c in lines(p)]
    contents=['<!doctype html><html lang="ru"><meta charset="utf-8"><title>ДЗ 11 — отчёт</title>',
      '<style>body{font:16px/1.6 system-ui;max-width:1120px;margin:40px auto;padding:0 24px;color:#172033}h1,h2{line-height:1.2}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:8px;border-bottom:1px solid #cbd5e1;text-align:left}img{width:100%}.missing{background:#fff1d6;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:16px}a{color:#1d4ed8}@media print{body{margin:0}h2{break-after:avoid}img,tr{break-inside:avoid}}</style>',
      '<h1>ДЗ 11. Сервис работает, пользователи ждут</h1>',
      '<p><b>backend_mode=simulated.</b> Части ответа имитатора не являются токенами модели. Используются TTFC и inter-chunk latency.</p>',
      '<p class="missing">Автор, группа, ссылка на итоговый репозиторий и коммит: заполнить перед сдачей. Привязку кейса к лекции подтвердить по видео.</p>']
    if not all_rows:
        contents.append('<p class="missing">Kubernetes-эксперименты ещё не выполнены. Этот файл — структура отчёта, результаты отсутствуют.</p>')
    contents.append('<h2>Сводка измерений</h2>')
    summary=[]
    for name,rows in groups.items():
        s=summarize(rows)
        summary.append([name,len(rows),s['completed'],s['ttfc_p50'],s['ttfc_p95'],s['error_fraction'],s['max_dispatch_lag'],s['outcomes']])
    contents.append(table(['Прогон','Запланировано','Успешно','p50 TTFC, с','p95 TTFC, с','Доля неуспеха','Макс. lag, с','Исходы'],summary))
    contents.append('<h2>Собственные проверки</h2>')
    contents.append(table(['Фаза','Проверка','Результат'],[[c['phase'],c['name'],'PASS' if c['passed'] else 'FAIL'] for c in checks]))
    if not checks or any(not c['passed'] for c in checks):
        contents.append('<p class="missing">Есть отсутствующие или неуспешные проверки. Нельзя считать работу полностью подтверждённой. См. results/*/checks.jsonl.</p>')
    contents += ['<h2>A. Прогрев, rollout и откат</h2>',
      '<p>Гипотеза: готовность HTTP-процесса не означает готовность backend. Дефект включается BROKEN_PROBES; API получает запросы до прогрева и возвращает unready. Исправление: startup/readiness учитывают завершение прогрева. Liveness проверяет только живость процесса. Во время штатного обновления maxUnavailable=0, новый Pod прогревается, preStop снимает готовность и ждёт 8 с, grace period 90 с позволяет закончить поток.</p>',
      '<p>Сравнить broken/fixed при одном расписании. Для rollout проверить все HTTP-статусы, терминальное событие done и присутствие B в ответах. Never-ready B исключается из готовых EndpointSlice. Управляемый возврат к A выполняется явным выпуском A через Helm. аварийный выход os._exit(137) — отдельный отказ с ожидаемым обрывом, не штатный rollout.</p>']
    cluster_a=lines(RESULTS/'A/cluster.jsonl')
    if cluster_a:
        origin=cluster_a[0]['wall']; ready=[]; endpoints=[]
        for rec in cluster_a:
            pods=[p for p in rec.get('pods',{}).get('items',[]) if p['metadata'].get('labels',{}).get('app')=='api']
            ready.append((rec['wall']-origin,sum(any(c.get('type')=='Ready' and c.get('status')=='True' for c in p['status'].get('conditions',[])) for p in pods)))
            endpoints.append((rec['wall']-origin,sum(e.get('conditions',{}).get('ready') is True for s in rec.get('endpoints',{}).get('items',[]) if s['metadata'].get('labels',{}).get('kubernetes.io/service-name')=='api' for e in s.get('endpoints',[]))))
        ttfc_points=[(r['sent_wall']-origin,r['ttfc']) for r in all_rows if r['run'].startswith('A/') and r.get('ttfc') is not None]
        contents.append(chart('01-rollout.svg','Прогрев / rollout: Ready, EndpointSlice и TTFC', [('Ready',ready),('Endpoints',endpoints),('TTFC, s',ttfc_points)],'Секунды от начала A','Реплики / TTFC, с'))
    else: contents.append('<p class="missing">График 1 появится после опыта A.</p>')
    contents += ['<h2>B. Привязка, кеш и горячая реплика</h2>',
      '<p>Обычный Service выбирается кандидатом исправления постоянной привязки. У каждого запроса отдельное TCP-соединение, Connection: close; четыре клиента имеют разные Pod IP. Сравниваются одинаковые входы и расписания при равномерном трафике и 80% трафика от client-0. После каждого полного rollout локальный кеш пуст; backend прогрет. Префиксный кеш LRU: 32 записи; стоимость hit 15000, miss 180000 итераций PBKDF2; обработка одна на Pod, очередь 32, тайм-аут очереди 30 с. Кеш имитирует стоимость пересчёта и не хранит состояние диалога.</p>',
      '<p>Вывод выбирается по всем трём повторам: p95, доля отказов и распределение запросов. Разница может отсутствовать: это допустимый наблюдаемый результат. Проверить исходные IP, раскладку клиентов по Pod, lag генератора и запас ёмкости. CPU-числа относятся только к этой модели нагрузки. Для настоящего inference engine отдельно проверяются KV-cache, batching, перенос контекста и стоимость prefill.</p>']
    hot=next((rows for name,rows in groups.items() if name.startswith('B/hot-api-sticky-')),[])
    if hot:
        start=min(r['scheduled_wall'] for r in hot); series=[]
        for pod in sorted({r.get('pod') for r in hot if r.get('pod')}):
            subset=sorted([r for r in hot if r.get('pod')==pod and r.get('ttfc') is not None],key=lambda r:r['sent_wall'])
            series.append((pod[-12:]+' TTFC',[(r['sent_wall']-start,r['ttfc']) for r in subset]))
            series.append((pod[-12:]+' queue',[(r['sent_wall']-start,r.get('queue_seconds',0)) for r in subset]))
        contents.append(chart('02-hot-pod.svg','Горячая реплика: TTFC и ожидание в очереди / Pod',series,'Секунды от начала прогона','Секунды'))
    else: contents.append('<p class="missing">График 2 появится после опыта B.</p>')
    comparison=defaultdict(list)
    for name,rows in groups.items():
        if name.startswith('B/'):
            label,repeat=name.rsplit('-',1)
            val=summarize(rows)['ttfc_p95']
            if val is not None: comparison[label].append((int(repeat),val))
    contents.append(chart('03-policies.svg','Политики и трафик: три повтора по >=120 с',[(name,sorted(points)) for name,points in comparison.items()],'Номер повтора','p95 TTFC, с'))
    contents += ['<h2>C. HPA и алерты</h2>',
      '<p>HPA autoscaling/v2: CPU target 55% от request=200m, минимум 2, максимум 3 реплики, scaleDown stabilization 60 с. CPU-нагрузка вычислительная. До измерений проверяется resource metrics API. Усреднение может скрыть горячую реплику: при 100% и 0% среднее 50%, ниже цели 55%. При сохранённой ClientIP-привязке новая реплика может оставаться без тяжёлого клиента.</p>',
      '<p>Алерты: p95 TTFC >0.8 с при >=10 завершениях за минуту; доля неуспешных >5% при >=20 исходах за минуту. Оба условия удерживаются 20 с. Фазы pending/firing и возврат фиксируются сырыми выборками ALERTS. Если стимул не дал переходов, это FAIL, требуется калибровка и полный повтор фазы.</p>']
    cluster_c=lines(RESULTS/'C/cluster.jsonl')
    if cluster_c:
        origin=cluster_c[0]['wall']; replicas=[]
        for rec in cluster_c:
            pods=[p for p in rec.get('pods',{}).get('items',[]) if p['metadata'].get('labels',{}).get('app')=='api' and not p['metadata'].get('deletionTimestamp')]
            replicas.append((rec['wall']-origin,len(pods)))
        arrival=Counter(int((r.get('sent_wall',r['scheduled_wall'])-origin)//10)*10 for r in all_rows if r['run'].startswith('C/'))
        contents.append(chart('04-hpa.svg','Масштабирование 2→3→2 и входящая нагрузка', [('Pod count',replicas),('sent req/s',[(t,n/10) for t,n in sorted(arrival.items())])],'Секунды от начала C','Реплики / запросы в секунду'))
    else: contents.append('<p class="missing">График 4 появится после опыта C.</p>')
    contents += ['<h2>Методика, PromQL и ограничения</h2>',
      '<p>Длительности на клиенте измеряются time.perf_counter. Wall clock нужен для выравнивания расписаний четырёх Pod, которые используют часы одной kind-ноды. TTFC — первый содержательный chunk минус отправка; inter-chunk latency — среднее соседних интервалов. p50/p95 рассчитываются только для completed, с числом наблюдений. Отказы, transport_error, aborted, timeout и not_sent учитываются отдельно; повторов нет, attempts=1. Сумма исходов равна числу запланированных запросов. Лимит генератора 64 потока и 128 ожидающих задач на клиент; lag записывается, а переполнение не скрывается.</p>',
      '<pre>'+esc('histogram_quantile(0.95, sum by (le,pod) (rate(lab_ttfc_seconds_bucket[1m])))')+'</pre>',
      '<p>Server Histogram наблюдает completed; клиент включает сетевую задержку и может иначе увидеть обрыв между последним chunk и done. Бакеты дают приближение. Сравнение нужно делать за одинаковый временной диапазон, набор завершённых запросов и Pod; короткие окна у границы rollout дают неполный набор. Готовая команда сверки — tools/reconcile.py. request ID хранится в журналах, метки Prometheus ограничены исходом/Pod/результатом кеша.</p>',
      '<h2>Ревью предложения закрепить диалог за Pod</h2>',
      '<p>При очереди A в 3 секунды и пересчёте контекста на свободной B за 0.8 секунды новый запрос следует направить на B, если оценка включает передачу истории и B проходит проверку готовности и допуска по ёмкости. Экономия около 2.2 секунды относится к указанным условиям; постоянная привязка теряет смысл, когда ожидание дороже пересчёта. Нужны измерения p95 очереди и prefill по длинам контекста, частоты hit/miss, стоимости передачи, доступной памяти, ошибок и точности прогноза очереди. Допуск нового запроса должен учитывать активные вычисления, ограниченную очередь, дедлайн и объём контекста.</p>',
      '<p>История диалога должна приходить от клиента или из внешнего хранилища: кеш A — ускорение, а не единственная копия состояния. После потери A клиент отправляет ту же историю на здоровую реплику, явно учитывая новую попытку и возможный повтор операции. Уже начатый поток нельзя незаметно переселить на B: во время штатного rollout он доживает на A в пределах grace period. После аварии клиент получает признак незавершённости; повтор оформляется отдельной попыткой, частичные фрагменты не склеиваются молча.</p>',
      '<p>Первичный источник для сопоставления: <a href="https://sgl-project.github.io/advanced_features/sgl_model_gateway.html">SGLang Model Gateway</a>. Это технический источник о маршрутизации и локальности кеша, а не подтверждение того, что этот кейс был в лекции. По видео нужно вписать компанию, таймкод и её первичную публикацию; затем связать конкретный вывод кейса с этими измерениями.</p>',
      '<h2>Окружение и исходные материалы</h2>',
      '<p>Версии, лимиты и инвентаризация: evidence/preflight.json, versions.json, final-inventory.json. Настройки опытов: results/*/*/settings.json и schedule.json. Dashboard: monitoring/dashboard.json. Инструкция: README.md. Подробные логи и контрольные результаты: results/*.</p>',
      '<p>Источник основы: <a href="https://github.com/valerianiv/MLOps/tree/22233cf89960b849a2c8f01e817d970f25979dbd">ДЗ 8–10</a>. Точный исходный коммит и характер адаптации указаны в PROVENANCE.md. Предыдущие результаты не выдаются за новые прогоны.</p></html>']
    (ROOT/'report.html').write_text('\n'.join(contents),encoding='utf-8')
    print(f'Report written: {ROOT / "report.html"}; raw requests: {len(all_rows)}; checks: {len(checks)}')


if __name__=='__main__': main()
