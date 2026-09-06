"""Генерация ответа на промпт из конфига."""

import random
import torch
from src.config import load_params
from src.model import generate, load_model


def main() -> None:
    params = load_params()
    
    # Фиксируем seed для воспроизводимости
    seed = params["generate"]["seed"]
    torch.manual_seed(seed)
    random.seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    tokenizer, model = load_model(params)
    print(f"Модель: {params['model']['name']}")
    text, n_tokens = generate(tokenizer, model, params, params["bench"]["prompt"])

    print(text)
    print(f"\n[{n_tokens} токенов]")


if __name__ == "__main__":
    main()