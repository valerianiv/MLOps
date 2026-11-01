#!/usr/bin/env bash
# Настоящий API в окружении оценки ДЗ 6+7: тот же интерпретатор и та же сборка torch.
# FastAPI лежит отдельно в ~/hw8910-serve, .venv ДЗ 6+7 не меняется.
# Без аргументов запускает сервис; иначе передаёт их в scripts/real_api.py.
set -e
cd "$(dirname "$0")/.."
PY=../mlops26-hw67/.venv/bin/python
if [ ! -d ~/hw8910-serve/fastapi ]; then
  UV=$(command -v uv || echo ~/.local/bin/uv)
  "$UV" pip install -q --python "$PY" --target ~/hw8910-serve fastapi==0.150.0 uvicorn==0.54.0
fi
export PYTHONPATH=~/hw8910-serve HF_HOME=${HF_CACHE:-/mnt/d/hf-cache} HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1
if [ "$#" -gt 0 ]; then
  exec "$PY" scripts/real_api.py "$@" --workspace .lab-real --hw67 ../mlops26-hw67
fi
exec "$PY" service.py --workspace .lab-real --backend transformers --port "${PORT:-8091}" --warmup-seconds 0
