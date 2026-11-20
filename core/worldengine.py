"""
Облако миров — порядок действий лаунчера (раздел 7 дизайна docs/world-sync-design.md).

CloudEngine связывает: state.json (core/worldsync.py), запросы (core/worldcloud.py) и события лаунчера
(запуск, «Играть», выход из игры). Что нельзя нарушать (правила раздела 2):
  1. локальный мир главный: заменённая папка уходит в корзину сборки, ничего не стирается;
  2. «какая копия новее» решает цепочка версий на сервере (relation), а не часы ПК — спорное решает игрок;
  3. пропажа мира из облака — не команда: только «облачной копии нет» и кнопка;
  4. мир не отправляется и не заменяется, пока его может писать игра (запущена, метка playing, session.lock).
По умолчанию выключено (config.world_sync).
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import skinserver, worldcloud, worldsync as S
from .worldcloud import CloudError

INFO_TTL = 300
SYNC_EVERY = 900          # список облака в фоне — раз в 15 минут
LEASE_EVERY = 300         # отметка «мир открыт на этом ПК» во время игры
RETRY_BUSY = 180          # чужая загрузка идёт — повтор через 3 минуты


def W_files(world: Path):
    from . import worlds as W
    for f in W.world_files(world):
        try:
            f.stat()
            yield f
        except OSError:
            continue


class Busy(Exception):
    """Сборка запущена (или мир открыт) — менять мир нельзя."""


class WorldError(Exception):
    """Понятная игроку ошибка с кодом (интерфейс показывает текст по code)."""

    def __init__(self, code: str, message: str = "", data: dict | None = None):
        super().__init__(message or code)
        self.code = code
        self.data = data or {}


class _Restart(Exception):
    """Загрузку надо начать заново (сессия истекла, части не собрались)."""


class CloudEngine:
    def __init__(self, cfg, accounts, instances, is_running, emit, log=print, state: S.SyncState | None = None):
        self.cfg, self.accounts, self.instances = cfg, accounts, instances
        self.is_running, self.emit, self.log = is_running, emit, log
        self._state = state
        self._state_lock = threading.Lock()
        self._info: dict | None = None
        self._info_at = 0.0
        self._cloud: dict[str, dict] = {}            # ник → {"ok", "at", "data"} — последний ответ list
        self._busy: set[tuple] = set()               # (ник, сборка, папка) — задача уже идёт
        self._busy_lock = threading.Lock()
        self._prog: dict[tuple, dict] = {}
        self._prog_at: dict[tuple, float] = {}
        self._retry: dict[tuple, float] = {}
        self._wait: dict[tuple, dict] = {}            # чужая загрузка: {device_name}
        self._err_data: dict[tuple, dict] = {}
        self._ilocks: dict[str, threading.RLock] = {}
        self._starts: dict[str, int] = {}
        self._pool = ThreadPoolExecutor(2, thread_name_prefix="cloud")
        self._lease_stop: threading.Event | None = None
        self._loop_started = False
        self._asked_choose: set[tuple] = set()

    # ------------------------------------------------------------------ служебное
    @property
    def state(self) -> S.SyncState:
        with self._state_lock:
            if self._state is None:
                self._state = S.SyncState()
            return self._state

    def _ilock(self, inst_id: str) -> threading.RLock:
        with self._state_lock:
            return self._ilocks.setdefault(inst_id, threading.RLock())

    def refresh_info(self) -> dict | None:
        """Что сервер умеет (status.worlds). None — облако недоступно: привязки на паузе, ничего не трогаем."""
        info = skinserver.worlds_status()
        self._info, self._info_at = info, time.time()
        return info

    def supported(self) -> bool:
        return self._info is not None

    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "world_sync", False)) and self.supported()

    def limits(self) -> dict:
        i = self._info or {}
        return {"max_world": int(i.get("max_world") or S.MAX_WORLD), "part_size": int(i.get("part_size") or S.PART_SIZE)}

    def _acct(self, nick: str):
        return next((a for a in self.accounts.list() if a.kind in ("offline", "elyby") and a.username.lower() == nick.lower()), None)

    def _kind(self, nick: str) -> str:
        a = self._acct(nick)
        return a.kind if a else ""

    def _key(self, b: dict) -> tuple:
        return (b["nick"].lower(), b["instance"], b["folder"])

    def _claim(self, key: tuple) -> bool:
        with self._busy_lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            return True

    def _unclaim(self, key: tuple) -> None:
        with self._busy_lock:
            self._busy.discard(key)

    def _claimed(self, key: tuple) -> bool:
        with self._busy_lock:
            return key in self._busy

    def _progress(self, b: dict, kind: str, pct: float, force: bool = False) -> None:
        key = self._key(b)
        now = time.time()
        if not force and now - self._prog_at.get(key, 0) < 0.4 and pct < 100:
            return
        self._prog_at[key] = now
        self._prog[key] = {"kind": kind, "pct": int(pct)}
        self.emit("cloud_progress", {"instance": b["instance"], "folder": b["folder"], "kind": kind, "pct": int(pct)})

    def _clear_progress(self, b: dict) -> None:
        self._prog.pop(self._key(b), None)

    def _set_err(self, b: dict, code: str, data: dict | None = None) -> None:
        self._err_data[self._key(b)] = data or {}
        self.state.update(b, err=code)
        self.emit("cloud_update", {"instance": b["instance"]})

    def _busy_game(self, inst_id: str) -> bool:
        """Игра этой сборки может писать мир: запущена этим лаунчером, метка playing с живым java, session.lock."""
        if self.is_running(inst_id):
            return True
        p = self.state.playing()
        if p.get("instance") == inst_id and S.java_pid_alive(int(p.get("pid") or 0)):
            return True
        inst = self.instances.get(inst_id)
        if inst:
            for b in self.state.for_instance(inst_id):
                if S.world_locked(inst.game_dir / "saves" / b["folder"]):
                    return True
        return False

    def _world(self, inst, b: dict) -> Path:
        return inst.game_dir / "saves" / b["folder"]

    # ------------------------------------------------------------------ список облака
    def list_cloud(self, nick: str) -> dict | None:
        """list для ника: relation каждой привязки, облачные миры, квота. Проверяет владельца (owner_tag, 7.1)."""
        kind = self._kind(nick)
        if not kind:
            return None
        mine = [b for b in self.state.for_nick(nick) if not b["off"]]
        dev = self.state.device
        try:
            res = worldcloud.call(nick, kind, "list", device=dev["id"], device_name=dev["name"],
                                  bases={b["wid"]: b["base_rid"] for b in mine})
        except CloudError as exc:
            self._cloud[nick.lower()] = {"ok": False, "at": time.time(), "err": exc.code, "data": {}}
            if exc.code == "disabled":
                self._info = None
            elif exc.status in (403, 409) and exc.code in ("auth", "taken", ""):
                for b in mine:
                    self.state.update(b, paused="taken")
            elif exc.status:
                self.log(f"[Облако] список: {exc}")
            self.emit("cloud_update", {"instance": ""})
            return None
        tag = str(res.get("owner_tag") or "")
        for b in mine:
            if tag and not b["owner_tag"]:
                self.state.update(b, owner_tag=tag, paused="")
            elif tag and b["owner_tag"] != tag:
                self.state.update(b, paused="owner")       # ник теперь другой аккаунт VaultMC — мир на ПК цел, облако на паузе
            elif b["paused"] in ("owner", "taken"):
                self.state.update(b, paused="")
        self._cloud[nick.lower()] = {"ok": True, "at": time.time(), "data": res}
        self.emit("cloud_update", {"instance": ""})
        return res

    def _entry(self, nick: str, wid: str) -> dict | None:
        c = self._cloud.get(nick.lower())
        if not c or not c["ok"]:
            return None
        return next((w for w in c["data"].get("worlds", []) if w["wid"] == wid), None)

    # ------------------------------------------------------------------ решение по одному миру
    def evaluate(self, inst, b: dict) -> tuple[str, dict | None]:
        c = self._cloud.get(b["nick"].lower())
        online = bool(c and c["ok"])
        data = c["data"] if online else {}
        world = self._world(inst, b)
        exists = world.is_dir()
        entry = next((w for w in data.get("worlds", []) if w["wid"] == b["wid"]), None)
        gone = b["wid"] in data.get("gone", [])
        if b["paused"] in ("taken", "owner"):
            return "paused_owner", entry
        if online and gone and not b["base_rid"] and not b["link"] and exists:
            return "upload", None                      # первая отправка нового мира
        older = False
        changed = True
        if exists:
            changed = not b["fp"] or S.fingerprint(world) != b["fp"]
            info = S.level_info(world)
            older = bool(info and b["level_mtime"] and info["mtime"] < b["level_mtime"] - 2)
        return S.decide(online=online, owner_ok=True, gone=gone, listed=entry is not None, folder_exists=exists,
                        changed=changed, relation=(entry or {}).get("relation"), hold=b["hold"], older=older,
                        choose=b["choose"]), entry

    # ------------------------------------------------------------------ задачи
    def _submit(self, key: tuple, fn) -> bool:
        if not self._claim(key):
            return False

        def run():
            try:
                fn()
            except Busy:
                pass
            except Exception as exc:  # noqa: BLE001  облако — не повод ронять лаунчер
                self.log(f"[Облако] {exc}")
            finally:
                self._unclaim(key)
                self.emit("cloud_update", {"instance": key[1]})
        self._pool.submit(run)
        return True

    def handle(self, inst, b: dict) -> str:
        """Одно действие по решению (фон): отправить / заранее скачать / показать окно выбора."""
        if b["off"] or not self._acct(b["nick"]):
            return "off"
        d, entry = self.evaluate(inst, b)
        key = self._key(b)
        if d == "upload" and not self._busy_game(inst.id):
            self._submit(key, lambda: self.upload(inst, b))
        elif d == "download" and entry and entry.get("head"):
            self._submit(key, lambda: self.prefetch(inst, b, entry["head"]))
        elif d == "choose" and key not in self._asked_choose:
            self._asked_choose.add(key)
            self.emit("cloud_choose", {"instance": inst.id, "folder": b["folder"]})
        return d

    def fresh(self, seconds: float) -> bool:
        """Все списки облака получены успешно и не раньше чем `seconds` назад."""
        now = time.time()
        have = list(self._cloud.values())
        return bool(have) and all(v.get("ok") and now - v.get("at", 0) < seconds for v in have)

    def sync_all(self) -> None:
        """Запуск лаунчера и раз в 15 минут: список облака → отправить изменённые, заранее скачать новые."""
        if not self.enabled():
            return
        for nick in {b["nick"] for b in self.state.all() if not b["off"]}:
            self.list_cloud(nick)
        for b in self.state.all():
            inst = self.instances.get(b["instance"])
            if inst is None or b["off"] or self._claimed(self._key(b)):
                continue
            try:
                self.handle(inst, b)
            except OSError as exc:
                self.log(f"[Облако] «{b['folder']}»: {exc}")

    # ------------------------------------------------------------------ отправка (7.3)
    def upload(self, inst, b: dict, force: bool = False) -> None:
        """Отправить мир в облако. force — игрок решил сам (отпечаток не сравниваем)."""
        for attempt in range(2):
            try:
                return self._upload_once(inst, b, force)
            except _Restart:
                b = self.state.update(b, upload=None) or b
                if attempt:
                    raise
        return None

    def _stop_if_game(self, inst, started: int):
        if self._busy_game(inst.id) or self._starts.get(inst.id, 0) != started:
            raise Busy()

    def _upload_once(self, inst, b: dict, force: bool) -> None:
        b = self.state.find(b["nick"], b["instance"], b["folder"]) or b
        if b["off"] or b["hold"] == "conflict" or (b["hold"] == "crash" and not force):
            return
        world = self._world(inst, b)
        kind = self._kind(b["nick"])
        started = self._starts.get(inst.id, 0)
        self._stop_if_game(inst, started)
        fp0 = S.fingerprint(world)
        up = b["upload"]
        if up and fp0 != up.get("zip_fp"):               # мир с тех пор менялся — дослать нельзя, заново
            self._abort_quiet(b, up)
            b = self.state.update(b, upload=None) or b
            up = None
        if not up and not force and b["fp"] and fp0 == b["fp"]:
            return
        if S.level_info(world) is None:
            self._set_err(b, "broken")
            return
        limits = self.limits()
        zp = S.SYNC_HOME / "out" / f"{b['wid']}.zip"
        try:
            if not up:
                self._progress(b, "pack", 0, True)
                info = S.build_archive(world, zp, limits["part_size"], lambda: self._starts.get(inst.id, 0) == started and not self._busy_game(inst.id))
                fp1 = S.fingerprint(world)
                if fp1 != fp0 or self._starts.get(inst.id, 0) != started:
                    zp.unlink(missing_ok=True)           # игру запускали, пока жали: отпечаток не видит запись в открытый файл
                    raise Busy()
                if info["size"] > limits["max_world"]:
                    zp.unlink(missing_ok=True)
                    self._set_err(b, "too_big", {"size": info["size"], "max": limits["max_world"],
                                                 "parts": [[n, s] for n, s in S.biggest_parts(world)]})
                    return
                up = self._begin(inst, b, fp0, info, limits, zp)
                if up is None:
                    return
                b = self.state.update(b, upload=up, err="") or b
            self._send_parts(b, up, zp)
            self._commit(b, up, fp0, zp, world)
        except Busy:
            raise
        finally:
            self._clear_progress(b)
            if not self.is_running(inst.id):
                self._release([b["wid"]], b["nick"])

    def _abort_quiet(self, b: dict, up: dict) -> None:
        try:
            worldcloud.call(b["nick"], self._kind(b["nick"]), "abort", sid=S.unseal(up.get("sid", "")))
        except Exception:  # noqa: BLE001  сервер сам уберёт загрузку по сроку
            pass

    def _begin(self, inst, b: dict, fp0: str, info: dict, limits: dict, zp: Path) -> dict | None:
        dev = self.state.device
        try:
            res = worldcloud.call(b["nick"], self._kind(b["nick"]), "begin", wid=b["wid"], base=b["base_rid"], size=info["size"],
                                  sha256=info["sha256"], meta=S.world_meta(self._world(inst, b), inst), device=dev["id"],
                                  device_name=dev["name"], **({"new": True} if not b["base_rid"] and not b["link"] else {}))
        except CloudError as exc:
            zp_keep = exc.code == "conflict"
            if exc.code == "conflict":
                self._conflict(b)
            elif exc.code == "busy":
                self._wait[self._key(b)] = {"device_name": exc.data.get("device_name", "")}
                self._retry[self._key(b)] = time.time() + RETRY_BUSY
            elif exc.code in ("quota", "full", "too_big"):
                self._set_err(b, exc.code, exc.data)
            elif exc.code == "gone":
                self.state.update(b, err="gone")
            elif exc.status == 0:
                self.log(f"[Облако] нет связи: {exc}")
            else:
                self.log(f"[Облако] отправка: {exc}")
            if not zp_keep:
                zp.unlink(missing_ok=True)
            return None
        self._wait.pop(self._key(b), None)
        return {"sid": S.seal(res["sid"]), "zip": str(zp), "zip_fp": fp0, "size": info["size"], "sha256": info["sha256"],
                "part_size": int(res.get("part_size") or limits["part_size"]), "parts": int(res["parts"]),
                "md5": info["md5"], "etags": {}, "rid": res.get("rid", "")}

    def _conflict(self, b: dict) -> None:
        self.state.update(b, choose=True)
        self._asked_choose.discard(self._key(b))
        self.emit("cloud_choose", {"instance": b["instance"], "folder": b["folder"]})

    def _send_parts(self, b: dict, up: dict, zp: Path) -> None:
        sid = S.unseal(up["sid"])
        etags = dict(up["etags"])
        size, ps = up["size"], up["part_size"]
        with open(zp, "rb") as fh:
            for n in range(1, up["parts"] + 1):
                if str(n) in etags:
                    continue
                fh.seek((n - 1) * ps)
                data = fh.read(ps)
                tries = 0
                while True:
                    try:
                        base = (n - 1) * ps
                        etags[str(n)] = worldcloud.put_part(sid, n, data, up["md5"][n - 1],
                                                           lambda sent, base=base: self._progress(b, "up", (base + sent) * 100 / size))
                        break
                    except CloudError as exc:
                        if exc.code == "session":
                            raise _Restart() from None
                        tries += 1
                        if exc.code == "part" and tries >= 3:
                            self._abort_quiet(b, up)
                            zp.unlink(missing_ok=True)
                            raise _Restart() from None
                        if exc.status == 0 and tries >= 3:
                            self.log(f"[Облако] нет связи, отправка продолжится позже: {exc}")
                            raise Busy() from None
                        if exc.code not in ("part", "") and exc.status != 0:
                            raise
                        time.sleep(2 * tries)
                self.state.update(b, upload={**up, "etags": etags})
        up["etags"] = etags

    def _commit(self, b: dict, up: dict, fp0: str, zp: Path, world: Path) -> None:
        sid = S.unseal(up["sid"])
        parts = [{"n": int(n), "etag": e} for n, e in sorted(up["etags"].items(), key=lambda kv: int(kv[0]))]
        for attempt in range(2):
            try:
                res = worldcloud.call(b["nick"], self._kind(b["nick"]), "commit", sid=sid, parts=parts)
                break
            except CloudError as exc:
                if exc.code == "conflict":
                    self._conflict_after_commit(b, up, fp0, exc)
                    return
                if exc.code == "in_progress":
                    time.sleep(5)
                    continue
                if exc.code == "parts" and attempt == 0:
                    self.state.update(b, upload={**up, "etags": {}})
                    raise _Restart() from None
                if exc.code == "session":
                    raise _Restart() from None
                if exc.code == "gone":
                    self.state.update(b, err="gone", upload=None)
                    return
                raise
        else:
            return
        info = S.level_info(world) or {}
        self.state.update(b, base_rid=res["rid"], base_rev=int(res["rev"]), fp=fp0, synced_at=int(time.time()),
                          level_mtime=info.get("mtime", 0.0), upload=None, err="", hold="", hold_fp="", choose=False, link=False)
        zp.unlink(missing_ok=True)
        self._asked_choose.discard(self._key(b))

    def _conflict_after_commit(self, b: dict, up: dict, fp0: str, exc: CloudError) -> None:
        head = (exc.data or {}).get("head") or {}
        if head.get("sha256") and head["sha256"] == up.get("sha256"):
            # это наша же отправка: ответ на commit потерялся, а загрузка уже убрана — берём её за основу (7.1)
            self.state.update(b, base_rid=head["rid"], base_rev=int(head.get("rev", 0)), fp=up.get("zip_fp", fp0),
                              upload=None, err="", choose=False, synced_at=int(time.time()))
            Path(up["zip"]).unlink(missing_ok=True)
            return
        self._conflict(b)

    def _release(self, wids: list[str], nick: str) -> None:
        try:
            worldcloud.call(nick, self._kind(nick), "release", device=self.state.device["id"], wids=wids)
        except Exception:  # noqa: BLE001  отметка пропадёт сама через 20 минут
            pass

    # ------------------------------------------------------------------ скачивание и замена (7.4)
    def _disk_ok(self, inst, head: dict) -> None:
        raw = int((head.get("meta") or {}).get("raw_size") or 0)
        need = int(head["size"] + raw * 1.1 + 200 * (1 << 20))
        S.SYNC_HOME.mkdir(parents=True, exist_ok=True)
        inst.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(S.SYNC_HOME).free < need or shutil.disk_usage(inst.root).free < need:
            raise WorldError("disk", "Не хватает места на диске", {"need": need})

    def prefetch(self, inst, b: dict, head: dict) -> Path:
        """Скачать версию, проверить sha256, распаковать в <сборка>/.vaultmc-sync/new-<wid>/world. Игра не мешает."""
        wid, rid = b["wid"], head["rid"]
        nd = S.new_dir(inst.root, wid)
        ready = nd / "ready.json"
        try:
            if json.loads(ready.read_text(encoding="utf-8")).get("rid") == rid and (nd / "world").is_dir():
                return nd / "world"
        except (OSError, ValueError):
            pass
        shutil.rmtree(nd, ignore_errors=True)
        self._disk_ok(inst, head)
        zp = S.SYNC_HOME / "in" / f"{wid}-{rid}.zip"
        part = zp.with_name(zp.name + ".part")
        kind = self._kind(b["nick"])
        for _ in range(3):
            if not zp.exists():
                try:
                    worldcloud.download(b["nick"], kind, wid, rid, part, head["size"],
                                        lambda have, size: self._progress(b, "down", have * 100 / size))
                except CloudError as exc:
                    self._clear_progress(b)
                    if exc.code == "missing":
                        self._set_err(b, "missing")
                    raise
                if S.sha256_file(part) != head["sha256"]:
                    part.unlink(missing_ok=True)
                    continue
                part.replace(zp)
            if S.sha256_file(zp) == head["sha256"]:
                break
            zp.unlink(missing_ok=True)
        else:
            self._clear_progress(b)
            self._set_err(b, "corrupt")
            raise WorldError("corrupt", "Облачная копия повреждена")
        try:
            raw = int((head.get("meta") or {}).get("raw_size") or 0)
            limit = min(3 * self.limits()["max_world"], int(raw * 1.01) + (1 << 20)) if raw else 3 * self.limits()["max_world"]
            S.extract_archive(zp, nd / "world", limit)
            if S.level_info(nd / "world") is None:
                raise S.UnsafeArchive("level.dat не читается")
            ready.write_text(json.dumps({"rid": rid, "rev": head["rev"], "fp_new": S.fingerprint(nd / "world")}), encoding="utf-8")
        except S.UnsafeArchive as exc:
            shutil.rmtree(nd, ignore_errors=True)
            zp.unlink(missing_ok=True)
            self._clear_progress(b)
            self._set_err(b, "corrupt")
            raise WorldError("corrupt", str(exc)) from None
        finally:
            self._clear_progress(b)
        return nd / "world"

    def apply(self, inst, b: dict, head: dict, ignore_fp: bool = False) -> bool:
        """Заменить папку мира версией из облака. False — менять нельзя (игрок играл, пока качали): нужен выбор."""
        new_world = self.prefetch(inst, b, head)
        wid = b["wid"]
        info = json.loads((S.new_dir(inst.root, wid) / "ready.json").read_text(encoding="utf-8"))
        with self._ilock(inst.id):
            if self._busy_game(inst.id):
                raise Busy()
            world = self._world(inst, b)
            cur = self.state.find(b["nick"], b["instance"], b["folder"]) or b
            if world.exists() and not ignore_fp and cur["fp"] and S.fingerprint(world) != cur["fp"]:
                self.state.update(b, choose=True)
                return False
            res = S.apply_cloud_copy(inst.root, inst.game_dir, b["folder"], new_world, wid=wid, rid=head["rid"],
                                     rev=int(head["rev"]), nick=b["nick"], fp_new=info["fp_new"], log=self.log)
            self._bind_result(b, res)
        for z in (S.SYNC_HOME / "in").glob(f"{wid}-*.zip"):
            z.unlink(missing_ok=True)
        return True

    def _bind_result(self, b: dict, res: dict) -> None:
        self.state.update(b, base_rid=res["rid"], base_rev=res["rev"], fp=res["fp"], level_mtime=res["level_mtime"],
                          synced_at=int(time.time()), choose=False, hold="", hold_fp="", err="", upload=None, link=False)
        self._asked_choose.discard(self._key(b))

    def recover_all(self) -> None:
        """Запуск лаунчера: довести прерванные замены (журнал в каждой сборке)."""
        for inst in self.instances.list():
            try:
                res = S.recover(inst.root, inst.game_dir, self.log)
            except OSError as exc:
                self.log(f"[Облако] восстановление замены не вышло: {exc}")
                continue
            if not res:
                continue
            b = self.state.find_folder(inst.id, res["folder"])
            if b is None and res.get("nick") and res.get("wid"):      # state.json потерян — журнал доводит до конца и привязывает
                b = self.state.add({**S.new_binding(res["nick"], inst.id, res["folder"], res["wid"]), "link": True})
            if b:
                self._bind_result(b, res)

    # ------------------------------------------------------------------ перед «Играть», после игры, во время игры
    def _bindings(self, inst) -> list[dict]:
        return [b for b in self.state.for_instance(inst.id) if not b["off"] and self._acct(b["nick"])]

    def _warnings(self, inst, meta: dict) -> list[dict]:
        out = []
        cm, im = re.findall(r"\d+", str(meta.get("mc", "")))[:3], re.findall(r"\d+", inst.game_version)[:3]
        if cm and im and [int(x) for x in cm] > [int(x) for x in im]:
            out.append({"code": "version", "cloud": meta.get("mc", ""), "here": inst.game_version})
        mods = [m["id"] for m in meta.get("mods", []) if isinstance(m, dict) and m.get("id")]
        if mods:
            have: set[str] = set()
            try:
                from .modcheck import read_mod
                for jar in inst.mods_dir.glob("*.jar"):
                    have.update(i.lower() for i in read_mod(jar).ids if i)
            except Exception:  # noqa: BLE001
                pass
            skip = {"minecraft", "java", "fabricloader", "fabric", "forge", "neoforge", "quilt_loader"}
            missing = [m for m in mods if m.lower() not in have and m.lower() not in skip]
            if meta.get("loader") and meta["loader"] != inst.loader and mods or missing:
                out.append({"code": "mods", "loader": meta.get("loader", ""), "here": inst.loader, "missing": missing[:8]})
        return out

    def before_play(self, inst, mode: str, progress=lambda p, t: None) -> dict | None:
        """Перед запуском. mode: "" — обычно, "go" — игрок подтвердил предупреждения, "skip" — «Играть без обновления».
        Возвращает None (можно играть) или gate {kind: choose | confirm, ...} — запуск остановить и спросить."""
        if mode == "skip" or not self.enabled():
            return None
        bs = self._bindings(inst)
        if not bs:
            return None
        with_nick = {b["nick"] for b in bs}
        for nick in with_nick:                       # свежий список; нет связи — играем в копию с этого ПК (8.8)
            progress(0.03, "Проверяю облако миров...")
            self.list_cloud(nick)
        if any(not (self._cloud.get(n.lower()) or {}).get("ok") for n in with_nick):
            self.emit("cloud_notice", {"code": "offline", "instance": inst.id})
        deadline = time.time() + 900
        while any(self._claimed(self._key(b)) for b in bs) and time.time() < deadline:
            progress(0.05, "Готовлю мир к игре...")
            time.sleep(0.5)
        plan = [(b, *self.evaluate(inst, b)) for b in (self.state.find(x["nick"], x["instance"], x["folder"]) or x for x in bs)]
        choose = [b["folder"] for b, d, _e in plan if d == "choose"]
        if choose:
            return {"kind": "choose", "folders": choose}
        if mode != "go":
            lease = [{"folder": b["folder"], "title": (e or {}).get("title", b["folder"]), **(e["lease"])} for b, _d, e in plan
                     if e and e.get("lease")]
            warn = [{"folder": b["folder"], "title": e["title"], "items": w} for b, d, e in plan if d == "download" and e
                    for w in [self._warnings(inst, e["head"]["meta"])] if w]
            if lease or warn:
                return {"kind": "confirm", "lease": lease, "warn": warn}
        downloads = [(b, e) for b, d, e in plan if d == "download" and e]
        for i, (b, e) in enumerate(downloads, 1):
            progress(0.05 + 0.1 * i / len(downloads), f"Готовлю мир к игре: {e['title']}")
            try:
                if not self.apply(inst, b, e["head"]):
                    return {"kind": "choose", "folders": [b["folder"]]}
            except (CloudError, WorldError) as exc:
                self.log(f"[Облако] мир «{e['title']}» не обновлён: {exc}")
                self.emit("cloud_notice", {"code": getattr(exc, "code", "") or "error", "title": e["title"], "instance": inst.id})
        for b in bs:
            if b["wid"]:
                self._lease(b)
        return None

    def _lease(self, b: dict) -> None:
        dev = self.state.device
        try:
            worldcloud.call(b["nick"], self._kind(b["nick"]), "lease", device=dev["id"], device_name=dev["name"], wids=[b["wid"]])
        except Exception:  # noqa: BLE001  отметка — подсказка, игре не мешает
            pass

    def note_launch(self, inst_id: str) -> None:
        """Игру этой сборки запускают: метка playing (до старта — PID пока неизвестен) и счётчик запусков."""
        self._starts[inst_id] = self._starts.get(inst_id, 0) + 1
        if self.enabled() or self.state.for_instance(inst_id):
            self.state.set_playing(inst_id, 0)

    def game_started(self, inst_id: str, pid: int) -> None:
        if not (self.enabled() or self.state.for_instance(inst_id)):
            return
        self.state.set_playing(inst_id, pid)
        self.game_stopped()
        stop = threading.Event()
        self._lease_stop = stop

        def loop():
            while not stop.wait(LEASE_EVERY):
                inst = self.instances.get(inst_id)
                for b in (self._bindings(inst) if inst else []):
                    self._lease(b)
        threading.Thread(target=loop, daemon=True).start()

    def game_stopped(self) -> None:
        if self._lease_stop is not None:
            self._lease_stop.set()
            self._lease_stop = None

    def after_game(self, inst, crashed: bool) -> None:
        """Игра закрылась. Вылет (код не 0, «убить игру», новый отчёт) → изменённые миры с hold = crash (правило 4).
        Нормальный выход → отправить изменённые."""
        self.game_stopped()
        for _ in range(40):
            if not self.is_running(inst.id):
                break
            time.sleep(0.5)
        self.state.set_playing()
        if not self.enabled():
            return
        for nick in {b["nick"] for b in self._bindings(inst)}:
            self.list_cloud(nick)
        for b in self._bindings(inst):
            self._after_one(inst, b, crashed)

    def _after_one(self, inst, b: dict, crashed: bool) -> None:
        world = self._world(inst, b)
        if not world.is_dir():
            return
        fp = S.fingerprint(world)
        changed = fp != b["fp"]
        if crashed:
            if changed:
                self.state.update(b, hold="crash", hold_fp=fp)
            self._release([b["wid"]], b["nick"])
            return
        if b["hold"] == "crash":
            if fp == b["hold_fp"]:
                self._release([b["wid"]], b["nick"])
                return                                   # с вылета мир не открывали — по-прежнему держим
            b = self.state.update(b, hold="", hold_fp="") or b
        if changed:
            self.handle(inst, b)
        else:
            self._release([b["wid"]], b["nick"])

    def check_playing_mark(self) -> None:
        """Метка playing: игра закрылась без нас (закрыли лаунчер) — код выхода неизвестен, считаем вылетом (7.2)."""
        p = self.state.playing()
        if not p or self.is_running(p.get("instance", "")):
            return
        if S.java_pid_alive(int(p.get("pid") or 0)):
            return
        inst = self.instances.get(p.get("instance", ""))
        self.state.set_playing()
        if inst and int(p.get("pid") or 0):
            for b in self._bindings(inst):
                world = self._world(inst, b)
                if world.is_dir() and S.fingerprint(world) != b["fp"]:
                    self.state.update(b, hold="crash", hold_fp=S.fingerprint(world))

    # ------------------------------------------------------------------ фон
    def start(self) -> None:
        """Запуск лаунчера (из потока): довести замены, проверить метку, список облака, дальше — раз в 15 минут."""
        try:
            self.recover_all()
            self.refresh_info()
            self.check_playing_mark()
            self.sync_all()
        except Exception as exc:  # noqa: BLE001
            self.log(f"[Облако] запуск: {exc}")
        if self._loop_started:
            return
        self._loop_started = True

        def loop():
            last = time.time()
            while True:
                time.sleep(60)
                try:
                    self.check_playing_mark()
                    due = [k for k, t in self._retry.items() if t <= time.time()]
                    if due or time.time() - last >= SYNC_EVERY:
                        for k in due:
                            self._retry.pop(k, None)
                        last = time.time()
                        if time.time() - self._info_at > INFO_TTL:
                            self.refresh_info()
                        self.sync_all()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"[Облако] {exc}")
        threading.Thread(target=loop, daemon=True, name="cloud-loop").start()

    # ------------------------------------------------------------------ действия игрока
    def status(self) -> dict:
        if time.time() - self._info_at > INFO_TTL:
            self.refresh_info()
        return {"supported": self.supported(), "enabled": bool(self.cfg.world_sync), "device": self.state.device["name"],
                "limits": self.limits()}

    def set_enabled(self, on: bool, device_name: str = "") -> dict:
        if device_name:
            self.state.set_device_name(device_name)
        self.cfg.world_sync = bool(on)
        return self.status()

    def enable_world(self, inst, folder: str, mode: str = "") -> dict:
        """«В облако» у мира. mode: "" — проверить, нет ли в облаке мира с тем же названием; "new" — новый облачный
        мир; "link:<wid>" — это тот же мир, что уже в облаке (дальше окно выбора копии)."""
        acc = self.accounts.active
        if acc is None or acc.kind not in ("offline", "elyby"):
            raise WorldError("account", "Облако миров работает с оффлайн-аккаунтом или Ely.by")
        world = inst.game_dir / "saves" / folder
        info = S.level_info(world)
        if info is None:
            raise WorldError("broken", "Мир повреждён: level.dat не читается")
        nick = acc.username
        existing = self.state.find_folder(inst.id, folder)
        if existing:
            if existing["nick"].lower() != nick.lower():
                raise WorldError("other_account", f"Этот мир уже связан с облаком аккаунта «{existing['nick']}»")
            self.state.update(existing, off=False)
            self.emit("cloud_update", {"instance": inst.id})
            self.sync_all_async()
            return {"ok": True}
        res = self.list_cloud(nick)
        if res is None:
            raise WorldError("offline", "Нет связи с облаком")
        worlds = res.get("worlds", [])
        bound = {b["wid"] for b in self.state.for_nick(nick)}
        if mode.startswith("link:"):
            wid = mode[5:]
            if wid in bound:
                raise WorldError("already", "Этот облачный мир уже связан с другим миром на этом ПК")
            if not any(w["wid"] == wid for w in worlds):
                raise WorldError("gone", "Облачной копии нет")
            link = True
        else:
            if not mode:
                similar = [{"wid": w["wid"], "title": w["title"], "device_name": (w.get("head") or {}).get("device_name", ""),
                            "at": (w.get("head") or {}).get("at", 0)} for w in worlds
                           if w["wid"] not in bound and w["title"].strip().lower() == info["title"].strip().lower()]
                if similar:
                    return {"similar": similar, "title": info["title"]}
            wid, link = secrets.token_hex(12), False
        b = S.new_binding(nick, inst.id, folder, wid, str(res.get("owner_tag") or ""))
        self.state.add({**b, "link": link})
        self.emit("cloud_update", {"instance": inst.id})
        self.sync_all_async()
        return {"ok": True}

    def sync_all_async(self) -> None:
        threading.Thread(target=self.sync_all, daemon=True).start()

    def disable_world(self, inst, folder: str) -> None:
        b = self.state.find_folder(inst.id, folder)
        if b:
            self.state.update(b, off=True)
            self.emit("cloud_update", {"instance": inst.id})

    def send_now(self, inst, folder: str) -> None:
        """«Отправить» после вылета (или после ошибки): hold снимается, мир отправляется."""
        b = self.state.find_folder(inst.id, folder)
        if not b:
            raise WorldError("gone", "Мир не связан с облаком")
        if self._busy_game(inst.id):
            raise WorldError("playing", "Закройте игру — мир может быть открыт")
        b = self.state.update(b, hold="", hold_fp="", err="") or b
        self._submit(self._key(b), lambda: self.upload(inst, b, True))

    def resolve(self, inst, folder: str, action: str, nick: str = "") -> None:
        """Окно выбора копии (7.5): keep_mine | take_cloud | keep_both | later. Только когда сборка не запущена."""
        b = self.state.find_folder(inst.id, folder)
        if not b:
            raise WorldError("gone", "Мир не связан с облаком")
        if self._busy_game(inst.id):
            raise WorldError("playing", "Закройте игру — мир может быть открыт")
        if action == "later":
            self.state.update(b, hold="conflict", choose=False)
            self.emit("cloud_update", {"instance": inst.id})
            return
        res = self.list_cloud(b["nick"])
        if res is None:
            raise WorldError("offline", "Нет связи с облаком")
        entry = self._entry(b["nick"], b["wid"])
        if entry is None or not entry.get("head"):
            raise WorldError("gone", "Облачной копии нет")
        head = entry["head"]
        if action == "keep_mine":
            b = self.state.update(b, base_rid=head["rid"], base_rev=int(head["rev"]), choose=False, hold="", link=False) or b
            self._submit(self._key(b), lambda: self.upload(inst, b, True))
        elif action == "take_cloud":
            b = self.state.update(b, hold="", choose=False) or b
            self._submit(self._key(b), lambda: self.apply(inst, b, head, True))
        elif action == "keep_both":
            self._keep_both(inst, b, entry)
        else:
            raise WorldError("bad", "Неизвестное действие")
        self.emit("cloud_update", {"instance": inst.id})

    def _keep_both(self, inst, b: dict, entry: dict) -> None:
        """Папку этого ПК переименовать в «<папка> (<название ПК>)», привязку снять; облачную — в исходную папку."""
        saves = inst.game_dir / "saves"
        world = saves / b["folder"]
        if world.is_dir():
            import os
            os.rename(world, saves / S.free_folder(saves, f"{b['folder']} ({self.state.device['name']})"))
        up = b.get("upload")
        if up:
            self._abort_quiet(b, up)
        self.state.remove(b)
        nb = S.new_binding(b["nick"], inst.id, b["folder"], b["wid"], b["owner_tag"])
        self.state.add({**nb, "link": True})
        nb = self.state.find(b["nick"], inst.id, b["folder"]) or nb
        head = entry["head"]

        def job():
            try:
                self.apply(inst, nb, head, True)
            except Exception:
                self.state.remove(nb)
                raise
        self._submit(self._key(nb), job)

    def download_cloud(self, inst, nick: str, wid: str, confirmed: bool = False) -> dict:
        """«Скачать в сборку…» мир, которого нет на этом ПК (8.2) — в свободное имя папки, с привязкой."""
        if self._busy_game(inst.id):
            raise WorldError("playing", "Закройте игру — мир может быть открыт")
        if not self._acct(nick):
            raise WorldError("account", "Войдите в аккаунт, под которым мир лежит в облаке")
        entry = self._entry(nick, wid)
        if not entry or not entry.get("head"):
            raise WorldError("gone", "Облачной копии нет")
        head = entry["head"]
        warn = self._warnings(inst, head["meta"])
        if warn and not confirmed:
            return {"warn": warn}
        saves = inst.game_dir / "saves"
        folder = S.free_folder(saves, head["meta"].get("folder") or entry["title"])
        tag = str((self._cloud.get(nick.lower()) or {}).get("data", {}).get("owner_tag") or "")
        nb = {**S.new_binding(nick, inst.id, folder, wid, tag), "link": True}
        if self.state.find_folder(inst.id, folder):
            raise WorldError("already", "Папка уже связана с облаком")
        self.state.add(nb)

        def job():
            try:
                self.apply(inst, nb, head, True)
            except Exception:
                self.state.remove(nb)
                raise
        self._submit(self._key(nb), job)
        return {"ok": True, "folder": folder}

    def redownload(self, inst, folder: str) -> None:
        """Мир привязан, а папки нет (удалили или потеряли): скачать из облака обратно — только по кнопке."""
        b = self.state.find_folder(inst.id, folder)
        entry = self._entry(b["nick"], b["wid"]) if b else None
        if not b or not entry or not entry.get("head"):
            raise WorldError("gone", "Облачной копии нет")
        self._submit(self._key(b), lambda: self.apply(inst, b, entry["head"], True))

    def reupload(self, inst, folder: str) -> None:
        """«Облачной копии нет» → «Загрузить заново»: мир уходит в облако как новый (только по кнопке, правило 3)."""
        b = self.state.find_folder(inst.id, folder)
        if not b:
            raise WorldError("gone", "Мир не связан с облаком")
        b = self.state.update(b, wid=secrets.token_hex(12), base_rid="", base_rev=0, fp="", link=False, err="", choose=False, hold="") or b
        self._submit(self._key(b), lambda: self.upload(inst, b, True))

    def delete_cloud(self, nick: str, wid: str) -> None:
        kind = self._kind(nick)
        if not kind:
            raise WorldError("account", "Войдите в аккаунт, под которым мир лежит в облаке")
        try:
            worldcloud.call(nick, kind, "delete", wid=wid)
        except CloudError as exc:
            raise WorldError(exc.code or "error", str(exc)) from None
        for b in self.state.by_wid(nick, wid):
            self.state.remove(b)
        self.list_cloud(nick)

    def restore_prev(self, nick: str, wid: str) -> None:
        """«Вернуть прошлую облачную версию»: она становится новой текущей, остальные ПК увидят её как обычное обновление."""
        entry = self._entry(nick, wid)
        if not entry or not entry.get("prev") or not entry.get("head"):
            raise WorldError("gone", "Прошлой версии уже нет")
        try:
            worldcloud.call(nick, self._kind(nick), "restore", wid=wid, rid=entry["prev"]["rid"], if_head=entry["head"]["rid"])
        except CloudError as exc:
            raise WorldError(exc.code or "error", str(exc)) from None
        self.list_cloud(nick)

    def abort_remote(self, nick: str, wid: str) -> None:
        """Снять загрузку с другого ПК (она держит место в квоте)."""
        try:
            worldcloud.call(nick, self._kind(nick), "abort", wid=wid)
        except CloudError as exc:
            raise WorldError(exc.code or "error", str(exc)) from None
        self.list_cloud(nick)

    def rename_nick(self, old: str, new: str) -> None:
        self.state.rename_nick(old, new)
        self._cloud.pop(old.lower(), None)

    def instance_deleted(self, inst_id: str) -> None:
        self.state.remove_instance(inst_id)

    def choose_info(self, inst, folder: str) -> dict:
        """Данные для окна выбора копии (7.5): обе копии рядом. Время «по часам» — только для показа."""
        b = self.state.find_folder(inst.id, folder)
        entry = self._entry(b["nick"], b["wid"]) if b else None
        if not b or not entry or not entry.get("head"):
            raise WorldError("gone", "Облачной копии нет")
        world = self._world(inst, b)
        info = S.level_info(world) or {}
        size = sum(f.stat().st_size for f in W_files(world))
        h = entry["head"]
        meta = h.get("meta") or {}
        return {"title": entry["title"], "device": self.state.device["name"],
                "local": {"last_played": info.get("last_played", 0), "mc": info.get("mc", ""), "size": size},
                "cloud": {"device_name": h["device_name"], "at": h["at"], "size": h["size"], "mc": meta.get("mc", ""),
                          "rev_ahead": max(0, int(h["rev"]) - int(b["base_rev"])) if entry.get("relation") == "unknown" else 0}}

    def bound_folders(self, inst_id: str) -> list[str]:
        return [b["folder"] for b in self.state.for_instance(inst_id)]

    # ------------------------------------------------------------------ для интерфейса
    def _badge(self, inst, b: dict) -> dict:
        key = self._key(b)
        out = {"nick": b["nick"], "wid": b["wid"], "status": "", "device_name": "", "err": b["err"], "data": self._err_data.get(key, {})}
        prog = self._prog.get(key)
        if b["off"]:
            return {**out, "status": "off"}
        if not self._acct(b["nick"]):
            return {**out, "status": "paused", "reason": "account"}
        if b["paused"]:
            return {**out, "status": "paused", "reason": b["paused"]}
        if prog:
            return {**out, "status": {"up": "uploading", "pack": "uploading", "down": "downloading"}.get(prog["kind"], "uploading"),
                    "pct": prog["pct"], "kind": prog["kind"]}
        if self._claimed(key):
            return {**out, "status": "working"}
        if b["err"]:
            return {**out, "status": "error"}
        if self.supported():
            d, entry = self.evaluate(inst, b)
        else:
            return {**out, "status": "paused", "reason": "server"}
        if d == "choose":
            return {**out, "status": "choose"}
        if b["hold"] == "crash":
            return {**out, "status": "crash"}
        if b["hold"] == "conflict":
            return {**out, "status": "later"}
        if d == "offline":
            return {**out, "status": "offline"}
        if d == "gone":
            return {**out, "status": "gone"}
        if d == "cloud_only":
            return {**out, "status": "missing_local"}
        if entry and entry.get("lease"):
            return {**out, "status": "leased", "device_name": entry["lease"]["device_name"]}
        wait = self._wait.get(key)
        if wait:
            return {**out, "status": "busy", "device_name": wait["device_name"]}
        if d == "download":
            return {**out, "status": "newer", "device_name": ((entry or {}).get("head") or {}).get("device_name", "")}
        if d == "upload":
            return {**out, "status": "pending"}
        return {**out, "status": "ok"}

    def view(self, inst) -> dict:
        """Состояние облака для вкладки «Миры» (без сети: по последнему ответу list)."""
        bound = self.state.for_instance(inst.id)
        out = {"supported": self.supported(), "enabled": bool(self.cfg.world_sync), "device": self.state.device["name"],
               "worlds": {b["folder"]: self._badge(inst, b) for b in bound}, "cloud_only": [], "usage": None,
               "limits": self.limits()}
        if not self.supported() or (not self.cfg.world_sync and not bound):
            return out
        nicks = {b["nick"].lower(): b["nick"] for b in self.state.all()}
        if self.accounts.active and self.accounts.active.kind in ("offline", "elyby"):
            nicks.setdefault(self.accounts.active.username.lower(), self.accounts.active.username)
        bound_wids = {(b["nick"].lower(), b["wid"]) for b in self.state.all()}
        for low, nick in nicks.items():
            c = self._cloud.get(low)
            if not c or not c["ok"]:
                continue
            if self.accounts.active and low == self.accounts.active.username.lower():
                out["usage"] = c["data"].get("usage")
            for w in c["data"].get("worlds", []):
                if (low, w["wid"]) in bound_wids or not w.get("head"):
                    continue
                h = w["head"]
                out["cloud_only"].append({"nick": nick, "wid": w["wid"], "title": w["title"], "size": h["size"], "at": h["at"],
                                          "device_name": h["device_name"], "mc": (h.get("meta") or {}).get("mc", ""),
                                          "prev": bool(w.get("prev")), "upload": w.get("upload")})
        return out
