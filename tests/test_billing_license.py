"""SaaS subscriptions (Stripe) and self-hosted license keys."""
import hashlib
import hmac
import json
import time
from datetime import date, timedelta

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import database
from app.models import Organization, now
from app.services import license as lic
from app.services import stripe_billing
from tests.conftest import data_url, face_image, login, noisy


@pytest.fixture()
def keypair(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = tmp_path / "pub.pem"
    pub.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setattr(lic, "PUBLIC_KEY_FILE", pub)
    from app.config import settings
    monkeypatch.setattr(settings, "license_file", tmp_path / "license.key")
    lic._cache.clear()
    return key


def test_license_sign_verify_and_tamper(keypair):
    text = lic.sign_license(keypair, "City College", 500, date.today() + timedelta(days=30))
    l = lic.verify_license(text)
    assert l.licensee == "City College" and l.max_people == 500
    body, sig = text.split(".")
    forged = lic._b64e(json.dumps({"licensee": "City College", "max_people": None, "expires": None,
                                   "issued": date.today().isoformat()}).encode()) + "." + sig
    with pytest.raises(lic.LicenseError):
        lic.verify_license(forged)
    with pytest.raises(lic.LicenseError, match="expired"):
        lic.verify_license(lic.sign_license(keypair, "Old", 5, date.today() - timedelta(days=1)))
    other = Ed25519PrivateKey.generate()
    with pytest.raises(lic.LicenseError):
        lic.verify_license(lic.sign_license(other, "Pirate", None))


def _add(client, code):
    return client.post("/students/new", data={"student_code": code, "name": code, "consent": "on"}, follow_redirects=True)


def test_self_hosted_limit_and_license_install(client, keypair, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "unlicensed_max_people", 2)
    login(client)
    _add(client, "P1"); _add(client, "P2")
    r = _add(client, "P3")
    assert "allows 2 people" in r.text
    r = client.post("/billing/license", data={"key": "garbage"}, follow_redirects=True)
    assert "not accepted" in r.text
    key = lic.sign_license(keypair, "My School", 10)
    r = client.post("/billing/license", data={"key": key}, follow_redirects=True)
    assert "License installed for My School" in r.text
    assert "allows" not in _add(client, "P3").text
    assert "Licensed to" in client.get("/billing").text


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


def test_webhook_signature():
    body = b'{"type":"x"}'
    assert stripe_billing.verify_webhook(body, sign(body, "whsec"), "whsec")["type"] == "x"
    for header in [sign(body, "wrong"), sign(body, "whsec", int(time.time()) - 3600), "garbage"]:
        with pytest.raises(stripe_billing.StripeError):
            stripe_billing.verify_webhook(body, header, "whsec")


def test_saas_subscription_lifecycle(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "edition", "saas")
    monkeypatch.setattr(settings, "public_signup", True)
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    client.post("/signup", data={"org_name": "Paying School", "kind": "school", "full_name": "A", "email": "a@pay.edu",
                                 "password": "password123", "terms": "on"})
    with database.SessionLocal() as db:
        org_id = db.query(Organization).filter_by(name="Paying School").one().id

    # checkout: the right request goes to Stripe
    sent = {}
    monkeypatch.setattr(stripe_billing, "stripe_post", lambda path, data: sent.update(path=path, data=data) or {"url": "https://checkout.stripe.test/x"})
    r = client.post("/billing/checkout", data={"plan": "pro"}, follow_redirects=False)
    assert r.headers["location"] == "https://checkout.stripe.test/x"
    assert sent["path"] == "checkout/sessions" and sent["data"]["line_items"][0]["price"] == "price_pro"
    assert sent["data"]["metadata"]["org_id"] == org_id

    def post_event(event):
        body = json.dumps(event).encode()
        return client.post("/billing/webhook", content=body, headers={"stripe-signature": sign(body, "whsec_test")})

    assert client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=bad"}).status_code == 400
    post_event({"type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(org_id), "customer": "cus_1", "subscription": "sub_1", "metadata": {"org_id": str(org_id), "plan": "pro"}}}})
    with database.SessionLocal() as db:
        org = db.get(Organization, org_id)
        assert (org.plan, org.plan_status, org.stripe_customer_id) == ("pro", "active", "cus_1")

    # payment fails -> recognition blocked (402), data still viewable
    post_event({"type": "invoice.payment_failed", "data": {"object": {"customer": "cus_1"}}})
    client.post("/courses/new", data={"code": "C1", "name": "C"})
    r = client.post("/students/new", data={"student_code": "S1", "name": "S", "consent": "on", "auto_enroll": "on"}, follow_redirects=False)
    sid = int(r.headers["location"].split("/")[2])
    assert client.post(f"/api/students/{sid}/faces", json={"images": [data_url(face_image(1))]}).status_code == 402
    assert "payment failed" in client.get("/").text
    assert client.get("/reports/monthly?fmt=csv").status_code == 200

    post_event({"type": "invoice.paid", "data": {"object": {"customer": "cus_1"}}})
    assert client.post(f"/api/students/{sid}/faces", json={"images": [data_url(noisy(face_image(1), k)) for k in range(3)]}).status_code == 200
    post_event({"type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_1", "id": "sub_1"}}})
    with database.SessionLocal() as db:
        assert db.get(Organization, org_id).plan_status == "canceled"


def test_trial_expiry_blocks_recognition(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "edition", "saas")
    monkeypatch.setattr(settings, "public_signup", True)
    client.post("/signup", data={"org_name": "Trial Gym", "kind": "event", "full_name": "A", "email": "a@gym.io",
                                 "password": "password123", "terms": "on"})
    with database.SessionLocal() as db:
        org = db.query(Organization).filter_by(name="Trial Gym").one()
        assert org.trial_ends_at > now() + timedelta(days=13)
        org.trial_ends_at = now() - timedelta(days=1)
        db.commit()
    assert client.post("/api/kiosk/frame", json={"image": data_url(face_image(1))}).status_code == 402
    assert "trial has ended" in client.get("/billing").text
