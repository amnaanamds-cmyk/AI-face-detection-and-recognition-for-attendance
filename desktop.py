"""FaceAttend desktop app: one double-click, no console window.

Starts the attendance server invisibly inside this process, opens the app in its own window
(Microsoft Edge or Chrome in "app" mode: no tabs or address bar), and stops everything when
that window is closed. Phones on the same Wi-Fi can still connect over HTTPS (port 8443).

    python desktop.py            # from source
    FaceAttend.exe               # built with packaging/build_exe.py

Data (database, keys, licence, certificates, logs) lives in %LOCALAPPDATA%\\FaceAttend for the
.exe, so the program folder can be read-only (Program Files) and updates keep the data.
"""
from __future__ import annotations

import os
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
HTTP_PORT, HTTPS_PORT = 8000, 8443
FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


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


def prepare_models() -> None:
    """Models ship inside the .exe; copy them to the data folder once, so a custom
    anti-spoofing model (antispoof.onnx + .json) can simply be dropped in there."""
    models = Path(os.environ.get("MODELS_DIR", DATA / "models" if FROZEN else BUNDLE / "models"))
    models.mkdir(parents=True, exist_ok=True)
    for f in (BUNDLE / "models").glob("*.onnx") if (BUNDLE / "models").exists() else []:
        if not (models / f.name).exists() and f.parent != models:
            shutil.copy2(f, models / f.name)
    os.environ["MODELS_DIR"] = str(models)


def setup_logging() -> None:
    import logging

    logs = DATA / "logs"
    logs.mkdir(exist_ok=True)
    handler = logging.FileHandler(logs / "app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    if sys.stdout is None or sys.stderr is None:  # windowed .exe: no console to print to
        sys.stdout = sys.stderr = open(logs / "console.log", "a", encoding="utf-8", buffering=1)


def already_running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/login", timeout=2) as r:
            return r.status == 200
    except OSError:
        return False


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


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


def open_window(url: str) -> subprocess.Popen | None:
    """App-mode window with its own profile (so closing it really ends the process we wait for)."""
    browser = find_browser()
    if not browser:
        webbrowser.open(url)
        return None
    args = [browser, f"--app={url}", f"--user-data-dir={DATA / 'window'}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1366,860", "--disable-features=Translate"]
    return subprocess.Popen(args)


def control_window(url: str, stop) -> None:
    """Fallback when no Edge/Chrome is installed: a small window to reopen or quit."""
    import tkinter as tk

    root = tk.Tk()
    root.title(APP_NAME)
    root.resizable(False, False)
    tk.Label(root, text=f"{APP_NAME} is running.\nClose this window to quit.", padx=24, pady=12).pack()
    tk.Button(root, text="Open app", width=20, command=lambda: webbrowser.open(url)).pack(pady=4)
    tk.Button(root, text="Quit", width=20, command=root.destroy).pack(pady=(0, 12))
    root.mainloop()
    stop()


def https_files():
    """Certificate for phones on the Wi-Fi (same local CA as run.py --lan)."""
    try:
        from run import ensure_certificate, lan_ip

        ip = lan_ip()
        cert, key = ensure_certificate(DATA / "tls", ip)
        os.environ.setdefault("PUBLIC_URL", f"https://{ip}:{HTTPS_PORT}")
        return str(cert), str(key)
    except Exception:  # noqa: BLE001 - phones are optional, the desktop window still works
        import logging
        logging.getLogger(APP_NAME).exception("HTTPS for phones disabled")
        return None


def main() -> int:
    setup_logging()
    url = f"http://127.0.0.1:{HTTP_PORT}"
    if already_running(url):  # second double-click: just show the window again
        open_window(url)
        return 0
    if not port_free(HTTP_PORT):
        _message(f"Port {HTTP_PORT} is used by another program. Close it and start {APP_NAME} again.")
        return 1
    prepare_models()
    sys.path.insert(0, str(BUNDLE))

    import asyncio

    import uvicorn

    from app.main import app

    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=HTTP_PORT, log_config=None,
                                             workers=1, proxy_headers=True))]
    tls = https_files() if port_free(HTTPS_PORT, "0.0.0.0") else None
    if tls:
        servers.append(uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=HTTPS_PORT, log_config=None,
                                                     lifespan="off", ssl_certfile=tls[0], ssl_keyfile=tls[1])))

    def stop():
        for s in servers:
            s.should_exit = True

    def window_thread():
        for _ in range(600):  # wait until the server answers (first start creates the database)
            if already_running(url):
                break
            time.sleep(0.1)
        proc = open_window(url)
        started = time.monotonic()
        if proc is not None:
            proc.wait()  # returns when the user closes the app window
            if time.monotonic() - started > 5:
                stop()
                return
            # the browser handed the window to an already running instance: we cannot see
            # when it is closed, so offer an explicit Quit button instead
        control_window(url, stop)

    threading.Thread(target=window_thread, daemon=True).start()

    async def serve():
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(serve())
    return 0


def _message(text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, APP_NAME, 0x10)
    except Exception:  # noqa: BLE001 - not on Windows
        print(text, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
