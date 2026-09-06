"""Замер производительности машины на выбранной модели.

Три числа меряются РАЗДЕЛЬНО — смешивать их бессмысленно:
  * время загрузки модели  — разовая стоимость старта;
  * tokens/sec             — скорость генерации, только после прогрева;
  * пиковая RSS            — максимум за процесс, а не снимок в конце.
"""

import json
import statistics
import time
from pathlib import Path

import psutil

from src.config import load_params
from src.model import generate, load_model


def peak_rss_mb() -> float:
    """Пиковая резидентная память процесса."""
    process = psutil.Process()
    return process.memory_info().peak_wset / (1024 * 1024)


def main() -> None:
    params = load_params()
    prompt = params["bench"]["prompt"]

    # 1. Замер времени загрузки модели
    t0 = time.perf_counter()
    tokenizer, model = load_model(params)
    load_time = time.perf_counter() - t0

    # 2. Прогрев (warmup) — не засчитывается
    print("Прогрев модели...")
    warmup_runs = params["bench"].get("warmup_runs", 1)
    for _ in range(warmup_runs):
        generate(tokenizer, model, params, prompt)

    # 3. Основные замеры
    speeds = []
    measure_runs = params["bench"]["measure_runs"]
    for _ in range(measure_runs):
        t_start = time.perf_counter()
        _, n_tokens = generate(tokenizer, model, params, prompt)
        elapsed = time.perf_counter() - t_start
        speeds.append(n_tokens / elapsed)

    # Медиана устойчивее среднего к одиночному выбросу.
    report = {
        "model": params["model"]["name"],
        "device": str(model.device),
        "dtype": params["model"]["dtype"],
        "load_time_sec": round(load_time, 2),
        "tokens_per_sec": round(statistics.median(speeds), 2),
        "tokens_per_sec_all": [round(s, 2) for s in speeds],
        "peak_rss_mb": round(peak_rss_mb(), 1),
    }

    Path("docs").mkdir(exist_ok=True)
    Path("docs/bench.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()