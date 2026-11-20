"""API вкладки «Свой сервер» (core/serverpack.py — сборка, core/serverrun.py — запуск). Подмешивается в класс Api."""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from core import serverpack as SP
from core import serverside
from core.serverrun import LocalServer, local_addresses


class ServerApiMixin:
    _server: LocalServer | None = None

    def _srv(self) -> LocalServer:
        if self._server is None:
            self._server = LocalServer(self._emit)
        return self._server

    def server_info(self, instance_id: str) -> dict:
        from core import worlds as W
        inst = self._inst(instance_id)
        folder = SP.server_dir(inst.id)
        man = SP.read_manifest(folder)
        try:
            SP.loader_of(inst)
            problem = ""
        except SP.ServerError as exc:
            problem = str(exc)
        acc = self._accounts.active
        nick = acc.username if acc else ""
        st = self._srv().status()
        mine = st["instance"] == inst.id
        ram = (man or {}).get("ram_gb") or (2 if inst.loader == "vanilla" else 4)
        return {"built": bool(man), "manifest": man or {}, "dir": str(folder), "problem": problem,
                "eula_url": SP.EULA_URL, "nick": nick, "ram": ram, "port": (man or {}).get("port") or 25565,
                "worlds": [w["folder"] for w in W.list_worlds(inst.game_dir) if w.get("folder")],
                "has_world": (folder / "world" / "level.dat").is_file(), "loader": inst.loader,
                "eula": (folder / "eula.txt").is_file() and "eula=true" in (folder / "eula.txt").read_text("utf-8", "replace"),
                "status": st if mine else {"state": "off" if st["state"] == "off" else "busy", "instance": st["instance"],
                                            "port": 0, "lines": []},
                "addresses": local_addresses()}

    def server_plan(self, instance_id: str) -> dict:
        """Моды сборки с пометкой «на сервер / только у игрока» (по .jar, Modrinth и списку)."""
        inst = self._inst(instance_id)
        if inst.loader == "vanilla":
            return {"mods": []}
        man = SP.read_manifest(SP.server_dir(inst.id)) or {}
        items = serverside.to_dicts(serverside.classify(inst.mods_dir))
        return {"mods": items, "previous": man.get("mods") if man else None, "skipped": man.get("skipped") or []}

    def server_build(self, instance_id: str, options: dict) -> dict:
        inst = self._inst(instance_id)
        try:
            opts = SP.ServerOptions.parse(options)
        except SP.ServerError as exc:
            return {"error": str(exc)}
        if self._srv().running_for(inst.id):
            return {"error": "Сначала остановите сервер"}
        if opts.world and self._instance_running(inst.id):
            return {"error": "Закройте игру: мир копируется, пока его никто не меняет"}
        runner = self._runner

        def job():
            builder = SP.Builder(self._repo, runner._choose_java, lambda m: self._on_runner_event("log", m),
                                 lambda v, t: self._on_runner_event("progress", (v, t)), runner.cancel_event)
            return {**SP.build(inst, builder, opts), "instance_id": inst.id}
        started = runner.run_task(f"Сервер «{inst.name}»...", job, "server_built")
        return {"started": True} if started else {"error": "Дождитесь окончания текущей операции"}

    def server_start(self, instance_id: str) -> dict:
        inst = self._inst(instance_id)
        folder = SP.server_dir(inst.id)
        man = SP.read_manifest(folder)
        if not man:
            return {"error": "Сначала соберите сервер"}
        if not (folder / "eula.txt").is_file():
            return {"error": "Без согласия с EULA Minecraft сервер не запустится — отметьте его и нажмите «Обновить сервер»"}
        java = str(man.get("java") or "")
        if not Path(java).is_file():
            return {"error": "Java для сервера не найдена — нажмите «Обновить сервер»"}
        srv = self._srv()
        if srv.proc is not None:
            return {"error": "Уже запущен другой сервер — сначала остановите его"}
        port = int(man.get("port") or 25565)
        if not SP.free_port(port):
            return {"error": f"Порт {port} занят (другой сервер или открытый мир) — смените порт и нажмите «Обновить сервер»"}
        try:
            srv.start(folder, inst.id, java, int(man.get("ram_gb") or 4), list(man["launch"]), port)
        except (OSError, RuntimeError) as exc:
            return {"error": f"Сервер не запустился: {exc}"}
        return {"ok": True}

    def server_stop(self) -> dict:
        self._srv().stop()
        return {"ok": True}

    def server_command(self, text: str) -> dict:
        try:
            self._srv().command(str(text or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True}

    def server_play(self, instance_id: str) -> dict:
        port = self._srv().port if self._srv().running_for(instance_id) else 0
        if not port:
            return {"error": "Сервер не запущен"}
        return self.play(instance_id, f"127.0.0.1:{port}")

    def server_open_folder(self, instance_id: str) -> dict:
        from core.utils import open_in_file_manager
        folder = SP.server_dir(self._inst(instance_id).id)
        if not folder.is_dir():
            return {"error": "Сервер ещё не собран"}
        open_in_file_manager(folder)
        return {"ok": True}

    def server_export(self, instance_id: str, with_world: bool = True) -> dict:
        inst = self._inst(instance_id)
        folder = SP.server_dir(inst.id)
        if not SP.read_manifest(folder):
            return {"error": "Сначала соберите сервер"}
        if self._srv().running_for(inst.id) and with_world:
            return {"error": "Остановите сервер: мир в архив кладётся целым, только когда сервер выключен"}
        target = self._dialog("save", save_filename=f"{inst.name}-server.zip", file_types=("Архив (*.zip)",))
        if not target:
            return {"cancelled": True}
        dest = Path(target if str(target).lower().endswith(".zip") else f"{target}.zip")

        def work():
            try:
                n = SP.export_zip(folder, dest, bool(with_world))
                self._emit("server_exported", {"path": str(dest), "files": n})
            except OSError as exc:
                self._emit("server_exported", {"error": f"Архив не создан: {exc}"})
        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    def server_delete(self, instance_id: str) -> dict:
        inst = self._inst(instance_id)
        if self._srv().running_for(inst.id):
            return {"error": "Сначала остановите сервер"}
        folder = SP.server_dir(inst.id)
        if folder.is_dir() and folder.parent == SP.SERVERS_DIR:
            shutil.rmtree(folder, ignore_errors=True)
        return {"ok": True}

    def _shutdown_server(self) -> None:
        """Лаунчер закрывается: сервер сохраняет мир и выключается (до 45 с)."""
        if self._server is not None:
            self._server.stop(wait=True)
