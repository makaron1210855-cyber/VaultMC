"""
Сборка команды запуска и сам запуск игры.

LaunchCommandBuilder — превращает version.json + настройки в список аргументов Java.
GameRunner          — фоновый поток: установка/починка -> выбор Java -> natives -> процесс игры.
GameRunner общается с интерфейсом только через post(event, payload):
    "log" str | "progress" (float, str) | "state" str | "installed" str
    "game_exit" dict | "simulated" str | "error" str
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import LauncherConfig
from .forge import parse_loader_id
from .installer import Installer
from .java_runtime import ensure_runtime
from .cds import ensure_base_cds
from .net import Cancelled
from .rules import rules_allow
from .friends import find_tunnel_address
from . import discordrpc
from .utils import (APP_NAME, APP_VERSION, available_ram_mb, find_java_installs, java_is_32bit, low_memory_warning, format_command, high_priority_flags, is_windows, set_process_priority,
                    java_major_version, maven_to_path, mojang_os_name, no_window_flags, offline_uuid,
                    pick_java, set_gpu_preference, timing_log)
from .versions import VersionRepository, parse_fabric_id

# Флаги сборщика мусора G1 для игры (на основе флагов Aikar). Пауза короче, чем у серверного набора (200 мс):
# в игре длинная пауза = заметный фриз. Только флаги, которые есть и в Java 17, и в Java 21.
G1_FLAGS = [
    "-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=50", "-XX:+PerfDisableSharedMem",
    "-XX:+UnlockExperimentalVMOptions", "-XX:+DisableExplicitGC", "-XX:G1NewSizePercent=30",
    "-XX:G1MaxNewSizePercent=40", "-XX:G1HeapRegionSize=8M", "-XX:G1ReservePercent=20",
    # как в официальном лаунчере Mojang: повторяющиеся строки хранятся один раз; стартовая куча занимается сразу
    "-XX:+UseStringDeduplication", "-XX:+AlwaysPreTouch",
]


def compact_headers_flags(java_major: int) -> list[str]:
    """Java 24+: короткие заголовки объектов (JEP 450/519) — меньше памяти и чуть быстрее; в Java 24 флаг экспериментальный."""
    if java_major < 24:
        return []
    return (["-XX:+UnlockExperimentalVMOptions"] if java_major == 24 else []) + ["-XX:+UseCompactObjectHeaders"]


def pick_gc_flags(java_major: int, ram_gb: int, cores: int) -> list[str]:
    """
    Сборщик мусора под ПК. ZGC (Java 21+, «поколенческий») держит паузы около миллисекунды, но сам ест больше
    процессора и памяти — поэтому только при 6+ ГБ для игры и 6+ потоках процессора. Иначе — G1 (флаги на основе Aikar):
    на слабых ПК он даёт больше FPS. Shenandoah не используем: его нет в некоторых сборках Java,
    а незнакомый флаг не даёт игре запуститься.
    """
    if java_major >= 21 and ram_gb >= 6 and cores >= 6:
        flags = ["-XX:+UseZGC"]
        if java_major < 23:              # в Java 23+ поколенческий режим включён сам, а в 24 этот флаг убран
            flags.append("-XX:+ZGenerational")
        # -ZUncommit: куча не возвращается системе и не выделяется заново (меньше микрофризов после сборки мусора);
        # AlwaysPreTouch: память под стартовую кучу занимается сразу, а не страницами во время игры.
        # Оба флага стоят в запуске PineconeMC (форк Prism) на этом же ПК — проверено по его журналу 16.11.2026
        return flags + ["-XX:-ZUncommit", "-XX:+AlwaysPreTouch", "-XX:+DisableExplicitGC", "-XX:+PerfDisableSharedMem",
                        "-XX:+UseStringDeduplication"]
    return list(G1_FLAGS)


class LaunchError(Exception):
    pass


@dataclass
class LaunchPlan:
    version_id: str
    command: list[str]
    classpath: list[Path]
    native_jars: list[Path]
    natives_dir: Path
    java_major: int
    is_stub: bool = False
    java_component: str = ""

    def missing_files(self) -> list[Path]:
        return [p for p in self.classpath + self.native_jars if not p.exists()]


def _lib_key(name: str) -> str:
    """'group:artifact:version[:classifier][@ext]' → 'group:artifact[:classifier]'."""
    parts = name.split("@")[0].split(":")
    return ":".join(parts[:2] + parts[3:]) if len(parts) >= 3 else name


def split_server(address: str) -> tuple[str, str]:
    address = address.strip()
    if address.count(":") == 1:
        host, port = address.split(":")
        return host, port
    return address, ""


@dataclass
class AuthInfo:
    """Данные игрока для команды запуска."""
    name: str
    uuid: str                  # без дефисов
    token: str = "0"
    user_type: str = "legacy"  # legacy (оффлайн) | mojang (Ely.by) | msa (Microsoft)
    jvm_args: list[str] = field(default_factory=list)  # например, -javaagent authlib-injector

    @classmethod
    def offline(cls, name: str) -> "AuthInfo":
        return cls(name, offline_uuid(name))


@dataclass
class LaunchTarget:
    """Что запускаем: версия + папка игры сборки + персональные настройки."""
    version_id: str
    game_dir: Path
    ram_gb: int
    extra_jvm: str = ""
    title: str = ""
    compat: bool = False       # без флагов GC/CDS лаунчера, без смены приоритета и без --fullscreen


class LaunchCommandBuilder:
    def __init__(self, config: LauncherConfig, repo: VersionRepository):
        self.config = config
        self.repo = repo

    @property
    def data_dir(self) -> Path:
        return self.repo.game_dir

    def _flatten(self, raw: list, features: dict) -> list[str]:
        result: list[str] = []
        for item in raw:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict) and rules_allow(item.get("rules"), features):
                value = item.get("value", [])
                result.extend([value] if isinstance(value, str) else value)
        return result

    def _libraries(self, data: dict) -> tuple[list[Path], list[Path]]:
        libs_dir = self.data_dir / "libraries"
        classpath: list[Path] = []
        natives: list[Path] = []
        seen: set[str] = set()
        for lib in data.get("libraries", []):
            if not rules_allow(lib.get("rules")):
                continue
            downloads = lib.get("downloads", {})
            if "natives" in lib:
                classifier = lib["natives"].get(mojang_os_name())
                if classifier:
                    classifier = classifier.replace("${arch}", "64")
                    rel = downloads.get("classifiers", {}).get(classifier, {}).get("path") \
                        or maven_to_path(f"{lib['name']}:{classifier}")
                    natives.append(libs_dir / rel)
                if "artifact" not in downloads:
                    continue
            rel = downloads.get("artifact", {}).get("path") or maven_to_path(lib["name"])
            # одна библиотека — одна версия: у Forge/Fabric своя версия идёт первой и побеждает ванильную
            key = _lib_key(lib.get("name", rel))
            if rel not in seen and key not in seen:
                seen.update((rel, key))
                classpath.append(libs_dir / rel)
        jar_id = data.get("jar", data.get("id"))
        classpath.append(self.data_dir / "versions" / jar_id / f"{jar_id}.jar")
        return classpath, natives

    @staticmethod
    def _stub_version(version_id: str) -> dict:
        parsed = parse_fabric_id(version_id)
        game = parsed[1] if parsed else version_id
        return {
            "id": version_id, "jar": game, "type": "release", "assets": game, "libraries": [],
            "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient" if parsed else "net.minecraft.client.main.Main",
            "minecraftArguments": "--username ${auth_player_name} --version ${version_name} --gameDir ${game_directory} "
                                  "--assetsDir ${assets_root} --assetIndex ${assets_index_name} --uuid ${auth_uuid} "
                                  "--accessToken ${auth_access_token} --userType ${user_type}",
        }

    def build(self, target: LaunchTarget, auth: AuthInfo, server: str = "", allow_stub: bool = False) -> LaunchPlan:
        cfg = self.config
        version_id = target.version_id
        if self.repo.is_installed(version_id):
            data, stub = self.repo.load_version_json(version_id), False
        elif allow_stub:
            data, stub = self._stub_version(version_id), True
        else:
            raise FileNotFoundError(version_id)

        custom_res = not cfg.fullscreen and cfg.window_width > 0 and cfg.window_height > 0
        features = {"has_custom_resolution": custom_res, "is_quick_play_multiplayer": bool(server)}
        natives_dir = self.data_dir / "versions" / version_id / "natives"
        classpath, native_jars = self._libraries(data)
        asset_id = data.get("assets") or data.get("assetIndex", {}).get("id", "legacy")
        host, port = split_server(server)

        values = {
            "auth_player_name": auth.name, "version_name": version_id, "game_directory": str(target.game_dir),
            "assets_root": str(self.data_dir / "assets"),
            "game_assets": str(self.data_dir / "assets" / "virtual" / asset_id),
            "assets_index_name": asset_id, "auth_uuid": auth.uuid.replace("-", ""), "auth_access_token": auth.token,
            "auth_session": auth.token, "clientid": "0", "auth_xuid": "0", "user_type": auth.user_type,
            "user_properties": "{}", "version_type": data.get("type", "release"), "natives_directory": str(natives_dir),
            "launcher_name": APP_NAME.replace(" ", "-"), "launcher_version": APP_VERSION,
            "classpath": os.pathsep.join(str(p) for p in classpath), "classpath_separator": os.pathsep,
            "library_directory": str(self.data_dir / "libraries"),
            "resolution_width": str(cfg.window_width), "resolution_height": str(cfg.window_height),
            "quickPlayMultiplayer": server,
        }

        def sub(arg: str) -> str:
            return re.sub(r"\$\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), arg)

        arguments = data.get("arguments", {})
        jvm_raw = self._flatten(arguments["jvm"], features) if "jvm" in arguments \
            else ["-Djava.library.path=${natives_directory}", "-cp", "${classpath}"]
        jvm = [f"-Xmx{target.ram_gb}G", f"-Xms{min(2, target.ram_gb)}G", *auth.jvm_args]
        if cfg.optimize_jvm and not target.compat:
            jvm += G1_FLAGS
        for extra in (cfg.extra_jvm_args, target.extra_jvm):
            if extra.strip():
                jvm += shlex.split(extra, posix=not is_windows())
        jvm += [sub(a) for a in jvm_raw]

        legacy = "game" not in arguments
        game_raw = data.get("minecraftArguments", "").split() if legacy else self._flatten(arguments["game"], features)
        game = [sub(a) for a in game_raw]
        if legacy and custom_res:
            game += ["--width", str(cfg.window_width), "--height", str(cfg.window_height)]
        if cfg.fullscreen and not target.compat:
            game.append("--fullscreen")
        if server and "is_quick_play_multiplayer" not in json.dumps(arguments.get("game", [])):
            game += ["--server", host] + (["--port", port] if port else [])

        java = data.get("javaVersion", {})
        java_major = int(java.get("majorVersion", 8))
        command = [cfg.java_path or "java", *jvm, data["mainClass"], *game]
        return LaunchPlan(version_id, command, classpath, native_jars, natives_dir, java_major, stub,
                          java.get("component", ""))


# строки лога, по которым видно, что мир открыли для сети (ванильные и мода-туннеля)
LAN_PORT_RE = re.compile(r"Started serving on (\d{2,5})\b")
LAN_MARKERS = ("Started serving on", "hosted on port", "hosted on domain", "Local game hosted", "Opened to LAN")


def extract_natives(plan: LaunchPlan) -> None:
    if not plan.native_jars:
        return
    plan.natives_dir.mkdir(parents=True, exist_ok=True)
    for jar in plan.native_jars:
        with zipfile.ZipFile(jar) as zf:
            for info in zf.infolist():
                member = info.filename
                if member.startswith("META-INF") or member.endswith("/"):
                    continue
                target = plan.natives_dir / Path(member).name
                # сверяем размер: обрезанный файл (игру закрыли во время распаковки) иначе остался бы навсегда
                if not target.exists() or target.stat().st_size != info.file_size:
                    tmp = target.with_name(target.name + ".tmp")
                    tmp.write_bytes(zf.read(info))
                    try:
                        tmp.replace(target)
                    except OSError:  # файл занят запущенной игрой — оставляем как есть
                        tmp.unlink(missing_ok=True)


class GameRunner:
    """
    События (post): "log" str | "progress" (float, str) | "state" str | "installed" str | "error" str
                    "game_exit" dict | "simulated" str | "room_address" str
    """

    def __init__(self, config: LauncherConfig, repo: VersionRepository, post: Callable[[str, object], None]):
        self.config = config
        self.repo = repo
        self.post = post
        self.cancel_event = threading.Event()
        self.process: subprocess.Popen | None = None
        self.busy = False
        self._start_lock = threading.Lock()
        self.state = "idle"
        self.killed = False

    def _log(self, text: str) -> None:
        self.post("log", text)

    def _progress(self, value: float, text: str) -> None:
        self.post("progress", (value, text))

    def _state(self, state: str) -> None:
        self.state = state
        self.post("state", state)

    def _installer(self) -> Installer:
        return Installer(self.repo, self.config.download_threads, self._progress, self._log, self.cancel_event,
                         java=self._java_for_install)

    def _apply_cds(self, plan, target) -> None:
        """Базовый архив классов Java (CDS). В Java от Mojang его нет (нет bin/server/classes.jsa) — Java грузила все
        классы с нуля. Лаунчер один раз создаёт архив сам (~0,5 с): Fabric 1.21.1 с 70 модами — 17,0 с вместо 17,3–18,1.
        Свой архив сборки (-XX:+AutoCreateSharedArchive) больше не включаем: запуск, во время которого он пишется,
        шёл 187 с вместо 18 (замер 19.11.2026, Java 21, и с G1, и с ZGC), а выигрыш потом — около 2 с."""
        java_major = getattr(self, "_java_major", 0) or plan.java_major
        if java_major < 12 or not self.config.optimize_jvm or target.compat:
            return
        ensure_base_cds(Path(plan.command[0]), self.repo.game_dir / "runtime", self._log)
        old_cds = self.repo.game_dir / "cds"        # архивы сборок до 8.0.4 (до 170 МБ на версию) больше не нужны
        if old_cds.is_dir():
            def unlock(func, path, _exc):          # Java делает .jsa «только для чтения» — без этого rmtree их пропускает
                try:
                    os.chmod(path, 0o666)
                    func(path)
                except OSError:
                    pass
            shutil.rmtree(old_cds, onexc=unlock)

    GC_LOG = "logs/vaultmc-gc.log"   # относительный путь: игра запускается из папки сборки (cwd), двоеточия диска не мешают

    def _apply_gc_log(self, plan, target) -> None:
        """Java 9+: короткий журнал сборщика мусора (core/lagcheck.py по нему находит долгие паузы).
        Проверено на Java 17, 21 и 25 с относительным и абсолютным путём. На Java 8 флага -Xlog нет — игра бы не запустилась."""
        java_major = getattr(self, "_java_major", 0) or plan.java_major
        if java_major < 9 or target.compat or any(a.startswith("-Xlog:gc") for a in plan.command):
            return
        (target.game_dir / "logs").mkdir(parents=True, exist_ok=True)
        plan.command[1:1] = [f"-Xlog:gc:file={self.GC_LOG}:time,uptime:filecount=2,filesize=2m"]

    def _apply_gc(self, plan, target) -> None:
        """Флаги G1 уже в команде; если Java и ПК позволяют — меняем их на ZGC. Свои флаги GC пользователя не трогаем."""
        if not self.config.optimize_jvm or target.compat:
            return
        own = f"{self.config.extra_jvm_args} {target.extra_jvm}"
        if re.search(r"-XX:\+Use\w+GC", own):
            return
        java_major = getattr(self, "_java_major", 0) or plan.java_major
        flags = pick_gc_flags(java_major, target.ram_gb, os.cpu_count() or 0)
        cmd = plan.command
        try:
            start = cmd.index(G1_FLAGS[0])
        except ValueError:
            return
        if cmd[start:start + len(G1_FLAGS)] != G1_FLAGS:
            return
        extra = [f for f in compact_headers_flags(java_major) if f not in cmd and f not in flags]
        if flags != G1_FLAGS:
            self._log(f"[Память] Сборщик мусора ZGC: паузы почти незаметны ({target.ram_gb} ГБ, Java {java_major})")
        cmd[start:start + len(G1_FLAGS)] = flags + extra
        if extra:
            self._log(f"[Память] Java {java_major}: короткие заголовки объектов (меньше памяти)")

    def _check_resources(self, java: str, target) -> None:
        """Предупреждения до запуска (запуск они не меняют): мало свободной памяти, 32-битная Java с большой кучей."""
        text = low_memory_warning(target.ram_gb, available_ram_mb())
        if text:
            self._log(f"[Память] Внимание: {text}")
            self.post("launch_warning", {"kind": "low_ram", "text": text})
        if java_is_32bit(java) and target.ram_gb > 2:
            text = (f"Установлена 32-битная Java, а игре отдано {target.ram_gb} ГБ — 32-битная Java больше 2 ГБ не поддерживает, "
                    f"игра может не запуститься. Включите «Автоматически подбирать Java» в настройках лаунчера")
            self._log(f"[Java] Внимание: {text}")
            self.post("launch_warning", {"kind": "java32", "text": text})

    def _java_for_install(self, major: int) -> str:
        path = self._choose_java(major)
        self._state("installing")      # _choose_java переключает статус на «подготовка»
        return path

    def _start(self, target, *args) -> bool:
        with self._start_lock:         # два быстрых нажатия «Играть» не должны запустить два потока
            if self.busy:
                return False
            self.busy = True
        self.cancel_event.clear()
        threading.Thread(target=target, args=args, daemon=True).start()
        return True

    # ---- публичный API ----
    def launch(self, target: LaunchTarget, prepare_auth: Callable[[], AuthInfo], server: str = "") -> bool:
        """prepare_auth выполняется в фоне (проверка токенов Ely.by/Microsoft, authlib-injector)."""
        return self._start(self._run_launch, target, prepare_auth, server)

    def install(self, version_id: str) -> bool:
        return self._start(self._run_install, version_id)

    def run_task(self, title: str, func: Callable[[], object], done_event: str = "task_done",
                 new_instance: bool = False) -> bool:
        """Любая долгая операция (пакет оптимизации, сборка Modrinth) с блокировкой кнопки «Играть».
        new_instance=True — задача создаёт НОВУЮ сборку (импорт, установка из каталога): панель её не привязывает к выбранной."""
        return self._start(self._run_task, title, func, done_event, new_instance)

    def cancel(self) -> None:
        self.cancel_event.set()

    def _check_cancel(self) -> None:
        """Раньше «Отмену» видела только загрузка файлов: нажатая во время проверки аккаунта, скинов или Java,
        она не действовала, и игра всё равно запускалась."""
        if self.cancel_event.is_set():
            raise Cancelled()

    def kill(self) -> None:
        if self.process and self.process.poll() is None:
            self.killed = True
            self.process.terminate()

    # ---- потоки ----
    def _finish(self) -> None:
        self.busy = False
        self.process = None
        self.cancel_event.clear()
        self._state("idle")

    def _run_task(self, title: str, func, done_event: str, new_instance: bool = False) -> None:
        try:
            self.post("task_new", bool(new_instance))   # до смены статуса: панель сразу знает, чья это задача
            self._state("installing")
            self._progress(0.3, title)
            result = func()
            self.post(done_event, result)
            self._progress(0.0, "Готово")
        except Exception as exc:  # noqa: BLE001
            self._log(f"[Ошибка] {exc}")
            self.post("error", str(exc))
            self._progress(0.0, "Ошибка")
        finally:
            self.post("task_new", False)
            self._finish()

    def _run_install(self, version_id: str) -> None:
        try:
            self._state("installing")
            self._installer().install(version_id)
            self._log(f"[Установка] {version_id} готова")
            self.post("installed", version_id)
            self._progress(0.0, "Готово к запуску")
        except Cancelled:
            self._log("[Установка] Отменено")
            self._progress(0.0, "Установка отменена")
        except Exception as exc:  # noqa: BLE001
            self._log(f"[Ошибка] {exc}")
            self.post("error", f"Установка не удалась: {exc}")
            self._progress(0.0, "Ошибка установки")
        finally:
            self._finish()

    def _run_launch(self, target: LaunchTarget, prepare_auth, server: str) -> None:
        builder = LaunchCommandBuilder(self.config, self.repo)
        try:
            self._state("preparing")
            self._progress(0.02, "Проверка аккаунта...")
            t0 = time.monotonic()
            auth = AuthInfo.offline("Player") if self.config.demo_mode else prepare_auth()
            t_auth = time.monotonic()
            self._check_cancel()          # «Отмена» во время проверки аккаунта и скинов
            if self.config.demo_mode:
                self._simulate(builder.build(target, auth, server, allow_stub=True))
                return

            self._progress(0.05, "Проверка файлов...")
            plan = self._try_build(builder, target, auth, server)
            if plan is None or plan.missing_files():
                self._state("installing")
                self._log(f"[Запуск] {target.version_id}: нужна установка или починка файлов")
                self._installer().install(target.version_id)
                plan = builder.build(target, auth, server)
                missing = plan.missing_files()
                if missing and parse_loader_id(target.version_id):
                    # файлы, которые делает установщик Forge, пропали — ставим Forge заново
                    self._log(f"[Запуск] Переустанавливаю {target.version_id}")
                    self.repo.delete_version(target.version_id)
                    self._installer().install(target.version_id)
                    plan = builder.build(target, auth, server)
                    missing = plan.missing_files()
                if missing:
                    raise LaunchError(f"После установки не хватает {len(missing)} файлов (например, {missing[0].name})")
            self._state("preparing")
            t_files = time.monotonic()

            java = self._choose_java(plan.java_major, plan.java_component)
            t_java = time.monotonic()
            plan.command[0] = java
            self._check_resources(java, target)
            self._apply_gc(plan, target)
            self._apply_cds(plan, target)
            self._apply_gc_log(plan, target)
            if self.config.discrete_gpu and set_gpu_preference(java):
                self._log("[Видеокарта] Для Java выбрана высокопроизводительная видеокарта")
            self._progress(0.95, "Распаковка нативных библиотек...")
            extract_natives(plan)
            t_natives = time.monotonic()
            prep_line = (f"Подготовка лаунчера: {t_natives - t0:.1f} с (аккаунт и скины {t_auth - t0:.1f}, "
                         f"файлы игры {t_files - t_auth:.1f}, Java {t_java - t_files:.1f}, библиотеки {t_natives - t_java:.1f})")
            self._log(f"[Запуск] {prep_line}")
            timing_log(f"{target.version_id}: {prep_line}")
            target.game_dir.mkdir(parents=True, exist_ok=True)
            self._log(f"[Запуск] {target.title or target.version_id} | {auth.name} | {target.ram_gb} ГБ")
            shown = format_command(plan.command)
            if auth.token not in ("", "0"):
                shown = shown.replace(auth.token, "***")  # токен не должен попадать в логи
            self._log("[Команда]\n" + shown)

            # пока игра грузится (все ядра заняты модами), она идёт с пониженным приоритетом — ПК остаётся отзывчивым,
            # окна не «зависают». Когда меню появилось, возвращаем обычный (или повышенный, если включено в настройках)
            self._check_cancel()          # «Отмена» во время подготовки Java и библиотек — игру не запускаем
            flags = no_window_flags()   # обычный приоритет: при пониженном игра плохо отзывается на окна при загрузке
            started = time.time()
            self.killed = False
            self.process = subprocess.Popen(plan.command, cwd=str(target.game_dir), stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                            errors="replace", creationflags=flags)
            proc = self.process
            normal = high_priority_flags() if self.config.high_priority else 0x00000020   # ABOVE_NORMAL / NORMAL

            def restore_priority(p=proc):
                if p.poll() is None and not target.compat:
                    set_process_priority(p, normal)
            if is_windows() and not target.compat and self.config.high_priority:
                _t = threading.Timer(120, restore_priority)   # повышенный приоритет — после загрузки, как в настройках
                _t.daemon = True
                _t.start()
            self._state("running")
            if self.config.discord_status:
                discordrpc.show_playing(target.title or "", target.version_id, started)
            self._progress(1.0, "Игра запущена")
            assert self.process.stdout is not None
            last_address = ""
            loaded = False
            tunnel_warned = False
            lan_open = False   # адрес комнаты принимаем только после «Открыть для сети» —
                               # иначе служебные адреса мода-туннеля при загрузке выглядели как «комната открыта»
            for line in self.process.stdout:
                line = line.rstrip()
                self._log(line)
                if not loaded and "Sound engine started" in line:   # главное меню появилось
                    loaded = True
                    restore_priority()
                    self._log(f"[Запуск] Игра загрузилась за {time.time() - started:.0f} с")
                    timing_log(f"{target.version_id}: процесс игры → звук готов за {time.time() - started:.1f} с")
                port_m = LAN_PORT_RE.search(line)
                if port_m:                                 # «Started serving on 54321» — порт мира для прямого подключения
                    self.post("lan_port", int(port_m.group(1)))
                if not lan_open and any(m in line for m in LAN_MARKERS):
                    lan_open = True
                    self._watch_tunnel(target.game_dir, lambda: last_address)
                if not lan_open:
                    continue
                low = line.lower()
                if ("e4mc" in low or "e4all" in low) and ("error" in low or "exception" in low or "failed" in low) \
                        and not tunnel_warned:
                    tunnel_warned = True
                    self.post("room_problem", "e4mc сообщил об ошибке — комната может не открыться. "
                                              "Подробности в «Консоли». Если адрес так и не появится — "
                                              "сервис e4mc может быть недоступен; попробуйте позже или другой мод-туннель.")
                address = find_tunnel_address(line)
                if address and address != last_address:  # одно и то же не показываем дважды
                    last_address = address
                    self.post("room_address", address)
            code = self.process.wait()
            if self.config.discord_status:
                discordrpc.clear()
            seconds = int(time.time() - started)
            crash = self._find_crash_report(target.game_dir, started) if code != 0 else None
            self._log(f"[Игра] Завершена с кодом {code} через {seconds} с")
            self.post("game_exit", {"version": target.version_id, "code": code, "seconds": seconds,
                                    "crash": str(crash) if crash else "", "killed": self.killed})
            self._progress(0.0, "Готово к запуску")
        except Cancelled:
            self._log("[Запуск] Отменено")
            self._progress(0.0, "Запуск отменён")
        except Exception as exc:  # noqa: BLE001
            self._log(f"[Ошибка] {type(exc).__name__}: {exc}")
            self.post("error", str(exc))
            self._progress(0.0, "Ошибка запуска")
        finally:
            self._finish()

    @staticmethod
    def _try_build(builder: LaunchCommandBuilder, target: LaunchTarget, auth: AuthInfo, server: str) -> LaunchPlan | None:
        try:
            return builder.build(target, auth, server)
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            return None

    def _choose_java(self, required: int, component: str = "") -> str:
        self._java_major = required          # уточняется ниже, когда известна выбранная Java
        cfg = self.config
        java_error = ""
        if cfg.java_auto:
            installs = find_java_installs(self.repo.game_dir)
            exact = [j for j in installs if j.major == required]
            if exact:
                self._log(f"[Java] Автовыбор: Java {required} — {exact[0].path}")
                return exact[0].path
            try:  # нужной Java нет — скачиваем официальную сборку Mojang (как официальный лаунчер)
                self._state("installing")
                path = str(ensure_runtime(self._installer(), component, required))
                self._state("preparing")
                return path
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(f"[Java] Не удалось скачать Java {required}: {type(exc).__name__}: {exc}")
                java_error = str(exc)
            choice = pick_java(installs, required)
            if choice:
                self._log(f"[Java] Автовыбор: Java {choice.major} — {choice.path}")
                if choice.major != required:
                    self._log(f"[Java] Внимание: этой версии нужна Java {required}, найдена {choice.major}")
                self._java_major = choice.major
                return choice.path
        path = cfg.java_path or shutil.which("javaw" if is_windows() else "java") or shutil.which("java")
        if not path:
            if java_error:
                raise LaunchError(f"Не удалось скачать Java {required}: {java_error}. Проверьте интернет и нажмите «Играть» "
                                  f"ещё раз (подробности — во вкладке «Консоль»)")
            raise LaunchError(f"Java не найдена. Включите «Автоматически подбирать Java» в настройках "
                              f"или установите Java {required} (adoptium.net)")
        major = java_major_version(path)
        self._java_major = major or required
        self._log(f"[Java] {path} (версия {major or '?'})")
        if major and major < required:
            raise LaunchError(f"Этой версии нужна Java {required}+, а выбрана Java {major}")
        if major and required <= 8 < major:
            text = (f"Этой версии нужна Java {required}, а выбрана Java {major} — моды и Forge старых версий на новой Java "
                    f"часто не запускаются. Включите «Автоматически подбирать Java» в настройках лаунчера")
            self._log(f"[Java] Внимание: {text}")
            self.post("launch_warning", {"kind": "java_version", "text": text})
        return path

    def _watch_tunnel(self, game_dir, current_address) -> None:
        """Мир открыт для сети, а адреса туннеля нет 40 с — предупреждаем, что e4mc, похоже, не отвечает."""
        mods = Path(game_dir) / "mods"
        try:
            has_tunnel = any(f.name.lower().startswith(("e4mc", "e4all")) and f.name.lower().endswith(".jar")
                             for f in mods.iterdir())
        except OSError:
            has_tunnel = False
        if not has_tunnel:
            return

        def check():
            if not current_address() and self.process is not None and self.process.poll() is None:
                self.post("room_problem", "Мир открыт для сети, но e4mc не выдал адрес за 40 секунд. "
                                          "Обычно это значит, что серверы e4mc сейчас недоступны или их блокирует "
                                          "провайдер/антивирус. Друзья в той же Wi-Fi сети всё равно могут зайти через «Сетевая игра».")
        t = threading.Timer(40, check)
        t.daemon = True
        t.start()

    @staticmethod
    def _find_crash_report(game_dir: Path, since: float) -> Path | None:
        folder = game_dir / "crash-reports"
        if not folder.is_dir():
            return None
        reports = [p for p in folder.glob("*.txt") if p.stat().st_mtime >= since]
        return max(reports, key=lambda p: p.stat().st_mtime) if reports else None

    def _simulate(self, plan: LaunchPlan) -> None:
        self._log(f"[Имитация] {plan.version_id}{' (не установлена — шаблон)' if plan.is_stub else ''}")
        self._log("[Команда]\n" + format_command(plan.command))
        for value, text in [(0.3, "Проверка файлов..."), (0.6, "Проверка ассетов..."),
                            (0.85, "Запуск JVM..."), (1.0, "Игра запущена (имитация)")]:
            if self.cancel_event.is_set():
                raise Cancelled()
            self._progress(value, text)
            time.sleep(0.4)
        self.post("simulated", plan.version_id)
        self._progress(0.0, "Готово к запуску")
