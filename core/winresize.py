"""Изменение размера окна без системной рамки (Windows).

У окна лаунчера своя верхняя полоса, поэтому Windows не даёт тянуть его за края.
Интерфейс сообщает, за какой край взялись, а здесь, пока зажата кнопка мыши,
окно подстраивается под курсор средствами самой Windows (без задержек JS).
"""
from __future__ import annotations

import os
import sys
import threading
import time

EDGES = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "move"}  # move — перетаскивание за заголовок
_busy = threading.Lock()

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _u = ctypes.windll.user32
    _ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u.EnumWindows.argtypes = [_ENUM, wintypes.LPARAM]
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u.IsWindowVisible.argtypes = [wintypes.HWND]
    _u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _u.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _u.GetAsyncKeyState.restype = ctypes.c_short
    _u.IsZoomed.argtypes = [wintypes.HWND]
    _u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]

SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0001, 0x0004, 0x0010, 0x0200
VK_LBUTTON, VK_RBUTTON, SM_SWAPBUTTON = 0x01, 0x02, 23


def find_window(title: str):
    """Окно этого процесса с таким заголовком (HWND) или None."""
    if sys.platform != "win32":
        return None
    pid, found = os.getpid(), []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _u.IsWindowVisible(hwnd):
            n = _u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            _u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(hwnd)
                return False
        return True

    _u.EnumWindows(_ENUM(cb), 0)
    return found[0] if found else None


def new_rect(start: tuple, edge: str, dx: int, dy: int, min_w: int, min_h: int) -> tuple:
    """Новый прямоугольник окна (x, y, w, h) — отдельно, чтобы проверять без Windows."""
    left, top, right, bottom = start
    if edge == "move":
        return left + dx, top + dy, right - left, bottom - top
    if "w" in edge:
        left = min(left + dx, right - min_w)
    if "e" in edge:
        right = max(right + dx, left + min_w)
    if "n" in edge:
        top = min(top + dy, bottom - min_h)
    if "s" in edge:
        bottom = max(bottom + dy, top + min_h)
    return left, top, right - left, bottom - top


def start(hwnd, edge: str, min_w: int, min_h: int) -> bool:
    if sys.platform != "win32" or not hwnd or edge not in EDGES or _u.IsZoomed(hwnd):
        return False
    if not _busy.acquire(blocking=False):
        return False
    threading.Thread(target=_loop, args=(hwnd, edge, min_w, min_h), daemon=True).start()
    return True


def _loop(hwnd, edge: str, min_w: int, min_h: int) -> None:
    try:
        rect, pt = wintypes.RECT(), wintypes.POINT()
        _u.GetWindowRect(hwnd, ctypes.byref(rect))
        _u.GetCursorPos(ctypes.byref(pt))
        sx, sy = pt.x, pt.y
        start_rect = (rect.left, rect.top, rect.right, rect.bottom)
        try:
            scale = (_u.GetDpiForWindow(wintypes.HWND(hwnd)) or 96) / 96
        except Exception:  # noqa: BLE001 — Windows старше 10 (1607)
            scale = 1.0
        mw, mh = int(min_w * scale), int(min_h * scale)
        button = VK_RBUTTON if _u.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
        last, deadline = None, time.time() + 300
        while _u.GetAsyncKeyState(button) & 0x8000 and time.time() < deadline:
            _u.GetCursorPos(ctypes.byref(pt))
            r = new_rect(start_rect, edge, pt.x - sx, pt.y - sy, mw, mh)
            if r != last:
                flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER | (SWP_NOSIZE if edge == "move" else 0)
                _u.SetWindowPos(hwnd, None, *r, flags)
                last = r
            time.sleep(0.01)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _busy.release()
