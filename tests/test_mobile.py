import json
from pathlib import Path

from cryptography import x509

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
    assert r.status_code == 200 and "Mobile app setup" in r.text
    assert "start-mobile.bat" in r.text  # plain http on the PC -> explain how to enable phones
    assert client.get("/mobile/qr.svg").content.lstrip().startswith(b"<?xml")
    assert client.get("/offline").status_code == 200


def test_mobile_page_shows_public_url_and_certificate(client, monkeypatch, tmp_path):
    from app.routers import mobile
    import run

    run.ensure_certificate(tmp_path, "192.168.1.50")
    monkeypatch.setattr(mobile, "CA_CERT", tmp_path / "ca.pem")
    monkeypatch.setenv("PUBLIC_URL", "https://192.168.1.50:8443")
    login(client)
    r = client.get("/mobile")
    assert "https://192.168.1.50:8443" in r.text and "attendance-ca.crt" in r.text
    der = client.get("/mobile/attendance-ca.crt")
    ca = x509.load_der_x509_certificate(der.content)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert b"PRIVATE" not in der.content


def test_certificate_authority_and_ip_change(tmp_path: Path):
    import run

    cert_p, key_p = run.ensure_certificate(tmp_path, "192.168.1.50")
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
    chain = x509.load_pem_x509_certificates(cert_p.read_bytes())
    leaf = chain[0]
    assert leaf.issuer == ca.subject and chain[1] == ca
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes,
                           __import__("cryptography.hazmat.primitives.asymmetric.padding", fromlist=["PKCS1v15"]).PKCS1v15(),
                           leaf.signature_hash_algorithm)
    ips = [str(i) for i in leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)]
    assert "192.168.1.50" in ips and (leaf.not_valid_after_utc - leaf.not_valid_before_utc).days <= 825

    same = cert_p.read_bytes()
    run.ensure_certificate(tmp_path, "192.168.1.50")
    assert cert_p.read_bytes() == same  # reused while the IP is unchanged

    run.ensure_certificate(tmp_path, "10.0.0.7")  # PC got a new IP address
    new_leaf = x509.load_pem_x509_certificates(cert_p.read_bytes())[0]
    new_ca = x509.load_pem_x509_certificate((tmp_path / "ca.pem").read_bytes())
    assert new_ca == ca  # phones keep trusting the server
    assert "10.0.0.7" in [str(i) for i in new_leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)]


def test_login_page_offers_open_in_app_link(client):
    page = client.get("/login").text
    assert 'id="openApp"' in page and "faceattend://connect?url=" in page and "FaceAttendAndroid" in page
