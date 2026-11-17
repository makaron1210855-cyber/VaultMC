"""Копия интерпретатора с названием и значком лаунчера.

Windows берёт название и значок программы из самого exe: в диспетчере задач, на панели задач и в «Открыть с помощью»
лаунчер, запущенный через pythonw.exe, назывался «Python» со значком Python. Поэтому делаем копию pythonw.exe —
VaultMC.exe — и вписываем в неё значок и сведения о файле («Описание файла» = VaultMC) через Windows API,
без сторонних библиотек. Сам интерпретатор не меняется: значок и сведения — отдельные ресурсы exe.
"""

from __future__ import annotations

import ctypes
import shutil
import struct
from ctypes import wintypes
from pathlib import Path

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
LANG_EN_US = 0x0409
LOAD_LIBRARY_AS_DATAFILE = 0x00000002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
_k32.BeginUpdateResourceW.restype = wintypes.HANDLE
_k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                 ctypes.c_void_p, wintypes.DWORD]
_k32.UpdateResourceW.restype = wintypes.BOOL
_k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
_k32.EndUpdateResourceW.restype = wintypes.BOOL
_k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
_k32.LoadLibraryExW.restype = wintypes.HMODULE
_k32.FreeLibrary.argtypes = [wintypes.HMODULE]
_ENUM_NAME = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ENUM_LANG = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                ctypes.c_void_p)
_k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, _ENUM_NAME, ctypes.c_void_p]
_k32.EnumResourceNamesW.restype = wintypes.BOOL
_k32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, _ENUM_LANG, ctypes.c_void_p]
_k32.EnumResourceLanguagesW.restype = wintypes.BOOL


def _read_ico(path: Path) -> list[tuple[bytes, bytes]]:
    """Список (запись каталога на 14 байт без nID, картинка) из .ico."""
    data = path.read_bytes()
    _, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1 or count == 0:
        raise ValueError(f"не похоже на .ico: {path}")
    items = []
    for i in range(count):
        w, h, colors, _r, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + i * 16)
        items.append((struct.pack("<BBBBHHI", w, h, colors, 0, planes, bpp, size), data[offset:offset + size]))
    return items


def _existing(exe: Path, rtype: int) -> list[tuple[int | str, int]]:
    """Какие ресурсы этого типа уже есть в exe: [(имя, язык)]. Их нужно убрать, иначе рядом с нашими останутся
    значок и сведения Python (на другом языке ресурса), и Windows может показать их."""
    module = _k32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not module:
        raise OSError(ctypes.get_last_error(), f"LoadLibraryEx {exe}")
    found: list[tuple[int | str, int]] = []
    try:
        names: list[int | str] = []

        def on_name(_m, _t, name, _p):
            names.append(name if name < 0x10000 else ctypes.wstring_at(name))   # число или строка
            return True
        _k32.EnumResourceNamesW(module, ctypes.c_void_p(rtype), _ENUM_NAME(on_name), None)
        for name in names:
            def on_lang(_m, _t, _n, lang, _p, name=name):
                found.append((name, lang))
                return True
            ref = ctypes.c_void_p(name) if isinstance(name, int) else ctypes.cast(ctypes.c_wchar_p(name), ctypes.c_void_p)
            _k32.EnumResourceLanguagesW(module, ctypes.c_void_p(rtype), ref, _ENUM_LANG(on_lang), None)
    finally:
        _k32.FreeLibrary(module)
    return found


def _update(handle, rtype: int, name: int | str, lang: int, payload: bytes | None) -> None:
    """payload=None — удалить ресурс."""
    ref = ctypes.c_void_p(name) if isinstance(name, int) else ctypes.cast(ctypes.c_wchar_p(name), ctypes.c_void_p)
    buf = ctypes.create_string_buffer(payload, len(payload)) if payload is not None else None
    if not _k32.UpdateResourceW(handle, ctypes.c_void_p(rtype), ref, lang,
                                ctypes.cast(buf, ctypes.c_void_p) if buf is not None else None,
                                len(payload) if payload is not None else 0):
        raise OSError(ctypes.get_last_error(), f"UpdateResource {rtype}/{name}")


# ------------------------------------------------------------------ сведения о файле (ресурс VERSIONINFO)
def _pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def _node(key: str, value: bytes, is_text: bool, children: bytes = b"", value_len: int | None = None) -> bytes:
    """Один блок VERSIONINFO: длина, длина значения, тип (1 — текст), имя в UTF-16, значение, вложенные блоки."""
    head = _pad(b"\0" * 6 + (key + "\0").encode("utf-16-le"))
    body = head + _pad(value) + children
    vlen = value_len if value_len is not None else len(value)
    return _pad(struct.pack("<HHH", len(body), vlen, 1 if is_text else 0) + body[6:])


def version_resource(version: str, name: str = "VaultMC", exe_name: str = "VaultMC.exe") -> bytes:
    parts = [int(p) if p.isdigit() else 0 for p in version.split(".")][:4]
    parts += [0] * (4 - len(parts))
    ms, ls = (parts[0] << 16) | parts[1], (parts[2] << 16) | parts[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    strings = {
        "CompanyName": name,
        "FileDescription": name,          # это название показывает диспетчер задач
        "FileVersion": version,
        "InternalName": name,
        "LegalCopyright": f"{name}. Внутри — интерпретатор Python (лицензия PSF).",
        "OriginalFilename": exe_name,
        "ProductName": name,
        "ProductVersion": version,
    }
    table = b"".join(_node(k, (v + "\0").encode("utf-16-le"), True, value_len=len(v) + 1) for k, v in strings.items())
    string_info = _node("StringFileInfo", b"", True, _node("040904B0", b"", True, table))
    var_info = _node("VarFileInfo", b"", True, _node("Translation", struct.pack("<HH", LANG_EN_US, 0x04B0), False))
    return _node("VS_VERSION_INFO", fixed, False, string_info + var_info)


def make_branded_copy(src_exe: Path, ico: Path, dst_exe: Path, version: str, name: str = "VaultMC") -> None:
    """dst_exe — копия src_exe со значком ico и названием name в сведениях о файле."""
    items = _read_ico(ico)
    shutil.copy2(src_exe, dst_exe)
    old = {t: _existing(dst_exe, t) for t in (RT_ICON, RT_GROUP_ICON, RT_VERSION)}
    handle = _k32.BeginUpdateResourceW(str(dst_exe), False)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"BeginUpdateResource {dst_exe}")
    ok = False
    try:
        for rtype, found in old.items():
            for res_name, lang in found:
                _update(handle, rtype, res_name, lang, None)
        group = struct.pack("<HHH", 0, 1, len(items))
        for n, (entry, image) in enumerate(items, start=1):
            _update(handle, RT_ICON, n, LANG_EN_US, image)
            group += entry + struct.pack("<H", n)
        _update(handle, RT_GROUP_ICON, 1, LANG_EN_US, group)
        _update(handle, RT_VERSION, 1, LANG_EN_US, version_resource(version, name, dst_exe.name))
        ok = True
    finally:
        if not _k32.EndUpdateResourceW(handle, not ok):   # ok=False — отменяем правку, exe не портим
            raise OSError(ctypes.get_last_error(), f"EndUpdateResource {dst_exe}")


def make_icon_copy(src_exe: Path, ico: Path, dst_exe: Path) -> None:
    """Старое имя функции: копия со значком и названием (версия в сведениях — 0)."""
    make_branded_copy(src_exe, ico, dst_exe, "0.0.0")
