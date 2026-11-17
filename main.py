"""
VaultMC — точка входа.

Обычно окно открывается через pywebview (на Windows — движок Edge WebView2).
Если pywebview не запустился (например, не установился pythonnet), лаунчер
САМ откроется в отдельном окне Microsoft Edge/Chrome — функции те же.
Все ошибки запуска пишутся в  ~/.vaultmc/crash.log

    python main.py            — обычный запуск
    python main.py --browser  — сразу открыть в окне браузера (без pywebview)
    python main.py --dev      — режим разработки (обычная вкладка браузера, F5 обновляет)
"""

import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

# В собранном .exe без консоли sys.stdout/sys.stderr = None — любой print() падает. Перенаправляем в никуда.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
LOG = Path.home() / ".vaultmc" / "crash.log"


def trace(step: str) -> None:
    """Журнал запуска ~/.vaultmc/launcher.log — видно, на каком шаге остановился запуск."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG.with_name("launcher.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {step}\n")
    except OSError:
        pass


def write_crash(title: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — {title}\n{traceback.format_exc()}")
    print(f"[VaultMC] {title}: подробности в {LOG}", file=sys.stderr)
    traceback.print_exc()


def message_box(text: str) -> None:
    """Окно с ошибкой (видно даже при запуске без консоли)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "VaultMC", 0x10)
    except Exception:  # noqa: BLE001 — не Windows
        print(text, file=sys.stderr)


STATE = LOG.with_name("webview_state.json")


def webview_state() -> dict:
    try:
        import json
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def set_webview_state(status: str) -> None:
    import json
    STATE.parent.mkdir(parents=True, exist_ok=True)
    from core.utils import APP_VERSION
    STATE.write_text(json.dumps({"python": sys.version.split()[0], "app": APP_VERSION, "status": status}), encoding="utf-8")


def restart_in_browser_mode() -> None:
    """Запускает отдельный процесс лаунчера в режиме браузера и завершает текущий (зависший)."""
    args = [sys.executable, "--browser"] if getattr(sys, "frozen", False) \
        else [sys.executable, str(ROOT / "main.py"), "--browser"]
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, cwd=str(ROOT), creationflags=flags, close_fds=True)
    os._exit(0)


def preflight_ok() -> bool:
    """
    Один раз для каждой версии Python проверяем в отдельном процессе, что pywebview
    и его компонент pythonnet вообще загружаются (на совсем новых Python они могут зависать).
    """
    if getattr(sys, "frozen", False) or os.name != "nt":
        return True
    state = webview_state()
    if state.get("status") == "pending" and "--browser" not in sys.argv:
        trace("предыдущая копия ещё открывает окно pywebview (или зависла на нём) — отмечаю как неудачу")
    from core.utils import APP_VERSION
    if state.get("python") == sys.version.split()[0] and state.get("app") == APP_VERSION and state.get("status") == "ok":
        return True
    try:
        result = subprocess.run([sys.executable, "-c", "import webview, clr"], timeout=20,
                                capture_output=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
        if result.returncode == 0:
            return True
        trace("проверка pywebview: " + result.stderr.decode("utf-8", "replace").strip().splitlines()[-1][:300])
    except subprocess.TimeoutExpired:
        trace("проверка pywebview: зависла дольше 20 с")
    except Exception as exc:  # noqa: BLE001
        trace(f"проверка pywebview: {exc}")
    set_webview_state("failed")
    return False


def log_dotnet_errors() -> None:
    """
    Ошибки .NET (WebView2/WinForms) убивают процесс с кодом 0xE0434352, не оставляя следов в логе.
    Перехватываем их и пишем текст ошибки в launcher.log — по нему видно настоящую причину.
    """
    if os.name != "nt":
        return
    try:
        import clr  # noqa: F401 — уже загружен pywebview
        clr.AddReference("System.Windows.Forms")
        from System import AppDomain, UnhandledExceptionEventHandler  # type: ignore
        from System.Threading import ThreadExceptionEventHandler  # type: ignore
        from System.Windows.Forms import Application, UnhandledExceptionMode  # type: ignore

        def on_domain(_sender, e):
            trace(".NET (фатально): " + str(e.ExceptionObject)[:6000])

        def on_ui(_sender, e):
            trace(".NET (окно): " + str(e.Exception)[:6000])

        AppDomain.CurrentDomain.UnhandledException += UnhandledExceptionEventHandler(on_domain)
        Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException)
        Application.ThreadException += ThreadExceptionEventHandler(on_ui)
    except Exception as exc:  # noqa: BLE001
        trace(f"перехват ошибок .NET не включился: {exc}")


def show_event_name() -> str:
    """Имя события Windows для «покажи окно»: у каждой версии своё, чтобы новая не будила старую."""
    from core.utils import APP_VERSION
    return f"Local\\VaultMC.Show.{APP_VERSION}"


def ping_running_window() -> bool:
    """Если окно этой же версии уже открыто (даже свёрнуто), поднимаем его и True — новую копию не запускаем."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenEventW(0x0002, False, show_event_name())   # EVENT_MODIFY_STATE
        if not h:
            return False
        k32.SetEvent(h)
        k32.CloseHandle(h)
        return True
    except Exception:  # noqa: BLE001
        return False


def start_show_listener(window) -> None:
    """В окне лаунчера: ждёт сигнала от второго запуска и поднимает окно (восстанавливает из свёрнутого)."""
    import threading
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateEventW.restype = wintypes.HANDLE
        h = k32.CreateEventW(None, False, False, show_event_name())
        if not h:
            return
    except Exception:  # noqa: BLE001
        return

    def loop():
        while True:
            k32.WaitForSingleObject(h, 0xFFFFFFFF)   # ждём бесконечно, в простое не просыпается
            try:
                window.restore()
                window.show()
                trace("второй запуск: окно лаунчера поднято")
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=loop, daemon=True, name="show-listener").start()


def run_app() -> None:
    import threading

    import webview  # если pywebview/pythonnet не установлены — исключение здесь
    trace(f"pywebview {getattr(webview, '__version__', '?')} импортирован")
    log_dotnet_errors()

    from backend.api import Api
    from backend.events import EventBus
    from core.utils import APP_NAME, resource_path

    bus = EventBus()
    api = Api(bus)
    x, y, width, height = window_geometry(api._cfg.ui.get("window"))  # по центру экрана
    window = webview.create_window(
        APP_NAME, str(resource_path("web/index.html")), js_api=api,
        width=width, height=height, x=x, y=y, min_size=(1100, 700),
        frameless=True, easy_drag=False, text_select=True, background_color="#09090f",
    )
    api._attach_window(window)
    bus.attach_window(window)
    start_show_listener(window)

    def remember_size():
        try:
            api.save_window_size(window.width, window.height)
        except Exception:  # noqa: BLE001
            pass
        t = threading.Thread(target=api.presence_offline, daemon=True)   # друзья сразу видят «не в сети»
        t.start()
        t.join(2.5)   # не держим закрытие окна дольше пары секунд

    window.events.closing += remember_size
    shown = threading.Event()

    def on_shown():
        shown.set()
        try:  # окно появилось — запись стека на случай зависания больше не нужна
            import faulthandler as _fh
            _fh.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001
            pass
        set_webview_state("ok")
        trace("окно pywebview показано")

    def watchdog():  # окно не появилось за 45 с — pywebview завис (так бывает на новых версиях Python)
        if not shown.wait(45):
            set_webview_state("failed")
            trace("окно pywebview не появилось за 45 с — перезапуск в окне браузера")
            if "--webview-child" in sys.argv:
                os._exit(4)          # запасной режим запустит родительский процесс
            restart_in_browser_mode()

    def on_loaded():
        trace("страница интерфейса загружена в окне pywebview")
        if not shown.is_set():   # у некоторых систем событие shown не приходит, а окно уже работает
            on_shown()

    window.events.shown += on_shown
    window.events.loaded += on_loaded
    set_webview_state("pending")
    threading.Thread(target=watchdog, daemon=True).start()
    # если окно зависнет так, что даже проверка выше не успеет, — через 25 с записываем в лог, где именно застряли
    try:
        import faulthandler
        _dump = open(LOG.with_name("launcher.log"), "a", encoding="utf-8")  # noqa: SIM115 — открыт до конца работы
        _dump.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  (если ниже появится стек — окно pywebview зависло здесь)\n")
        _dump.flush()
        faulthandler.dump_traceback_later(25, repeat=False, file=_dump)
    except Exception:  # noqa: BLE001
        faulthandler = None
    trace("запускаю окно pywebview")
    try:
        # На Windows значок окна читает System.Drawing.Icon — ему нужен настоящий .ico (с PNG он падал с ArgumentException
        # и убивал процесс окна: из-за этого лаунчер всегда открывался в Edge). Значок без PNG-кадров — для старого .NET.
        icon = resource_path("assets/logo_win.ico" if os.name == "nt" else "assets/logo.png")
        webview.start(debug="--debug" in sys.argv, http_server=True, icon=str(icon) if icon.exists() else None)
    finally:
        if faulthandler:
            faulthandler.cancel_dump_traceback_later()
    trace("окно pywebview закрыто")


def screen_work_area() -> tuple[int, int, int, int]:
    """Рабочая область экрана без панели задач: (x, y, ширина, высота)."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:  # noqa: BLE001
        return 0, 0, 1920, 1040


def window_geometry(saved=None) -> tuple[int, int, int, int]:
    """Удобное окно по центру экрана: (x, y, ширина, высота)."""
    ax, ay, aw, ah = screen_work_area()
    w, h = saved or (1280, 800)
    w = max(min(int(w), int(aw * 0.85)), min(1100, aw))   # не во весь экран и не узкая полоса
    h = max(min(int(h), int(ah * 0.85)), min(700, ah))
    return ax + (aw - w) // 2, ay + (ah - h) // 2, w, h


def browser_window_args(url: str, profile: Path) -> list[str]:
    """Аргументы Edge/Chrome для окна-приложения по центру экрана.
    Браузер запоминает прошлое положение окна — стираем его, чтобы окно всегда было по центру."""
    import json
    prefs = profile / "Default" / "Preferences"
    try:
        data = json.loads(prefs.read_text(encoding="utf-8"))
        browser = data.get("browser", {})
        if browser.pop("app_window_placement", None) is not None or browser.pop("window_placement", None) is not None:
            prefs.write_text(json.dumps(data), encoding="utf-8")
    except Exception:  # noqa: BLE001 — первого запуска профиля ещё нет
        pass
    x, y, w, h = window_geometry()
    return [f"--app={url}", f"--user-data-dir={profile}", f"--window-size={w},{h}", f"--window-position={x},{y}"]


def find_app_browser() -> str | None:
    """Edge есть в любой Windows 10/11; Chrome — запасной вариант."""
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium")


def kill_old_copies() -> None:
    """
    Закрывает зависшие копии лаунчера из ДРУГИХ папок (старые версии, распакованные архивы).
    Без этого новая версия могла подключиться к старой копии и показывать старый код.
    Игру (java) это не трогает.
    """
    if os.name != "nt":
        return
    me = str(ROOT).lower()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' OR Name='python.exe' OR Name='NeonLauncher.exe' OR Name='VaultMC.exe'\" | "
        "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.CommandLine }"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=15, creationflags=0x08000000).stdout
    except Exception:  # noqa: BLE001
        return
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        low = cmd.lower()
        if not pid.strip().isdigit() or int(pid) == os.getpid():
            continue
        is_launcher = ("main.py" in low and ("neonlauncher" in low or "vaultmc" in low)) or \
            low.strip('" ').endswith(("neonlauncher.exe", "vaultmc.exe"))
        if is_launcher and me not in low:
            subprocess.run(["taskkill", "/PID", pid.strip(), "/F", "/T"], capture_output=True, creationflags=0x08000000)
            trace(f"закрыта старая копия лаунчера: PID {pid.strip()} {cmd.strip()[:150]}")


def probe_port(port: int):
    """None — порт свободен; 'old' — занят чем-то без /version; иначе — версия запущенной копии."""
    import json
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=1.5) as r:
            return json.loads(r.read().decode()).get("version", "old")
    except urllib.error.HTTPError:
        return "old"
    except Exception:  # noqa: BLE001 — соединение отклонено: порт свободен
        import socket
        with socket.socket() as sock:
            return None if sock.connect_ex(("127.0.0.1", port)) != 0 else "old"


def run_in_browser() -> None:
    """Запасной режим: интерфейс в окне браузера без вкладок и адресной строки (--app)."""
    import threading
    import webbrowser

    from backend.devserver import serve

    from core.utils import APP_BUILD, APP_VERSION

    port = 8765
    for candidate in range(8765, 8800):
        running = probe_port(candidate)
        if running is None:
            port = candidate
            break
        if running == f"{APP_VERSION}+{APP_BUILD}":  # эта же версия уже запущена — просто показываем окно
            url = f"http://127.0.0.1:{candidate}/"
            browser = find_app_browser()
            profile = Path.home() / ".vaultmc" / "browser-profile"
            subprocess.Popen([browser, *browser_window_args(url, profile)]) if browser else webbrowser.open(url)
            trace(f"уже запущен на порту {candidate} — показываю окно")
            return
        trace(f"порт {candidate} занят другой версией ({running}) — беру следующий")
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=serve, kwargs={"port": port, "open_browser": False, "auto_exit": True},
                     daemon=True).start()
    time.sleep(0.8)
    trace(f"сервер интерфейса на порту {port}")
    browser = find_app_browser()
    profile = Path.home() / ".vaultmc" / "browser-profile"
    trace(f"запасной режим: браузер {browser or 'по умолчанию'}")
    if browser:
        subprocess.Popen([browser, *browser_window_args(url, profile)])
    else:
        webbrowser.open(url)
    while True:            # сервер сам завершит процесс, когда окно закроют
        time.sleep(3600)


def supervise_webview() -> bool:
    """
    Окно pywebview открываем в отдельном процессе и следим за ним отсюда.
    Если .NET/WebView2 зависнет намертво (вместе со всеми потоками Python), этот процесс
    не зависнет: через 30 с он закроет зависшую копию и откроет запасное окно.
    True — окно работало и уже закрыто пользователем; False — нужен запасной режим.
    """
    args = [sys.executable, "--webview-child"] if getattr(sys, "frozen", False) \
        else [sys.executable, str(ROOT / "main.py"), "--webview-child"]
    args += [a for a in sys.argv[1:] if a == "--debug"]
    set_webview_state("pending")       # «ok» от прошлого запуска не должен сойти за показанное окно
    try:
        child = subprocess.Popen(args, cwd=str(ROOT))
    except OSError as exc:
        trace(f"не удалось запустить процесс окна: {exc}")
        return False
    deadline = time.time() + 30
    while time.time() < deadline:
        code = child.poll()
        status = webview_state().get("status")
        if status == "ok":
            trace(f"окно pywebview работает (процесс {child.pid})")
            child.wait()
            return True
        if code is not None:
            hint = " (ошибка .NET/WebView2 — подробности строкой выше, если успели записаться)" \
                if code in (0xE0434352, -532462766) else ""
            trace(f"процесс окна завершился с кодом {code} до показа окна{hint}")
            if code != 0:
                set_webview_state("failed")
            return code == 0
        time.sleep(0.25)
    trace("окно pywebview не открылось за 30 с — закрываю зависший процесс, открываю запасное окно")
    set_webview_state("failed")
    try:
        child.kill()
    except OSError:
        pass
    return False


def restart_branded() -> bool:
    """Старый ярлык (или закреплённый на панели задач значок) запускает лаунчер через pythonw.exe — тогда Windows
    везде называет его «Python». Рядом лежит VaultMC.exe — тот же Python с названием и значком лаунчера:
    перезапускаемся через него. True — перезапустились, этот процесс можно закрывать."""
    exe = Path(sys.executable)
    branded = exe.with_name("VaultMC.exe")
    if os.name != "nt" or exe.name.lower() != "pythonw.exe" or not branded.is_file():
        return False
    try:
        subprocess.Popen([str(branded), str(ROOT / "main.py"), *sys.argv[1:]], cwd=str(ROOT))
        return True
    except OSError as exc:
        trace(f"VaultMC.exe не запустился ({exc}) — работаю через pythonw.exe")
        return False


def main() -> None:
    from core.utils import APP_VERSION
    trace(f"старт v{APP_VERSION}: python {sys.version.split()[0]} | {sys.executable} | {' '.join(sys.argv)}")
    if restart_branded():
        return
    # двойной щелчок по .mrpack: запоминаем путь — запущенный лаунчер сам заберёт его (см. Api._open_pack_loop)
    pack_arg = next((a for a in sys.argv[1:] if a.lower().endswith(".mrpack") and os.path.isfile(a)), "")
    # ссылка на сборку с сайта (pack.html → «Открыть в VaultMC»): vaultmc://pack/K7XQ2M
    import re
    link = next((m for m in (re.match(r"(?i)^vaultmc://pack/([a-z0-9]{6})/?$", a) for a in sys.argv[1:]) if m), None)
    open_value = os.path.abspath(pack_arg) if pack_arg else f"code:{link.group(1).upper()}" if link else ""
    if open_value:
        from core.utils import LAUNCHER_HOME
        try:
            LAUNCHER_HOME.mkdir(parents=True, exist_ok=True)
            (LAUNCHER_HOME / "open_pack.txt").write_text(open_value, encoding="utf-8")
        except OSError as exc:
            trace(f"не записал сборку для открытия: {exc}")
    if "--dev" not in sys.argv and "--browser" not in sys.argv and "--webview-child" not in sys.argv:
        if ping_running_window():                     # окно уже открыто — просто показываем его, вторую копию не запускаем
            trace("окно лаунчера уже открыто — поднимаю его, вторую копию не запускаю")
            return
        import threading   # поиск старых копий — PowerShell на 1–3 с: делаем в фоне, окно открывается сразу
        threading.Thread(target=kill_old_copies, daemon=True).start()
    if "--dev" in sys.argv:
        from backend.devserver import serve
        return serve()
    if "--webview-child" in sys.argv:
        try:
            run_app()
            os._exit(0)
        except Exception:  # noqa: BLE001
            set_webview_state("failed")
            write_crash("Окно pywebview не запустилось")
            os._exit(3)
    state = webview_state()
    try:
        fresh = time.time() - STATE.stat().st_mtime < 35
    except OSError:
        fresh = False
    if state.get("status") == "pending" and fresh and "--browser" not in sys.argv and "--webview" not in sys.argv:
        trace("окно лаунчера уже открывается (двойной клик) — эта копия не нужна")
        return
    # после обновления лаунчера пробуем обычное окно заново
    skip = (state.get("status") in ("failed", "pending") and state.get("python") == sys.version.split()[0]
            and state.get("app") == APP_VERSION)
    if "--webview" in sys.argv:          # принудительно попробовать окно pywebview снова
        skip = False
    if skip and "--browser" not in sys.argv:
        trace("pywebview пропущен: в прошлый раз окно не открылось на этой версии Python")
    if "--browser" not in sys.argv and not skip and not preflight_ok():
        skip = True
    if "--browser" not in sys.argv and not skip:
        if os.name == "nt" and supervise_webview():
            return
        if os.name != "nt":
            try:
                return run_app()
            except Exception:  # noqa: BLE001
                set_webview_state("failed")
                write_crash("Окно pywebview не запустилось, открываю в браузере")
    try:
        run_in_browser()
    except Exception:  # noqa: BLE001
        write_crash("Лаунчер не запустился")
        message_box(f"Лаунчер не запустился.\n\nПодробности в файле:\n{LOG}\n\nПришлите его содержимое разработчику.")


if __name__ == "__main__":
    main()
