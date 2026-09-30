"""Public product page (SaaS), branding and the operator's biometric purge."""
from fastapi.testclient import TestClient

from app import database
from app.models import FaceEmbedding, Organization
from tests.conftest import login
from tests.test_multitenancy import build_org, signup


def test_landing_page_only_when_signup_is_public(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", False)
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    monkeypatch.setattr(settings, "public_signup", True)
    r = client.get("/")
    assert r.status_code == 200 and "Start free trial" in r.text
    assert "$99" in r.text and "Up to 1,000 people" in r.text  # pricing comes from the plan table
    assert 'href="/signup?plan=pro"' in r.text


def test_manifest_uses_product_name(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "app_name", "RollCall Pro")
    assert client.get("/manifest.webmanifest").json()["name"] == "RollCall Pro"


def test_operator_purges_face_data_of_closed_account(client, monkeypatch, db_url):
    from app.config import settings
    monkeypatch.setattr(settings, "public_signup", True)
    signup(client, "Closing Co", "owner@closing.io")
    build_org(client, "CC", 7)
    with database.SessionLocal() as db:
        oid = db.query(Organization).filter_by(name="Closing Co").one().id
        assert db.query(FaceEmbedding).count() == 3
    op = TestClient(client.app)
    login(op)
    op.post(f"/platform/orgs/{oid}", data={"action": "purge"})
    with database.SessionLocal() as db:
        assert db.query(FaceEmbedding).count() == 3  # refused while the account is active
    op.post(f"/platform/orgs/{oid}", data={"action": "toggle"})
    op.post(f"/platform/orgs/{oid}", data={"action": "purge"})
    with database.SessionLocal() as db:
        assert db.query(FaceEmbedding).count() == 0


def test_saas_refuses_demo_defaults(monkeypatch):
    import pytest
    from app.config import settings
    from app.main import check_production_settings
    monkeypatch.setattr(settings, "edition", "saas")
    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8000")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD.*PUBLIC_BASE_URL"):
        check_production_settings()
    monkeypatch.setattr(settings, "admin_password", "a-long-random-password")
    monkeypatch.setattr(settings, "public_base_url", "https://attend.example.com")
    check_production_settings()
