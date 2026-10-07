import json


from tests.conftest import login


def test_pwa_files_are_served(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(m.content)
    assert manifest["display"] == "standalone" and manifest["start_url"].startswith("/")
    sizes = {i["sizes"] for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes and any(i["purpose"] == "maskable" for i in manifest["icons"])
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert sw.headers["service-worker-allowed"] == "/"
    # every file the service worker pre-caches must exist, or installation fails
    for path in [line.strip().strip("',") for line in sw.text.split("const SHELL = [")[1].split("];")[0].splitlines()]:
        if path:
            assert client.get(path).status_code == 200, path


def test_pages_link_manifest_and_mobile_setup(client):
    html = client.get("/login").text
    assert 'rel="manifest"' in html and "serviceWorker" in html and 'href="/mobile"' in html
    r = client.get("/mobile")  # public: phones open it before logging in
    assert r.status_code == 200 and "Connect phones" in r.text
    assert "Share online" in r.text and "192.168" not in r.text and "certificate" not in r.text  # no Wi-Fi option
    assert client.get("/mobile/qr.svg").status_code == 404           # no QR code before sharing online
    from types import SimpleNamespace

    from app.services.tunnel import tunnel
    tunnel.proc, tunnel.url = SimpleNamespace(poll=lambda: None), "https://demo-school.trycloudflare.com"   # sharing
    try:
        assert client.get("/mobile/qr.svg").content.lstrip().startswith(b"<?xml")
    finally:
        tunnel.proc, tunnel.url = None, None
    assert client.get("/offline").status_code == 200


def test_login_page_offers_open_in_app_link(client):
    page = client.get("/login").text
    assert 'id="openApp"' in page and "faceattend://connect?url=" in page and "FaceAttendAndroid" in page


def test_app_menu_follows_role(client):
    login(client)
    page = client.get("/").text
    assert "FaceAttendApp.setNav" in page and '"p": "/overview"' in page and '"/backup"' in page
    client.post("/users/new", data={"username": "t9", "full_name": "T", "password": "teacher123", "role": "teacher"})
    client.get("/logout")
    assert "setNav(JSON.stringify(null))" in client.get("/login").text
    login(client, "t9", "teacher123")
    page = client.get("/").text
    assert '"p": "/timetable"' in page and '"/overview"' not in page and '"/users"' not in page
