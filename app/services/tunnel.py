"""Share online: a public https:// address for this server, via a Cloudflare Tunnel.

Phones anywhere can then open the app (camera and "install app" work because the address is real
HTTPS), without being on the school Wi-Fi and without installing certificates. Uses Cloudflare's
free "quick tunnel" (cloudflared, Apache-2.0, downloaded once on first use): no account needed,
but the address changes every time sharing is started. For a permanent address use a named tunnel
with your own domain or the hosted edition (docs/SAAS_DEPLOYMENT.md).
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from app.config import DATA_DIR

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
BIN_DIR = DATA_DIR / "bin"
REMEMBER = DATA_DIR / "share-online.on"   # sharing was on: start it again after a restart
RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"


class TunnelError(RuntimeError):
    pass


def _asset() -> tuple[str, str]:
    arch = platform.machine().lower()
    arm = arch in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "cloudflared-windows-amd64.exe", "cloudflared.exe"
    if sys.platform == "darwin":
        return ("cloudflared-darwin-arm64.tgz" if arm else "cloudflared-darwin-amd64.tgz"), "cloudflared"
    return ("cloudflared-linux-arm64" if arm else "cloudflared-linux-amd64"), "cloudflared"


def binary() -> Path:
    """cloudflared: CLOUDFLARED env, on PATH, or downloaded once into the data folder."""
    if os.environ.get("CLOUDFLARED"):
        return Path(os.environ["CLOUDFLARED"])
    found = shutil.which("cloudflared")
    if found:
        return Path(found)
    asset, name = _asset()
    target = BIN_DIR / name
    if target.exists():
        return target
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".download")
    log.info("downloading %s", asset)
    try:
        with urllib.request.urlopen(RELEASES + asset, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        if asset.endswith(".tgz"):
            import tarfile
            with tarfile.open(tmp) as tf:
                member = next(m for m in tf.getmembers() if m.name.endswith("cloudflared"))
                with tf.extractfile(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            tmp.unlink()
        else:
            tmp.replace(target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise TunnelError(f"Could not download the tunnel program (internet connection?): {exc}") from exc
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


class Tunnel:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.url: str | None = None
        self.error: str | None = None
        self.starting = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict:
        return {"running": self.running, "starting": self.starting and not self.url, "url": self.url if self.running else None,
                "error": self.error}

    def start(self, local_url: str, remember: bool = True, insecure_tls: bool = False) -> None:
        """Start in the background; the address appears in status() after a few seconds."""
        with self._lock:
            if self.running or self.starting:
                return
            self.starting, self.error, self.url = True, None, None
        if remember:
            REMEMBER.write_text(local_url)
        threading.Thread(target=self._run, args=(local_url, insecure_tls), daemon=True).start()

    def _run(self, local_url: str, insecure_tls: bool = False) -> None:
        try:
            exe = binary()
            kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window
            cmd = [str(exe), "tunnel", "--no-autoupdate", "--url", local_url]
            if insecure_tls:  # our own local certificate on 127.0.0.1
                cmd.append("--no-tls-verify")
            self.proc = subprocess.Popen(cmd,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, **kwargs)
            last = ""
            for line in self.proc.stdout:  # cloudflared logs the address once the tunnel is up
                last = line.strip() or last
                m = URL_RE.search(line)
                if m and not self.url:
                    self.url = m.group(0)
                    self.starting = False
                    log.info("sharing online at %s", self.url)
            if not self.url:
                self.error = "The tunnel could not start: " + (last[-200:] or "no output")
        except (OSError, TunnelError) as exc:
            self.error = str(exc)
        finally:
            self.starting = False

    def stop(self) -> None:
        """Switched off by the administrator: also forget it for the next start."""
        REMEMBER.unlink(missing_ok=True)
        self.stop_process()

    def stop_process(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.url, self.starting = None, None, False


tunnel = Tunnel()


def wait_for_url(timeout: float = 60) -> str | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if tunnel.url or tunnel.error:
            return tunnel.url
        time.sleep(0.25)
    return tunnel.url


def resume_if_remembered() -> None:
    if REMEMBER.exists():
        target = REMEMBER.read_text().strip() or "http://127.0.0.1:8000"
        tunnel.start(target, remember=False, insecure_tls=target.startswith("https://"))
