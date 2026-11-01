ДЗ 8+9+10: процесс выпуска модели и сервис

Исправленный проект. Отчёт — REPORT.txt, разбор дефектов — defects.csv,
журналы — evidence/. Исходный прототип лежит в коммите 72a7c67.

Команды для PowerShell. В Git Bash используйте .venv/Scripts/python.exe
и export вместо $env:. Задание предполагает Python 3.12.

Короткие команды (нужен make, запускать из Git Bash)
  make check          все быстрые тесты, около 5 минут
  make red            те же 11 тестов на исходном коммите, должны упасть, около 7 секунд
  make break-demo     намеренная поломка в копии, ожидается ошибка
  make red-green      красный и зелёный прогоны и 7 мутаций, около 9 минут; перезаписывает evidence/
  make real-init      настоящий выпуск baseline
  make evidence-real  настоящая попытка выпуска кандидата, отказ ожидаем; нужен Docker
  make serve-real     настоящий API в WSL на порту 8091, остановка Ctrl+C
  make smoke-real     опыт с настоящим API; перезаписывает evidence/real/smoke.json

Быстрые проверки: без весов, torch и сети
  uv sync --frozen --python 3.12
  .venv\Scripts\python.exe scripts\check.py               # около 5 минут
  .venv\Scripts\python.exe scripts\check.py --demo-break  # ожидается код не 0
  .venv\Scripts\python.exe scripts\red_green.py           # красный/зелёный + 7 мутаций
  .venv\Scripts\python.exe -m unittest discover -s tests -v   # только публичные 13 тестов
Сеть нужна только для uv sync. Prefect поднимает временный локальный сервер.

Стенд STUB ONLY: управление, а не оценка модели
  .venv\Scripts\python.exe lab.py --workspace .lab-a init
  .venv\Scripts\python.exe lab.py --workspace .lab-a run --fail-after-prepare
  .venv\Scripts\python.exe lab.py --workspace .lab-a run
  .venv\Scripts\python.exe lab.py --workspace .lab-a rollback --to fixture-A
  .venv\Scripts\python.exe service.py --workspace .lab-a --port 8091 --warmup-seconds 10
Для каждого опыта берите новый --workspace. Все опыты разом: scripts\run_experiments.py.

Настоящий выпуск из ДЗ 6+7
  uv sync --frozen --python 3.12 --extra model
  docker start mlops-hw6-db-1 mlops-hw6-mlflow-1     # MLflow ДЗ 6+7, порт 5006
  .venv\Scripts\python.exe scripts\link_base_snapshot.py --cache D:/hf-cache --hf-home models/hf-home
  $env:HF_HOME = "$PWD\models\hf-home"; $env:HF_HUB_OFFLINE = "1"; $env:PYTHONUTF8 = "1"
  .venv\Scripts\python.exe lab.py --workspace .lab-real run --hw67 ..\mlops26-hw67 --role baseline
  .venv\Scripts\python.exe lab.py --workspace .lab-real run --hw67 ..\mlops26-hw67 --role candidate
Вторая команда обязана отказать: кандидат не прошёл gate ДЗ 6+7 (25/56 < 30/56).
link_base_snapshot.py нужен, потому что кеш D:/hf-cache создан в WSL, а его симлинки
Windows-процесс не читает. Скрипт создаёт жёсткие ссылки на те же blob-файлы и сверяет
их хеши; сам кеш не меняется. Если базу скачал Windows-процесс, достаточно задать HF_HOME.
Без MLflow не сработает только --role candidate: baseline читается из снимка.

Сервис настоящей модели
  .venv\Scripts\python.exe service.py --workspace .lab-real --backend transformers --port 8091 --warmup-seconds 0
  curl.exe http://127.0.0.1:8091/ready
В Git Bash запрос генерации:
  curl -s http://127.0.0.1:8091/generate -H 'Content-Type: application/json' -d '{"prompt":"..."}'
Опыты с моделью: scripts\real_api.py smoke | replay | loadtest (результаты в evidence/real).

Окружение оценки: WSL Debian, интерпретатор и torch 2.14.0+cu130 из .venv ДЗ 6+7.
На Windows torch 2.14.0+cpu один ответ из 59 расходится с оценкой (см. REPORT.txt, п. 7),
поэтому настоящий API, совпадающий с оценкой, запускается так, из Git Bash:
  MSYS_NO_PATHCONV=1 wsl -d Debian --cd /mnt/d/mlops/MLOps/hw8910-broken -- bash scripts/wsl_serve.sh
scripts/wsl_serve.sh при первом запуске ставит fastapi и uvicorn в ~/hw8910-serve,
окружение ДЗ 6+7 не меняется. Запросы к нему: http://127.0.0.1:8091 (порт пробрасывает WSL).

Устройство
  release.py       ключ кеша, контракт отчёта, атомарный указатель, журнал, откат (только stdlib)
  flow.py          Prefect-flow: prepare-input → fetch-artifact → evaluate-gate → publish-release
  sources.py       источник данных flow: STUB-стенд или ДЗ 6+7
  hw67_bridge.py   вызов исходных score/quality_gate ДЗ 6+7 в отдельном процессе
  lab.py           CLI: init, run, rollback
  service.py       FastAPI: /health, /ready, /generate; /debug только у stub
  backends.py      StubBackend и TransformersBackend с общим интерфейсом
  tests/           публичные 13 тестов, не изменялись
  integration_tests/  11 собственных тестов; HW_TARGET_DIR выбирает проверяемую версию

Веса, .venv, кеши, рабочие каталоги .lab* в Git не входят. Версии закреплены в uv.lock.
Базовые исходные материалы задания: INCIDENTS.txt, CONTRACTS.txt.
