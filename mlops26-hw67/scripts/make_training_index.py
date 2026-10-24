"""data/train.jsonl (вход стадии tokenize ДЗ 4) -> data/eval/training-index.jsonl.

Полный список исходных train-вопросов: id, group_id, input. Берётся из ТЕХ ЖЕ
сырых строк, что токенизировались в data/tokenized/train.pt, поэтому:
  * если train.pt доступен, скрипт сверяет, что каждый id из тензоров есть в индексе;
  * результат сверки пишется в metrics/training-index.json (для DVC и отчёта).

Формат строки train.jsonl (ДЗ 3/4): {"id", "topic", "messages": [{role, content}, ...]}.
input — текст реплик пользователя (без ответа ассистента): ровно то, с чем
сравнивается golden при проверке утечки.

Запуск:  uv run python scripts/make_training_index.py [--train data/train.jsonl] [--tokenized data/tokenized/train.pt]
"""
import argparse
import hashlib
import json
from pathlib import Path


def user_text(messages):
    parts = [m["content"] for m in messages if m.get("role") == "user" and str(m.get("content", "")).strip()]
    if not parts:
        raise ValueError("в диалоге нет реплики пользователя")
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/train.jsonl")
    ap.add_argument("--tokenized", default="data/tokenized/train.pt")
    ap.add_argument("--out", default="data/eval/training-index.jsonl")
    args = ap.parse_args()

    src = Path(args.train)
    if not src.exists():
        raise SystemExit(f"Нет {src}: положите сюда выход стадии split из ДЗ 3 (тот же файл, что токенизировался в ДЗ 4)")
    rows, seen = [], set()
    for n, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        rid = str(record.get("id") or f"line-{n}")
        if rid in seen:
            raise SystemExit(f"{src}:{n}: повтор id {rid}")
        seen.add(rid)
        rows.append({"id": f"train-{rid}", "group_id": f"train-{rid}", "input": user_text(record["messages"]),
                     "topic": record.get("topic")})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")

    proof = {"train_jsonl": str(src), "train_jsonl_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
             "n_index": len(rows)}
    tok = Path(args.tokenized)
    if tok.exists():
        import torch
        blob = torch.load(tok, weights_only=True)
        ids = [str(e.get("id")) for e in blob["examples"] if e.get("id") is not None]
        missing = sorted(set(ids) - seen)
        proof.update(tokenized=str(tok), n_tokenized=len(blob["examples"]), n_tokenized_with_id=len(ids),
                     missing_in_index=len(missing))
        if missing:
            raise SystemExit(f"В {tok} есть id, которых нет в {src}: {missing[:5]} — индекс собран не из тех данных")
    Path("metrics").mkdir(exist_ok=True)
    Path("metrics/training-index.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
