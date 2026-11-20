"""Страница версий для сайта (site/versions.html): все выпуски с изменениями, берутся из core/changelog.py.

Выпуски до RELEASE_FROM помечены «бета». RELEASE_FROM = (10, 0): бета — всё до 10.0.0, релиз — с 10.0.0 (так решил владелец 20.11.2026; раньше с 18.11.2026 были бета все). Страница сама пересобирается
при каждой сборке релиза (tools/release.py), так что вручную её править не нужно.
"""
from __future__ import annotations

import html
from pathlib import Path

RELEASE_FROM = (10, 0)      # с какой версии «релиз» (10.0.0 — решение владельца 20.11.2026); None — все выпуски бета


def _key(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in v.split("."))


def render(changelog: dict, current: str) -> str:
    rows = []
    for ver in sorted(changelog, key=_key, reverse=True):
        beta = RELEASE_FROM is None or _key(ver) < RELEASE_FROM
        items = "".join(f"<li>{html.escape(line)}</li>" for line in changelog[ver])
        badge = '<span class="tag beta">бета</span>' if beta else '<span class="tag ok">релиз</span>'
        now = ' <span class="tag now">текущая</span>' if ver == current else ""
        rows.append(f'<section id="v{html.escape(ver)}"><h2>{html.escape(ver)} {badge}{now}</h2><ul>{items}</ul></section>')
    return PAGE.replace("{{VERSION}}", html.escape(current)).replace("{{ROWS}}", "\n".join(rows))


def write(site_dir: Path, changelog: dict, current: str) -> Path:
    out = site_dir / "versions.html"
    out.write_text(render(changelog, current), encoding="utf-8")
    return out


PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Версии VaultMC — что менялось</title>
<meta name="description" content="Все версии лаунчера VaultMC и что в каждой изменилось.">
<meta name="theme-color" content="#07090c">
<link rel="icon" href="img/logo.png">
<style>
:root { --bg: #07090c; --card: #0e1318; --line: #1d2730; --ink: #eaf1f4; --dust: #8fa0ab; --neon: #2fd3e6; --gold: #f2b63c; --ok: #2bd46b; color-scheme: dark; }
@media (prefers-color-scheme: light) { :root { --bg: #f4f6f8; --card: #fff; --line: #dde3e8; --ink: #14202a; --dust: #5b6b77; --neon: #0a8fa3; --gold: #a86f00; --ok: #138a45; color-scheme: light; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 "Segoe UI", system-ui, sans-serif; }
main { width: min(780px, 100%); margin: 0 auto; padding: 32px 16px 64px; }
a { color: var(--neon); }
h1 { font-size: clamp(26px, 5vw, 38px); margin: 8px 0 6px; }
.lead { color: var(--dust); margin: 0 0 24px; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 20px; margin: 0 0 12px; }
h2 { font-size: 19px; margin: 0 0 8px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
ul { margin: 0; padding-left: 20px; } li { margin: 4px 0; }
.tag { font-size: 12px; font-weight: 700; padding: 2px 9px; border-radius: 999px; border: 1px solid currentColor; }
.beta { color: var(--gold); } .ok { color: var(--ok); } .now { color: var(--neon); }
</style>
</head>
<body>
<main>
<a href="./">← На главную</a>
<h1>Версии VaultMC</h1>
<p class="lead">Текущая версия — {{VERSION}}. Все версии пока — бета: лаунчер ещё обкатывается.</p>
{{ROWS}}
</main>
</body>
</html>
"""
