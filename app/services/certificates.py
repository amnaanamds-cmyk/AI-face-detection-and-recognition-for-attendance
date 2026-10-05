"""Verifiable attendance certificates.

A certificate (PDF with a QR code) states a person's attendance per group for a period. Its content
is signed with this installation's Ed25519 key, so anyone - a scholarship board, an employer, a
visa office - can scan the QR code and see on /verify whether it is genuine and unaltered, without
logging in. The certificate also records the attendance-ledger fingerprint at the time of issue.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.models import Attendance, Course, Organization, Student
from app.services import ledger
from app.services.analytics import ATTENDED, NEUTRAL

KEY_FILE = DATA_DIR / "certificate-signing-key.pem"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def signing_key() -> Ed25519PrivateKey:
    import os

    seed = os.environ.get("CERTIFICATE_SIGNING_SEED", "")
    if seed:   # hosts without a persistent disk (Render, Heroku): the key comes from the environment
        return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed.encode()).digest())
    if KEY_FILE.exists():
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    try:
        KEY_FILE.chmod(0o600)
    except OSError:
        pass
    return key


def public_key() -> Ed25519PublicKey:
    return signing_key().public_key()


def key_id() -> str:
    raw = public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:12]


def build(db: Session, student: Student, start: date, end: date) -> dict:
    org = db.get(Organization, student.org_id)
    recs = db.scalars(select(Attendance).where(Attendance.student_id == student.id, Attendance.date >= start,
                                               Attendance.date <= end)).all()
    per = {}
    for r in recs:
        if r.status in NEUTRAL:
            continue
        att, cnt = per.get(r.course_id, (0, 0))
        per[r.course_id] = (att + (r.status in ATTENDED), cnt + 1)
    courses = {c.id: c for c in db.scalars(select(Course).where(Course.id.in_(list(per)))).all()}
    rows = [{"code": courses[cid].code, "name": courses[cid].name, "attended": a, "classes": n,
             "pct": round(100 * a / n, 1)} for cid, (a, n) in sorted(per.items(), key=lambda kv: courses[kv[0]].code)]
    tot_a, tot_n = sum(r["attended"] for r in rows), sum(r["classes"] for r in rows)
    return {"v": 1, "org": org.name if org else "", "name": student.name, "id": student.student_code,
            "from": start.isoformat(), "to": end.isoformat(), "groups": rows,
            "overall": round(100 * tot_a / tot_n, 1) if tot_n else None,
            "issued": datetime.now().replace(microsecond=0).isoformat(),
            "ledger": ledger.fingerprint(db, student.org_id), "kid": key_id()}


def sign(data: dict) -> str:
    body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return _b64e(body) + "." + _b64e(signing_key().sign(body))


@dataclass
class Verification:
    valid: bool
    data: dict | None
    reason: str = ""


def verify(token: str) -> Verification:
    try:
        body_b64, sig_b64 = token.strip().split(".")
        body = _b64d(body_b64)
        data = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return Verification(False, None, "This is not a FaceAttend certificate code.")
    try:
        public_key().verify(_b64d(sig_b64), body)
    except (InvalidSignature, ValueError):
        if data.get("kid") and data.get("kid") != key_id():
            return Verification(False, data, "Signed by a different FaceAttend installation - verify it on the issuer's server.")
        return Verification(False, data, "The certificate was altered: its content does not match the signature.")
    return Verification(True, data)


def pdf(data: dict, token: str, verify_url: str) -> bytes:
    import segno
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    qr_png = io.BytesIO()
    segno.make(f"{verify_url}?c={token}", error="l").save(qr_png, kind="png", scale=4, border=2)
    qr_png.seek(0)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title="Attendance certificate", leftMargin=20 * mm, rightMargin=20 * mm)
    from html import escape as e
    from reportlab.lib.styles import ParagraphStyle

    st = getSampleStyleSheet()
    code_style = ParagraphStyle("code", parent=st["Normal"], fontName="Courier", fontSize=6, leading=7, wordWrap="CJK")
    story = [Paragraph(e(data["org"]), st["Heading2"]), Paragraph("Attendance Certificate", st["Title"]),
             Paragraph(f"This certifies the recorded attendance of <b>{e(data['name'])}</b> ({e(data['id'])}) "
                       f"from {data['from']} to {data['to']}.", st["Normal"]), Spacer(1, 10)]
    rows = [["Group", "Attended", "Classes", "Attendance"]] + [
        [f"{g['code']} {g['name']}", g["attended"], g["classes"], f"{g['pct']}%"] for g in data["groups"]]
    rows.append(["Overall", "", "", f"{data['overall']}%" if data["overall"] is not None else "--"])
    t = Table(rows, colWidths=[90 * mm, 25 * mm, 25 * mm, 30 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [t, Spacer(1, 14), Image(qr_png, width=45 * mm, height=45 * mm),
              Paragraph(f"Scan to verify, or open {verify_url} and paste the code below. The certificate is "
                        f"digitally signed (Ed25519, key {data['kid']}); any change makes it invalid.", st["Normal"]),
              Spacer(1, 6), Paragraph(f"Issued {data['issued'].replace('T', ' ')} &middot; attendance ledger {data['ledger']}",
                                      st["Italic"]),
              Spacer(1, 6), Paragraph(token, code_style)]
    doc.build(story)
    return buf.getvalue()
