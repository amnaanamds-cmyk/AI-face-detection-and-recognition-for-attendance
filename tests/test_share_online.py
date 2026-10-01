"""Share online: public link through a (stand-in) cloudflared tunnel."""
import sys
import textwrap
import time

import pytest

from app.services import tunnel as tunnel_mod
from tests.conftest import login


@pytest.fixture()
def fake_cloudflared(tmp_path, monkeypatch):
    script = tmp_path / "cloudflared"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys, time
        print("INF Requesting new quick Tunnel on trycloudflare.com...", flush=True)
        print("INF |  https://quiet-river-1234.trycloudflare.com  |", flush=True)
        open(r"{tmp_path / 'args.txt'}", "w").write(" ".join(sys.argv[1:]))
        time.sleep(60)
    """))
    script.chmod(0o755)
    monkeypatch.setenv("CLOUDFLARED", str(script))
    monkeypatch.setattr(tunnel_mod, "REMEMBER", tmp_path / "share-online.on")
    yield tmp_path
    tunnel_mod.tunnel.stop()


def test_share_online_link(client, fake_cloudflared):
    assert client.post("/share/start").status_code == 403  # not logged in
    login(client)
    r = client.post("/share/start")
    assert r.status_code == 400 and "admin123" in r.json()["error"]  # default password must be changed first
    client.post("/account/password", data={"current": "admin123", "new": "a-much-better-pw", "confirm": "a-much-better-pw"})
    assert client.post("/share/start").status_code == 200
    for _ in range(100):
        if client.get("/share/status").json()["url"]:
            break
        time.sleep(0.1)
    st = client.get("/share/status").json()
    assert st["running"] and st["url"] == "https://quiet-river-1234.trycloudflare.com"
    assert "tunnel --no-autoupdate --url http://127.0.0.1:" in (fake_cloudflared / "args.txt").read_text()
    assert tunnel_mod.REMEMBER.exists()  # restarts with the server
    page = client.get("/mobile").text
    assert "https://quiet-river-1234.trycloudflare.com" in page and "Stop sharing" in page
    assert client.get("/mobile/qr.svg?u=online").status_code == 200
    client.post("/share/stop")
    assert not client.get("/share/status").json()["running"] and not tunnel_mod.REMEMBER.exists()


def test_teacher_cannot_share(client, fake_cloudflared):
    from app import database
    from app.models import Role, User
    from app.security import hash_password
    with database.SessionLocal() as db:
        db.add(User(username="t1", password_hash=hash_password("teacherpass1"), full_name="T", role=Role.teacher, org_id=1))
        db.commit()
    login(client, "t1", "teacherpass1")
    assert client.post("/share/start").status_code == 403
    assert "Share online" not in client.get("/mobile").text
