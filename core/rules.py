"""Правила (rules) из version.json: какие библиотеки и аргументы нужны на этой ОС и с этими функциями."""

from __future__ import annotations

import platform

from .utils import mojang_os_name


def current_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("i386", "i686", "x86"):
        return "x86"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x86_64"


def rule_matches(rule: dict, features: dict) -> bool:
    os_rule = rule.get("os")
    if os_rule:
        if "name" in os_rule and os_rule["name"] != mojang_os_name():
            return False
        if "arch" in os_rule and os_rule["arch"] != current_arch():
            return False
    for key, value in rule.get("features", {}).items():
        if bool(features.get(key, False)) != bool(value):
            return False
    return True


def rules_allow(rules: list[dict] | None, features: dict | None = None) -> bool:
    """Алгоритм Mojang: решает последнее подходящее правило; нет правил — разрешено."""
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        if rule_matches(rule, features):
            allowed = rule.get("action") == "allow"
    return allowed
