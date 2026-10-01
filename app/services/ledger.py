"""Tamper-evident attendance ledger.

Every create / update / delete of an attendance record is captured automatically (SQLAlchemy
session events, so no code path can forget it) and appended to a per-organization hash chain:

    hash(n) = SHA-256( hash(n-1) | entry n )

The integrity check recomputes the chain and compares every attendance record with its last
ledger entry, so it reveals:
* an edited or deleted ledger entry (the chain breaks at that point);
* attendance changed directly in the database, bypassing FaceAttend (record != ledger);
* attendance rows inserted behind FaceAttend's back (no ledger entry).
The current chain head ("fingerprint") is printed on reports, so even rewriting the whole chain
is detectable by anyone who kept an old report.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime

from sqlalchemy import event, func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models import Attendance, Course, LedgerEntry, Student, now

GENESIS = "0" * 64
_seal_lock = threading.Lock()
TRACKED = ("student_id", "course_id", "session_id", "date", "status", "method", "marked_at", "checked_out_at", "note")


def _iso(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return getattr(v, "value", v)


def record_state(a: Attendance) -> dict:
    return {"id": a.id, **{k: _iso(getattr(a, k)) for k in TRACKED}}


def canonical(e: LedgerEntry) -> str:
    return json.dumps({"org": e.org_id, "at": e.at.isoformat(timespec="seconds"), "actor": e.actor, "action": e.action,
                       "attendance": e.attendance_id, "record": json.loads(e.payload)},
                      sort_keys=True, separators=(",", ":"))


def entry_hash(prev: str, e: LedgerEntry) -> str:
    return hashlib.sha256((prev + "|" + canonical(e)).encode()).hexdigest()


# ------------------------------------------------------------------ capture
def _org_of(session: Session, a: Attendance, cache: dict) -> int | None:
    """Organization of a record; via the student when its course is being deleted in the same flush."""
    key = (a.course_id, a.student_id)
    if key not in cache:
        conn = session.connection()
        org = conn.execute(select(Course.org_id).where(Course.id == a.course_id)).scalar()
        if org is None:
            org = conn.execute(select(Student.org_id).where(Student.id == a.student_id)).scalar()
        cache[key] = org
    return cache[key]


@event.listens_for(Session, "after_flush")
def _capture(session: Session, _ctx) -> None:
    if session.info.get("ledger_off"):
        return
    actor = str(session.info.get("actor", "system"))[:80]
    orgs, cache, entries = session.info.setdefault("ledger_orgs", set()), {}, []
    for obj in session.new:
        if isinstance(obj, Attendance):
            entries.append(("create", obj))
    for obj in session.dirty:
        if isinstance(obj, Attendance):
            state = sa_inspect(obj)
            if any(state.attrs[k].history.has_changes() for k in TRACKED):
                entries.append(("update", obj))
    for obj in session.deleted:
        if isinstance(obj, Attendance):
            entries.append(("delete", obj))
    for action, a in entries:
        org = _org_of(session, a, cache)
        if org is None:
            continue
        state = record_state(a)
        if action == "delete":
            state["deleted"] = True
        session.add(LedgerEntry(org_id=org, at=now().replace(microsecond=0), actor=actor, action=action,
                                attendance_id=a.id, payload=json.dumps(state, sort_keys=True)))
        orgs.add(org)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    orgs = session.info.pop("ledger_orgs", None)
    if orgs:
        for org in orgs:
            seal(org)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("ledger_orgs", None)


# ------------------------------------------------------------------ chain
def seal(org_id: int) -> int:
    """Give new entries their place in the chain (one writer at a time). Returns the head sequence number."""
    from app import database

    with _seal_lock, database.SessionLocal() as db:
        db.info["ledger_off"] = True
        head = db.scalar(select(LedgerEntry).where(LedgerEntry.org_id == org_id, LedgerEntry.seq.is_not(None))
                         .order_by(LedgerEntry.seq.desc()).limit(1))
        prev, seq = (head.hash, head.seq) if head else (GENESIS, 0)
        for e in db.scalars(select(LedgerEntry).where(LedgerEntry.org_id == org_id, LedgerEntry.seq.is_(None))
                            .order_by(LedgerEntry.id)).all():
            seq += 1
            e.seq, e.prev_hash, e.hash = seq, prev, entry_hash(prev, e)
            prev = e.hash
        db.commit()
        return seq


def head(db: Session, org_id: int) -> tuple[int, str]:
    e = db.scalar(select(LedgerEntry).where(LedgerEntry.org_id == org_id, LedgerEntry.seq.is_not(None))
                  .order_by(LedgerEntry.seq.desc()).limit(1))
    return (e.seq, e.hash) if e else (0, GENESIS)


def fingerprint(db: Session, org_id: int) -> str:
    seq, h = head(db, org_id)
    return f"#{seq} {h[:16]}"


def ensure_baseline(db: Session, org_id: int) -> int:
    """First start with the ledger: record the existing attendance once as the starting point."""
    if db.scalar(select(func.count(LedgerEntry.id)).where(LedgerEntry.org_id == org_id)):
        return 0
    rows = db.scalars(select(Attendance).join(Course).where(Course.org_id == org_id).order_by(Attendance.id)).all()
    if not rows:
        return 0
    db.info["ledger_off"] = True
    try:
        for a in rows:
            db.add(LedgerEntry(org_id=org_id, at=now().replace(microsecond=0), actor="system", action="baseline",
                               attendance_id=a.id, payload=json.dumps(record_state(a), sort_keys=True)))
        db.commit()
    finally:
        db.info.pop("ledger_off", None)
    seal(org_id)
    return len(rows)


def verify(db: Session, org_id: int) -> dict:
    """Recompute the chain and compare it with the attendance table."""
    entries = db.scalars(select(LedgerEntry).where(LedgerEntry.org_id == org_id).order_by(LedgerEntry.seq.is_(None),
                                                                                         LedgerEntry.seq, LedgerEntry.id)).all()
    problems: list[str] = []
    prev, expected_seq, last_state = GENESIS, 1, {}
    for e in entries:
        if e.seq is None:
            continue  # not sealed yet (sealing happens right after each commit)
        if e.seq != expected_seq:
            problems.append(f"Entry #{expected_seq} is missing (next found: #{e.seq}) - ledger entries were deleted.")
            expected_seq = e.seq
        if e.prev_hash != prev or e.hash != entry_hash(prev, e):
            problems.append(f"Entry #{e.seq} ({e.at:%d %b %Y %H:%M}, by {e.actor}) was altered after it was written.")
        prev, expected_seq = e.hash, e.seq + 1
        last_state[e.attendance_id] = json.loads(e.payload)
    current = {a.id: a for a in db.scalars(select(Attendance).join(Course).where(Course.org_id == org_id)).all()}
    for aid, a in current.items():
        logged = last_state.get(aid)
        if logged is None:
            problems.append(f"Attendance record {aid} is not in the ledger - added outside FaceAttend.")
        elif logged.get("deleted"):
            problems.append(f"Attendance record {aid} was deleted in FaceAttend but exists again in the database.")
        else:
            now_state = record_state(a)
            diff = [k for k in TRACKED if logged.get(k) != now_state.get(k)]
            if diff:
                problems.append(f"Attendance record {aid} was changed outside FaceAttend ({', '.join(diff)}: "
                                f"{logged.get(diff[0])} -> {now_state.get(diff[0])}).")
    for aid, st in last_state.items():
        if aid not in current and not st.get("deleted"):
            problems.append(f"Attendance record {aid} was deleted outside FaceAttend.")
    seq, h = head(db, org_id)
    return {"ok": not problems, "problems": problems, "entries": len(entries), "records": len(current),
            "head_seq": seq, "head_hash": h}


def history(db: Session, attendance_id: int) -> list[LedgerEntry]:
    return db.scalars(select(LedgerEntry).where(LedgerEntry.attendance_id == attendance_id).order_by(LedgerEntry.id)).all()
