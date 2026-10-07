"""FaceAttend desktop app.

The attendance server runs in the background (tray icon next to the clock) and the app opens in
its own window. Closing the window only closes the window: the server keeps running, so the app
can be reopened from any shortcut, the taskbar or a phone at any time. Quit from the tray icon.

    FaceAttend.exe                 open the app (starts the background server if needed)
    FaceAttend.exe --background    only the background server + tray icon (used at Windows start-up)
    FaceAttend.exe --quit          stop the background server
    faceattend://start             same as --background (link on the "server not reachable" page)
    python desktop.py [...]        the same from source

Data (database, keys, licence, certificates, logs) lives in %LOCALAPPDATA%\\FaceAttend for the
.exe, so the program folder can be read-only (Program Files) and updates keep the data.
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "FaceAttend"
HTTP_PORT = 8000
URL = f"http://127.0.0.1:{HTTP_PORT}"
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WINDOWS = sys.platform == "win32"


def data_dir() -> Path:
    if os.environ.get("DATA_DIR"):
        return Path(os.environ["DATA_DIR"])
    if not FROZEN:
        return BUNDLE / "data"  # running from source: same folder as start.bat uses
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


DATA = data_dir()
DATA.mkdir(parents=True, exist_ok=True)
os.environ["DATA_DIR"] = str(DATA)
TOKEN_FILE = DATA / ".desktop-token"


# ------------------------------------------------------------------ helpers
def setup_logging(name: str) -> None:
    import logging

    logs = DATA / "logs"
    logs.mkdir(exist_ok=True)
    handler = logging.FileHandler(logs / f"{name}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:  # windowed .exe: no console to print to
        sys.stdout = sys.stderr = open(logs / f"{name}-console.log", "a", encoding="utf-8", buffering=1)


def server_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/login", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


def self_command(*args: str) -> list[str]:
    """Command line that runs this program again (the .exe, or pythonw desktop.py from source)."""
    if FROZEN:
        return [sys.executable, *args]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return [str(pythonw if WINDOWS and pythonw.exists() else exe), str(Path(__file__).resolve()), *args]


def spawn_background() -> None:
    kwargs = {"creationflags": 0x00000008 | 0x08000000} if WINDOWS else {"start_new_session": True}  # detached, no window
    subprocess.Popen(self_command("--background"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **kwargs)


def find_browser() -> str | None:
    """Edge is on every Windows 10/11 PC; Chrome/Chromium elsewhere."""
    if os.environ.get("FACEATTEND_BROWSER"):
        return os.environ["FACEATTEND_BROWSER"]
    candidates = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(env)
        if root:
            candidates += [Path(root) / "Microsoft/Edge/Application/msedge.exe",
                           Path(root) / "Google/Chrome/Application/chrome.exe"]
    candidates += [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                   Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def open_window(path: str = "/") -> None:
    """The app in its own window (browser 'app mode': no tabs or address bar, own profile)."""
    browser = find_browser()
    if not browser:
        webbrowser.open(URL + path)
        return
    subprocess.Popen([browser, f"--app={URL}{path}", f"--user-data-dir={DATA / 'window'}", "--no-first-run",
                      "--no-default-browser-check", "--window-size=1366,860", "--disable-features=Translate"])


def message(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, 0x10)
    except Exception:  # noqa: BLE001 - not on Windows
        print(text, file=sys.stderr)


# ------------------------------------------------------------------ Windows integration
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def register_url_protocol() -> None:
    """faceattend://start launches the background server (button on the offline page)."""
    if not WINDOWS:
        return
    import winreg

    cmd = subprocess.list2cmdline(self_command("--background")) + ' "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\faceattend") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:FaceAttend")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\faceattend\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, cmd)


def autostart_enabled() -> bool:
    if not WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    if not WINDOWS:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(self_command("--background")))
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


# ------------------------------------------------------------------ background server
def prepare_models() -> None:
    """Models ship inside the .exe; copy them to the data folder once, so a custom
    anti-spoofing model (antispoof.onnx + .json) can simply be dropped in there."""
    models = Path(os.environ.get("MODELS_DIR", DATA / "models" if FROZEN else BUNDLE / "models"))
    models.mkdir(parents=True, exist_ok=True)
    for f in (BUNDLE / "models").glob("*.onnx") if (BUNDLE / "models").exists() else []:
        if not (models / f.name).exists() and f.parent != models:
            shutil.copy2(f, models / f.name)
    os.environ["MODELS_DIR"] = str(models)


def start_tray(stop) -> None:
    """Tray icon with Open / Phones / Start with Windows / Quit. Optional: without a desktop
    session (servers, CI) the background server simply runs without it."""
    try:
        import pystray
        from PIL import Image
    except Exception:  # noqa: BLE001
        return
    icon_file = BUNDLE / "app" / "static" / "icons" / "icon-192.png"

    def toggle_autostart(_icon, _item):
        set_autostart(not autostart_enabled())

    def quit_app(icon, _item):
        icon.stop()
        stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open FaceAttend", lambda *_: open_window(), default=True),
        pystray.MenuItem("Connect phones / share online", lambda *_: open_window("/mobile")),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda _item: autostart_enabled(),
                         visible=WINDOWS),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit FaceAttend", quit_app),
    )
    try:
        icon = pystray.Icon(APP_NAME, Image.open(icon_file), f"{APP_NAME} - attendance server running", menu)
        threading.Thread(target=icon.run, daemon=True).start()
    except Exception:  # noqa: BLE001 - no desktop session
        import logging
        logging.getLogger(APP_NAME).warning("tray icon not available", exc_info=True)


def run_background() -> int:
    setup_logging("server")
    if not port_free(HTTP_PORT):
        return 0  # already running (or starting) - nothing to do
    prepare_models()
    sys.path.insert(0, str(BUNDLE))
    try:
        register_url_protocol()
    except OSError:
        pass

    import asyncio

    import uvicorn
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from app.main import app

    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token)
    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=HTTP_PORT, log_config=None,
                                             proxy_headers=True))]
    # phones connect through Share online (a secure https link), never directly over the Wi-Fi

    def stop():
        for s in servers:
            s.should_exit = True

    async def desktop_quit(request):
        """FaceAttend.exe --quit (and the uninstaller) stop the server through this local-only route."""
        if request.client is None or request.client.host != "127.0.0.1" or request.headers.get("x-desktop-token") != token:
            return PlainTextResponse("forbidden", status_code=403)
        stop()
        return PlainTextResponse("bye")

    app.router.routes.append(Route("/__desktop/quit", desktop_quit, methods=["POST"]))
    start_tray(stop)

    async def serve():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve())
    TOKEN_FILE.unlink(missing_ok=True)
    return 0


def quit_background() -> int:
    if not TOKEN_FILE.exists():
        return 0
    req = urllib.request.Request(URL + "/__desktop/quit", method="POST",
                                 headers={"X-Desktop-Token": TOKEN_FILE.read_text().strip()})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except OSError:
        pass
    for _ in range(100):
        if port_free(HTTP_PORT):
            return 0
        time.sleep(0.1)
    return 1


# ------------------------------------------------------------------ launcher
def launch() -> int:
    """Double-click: make sure the background server runs, then open the app window."""
    setup_logging("launcher")
    if not server_running():
        if port_free(HTTP_PORT):
            spawn_background()
        for _ in range(900):  # up to 90 s (the very first start creates the database)
            if server_running():
                break
            time.sleep(0.1)
        else:
            message(f"{APP_NAME} could not start. Another program may be using port {HTTP_PORT}.\n"
                    f"Details: {DATA / 'logs' / 'server.log'}")
            return 1
    open_window()
    return 0


def main(argv: list[str]) -> int:
    if "--quit" in argv:
        return quit_background()
    if "--background" in argv or any(a.startswith("faceattend:") for a in argv):
        return run_background()
    return launch()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
