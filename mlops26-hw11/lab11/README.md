# Лабораторная №11 — запуск на Windows 10/11, 16 ГБ RAM

Папка содержит приложение, Helm-чарт, мониторинг, нагрузочные опыты и генератор
отчёта. Все команды после установки WSL выполняются **в Ubuntu**, не в PowerShell.
GitHub и Git для выполнения не нужны. Публикация остаётся за автором работы.

**Важно:** комплект подготовлен для запуска. Kubernetes-результаты пока не получены.
После опытов report.html и results/ будут содержать реальные данные. FAIL нельзя
заменять на PASS вручную. Видеолекцию нужно использовать для последнего пункта ревью.

## 1. Что установить один раз

1. Включить виртуализацию в BIOS/UEFI, если она выключена.
2. Открыть PowerShell от администратора и выполнить:

   ```powershell
   wsl --install -d Ubuntu-24.04
   ```

   Перезагрузить Windows, открыть Ubuntu, создать имя пользователя и пароль.
   Если Ubuntu уже есть, проверить `wsl -l -v`: у неё должен быть VERSION 2.
3. Установить [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/),
   запустить и дождаться готовности Engine. В Settings → General включить
   **Use the WSL 2 based engine**; в Resources → WSL Integration включить Ubuntu.
   Встроенный Kubernetes Docker Desktop включать не требуется — используется kind.
4. Рекомендуемый бюджет WSL: 4 CPU, 8 ГБ RAM, swap 2 ГБ. Если нужно задать его явно,
   создать/отредактировать `%UserProfile%\.wslconfig` в Windows:

   ```ini
   [wsl2]
   memory=8GB
   processors=4
   swap=2GB
   ```

   После сохранения закрыть WSL, выполнить `wsl --shutdown` в PowerShell и снова
   открыть Docker Desktop и Ubuntu. Существующие настройки объединить с этими,
   не стирать остальные разделы файла. На ноутбуке с 16 ГБ закрыть тяжёлые программы.

Официальные пояснения: [Docker + WSL2](https://docs.docker.com/desktop/features/wsl/).

## 2. Куда положить папку

Распаковать `lab11.zip` в Windows, например `C:\Users\ИМЯ\Downloads\lab11`.
В Ubuntu скопировать папку в Linux-файловую систему (так быстрее и надёжнее):

```bash
cp -r '/mnt/c/Users/ИМЯ/Downloads/lab11' ~/lab11
cd ~/lab11
```

Заменить ИМЯ и путь на свои. Дальше работать именно в `~/lab11`.

В Ubuntu выполнить:

```bash
sudo apt update
sudo apt install -y python3 python3-venv curl ca-certificates tar unzip
docker version
df -h .
```

У `docker version` должны отображаться Client **и Server**. Если Server отсутствует,
проверить Docker Desktop и WSL Integration. Отдельный Docker Engine в Ubuntu не ставить.
Нужно ориентировочно **12–15 ГБ свободного места** на диске, где хранится Docker/WSL.
Это запас, а не измеренный размер: kind и образы обычно занимают несколько ГБ.
Prometheus ограничен 384 МБ/6 часами, его том 512 MiB; Grafana 128 MiB.
Фактические размеры до/после сохраняются в evidence/; отдельно проверить свободное
место диска Windows через Проводник. Место Linux VHDX не всегда автоматически
возвращается Windows после удаления файлов.

## 3. Установить инструменты и поднять стенд

```bash
cd ~/lab11
bash scripts/install-tools.sh
bash scripts/lab.sh setup
```

Установщик скачает закреплённые kind 0.27.0, kubectl 1.32.2, Helm 3.17.2 и
metrics-server 0.7.2. Бинарные инструменты проверяются по опубликованным SHA-256,
хеши сохраняются. Python-зависимости закреплены с хешами в requirements.lock.
Загрузка и сборка требуют интернета. Первая установка может занять 10–25 минут,
в зависимости от сети. setup создаёт только кластер `mlops-lab11` и namespace `lab11`.
Команды Kubernetes явно указывают контекст `kind-mlops-lab11`.

Образы приложения/мониторинга закреплены версиями; kind-node дополнительно digest.
Фактически использованные imageID/digest сохраняются в снимках Pod и versions.json.
Пакет предназначен для локального учебного кластера, без публикации портов в интернет.
`--kubelet-insecure-tls` у metrics-server используется только внутри локального kind.

После установки:

```bash
export PATH="$PWD/.tools:$PATH"
kubectl --context kind-mlops-lab11 -n lab11 get pods -o wide
kubectl --context kind-mlops-lab11 -n lab11 top pods
kubectl --context kind-mlops-lab11 get --raw /apis/metrics.k8s.io/v1beta1/pods
```

Если `top` пока недоступен, подождать 30–60 секунд. Затем переходить к опытам.

## 4. Проверки и эксперименты

Не запускать фазы параллельно: они меняют одну и ту же конфигурацию сервиса.

Локальные проверки приложения (без Kubernetes):

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Полный цикл одной командой (ориентир **45–70 минут**, без первой установки):

```bash
set -o pipefail
bash scripts/lab.sh all 2>&1 | tee run.log
```

Или последовательно по частям, чтобы разбирать результат:

```bash
bash scripts/lab.sh check
bash scripts/lab.sh a
bash scripts/lab.sh b
bash scripts/lab.sh c
bash scripts/lab.sh inventory
bash scripts/lab.sh report
```

`all` уже включает эти шаги. Не запускать их второй раз в ту же results/.

- **check:** холодная readiness в Kubernetes, замена Pod с тем же хешем, изоляция
  диалогов, правильное различение отказа и оборванного ответа.
- **A:** одинаковые расписания broken/fixed, rollout A→B с длинным потоком,
  исключение заведомо неготовой B, возврат A, аварийный выход os._exit(137) и восстановление.
- **B:** два Service × два вида трафика × три повтора × 120 секунд измерения.
  Backend во всех вариантах один и тот же; кеш очищается полной заменой Pod.
  Порядок прогонов сохранён, ноутбук подключить к питанию, отключить переход в сон.
- **C:** вычислительная нагрузка, HPA 2→3→2, повтор горячего клиента, два алерта.

В терминале появляются PASS/FAIL, подробности — `results/*/checks.jsonl`.
Автоматическое завершение скрипта не означает, что все условия выполнены: просмотреть
проверки и отчёт. Непрерывные снимки Ready, EndpointSlice, HPA и ALERTS сохраняются.
У уже запущенного потока HTTP 200 ещё не означает успех — клиент ждёт `done`.

### Если опыт не дал ожидаемого эффекта

Сначала сохранить исходную results/ вместе с настройками и run.log. Например:

```bash
mv results results-first-attempt
```

Старые данные не удалять. После анализа повторить нужную фазу, например:

```bash
bash scripts/lab.sh b --rps 12
bash scripts/lab.sh c --hpa-rps 32
```

`--rps` меняет входящую нагрузку B и горячей фазы C; `--hpa-rps` — HPA/алерт-стимул.
У B нельзя сокращать измерение ниже 120 с и число повторов ниже трёх.
При сравнении политик менять нагрузку одинаково для всех комбинаций.
Не увеличивать частоту, если велик dispatch_lag или есть not_sent: это перегрузка
генератора, а не только сервиса. Сначала снизить частоту/закрыть фоновые программы.
Если перекоса нет, сохранить этот результат и объяснить его по Pod/IP/очередям.
Если HPA не масштабируется, проверить CPU requests, metrics API, условия HPA и
наличие именно CPU-нагрузки. Если новый Pod Pending — проверить ресурсы ноутбука.

## 5. Grafana, Prometheus и сверка

Открыть ещё два терминала Ubuntu, в каждом:

```bash
cd ~/lab11
export PATH="$PWD/.tools:$PATH"
```

В первом:

```bash
kubectl --context kind-mlops-lab11 -n lab11 port-forward svc/grafana 3000:3000
```

Открыть в Windows `http://localhost:3000`, выбрать дашборд Lab11. Вход не нужен,
анонимный доступ только для просмотра. Выбрать диапазон фактического опыта.
Сохранённый исходный экспорт — `monitoring/dashboard.json`.

Во втором:

```bash
kubectl --context kind-mlops-lab11 -n lab11 port-forward svc/prometheus 19090:9090
```

Открыть `http://localhost:19090`. **Не держать этот port-forward во время опытов**:
их наблюдатель сам использует 19090. После опытов до удаления кластера выполнить
в третьем терминале сверку одного полного прогона:

```bash
cd ~/lab11
.venv/bin/python -m tools.reconcile results/B/hot-api-1
```

Получится `reconciliation.json`: клиентский p95, PromQL, серверный p95, число
наблюдений и buckets за один диапазон. Объяснить разницу сетевым временем,
интерполяцией histogram и границами scrape, проверить совпадение набора исходов.
Для низкой погрешности использовать весь 120-секундный прогон, не короткий участок
rollout. Данные Prometheus живут 6 часов и исчезают при удалении его Pod/кластера.

В **Status → Rules** проверить алерты. Переходы сохраняет и наблюдатель в
results/C/prometheus.jsonl. Отсутствие ошибки в PromQL не доказывает firing.

## 6. Отчёт и сдача

```bash
bash scripts/lab.sh inventory
bash scripts/lab.sh report
explorer.exe .
```

Открыть `report.html`. Генератор создаёт сводную `results/requests.csv` и четыре SVG
в `results/figures/`, если есть необходимые данные. Исходные JSONL остаются рядом.
Графики 1 и 4 показывают две подписанные величины на общей шкале; при подробном
разборе использовать также Grafana с отдельными панелями CPU/очередей/Ready.

Перед сдачей:

1. Проверить все PASS/FAIL, результаты до/после, отсутствие 5xx и обрывов в штатном
   rollout. Принудительный аварийный выход os._exit(137) рассматривается отдельно.
2. По реальным таблицам написать окончательный выбор политики. В `report.html`
   дано объяснение методики, но оценку измеренных различий нужно проверить.
3. Заполнить ФИО, группу, ссылку на свой репозиторий и коммит.
4. В `REVIEW.md` и отчёте указать компанию **из видеолекции**, таймкод и первичный
   источник; готовый текст ревью занимает примерно половину страницы. Достаточно
   найти нужный фрагмент видео — пересылать все 45 минут не требуется.
5. При необходимости сохранить PDF через браузер → Печать → Сохранить как PDF.
   После ручной правки report.html не запускать генератор без сохранения копии:
   он перезапишет файл.

Скопировать готовую папку обратно в Windows:

```bash
mkdir -p '/mnt/c/Users/ИМЯ/Desktop/lab11-ready'
cp -r app artifacts chart infra monitoring tools tests reference scripts \
  requirements.in requirements.lock Dockerfile README.md PROVENANCE.md REVIEW.md \
  report.html results evidence run.log '/mnt/c/Users/ИМЯ/Desktop/lab11-ready/'
```

Если запускали по частям и run.log отсутствует, убрать его из команды.
Затем самостоятельно загрузить код и результаты в свой репозиторий. `.venv`,
`.tools`, vendor и кеши публиковать не нужно; содержимое evidence проверить на
локальные имена пользователя/пути, секреты Kubernetes и kubeconfig не публиковать.
Скрипты не сохраняют kubeconfig или токены service account.

## 7. Остановка и удаление

Сначала сохранить результаты и reconciliation.json. В окнах port-forward нажать
Ctrl+C, затем:

```bash
cd ~/lab11
bash scripts/lab.sh cleanup
```

Удаляется только кластер kind с именем `mlops-lab11`, включая его учебные тома и
мониторинг. Папка результатов остаётся. При необходимости удалить только образ
приложения:

```bash
docker image rm lab11-api:1.0.0
```

Не применять глобальные `docker system prune` к ноутбуку: там могут быть другие
проекты. Базовые скачанные образы/кеш сборки могут остаться; их размер виден в
`docker system df -v`, удалять вручную только проверенные ненужные элементы.

## Содержимое

| Путь | Назначение |
|---|---|
| app/ | FastAPI, CPU-имитатор, очередь, streaming, probes и метрики |
| artifacts/ | A/B и ожидаемые SHA-256 |
| chart/ | Helm Deployment/ConfigMap/два Service/HPA |
| infra/ | kind и Prometheus/Grafana/kube-state-metrics |
| monitoring/ | Экспорт Grafana |
| tools/ | Генератор нагрузки, сценарии Kubernetes, отчёт и сверка |
| tests/ | Вововосемь локальных HTTP/генераторных проверок |
| reference/ | Исходные файлы ДЗ 8–10 для прозрачности адаптации |
| evidence/ | Проверки комплекта и инвентаризация стенда |
| results/ | Только новые сырые измерения и графики |

Дополнительные источники: [probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/),
[HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/),
[histogram](https://prometheus.io/docs/practices/histograms/),
[kind 0.27 / Kubernetes 1.32.2](https://github.com/kubernetes-sigs/kind/releases/tag/v0.27.0).
Это закреплённый учебный набор версий, не рекомендация для производственной эксплуатации.
