"""Проверка английского перевода интерфейса: какие русские тексты в web/js и web/index.html остались без перевода.

Как работает перевод в самом лаунчере (web/js/i18n.js): каждый показанный текст сверяется со словарём I18N_EN,
фразы подставляются по самым длинным совпадениям. Здесь то же самое повторяется заранее: из исходников достаются все
русские куски текста (строки, шаблоны, текст между HTML-тегами, title/placeholder/aria-label), прогоняются через настоящую
функцию trText (запускается в Node) — и всё, где осталась кириллица, попадает в список «не переведено».

Запуск:  py tools/i18n_scan.py          — список недостающего
         py tools/i18n_scan.py --orphans — ещё и ключи словаря, которых больше нет в исходниках (после правки русского текста)
Тесты (tests/test_i18n.py) берут эти же функции.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CYR = re.compile(r"[А-Яа-яЁё]")
# что не переводится намеренно: названия языков, клавиши (Ctrl+К на русской раскладке), ники и т. п.
ALLOW = {"Русский", "English", "к", "К"}
# скрытые функции (вход Microsoft выключен флагом MS_LOGIN_ENABLED) и двуязычные подписи выбора языка
IGNORE_RE = re.compile(r"Microsoft|Client ID|Mojang на доступ|Language|Choose the interface|data-lang=|You can change it")

_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {""}


def _skip_ws(src: str, i: int) -> int:
    while i < len(src) and src[i] in " \t\r\n":
        i += 1
    return i


def scan_js(src: str):
    """Достаёт куски строк из кода JS: (текст, номер строки). Комментарии и регулярные выражения пропускаются."""
    out: list[tuple[str, int]] = []

    def code(i: int, stop_on_brace: bool) -> int:
        depth, prev = 0, ""
        n = len(src)
        while i < n:
            c = src[i]
            if c == "/" and src[i + 1:i + 2] == "/":
                while i < n and src[i] != "\n":
                    i += 1
                continue
            if c == "/" and src[i + 1:i + 2] == "*":
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c == "/" and prev in _REGEX_PREV:                     # регулярное выражение
                i += 1
                in_class = False
                while i < n and (src[i] != "/" or in_class) and src[i] != "\n":
                    if src[i] == "\\":
                        i += 1
                    elif src[i] == "[":
                        in_class = True
                    elif src[i] == "]":
                        in_class = False
                    i += 1
                i += 1
                while i < n and src[i].isalpha():
                    i += 1
                prev = "x"
                continue
            if c in "'\"":
                j, buf = i + 1, []
                while j < n and src[j] != c and src[j] != "\n":
                    if src[j] == "\\" and j + 1 < n:
                        buf.append(src[j + 1] if src[j + 1] not in "nrt" else " ")
                        j += 2
                        continue
                    buf.append(src[j])
                    j += 1
                out.append(("".join(buf), src.count("\n", 0, i) + 1))
                i, prev = j + 1, "x"
                continue
            if c == "`":
                i = template(i + 1)
                prev = "x"
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0 and stop_on_brace:
                    return i + 1
                depth -= 1
            if not c.isspace():
                prev = c if not (c.isalnum() or c in "_$)]") else "x"
            i += 1
        return i

    def template(i: int) -> int:
        n, buf, start = len(src), [], i
        while i < n:
            c = src[i]
            if c == "\\" and i + 1 < n:
                buf.append(" " if src[i + 1] in "nrt" else src[i + 1])
                i += 2
                continue
            if c == "`":
                out.append(("".join(buf), src.count("\n", 0, start) + 1))
                return i + 1
            if c == "$" and src[i + 1:i + 2] == "{":
                out.append(("".join(buf), src.count("\n", 0, start) + 1))
                buf = []
                i = code(i + 2, True)
                start = i
                continue
            buf.append(c)
            i += 1
        return i

    code(0, False)
    return out


_ATTR = re.compile(r"""\b(?:title|placeholder|data-tip|aria-label)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TAG = re.compile(r"<[^>]*>")


def html_pieces(text: str):
    """Один кусок строки → тексты, которые увидит игрок: значения title/placeholder и текст между тегами."""
    for m in _ATTR.finditer(text):
        yield m.group(1) or m.group(2) or ""
    for part in _TAG.split(text):
        if part.strip():
            yield part


# Python: ключи словарей и вызовы, значения которых лаунчер показывает игроку (тосты, окна, подсказки)
PY_UI_KEYS = {"error", "ok", "text", "title", "label", "note", "reason", "summary", "hint", "detail", "why", "warn", "message",
              "needPassword", "recoveryError"}
PY_UI_CALLS = {"add"}                      # crash.py: add(ключ, заголовок, текст, исправление)
# внутренние тексты: журнал лаунчера, команды разработчику
PY_SKIP_FILES = {"changelog.py", "keys.py", "microsoft.py", "dnsrescue.py"}   # changelog — отдельно; Microsoft выключен; dnsrescue — внутренние сообщения для журнала


def python_messages() -> list[tuple[str, str, int]]:
    import ast
    out: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()

    def strings(node):
        """Русские куски строк в выражении: обычные, f-строки (только постоянные части) и склейки."""
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and CYR.search(n.value):
                yield n.value, n.lineno

    for folder in ("core", "backend"):
        for p in sorted((ROOT / folder).rglob("*.py")):
            if p.name in PY_SKIP_FILES:
                continue
            try:
                source = p.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source)
            except SyntaxError:
                continue
            skip_lines = {i for i, ln in enumerate(source.splitlines(), 1) if "i18n-skip" in ln}   # внутренние сообщения
            nodes = []
            for n in ast.walk(tree):
                if isinstance(n, ast.Dict):
                    for k, v in zip(n.keys, n.values):
                        if isinstance(k, ast.Constant) and k.value in PY_UI_KEYS:
                            nodes.append(v)
                elif isinstance(n, ast.Call):
                    name = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
                    if name in PY_UI_CALLS or name.endswith("Error"):
                        nodes += n.args[:4]
                    if name in ("toast", "notice"):
                        nodes += n.args[:2]
            for node in nodes:
                for text, line in strings(node):
                    if line in skip_lines:
                        continue
                    for piece in re.split(r"\n", text):
                        if piece.strip() and CYR.search(piece) and (p.name, piece) not in seen:
                            seen.add((p.name, piece))
                            out.append((p.name, piece, line))
    return out


def collect(with_python: bool = True) -> list[tuple[str, str, int]]:
    """[(файл, текст, строка)] — все русские куски интерфейса."""
    found: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    files = sorted((WEB / "js").rglob("*.js"))
    for f in files:
        if f.name == "i18n.js":
            continue
        src = f.read_text(encoding="utf-8")
        # строки, помеченные комментарием «i18n-skip», не проверяются: журнал для разработчика, скрытые функции, двуязычные подписи
        skip_lines, in_region = set(), False
        for n, ln in enumerate(src.splitlines(), 1):
            if "i18n-skip-start" in ln:
                in_region = True
            if in_region or "i18n-skip" in ln:
                skip_lines.add(n)
            if "i18n-skip-end" in ln:
                in_region = False
        for text, line in scan_js(src):
            if line in skip_lines:
                continue
            for piece in html_pieces(text):
                if IGNORE_RE.search(piece):
                    continue
                if CYR.search(piece) and (f.name, piece) not in seen and piece.strip() not in ALLOW:
                    seen.add((f.name, piece))
                    found.append((f.name, piece, line))
    page = (WEB / "index.html").read_text(encoding="utf-8")
    page = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->", "", page)
    for piece in html_pieces(page):
        if CYR.search(piece) and ("index.html", piece) not in seen and piece.strip() not in ALLOW:
            seen.add(("index.html", piece))
            found.append(("index.html", piece, 0))
    if with_python:
        found += python_messages()
    return found


NODE_RUNNER = r"""
const vm = require('vm'), fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
// i18n.js при загрузке создаёт MutationObserver — в Node его нет, подставляем заглушку
const trText = vm.runInNewContext(src + '\n;trText', { MutationObserver: class { observe() {} disconnect() {} } });
const items = JSON.parse(fs.readFileSync(0, 'utf8'));
const left = items.filter((it) => /[А-Яа-яЁё]/.test(trText(it[1])));
console.log(JSON.stringify(left.map((it) => [it[0], it[1], it[2], trText(it[1])])));
"""


def untranslated() -> list[tuple[str, str, int, str]]:
    """Куски, в которых после перевода осталась кириллица: (файл, текст, строка, что получилось)."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("нужен Node (проверка запускает настоящую функцию trText)")
    items = collect()
    res = subprocess.run([node, "-e", NODE_RUNNER, str(WEB / "js" / "i18n.js")], input=json.dumps(items, ensure_ascii=False),
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return [tuple(x) for x in json.loads(res.stdout)]


def dictionary_keys() -> list[str]:
    node = shutil.which("node")
    code = ("const vm=require('vm'),fs=require('fs');"
            "const d=vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8')+'\\n;I18N_EN',{MutationObserver:class{observe(){}disconnect(){}}});"
            "console.log(JSON.stringify(Object.keys(d)))")
    res = subprocess.run([node, "-e", code, str(WEB / "js" / "i18n.js")], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(res.stdout)


def orphans() -> list[str]:
    """Ключи словаря, которых нет ни в JS, ни в Python-сообщениях (после правки русского текста перевод «осиротел»)."""
    corpus = [p.read_text(encoding="utf-8", errors="replace") for p in list((WEB / "js").rglob("*.js")) + [WEB / "index.html"]
              if p.name != "i18n.js"]
    for folder in ("core", "backend"):
        corpus += [p.read_text(encoding="utf-8", errors="replace") for p in (ROOT / folder).rglob("*.py")]
    corpus += [(ROOT / "main.py").read_text(encoding="utf-8", errors="replace")]
    # тексты сервера (отказы, ошибки) тоже приходят в интерфейс и переводятся этим же словарём
    server_files = list((ROOT / "server").glob("*.js"))
    corpus += [p.read_text(encoding="utf-8", errors="replace") for p in server_files]
    # Python: строки, записанные в несколько строк подряд («...» \n «...»), склеены самим языком — берём их через ast
    import ast
    for p in list((ROOT / "core").rglob("*.py")) + list((ROOT / "backend").rglob("*.py")) + [ROOT / "main.py"]:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        corpus += [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and CYR.search(n.value)]
    # JS: тексты, склеенные плюсом («...» + «...»), на экране идут подряд
    for p in list((WEB / "js").rglob("*.js")) + server_files:
        if p.name != "i18n.js":
            corpus.append("".join(t for t, _ in scan_js(p.read_text(encoding="utf-8", errors="replace"))))
    blob = "\n".join(corpus)
    norm = re.sub(r"\s+", " ", blob)
    return [k for k in dictionary_keys() if k not in blob and re.sub(r"\s+", " ", k) not in norm]


if __name__ == "__main__":
    miss = untranslated()
    for name, text, line, got in miss:
        print(f"{name}:{line}: {text.strip()[:110]!r}")
    print(f"\nне переведено: {len(miss)}")
    if "--orphans" in sys.argv:
        orph = orphans()
        for k in orph:
            print("осиротел:", repr(k[:100]))
        print(f"осиротевших ключей: {len(orph)}")
