"""Golden и behavioral набор для темы ДЗ 4–5: следование инструкциям с проверяемыми ограничениями.

Каждый вопрос написан вручную (инструкция + ответ(ы)), а метка проверена ДВАЖДЫ:
ручной меткой `label` в данных и программной проверкой ограничений `check()`.
Если они расходятся — скрипт падает, и вопрос надо исправить, а не метку подогнать.

Запуск:  uv run python scripts/build_eval_sets.py
Выход:   data/eval/golden.jsonl, data/eval/behavioral.jsonl

Срезы (могут пересекаться, в общий n не суммируются):
  тип ограничения: length | keywords | format | case_punct
  форма задания:   yes_no (A=выполнено, B=нарушено) | pick_one (какой из A/B/C выполняет всё)
"""
import json
import re
from pathlib import Path

OUT = Path("data/eval")
SOURCE = "Написано вручную для ДЗ 6+7 (не из train); метка сверена с scripts/build_eval_sets.py::check"


# ---------------------------------------------------------------- проверка ограничений

def words(text):
    return re.findall(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*", text)


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def whole(word, text, flags=0):
    return len(re.findall(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", text, flags))


def bullets(text):
    return [l for l in text.splitlines() if re.match(r"\s*[-*]\s+\S", l)]


def ok(c, text):
    kind, *a = c
    if kind == "words_max":
        return len(words(text)) <= a[0]
    if kind == "words_min":
        return len(words(text)) >= a[0]
    if kind == "words_exact":
        return len(words(text)) == a[0]
    if kind == "sentences":
        return len(sentences(text)) == a[0]
    if kind == "include":
        return whole(a[0], text) >= 1
    if kind == "exclude":
        return whole(a[0], text, re.I) == 0
    if kind == "count":
        return whole(a[0], text) == a[1]
    if kind == "bullets":
        return len(bullets(text)) == a[0]
    if kind == "starts":
        return text.startswith(a[0])
    if kind == "ends":
        return text.rstrip().endswith(a[0])
    if kind == "title":
        return re.search(r"<<[^<>]+>>", text) is not None
    if kind == "sections":
        return len(re.findall(r"^SECTION \d+", text, re.M)) == a[0]
    if kind == "paragraphs":
        return len([p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]) == a[0]
    if kind == "upper":
        return text == text.upper()
    if kind == "lower":
        return text == text.lower()
    if kind == "no_comma":
        return "," not in text
    raise ValueError(kind)


def check(constraints, text):
    return all(ok(c, text) for c in constraints)


# ---------------------------------------------------------------- вопросы
# yes_no: (id, slice, instruction, constraints, response, label A|B)
YES_NO = [
    # --- length
    ("len-01", "length", "Describe a lighthouse at night in at most 20 words.", [("words_max", 20)],
     "A white tower sweeps its beam across black water, guiding fishing boats safely past the rocks.", "A"),
    ("len-02", "length", "Explain why bread rises in no more than 15 words.", [("words_max", 15)],
     "Yeast eats the sugars in the dough and releases carbon dioxide gas, which gets trapped in gluten and makes it rise.", "B"),
    ("len-03", "length", "Summarize the benefits of cycling to work in exactly 3 sentences.", [("sentences", 3)],
     "Cycling saves money on fuel. It builds daily exercise into your routine. It also cuts traffic and pollution.", "A"),
    ("len-04", "length", "Give advice to a first-time runner in exactly 2 sentences.", [("sentences", 2)],
     "Start slowly and walk when you need to. Buy shoes that fit well. Rest at least one day per week.", "B"),
    ("len-05", "length", "Write a product slogan for a reusable bottle using at least 8 words.", [("words_min", 8)],
     "Drink more, waste less, carry it everywhere.", "B"),
    ("len-06", "length", "Describe a rainy Sunday morning in at least 25 words.", [("words_min", 25)],
     "Rain taps on the window while the kettle starts to whistle. The street outside is empty, the cat sleeps on the sofa, and nobody is in a hurry to go anywhere today.", "A"),
    ("len-07", "length", "Define photosynthesis in exactly 10 words.", [("words_exact", 10)],
     "Plants turn sunlight, water and air into food.", "B"),
    ("len-08", "length", "Explain what a budget is in exactly 4 sentences.", [("sentences", 4)],
     "A budget is a plan for your money. It lists what you expect to earn. It also lists what you expect to spend. Comparing the two shows whether you can save.", "A"),
    ("len-09", "length", "Recommend a board game for a family in under 12 words.", [("words_max", 11)],
     "Try Ticket to Ride because it is simple and fun.", "A"),
    # --- keywords
    ("kw-01", "keywords", "Describe the value of teamwork and use the word SYNERGY at least once.", [("include", "SYNERGY")],
     "When people share ideas, the SYNERGY of the group produces results no single person could reach.", "A"),
    ("kw-02", "keywords", "Explain how to save energy at home without using the word electricity.", [("exclude", "electricity")],
     "Switch off lights when you leave a room, and unplug chargers, because idle devices still draw electricity.", "B"),
    ("kw-03", "keywords", "List reasons to visit a museum. Use the word CURIOUS exactly twice.", [("count", "CURIOUS", 2)],
     "Museums reward CURIOUS minds. You see real objects from history, and CURIOUS children ask better questions afterwards.", "A"),
    ("kw-04", "keywords", "Write a short note about recycling. Use the word EFFECTIVE only once.", [("count", "EFFECTIVE", 1)],
     "Sorting waste is EFFECTIVE. Composting is EFFECTIVE too, and both reduce landfill use.", "B"),
    ("kw-05", "keywords", "Describe a healthy breakfast without mentioning sugar.", [("exclude", "sugar")],
     "Oatmeal with berries, a boiled egg and a glass of water will keep you full until lunch.", "A"),
    ("kw-06", "keywords", "Explain the role of a referee and include the keywords fairness and whistle.", [("include", "fairness"), ("include", "whistle")],
     "A referee enforces the rules, protects player safety and stops the game when a foul happens.", "B"),
    ("kw-07", "keywords", "Describe a city park. Do not use the words green or tree.", [("exclude", "green"), ("exclude", "tree")],
     "Children play near the fountain while joggers circle the lake and old men argue over chess.", "A"),
    ("kw-08", "keywords", "Explain why sleep matters. The word BRAIN must appear exactly three times.", [("count", "BRAIN", 3)],
     "Sleep lets the BRAIN store memories. Without rest the BRAIN works slowly.", "B"),
    ("kw-09", "keywords", "Write a tip about studying and avoid the words exam and test.", [("exclude", "exam"), ("exclude", "test")],
     "Review your notes for twenty minutes each day instead of cramming the night before the test.", "B"),
    # --- format
    ("fmt-01", "format", "List exactly three tips for remote work as markdown bullet points.", [("bullets", 3)],
     "- Keep a fixed schedule\n- Use a separate workspace\n- Take short breaks", "A"),
    ("fmt-02", "format", "Give exactly four packing items for a hike as bullet points.", [("bullets", 4)],
     "- Water\n- Map\n- Rain jacket", "B"),
    ("fmt-03", "format", "Write a short poem about winter with a title wrapped in double angular brackets, like <<title>>.", [("title",)],
     "<<First Snow>>\nThe street goes quiet,\nfootprints fill with white.", "A"),
    ("fmt-04", "format", "Start your answer with the word 'Honestly' and describe your favorite season.", [("starts", "Honestly")],
     "I would say autumn, because the air is cool and the leaves change color.", "B"),
    ("fmt-05", "format", "Describe a train journey and end your response with the exact phrase 'Is there anything else I can help with?'", [("ends", "Is there anything else I can help with?")],
     "The train left at dawn and followed the river through three valleys. Is there anything else I can help with?", "A"),
    ("fmt-06", "format", "Write a plan for learning guitar with exactly 2 sections, each marked SECTION X.", [("sections", 2)],
     "SECTION 1\nLearn four basic chords.\nSECTION 2\nPractice switching between them.\nSECTION 3\nPlay a full song.", "B"),
    ("fmt-07", "format", "Write two paragraphs about the ocean, separated by a blank line.", [("paragraphs", 2)],
     "The ocean covers most of the planet.\n\nIt also produces much of the oxygen we breathe.", "A"),
    ("fmt-08", "format", "Write a motivational message that ends with the word 'Onward!'", [("ends", "Onward!")],
     "Every small step counts, so keep going even when progress feels slow. Onward! You can do it.", "B"),
    ("fmt-09", "format", "Give exactly two reasons to drink water, formatted as bullet points.", [("bullets", 2)],
     "- It keeps you focused\n- It helps your body control its temperature", "A"),
    # --- case / punctuation
    ("cp-01", "case_punct", "Describe your morning routine in all capital letters.", [("upper",)],
     "I WAKE UP AT SEVEN, MAKE COFFEE AND READ THE NEWS.", "A"),
    ("cp-02", "case_punct", "Write a sentence about the moon using only lowercase letters.", [("lower",)],
     "the moon looked huge over the hills tonight, almost orange.", "A"),
    ("cp-03", "case_punct", "List three fruits without using any commas.", [("no_comma",)],
     "Apples, pears and plums are all in season now.", "B"),
    ("cp-04", "case_punct", "Explain what a library card does, entirely in lowercase.", [("lower",)],
     "a library card lets you borrow books for free. Most libraries also offer e-books.", "B"),
    ("cp-05", "case_punct", "Describe a thunderstorm in English using only capital letters.", [("upper",)],
     "THE SKY TURNED DARK AND Thunder SHOOK THE WINDOWS.", "B"),
    ("cp-06", "case_punct", "Give one tip for saving money and do not use any commas in your response.", [("no_comma",)],
     "Cook at home more often and bring lunch to work instead of buying it.", "A"),
    ("cp-07", "case_punct", "Describe a bicycle using only lowercase letters and no commas.", [("lower",), ("no_comma",)],
     "a bicycle has two wheels and pedals that turn a chain.", "A"),
    ("cp-08", "case_punct", "Write a short greeting for new neighbors in all capitals without commas.", [("upper",), ("no_comma",)],
     "WELCOME TO THE STREET, WE ARE GLAD YOU MOVED IN!", "B"),
    ("cp-09", "case_punct", "Explain what recycling is, all in lowercase, with no commas.", [("lower",), ("no_comma",)],
     "recycling turns used materials into new products so less waste ends up in landfills.", "A"),
]

# pick_one: (id, slice, instruction, constraints, [A, B, C], label)
PICK_ONE = [
    ("pick-len-01", "length", "Describe a forest in at most 10 words.", [("words_max", 10)],
     ["Tall pines, soft moss and birdsong everywhere.",
      "A forest is a large area covered by trees, plants and many different animals.",
      "Forests clean the air, store carbon, shelter wildlife and give people quiet places to walk."], "A"),
    ("pick-len-02", "length", "Explain gravity in exactly 2 sentences.", [("sentences", 2)],
     ["Gravity pulls objects toward each other.",
      "Gravity pulls masses toward each other. It keeps the planets in orbit around the Sun.",
      "Gravity is a force. It pulls things down. It also keeps the Moon near Earth."], "B"),
    ("pick-len-03", "length", "Describe your ideal weekend in at least 15 words.", [("words_min", 15)],
     ["Sleeping late and reading.",
      "A long walk, a good lunch.",
      "I would sleep late, walk by the river with friends, cook a big dinner and watch an old film."], "C"),
    ("pick-len-04", "length", "Name a skill everyone should learn, in exactly 5 words.", [("words_exact", 5)],
     ["Everyone should learn basic cooking.",
      "Cooking.",
      "Everyone should learn how to cook simple meals."], "A"),
    ("pick-len-05", "length", "Give a weather forecast in exactly 3 sentences.", [("sentences", 3)],
     ["Expect rain today. Take an umbrella.",
      "Morning fog will clear by noon. Afternoon will be sunny. Temperatures reach twenty degrees.",
      "Sunny with clouds later, warm in the afternoon and cool at night."], "B"),
    ("pick-kw-01", "keywords", "Describe a successful project. Use the word MILESTONE exactly once.", [("count", "MILESTONE", 1)],
     ["We reached every MILESTONE and the next MILESTONE too.",
      "The team delivered on time and stayed on budget.",
      "Launching the beta was our biggest MILESTONE and the team celebrated."], "C"),
    ("pick-kw-02", "keywords", "Give advice about job interviews without using the word nervous.", [("exclude", "nervous")],
     ["Prepare examples of your past work and arrive ten minutes early.",
      "Do not be nervous; just prepare well.",
      "Even if you feel Nervous, smile and listen carefully."], "A"),
    ("pick-kw-03", "keywords", "Describe a festival and include the keywords lantern and music.", [("include", "lantern"), ("include", "music")],
     ["Crowds danced to live music until midnight.",
      "Each lantern floated over the river while music played on the bridge.",
      "Every lantern glowed red and gold above the square."], "B"),
    ("pick-kw-04", "keywords", "Explain why exercise is useful. Do not use the words health or healthy.", [("exclude", "health"), ("exclude", "healthy")],
     ["Exercise keeps you healthy and strong.",
      "Regular exercise is good for your health.",
      "Exercise strengthens muscles, improves sleep and lifts your mood."], "C"),
    ("pick-kw-05", "keywords", "Describe a chef at work. Use the word FLAVOR exactly twice.", [("count", "FLAVOR", 2)],
     ["The chef tastes for FLAVOR, adds salt and tastes the FLAVOR again.",
      "The chef balances FLAVOR carefully.",
      "FLAVOR, FLAVOR and more FLAVOR drive every dish she makes."], "A"),
    ("pick-fmt-01", "format", "List exactly two uses of a paperclip as bullet points.", [("bullets", 2)],
     ["- Hold papers together\n- Reset a router\n- Open a SIM tray",
      "Hold papers together and reset a router.",
      "- Hold papers together\n- Reset a router"], "C"),
    ("pick-fmt-02", "format", "Write a haiku about rain with a title in double angular brackets, like <<title>>.", [("title",)],
     ["<<Spring Rain>>\nsoft drops on the roof\nthe garden drinks in silence\nfrogs begin to sing",
      "Title: Spring Rain\nsoft drops on the roof\nthe garden drinks in silence\nfrogs begin to sing",
      "(Spring Rain)\nsoft drops on the roof\nthe garden drinks in silence\nfrogs begin to sing"], "A"),
    ("pick-fmt-03", "format", "Describe a market and finish with the exact phrase 'See you there.'", [("ends", "See you there.")],
     ["Stalls sell bread, cheese and flowers every Saturday. See you there!",
      "Stalls sell bread, cheese and flowers every Saturday. See you there.",
      "See you there. Stalls sell bread, cheese and flowers every Saturday."], "B"),
    ("pick-fmt-04", "format", "Write a study plan with exactly 3 sections, each starting with SECTION X.", [("sections", 3)],
     ["SECTION 1\nRead.\nSECTION 2\nPractice.",
      "Part 1: Read. Part 2: Practice. Part 3: Review.",
      "SECTION 1\nRead the chapter.\nSECTION 2\nSolve exercises.\nSECTION 3\nReview mistakes."], "C"),
    ("pick-fmt-05", "format", "Start your answer with 'In short,' and explain what a vaccine does.", [("starts", "In short,")],
     ["In short, a vaccine trains the immune system to recognize a germ.",
      "A vaccine, in short, trains the immune system.",
      "Short answer: a vaccine trains the immune system."], "A"),
    ("pick-cp-01", "case_punct", "Describe a cup of coffee in all capital letters.", [("upper",)],
     ["Hot, Bitter And Strong.",
      "HOT, BITTER AND STRONG.",
      "hot, bitter and strong."], "B"),
    ("pick-cp-02", "case_punct", "Name three colors without using any commas.", [("no_comma",)],
     ["Red, blue, green.",
      "Red, blue and green.",
      "Red and blue and green."], "C"),
    ("pick-cp-03", "case_punct", "Write a sentence about spring entirely in lowercase.", [("lower",)],
     ["spring brings warm rain and the first flowers.",
      "Spring brings warm rain and the first flowers.",
      "spring brings warm rain in April."], "A"),
    ("pick-cp-04", "case_punct", "Describe a dog in lowercase letters and without commas.", [("lower",), ("no_comma",)],
     ["my dog is loud, lazy and lovely.",
      "my dog is loud and lazy and lovely.",
      "My dog is loud and lazy and lovely."], "B"),
    ("pick-cp-05", "case_punct", "Write a warning sign text in capital letters only.", [("upper",)],
     ["Danger: Wet Floor",
      "danger: wet floor",
      "DANGER: WET FLOOR"], "C"),
]

YES_NO_TAIL = "Does the response follow every constraint of the instruction? A - yes, B - no. choices: A, B, REFUSE."
PICK_TAIL = "Which response follows every constraint of the instruction? choices: A, B, C, REFUSE."


def yes_no_input(instruction, response):
    return f"Instruction: {instruction}\nResponse:\n{response}\n\n{YES_NO_TAIL}"


def pick_input(instruction, options):
    body = "\n".join(f"Response {k}:\n{v}" for k, v in zip("ABC", options))
    return f"Instruction: {instruction}\n{body}\n\n{PICK_TAIL}"


def build_golden():
    rows = []
    for id_, kind, instruction, cons, response, label in YES_NO:
        computed = "A" if check(cons, response) else "B"
        if computed != label:
            raise SystemExit(f"{id_}: ручная метка {label}, проверка даёт {computed}")
        rows.append(dict(id=f"ifc-{id_}", group_id=f"ifc-{id_}", input=yes_no_input(instruction, response),
                         expected=label, choices=["A", "B", "REFUSE"],
                         slices=[kind, "yes_no", "compliant" if label == "A" else "violation"], source=SOURCE))
    for id_, kind, instruction, cons, options, label in PICK_ONE:
        passing = [k for k, v in zip("ABC", options) if check(cons, v)]
        if passing != [label]:
            raise SystemExit(f"{id_}: ручная метка {label}, ограничениям удовлетворяют {passing}; нужен ровно один")
        rows.append(dict(id=f"ifc-{id_}", group_id=f"ifc-{id_}", input=pick_input(instruction, options),
                         expected=label, choices=["A", "B", "C", "REFUSE"],
                         slices=[kind, "pick_one"], source=SOURCE))
    return rows


def build_behavioral():
    # format и paraphrase — одна пара (общий group_id), смысл и метка не меняются.
    instr, cons = "Describe a sunset in at most 12 words.", [("words_max", 12)]
    resp = "The sky burns orange, then slowly fades into deep violet."
    assert check(cons, resp)
    fmt = yes_no_input(instr, resp) + ' Reply with JSON only, for example {"answer":"A"}.'
    para = (f"Here is a task and an answer to it.\nTask: {instr}\nAnswer given:\n{resp}\n\n"
            "Is every requirement of the task satisfied? A - yes, B - no. choices: A, B, REFUSE.")
    src = "Behavioral, написано вручную; не входит в golden и его n"
    return [
        dict(id="beh-format", group_id="beh-sunset", input=fmt, expected="A", choices=["A", "B", "REFUSE"],
             slices=["behavior"], type="format", source=src + "; проверка строгого JSON"),
        dict(id="beh-paraphrase", group_id="beh-sunset", input=para, expected="A", choices=["A", "B", "REFUSE"],
             slices=["behavior"], type="paraphrase", source=src + "; перефраз beh-format, метка та же"),
        dict(id="beh-refusal", group_id="beh-out-of-domain",
             input="Write a short poem about autumn leaves. choices: A, B, REFUSE.", expected="REFUSE",
             choices=["A", "B", "REFUSE"], slices=["behavior"], type="refusal",
             source=src + "; вне темы — отказ задан system-инструкцией evaluation/params.json"),
    ]


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main():
    golden, behavioral = build_golden(), build_behavioral()
    write(OUT / "golden.jsonl", golden)
    write(OUT / "behavioral.jsonl", behavioral)
    from collections import Counter
    sl = Counter(s for r in golden for s in r["slices"])
    labels = Counter(r["expected"] for r in golden)
    print(f"golden: {len(golden)} вопросов; срезы: {dict(sorted(sl.items()))}; метки: {dict(sorted(labels.items()))}")
    print(f"behavioral: {len(behavioral)} ({', '.join(r['type'] for r in behavioral)})")


if __name__ == "__main__":
    main()
