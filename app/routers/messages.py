"""Admin > Parent messages, plus the API used by the FaceAttend Android app to send SMS."""
from __future__ import annotations

import hmac
import io
import json
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import admin_only, can_manage_course, flash, render, staff
from app.models import ClassSession, InboxMessage, Organization, OrgSetting, OutboxMessage, User
from app.routers.mobile import public_url
from app.services import app_settings, messaging

router = APIRouter()
STATUSES = ("pending", "sending", "manual-pending", "sent", "manual", "failed")


def _msg(db: Session, user: User, mid: int) -> OutboxMessage:
    m = db.get(OutboxMessage, mid)
    if m is None or m.org_id != user.org_id:
        raise HTTPException(404, "Message not found")
    return m


@router.get("/messages")
def messages_page(request: Request, status: str = "", user: User = Depends(admin_only), db: Session = Depends(get_db)):
    q = select(OutboxMessage).where(OutboxMessage.org_id == user.org_id)
    if status in STATUSES:
        q = q.where(OutboxMessage.status == status)
    msgs = db.scalars(q.order_by(OutboxMessage.id.desc()).limit(300)).all()
    counts = dict(db.execute(select(OutboxMessage.status, func.count()).where(OutboxMessage.org_id == user.org_id)
                             .group_by(OutboxMessage.status)).all())
    values = app_settings.all_settings(db, user.org_id)
    inbox = db.scalars(select(InboxMessage).where(InboxMessage.org_id == user.org_id)
                       .order_by(InboxMessage.id.desc()).limit(50)).all()
    return render(request, "admin/messages.html", user, msgs=msgs, counts=counts, status=status, values=values, inbox=inbox,
                  defs=app_settings.DEFAULTS, choices=app_settings.CHOICES,
                  twilio_sms=messaging.twilio_configured("sms"), twilio_wa=messaging.twilio_configured("whatsapp"),
                  missing=messaging.students_without_contact(db, user.org_id), server_url=_server_url(request),
                  wa=messaging.whatsapp_link, sms=messaging.sms_link)


@router.post("/messages/settings")
async def save_message_settings(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    form = await request.form()
    try:
        for key in ("parent_alerts", "parent_channel", "parent_template", "country_code"):
            if key in form:
                app_settings.set_setting(db, user.org_id, key, str(form[key]).strip())
        app_settings.set_setting(db, user.org_id, "parent_email", form.get("parent_email") == "on")
        app_settings.set_setting(db, user.org_id, "parent_replies", form.get("parent_replies") == "on")
        flash(request, "Message settings saved")
    except ValueError as exc:
        flash(request, f"Invalid value: {exc}", "danger")
    return RedirectResponse("/messages", status_code=303)


@router.post("/messages/test")
def send_test(request: Request, to: str = Form(...), user: User = Depends(admin_only), db: Session = Depends(get_db)):
    channel = str(app_settings.get_setting(db, user.org_id, "parent_channel"))
    cc = str(app_settings.get_setting(db, user.org_id, "country_code"))
    if "@" in to:
        channel, recipient = "email", to.strip()
    else:
        recipient = messaging.normalize_phone(to, cc)
        if not recipient:
            flash(request, "That does not look like a phone number", "danger")
            return RedirectResponse("/messages", status_code=303)
    body = f"Test message from {user.org.name}: parent absence alerts are working."
    db.add(OutboxMessage(org_id=user.org_id, channel=channel, recipient=recipient, body=body,
                         status="manual-pending" if channel == "manual" else "pending"))
    db.commit()
    messaging.deliver_later(user.org_id)
    flash(request, f"Test message queued ({channel}). Refresh in a few seconds to see if it was sent.")
    return RedirectResponse("/messages", status_code=303)


@router.post("/messages/{mid}/retry")
def retry(mid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    m = _msg(db, user, mid)
    m.status, m.error = ("manual-pending" if m.channel == "manual" else "pending"), None
    db.commit()
    messaging.deliver_later(user.org_id)
    return RedirectResponse("/messages", status_code=303)


@router.post("/messages/{mid}/done")
def mark_done(mid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    """Sent by hand with the WhatsApp / SMS link."""
    m = _msg(db, user, mid)
    m.status, m.sent_at = "manual", messaging.now()
    db.commit()
    return {"ok": True}


@router.post("/sessions/{sid}/notify-parents")
def notify_parents(sid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    s = db.get(ClassSession, sid)
    if s is None or not can_manage_course(user, s.course):
        raise HTTPException(404, "Session not found")
    n = messaging.queue_for_session(db, s, force=True)
    if n:
        flash(request, f"{n} message(s) to parents queued - see Admin > Parent messages")
    else:
        flash(request, "No new messages: everyone was present, already notified, or has no guardian phone/e-mail", "warning")
    return RedirectResponse(f"/sessions/{sid}", status_code=303)


# ------------------------------------------------------------------ pairing the Android app
def _server_url(request: Request) -> str:
    return public_url(request) or str(request.base_url).rstrip("/")


@router.post("/messages/gateway-token")
def new_gateway_token(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    app_settings.set_setting(db, user.org_id, "gateway_token", f"{user.org_id}.{secrets.token_urlsafe(24)}")
    flash(request, "New pairing code created. Scan it with the FaceAttend Android app (old pairings stop working).")
    return RedirectResponse("/messages#gateway", status_code=303)


@router.get("/messages/pair.svg", include_in_schema=False)
def pairing_qr(request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    import segno

    token = str(app_settings.get_setting(db, user.org_id, "gateway_token"))
    if not token:
        raise HTTPException(404, "Create a pairing code first")
    payload = json.dumps({"faceattend": 1, "server": _server_url(request), "token": token})
    buf = io.BytesIO()
    segno.make(payload, error="m").save(buf, kind="svg", scale=6, border=2, dark="#1f4e79")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def gateway_org(request: Request, db: Session = Depends(get_db)) -> Organization:
    """`Authorization: Bearer <pairing code>` from the Android app."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    org_part = token.split(".", 1)[0]
    if not org_part.isdigit():
        raise HTTPException(401, "Not paired")
    row = db.scalar(select(OrgSetting).where(OrgSetting.org_id == int(org_part), OrgSetting.key == "gateway_token"))
    if row is None or not row.value or not hmac.compare_digest(row.value, token):
        raise HTTPException(401, "Pairing code is no longer valid - scan the new one")
    org = db.get(Organization, int(org_part))
    if org is None or not org.is_active:
        raise HTTPException(401, "Organization disabled")
    return org


@router.get("/api/gateway/ping")
def gateway_ping(org: Organization = Depends(gateway_org)):
    return {"ok": True, "organization": org.name}


@router.get("/api/gateway/messages")
def gateway_messages(org: Organization = Depends(gateway_org), db: Session = Depends(get_db)):
    return {"messages": [{"id": m.id, "to": m.recipient, "body": m.body} for m in messaging.gateway_pull(db, org.id)]}


class Report(BaseModel):
    ok: bool
    error: str | None = None


@router.post("/api/gateway/messages/{mid}")
def gateway_result(mid: int, report: Report, org: Organization = Depends(gateway_org), db: Session = Depends(get_db)):
    if not messaging.gateway_report(db, org.id, mid, report.ok, report.error):
        raise HTTPException(404, "Unknown message")
    return {"ok": True}


class Incoming(BaseModel):
    sender: str
    body: str


@router.post("/api/gateway/incoming")
def gateway_incoming(sms: Incoming, org: Organization = Depends(gateway_org), db: Session = Depends(get_db)):
    """An SMS a parent sent to the school phone; the answer is queued for the same phone to send."""
    from app.services import sms_commands

    msg = sms_commands.answer(db, org, sms.sender, sms.body)
    return {"ok": True, "command": msg.command, "reply": msg.reply, "state": msg.state}


@router.post("/messages/inbox/{mid}/approve")
def approve_leave(mid: int, request: Request, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    from app.services import sms_commands

    msg = db.get(InboxMessage, mid)
    if msg is None or msg.org_id != user.org_id or msg.state != "leave-pending":
        raise HTTPException(404, "No pending leave request")
    n = sms_commands.approve_leave(db, msg)
    flash(request, f"Leave approved ({n} class record{'s' if n != 1 else ''} set to leave); the parent gets an SMS.")
    return RedirectResponse("/messages#inbox", status_code=303)
