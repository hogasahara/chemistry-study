#!/usr/bin/env python3
"""drill.json の検証と紙版(question.md / answers.md)の生成。

使い方(リポジトリのルートで):
  python3 tools/build_paper.py          全ドリルを検証し、紙版を再生成する
  python3 tools/build_paper.py --check  検証だけ行う(ファイルは書かない)

データは drill.json が正。紙版は必ずこのスクリプトで生成し、手で編集しない。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRILLS = ROOT / "drills"

CAT_LABELS = {
    "term": "用語",
    "calc": "計算",
    "fill": "穴埋め",
    "table": "グラフ・表",
    "combo": "組み合わせ",
    "equation": "反応式",
}
ALLOWED_TAG = re.compile(r"</?(sub|sup)>|<br>")


def check_markup(where, text, errors):
    """化学式の表記ルール: 使えるタグは <sub> <sup> <br> だけ。電荷の負号は − (U+2212)。"""
    if not isinstance(text, str):
        errors.append(f"{where}: 文字列ではありません")
        return
    rest = ALLOWED_TAG.sub("", text)
    if "<" in rest or ">" in rest:
        errors.append(f"{where}: 使えないタグ/記号があります(<sub><sup><br>のみ可): {text}")
    if re.search(r"<sup>[^<]*-[^<]*</sup>", text):
        errors.append(f"{where}: 電荷の負号は半角ハイフンではなく − (U+2212) を使う: {text}")
    for ch in "₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻":
        if ch in text:
            errors.append(f"{where}: Unicodeの上付き/下付き文字は使わず <sub>/<sup> で書く: {text}")
            break


def validate(d, entry):
    errors = []
    for key in ("id", "number", "title", "unit", "source", "date", "terms", "questions"):
        if key not in d:
            errors.append(f"必須キー {key} がありません")
    if errors:
        return errors
    for i, t in enumerate(d["terms"]):
        if not (isinstance(t, list) and len(t) == 2):
            errors.append(f"terms[{i}]: [表, 裏] の2要素にしてください")
            continue
        check_markup(f"terms[{i}]", t[0], errors)
        check_markup(f"terms[{i}]", t[1], errors)
    for i, q in enumerate(d["questions"]):
        w = f"Q{i + 1}"
        check_markup(w + ".q", q.get("q"), errors)
        check_markup(w + ".explain", q.get("explain"), errors)
        if q.get("type") == "tf":
            if not isinstance(q.get("answer"), bool):
                errors.append(f"{w}: tf の answer は true/false")
        elif q.get("type") == "mc":
            ch = q.get("choices") or []
            if len(ch) != 4:
                errors.append(f"{w}: 選択肢は4つ")
            for c in ch:
                check_markup(w + ".choices", c, errors)
            if len(set(ch)) != len(ch):
                errors.append(f"{w}: 選択肢が重複しています")
            a = q.get("answer")
            if not (isinstance(a, int) and 0 <= a < len(ch)):
                errors.append(f"{w}: answer は選択肢の番号(0始まり)")
            if q.get("cat") and q["cat"] not in CAT_LABELS:
                errors.append(f"{w}: cat は {list(CAT_LABELS)} のいずれか")
            traps = q.get("traps")
            if traps is not None:
                if len(traps) != len(ch):
                    errors.append(f"{w}: traps は選択肢と同じ数にする(正解の位置は null)")
                elif isinstance(a, int) and 0 <= a < len(traps) and traps[a] is not None:
                    errors.append(f"{w}: 正解の選択肢の traps は null にする")
            for r, row in enumerate(q.get("table") or []):
                for c in row:
                    check_markup(f"{w}.table[{r}]", c, errors)
        else:
            errors.append(f"{w}: type は tf か mc")
    if entry is None:
        errors.append("drills/drills.json にエントリがありません")
    else:
        for key in ("number", "title", "unit", "date"):
            if entry.get(key) != d.get(key):
                errors.append(f"drills.json と {key} が一致しません")
        if entry.get("questions") != len(d["questions"]):
            errors.append("drills.json の questions(問題数)が一致しません")
    return errors


def qtext(q, i):
    label = f"【{CAT_LABELS[q['cat']]}】 " if q.get("cat") in CAT_LABELS else ""
    return f"**Q{i + 1}.** {label}{q['q']}"


def table_md(rows):
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def build_question_md(d):
    lines = [f"# 確認ドリル #{d['number']} — {d['title']}", "",
             f"分野: {d['unit']} ・ 出典: {d['source'].get('label', '')} ・ 目安 {d.get('targetMinutes', 10)}分", "",
             "[解答・解説](answers.md) ・ Web版はトップページから", "",
             "## 用語チェック(先に目を通す)", "", "| 用語 | 意味 |", "|---|---|"]
    lines += [f"| {a} | {b} |" for a, b in d["terms"]]
    lines += ["", "## 設問", "", "所要時間を測ってから始める。", ""]
    for i, q in enumerate(d["questions"]):
        lines.append(qtext(q, i))
        lines.append("")
        if q.get("table"):
            lines += [table_md(q["table"]), ""]
        if q["type"] == "tf":
            lines.append("正 / 誤")
        else:
            lines += [f"- ({'abcd'[j]}) {c}" for j, c in enumerate(q["choices"])]
        lines.append("")
    return "\n".join(lines)


def build_answers_md(d):
    lines = [f"# 確認ドリル #{d['number']} 解答・解説", "", "[問題に戻る](question.md)", "",
             "| 問 | 答え |", "|---|---|"]
    for i, q in enumerate(d["questions"]):
        ans = ("正" if q["answer"] else "誤") if q["type"] == "tf" else f"({'abcd'[q['answer']]}) {q['choices'][q['answer']]}"
        lines.append(f"| Q{i + 1} | {ans} |")
    lines += ["", "## 解説", ""]
    for i, q in enumerate(d["questions"]):
        lines.append(f"**Q{i + 1}.** {q['explain']}")
        traps = [(j, t) for j, t in enumerate(q.get("traps") or []) if t]
        if traps:
            lines.append("")
            lines.append("ひっかけ: " + " / ".join(f"({'abcd'[j]}) {t}" for j, t in traps))
        lines.append("")
    if d.get("points"):
        lines += ["## ここを押さえる", ""] + [f"- {p}" for p in d["points"]] + [""]
    return "\n".join(lines)


def main():
    check_only = "--check" in sys.argv
    manifest = json.loads((DRILLS / "drills.json").read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in manifest}
    failed = False
    for path in sorted(DRILLS.glob("*/drill.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        if d.get("id") != path.parent.name:
            print(f"NG {path}: id とフォルダ名が一致しません")
            failed = True
            continue
        errors = validate(d, entries.get(d["id"]))
        if errors:
            failed = True
            print(f"NG {path}")
            for e in errors:
                print("   - " + e)
            continue
        if not check_only:
            (path.parent / "question.md").write_text(build_question_md(d), encoding="utf-8")
            (path.parent / "answers.md").write_text(build_answers_md(d), encoding="utf-8")
        print(f"OK {path.parent.name}")
    missing = set(entries) - {p.parent.name for p in DRILLS.glob("*/drill.json")}
    for m in sorted(missing):
        print(f"NG drills.json のエントリ {m} に対応するフォルダがありません")
        failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
