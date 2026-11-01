ifeq ($(OS),Windows_NT)
PYTHON ?= .venv/Scripts/python.exe
else
PYTHON ?= .venv/bin/python
endif
HW67 ?= ../mlops26-hw67
ORIGINAL ?= 72a7c67
export PYTHONUTF8 = 1
WSL = MSYS_NO_PATHCONV=1 wsl -d Debian --cd /mnt/d/mlops/MLOps/hw8910-broken -- bash scripts/wsl_serve.sh
REAL_ENV = HF_HOME="$(CURDIR)/models/hf-home" HF_HUB_OFFLINE=1
.PHONY: video check check-public red red-green break-demo experiments real-init evidence-real serve-real smoke-real replay-real loadtest-real

# Показ для видео одной командой: тесты, процесс, отказ, API, измерения. Нужен Docker Desktop.
video:
	bash scripts/video_demo.sh

# Быстрые проверки без весов, около 5 минут.
check:
	$(PYTHON) scripts/check.py
check-public:
	$(PYTHON) -m unittest discover -s tests -v
# Те же 11 тестов на исходном коммите: обязаны упасть, поэтому код завершения не 0. Около 7 секунд.
red:
	rm -rf /tmp/orig && mkdir -p /tmp/orig && git archive $(ORIGINAL) . | tar -x -C /tmp/orig
	HW_TARGET_DIR=/tmp/orig $(PYTHON) -m unittest discover -s integration_tests -t .
# Красный и зелёный прогоны и 7 мутаций, около 9 минут. Перезаписывает журналы в evidence/.
red-green:
	$(PYTHON) scripts/red_green.py
# Намеренная поломка во временной копии: ожидается ненулевой код завершения.
break-demo:
	$(PYTHON) scripts/check.py --demo-break
experiments:
	$(PYTHON) scripts/run_experiments.py

# Настоящий выпуск baseline: снимок базы и Prefect-flow.
real-init:
	$(PYTHON) scripts/link_base_snapshot.py --cache D:/hf-cache --hf-home models/hf-home
	$(REAL_ENV) $(PYTHON) lab.py --workspace .lab-real run --hw67 $(HW67) --role baseline
# Настоящая попытка выпуска кандидата: отказ gate ожидаем, указатель не должен измениться.
# Нужны запущенный Docker Desktop и MLflow ДЗ 6+7.
evidence-real:
	docker start mlops-hw6-db-1 mlops-hw6-mlflow-1
	sha256sum .lab-real/accepted.json
	@if $(REAL_ENV) $(PYTHON) lab.py --workspace .lab-real run --hw67 $(HW67) --role candidate; then \
		echo "ОШИБКА: кандидат выпущен"; exit 1; else echo "Отказ получен, как и ожидалось"; fi
	sha256sum .lab-real/accepted.json

# Настоящий API в окружении оценки (WSL), порт 8091. Остановка: Ctrl+C.
serve-real:
	$(WSL)
# Запуск, 503 при загрузке, 422 на неверные запросы, настоящий ответ. Перезаписывает evidence/real/smoke.json.
smoke-real:
	$(WSL) smoke
replay-real:
	$(WSL) replay
loadtest-real:
	$(WSL) loadtest
