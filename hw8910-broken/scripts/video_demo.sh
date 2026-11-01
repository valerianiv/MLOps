#!/usr/bin/env bash
# Показ для видео одной командой: make video. Запускать из Git Bash.
# Полные тесты идут параллельно, их итог печатается в конце. Нужен запущенный Docker Desktop.
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/Scripts/python.exe}
HW67=../mlops26-hw67
DEMO=.lab-demo
export PYTHONUTF8=1
TMP=$(mktemp -d)
STARTED=$SECONDS

title() { echo; echo "================ $* ================"; sleep 2; }
demo() { echo "\$ python lab.py $*"; "$PY" lab.py --workspace "$DEMO" "$@" 2>&1 | grep -E "^(PREPARED|CACHE_HIT|PUBLISHED|ALREADY_PUBLISHED|RECOVERED|REJECTED|RuntimeError)" | cut -c1-110; }
real() { HF_HOME="$PWD/models/hf-home" HF_HUB_OFFLINE=1 "$PY" lab.py --workspace .lab-real run --hw67 "$HW67" --role "$1" 2>&1 | grep -E "^(PREPARED|CACHE_HIT|PUBLISHED|ALREADY_PUBLISHED|REJECTED)" | cut -c1-120; }

title "ТЕСТЫ: запущен полный прогон, 13 публичных + 11 интеграционных; итог в конце"
"$PY" scripts/check.py > "$TMP/check.log" 2>&1 &
CHECK=$!

title "ТЕСТЫ: те же 11 тестов на ИСХОДНОЙ версии, обязаны упасть"
mkdir -p "$TMP/orig" && git archive 72a7c67 . | tar -x -C "$TMP/orig"
HW_TARGET_DIR="$TMP/orig" "$PY" -m unittest discover -s integration_tests -t . 2>&1 | grep -E "^(AssertionError|Ran|FAILED)" | grep -v "^AssertionError: False is not true" | cut -c1-90

title "ТЕСТЫ: намеренная поломка в копии кода, ожидается ненулевой код"
"$PY" scripts/check.py --demo-break 2>&1 | grep -E "Намеренная|^FAILED|public-tests: exit" | cut -c1-110

title "ПРОЦЕСС: Prefect-flow, задачи"
grep -E "^@(flow|task)" flow.py | cut -c1-70
rm -rf "$DEMO"
"$PY" lab.py --workspace "$DEMO" init

title "ПРОЦЕСС: сбой после подготовки"
demo run --fail-after-prepare

title "ПРОЦЕСС: новый процесс, подготовка берётся из кеша"
demo run

title "ПРОЦЕСС: повтор выпуска, второй публикации нет"
demo run
cut -d, -f3,4 "$DEMO/publications.jsonl"

title "ПРОЦЕСС: новые байты по прежнему пути, кеш не используется"
echo "новые байты" > "$DEMO/input.txt"
demo run

title "ОТКАЗ: неполный отчёт на стенде, хеш указателя не меняется"
sha256sum "$DEMO/accepted.json"
demo run --report report-incomplete.json
sha256sum "$DEMO/accepted.json"

title "ОТКАЗ: настоящий кандидат, quality_gate ДЗ 6+7"
"$PY" scripts/link_base_snapshot.py --cache D:/hf-cache --hf-home models/hf-home > /dev/null
docker start mlops-hw6-db-1 mlops-hw6-mlflow-1 > /dev/null
sleep 8
sha256sum .lab-real/accepted.json
real candidate
sha256sum .lab-real/accepted.json
echo "Принятая версия остаётся baseline:"
real baseline

title "API: настоящая модель, запуск, загрузка, неверные запросы, ответ"
cp evidence/real/smoke.json "$TMP/smoke.json"
MSYS_NO_PATHCONV=1 wsl -d Debian --cd /mnt/d/mlops/MLOps/hw8910-broken -- bash scripts/wsl_serve.sh smoke 2>&1 | grep -vE "Loading weights|^transformers:"
cp "$TMP/smoke.json" evidence/real/smoke.json

title "ИЗМЕРЕНИЯ: сохранённый журнал, 8 + 8 запросов, параллельность 1 и 2"
cat evidence/real/load-summary.csv
echo "Запр/с почти те же, задержка выросла из-за очереди: сервер считает по одному запросу."
sleep 8

title "ВЕРСИИ: A -> B -> A на STUB, сохранённый журнал"
sed -n '/===== 5/,$p' evidence/experiments.log | grep -E "работает на|без перезапуска|после перезапуска|после отката|publications" | cut -c1-120
sleep 6

title "ТЕСТЫ: итог полного прогона на исправленной версии"
if kill -0 "$CHECK" 2> /dev/null; then echo "Прогон ещё идёт, ждём завершения..."; fi
wait "$CHECK"
CODE=$?
grep -E "^(Ran|OK|FAILED)|^=== .*exit" "$TMP/check.log" | cut -c1-90
grep -E "^test_.* \.\.\. |\.\.\. (ok|FAIL|ERROR)$" "$TMP/check.log" | grep -cE "ok$" | sed 's/^/Тестов и проверок с результатом ok: /'
echo "Код завершения полного прогона: $CODE"
echo "Показ занял $((SECONDS - STARTED)) с"
rm -rf "$TMP" "$DEMO"
exit "$CODE"
