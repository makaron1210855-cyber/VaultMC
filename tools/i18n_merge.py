"""Одноразовая помощница: вносит переводы из файла «N|английский» в словарь I18N_EN (web/js/i18n.js).

Нумерация — по списку gaps.json (его печатает tools/i18n_scan.py: найденные, но не переведённые русские куски).
Ключ словаря — точный русский кусок (с теми же пробелами по краям), перевод получает такие же пробелы по краям,
чтобы склейка «кусок + число + кусок» выглядела правильно. Одинаковые куски из разных файлов вносятся один раз.
Запуск: py tools/i18n_merge.py <gaps.json> <en.txt>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "web" / "js" / "i18n.js"


def js_str(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def main(gaps_path: str, en_path: str) -> None:
    gaps = json.loads(Path(gaps_path).read_text(encoding="utf-8"))
    pairs: dict[str, str] = {}
    for line in Path(en_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n, _, english = line.partition("|")
        idx = int(n) - 1
        english = english.strip()
        if english == "SKIP":
            continue
        seg = gaps[idx]
        lead = seg[: len(seg) - len(seg.lstrip())]
        trail = seg[len(seg.rstrip()):]
        value = lead + english + trail
        if seg in pairs and pairs[seg] != value:
            print(f"! разные переводы одного куска: {seg!r}: {pairs[seg]!r} / {value!r}")
        pairs.setdefault(seg, value)
    existing = I18N.read_text(encoding="utf-8")
    marker = "// 6.4.21: тексты из Python"
    block = "\n".join(f"{js_str(k)}: {js_str(v)}," for k, v in pairs.items())
    head, sep, tail = existing.partition("\n};\n\nconst I18N = {")
    if not sep:
        raise SystemExit("не нашёл конец словаря в i18n.js")
    I18N.write_text(f"{head}\n{marker} (проверка tools/i18n_scan.py)\n{block}\n}};\n\nconst I18N = {{{tail}", encoding="utf-8")
    print(f"добавлено ключей: {len(pairs)}")


def prune_orphans() -> int:
    """Удаляет из словаря ключи, которых больше нет ни в одном исходнике (tools/i18n_scan.orphans)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import re
    import i18n_scan
    orphan = set(i18n_scan.orphans())
    lines = I18N.read_text(encoding="utf-8").split("\n")
    end = next(i for i, ln in enumerate(lines) if ln.startswith("const I18N = {"))
    kept, removed = [], 0
    for i, line in enumerate(lines):
        m = re.match(r'^"((?:[^"\\]|\\.)*)":\s', line)
        if i < end and m and json.loads('"' + m.group(1) + '"') in orphan:
            removed += 1
            continue
        kept.append(line)
    I18N.write_text("\n".join(kept), encoding="utf-8")
    return removed


if __name__ == "__main__":
    if sys.argv[1:2] == ["--prune"]:
        print("удалено ключей:", prune_orphans())
    else:
        main(sys.argv[1], sys.argv[2])
