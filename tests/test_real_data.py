"""End-to-end test on REAL face photos with the REAL models (skipped unless configured).

    REAL_FACES_DIR=path/to/dataset  MODELS_DIR=models  pytest tests/test_real_data.py -s

REAL_FACES_DIR: one folder per person with >= 3 photos each (e.g. your own consenting
volunteers, exported from the enrollment day). Optional SPOOF_DIR: photos of printed
photos / phone screens, all of which must be rejected.

What it does, through the web API exactly as the browser would:
  1. imports a class list (CSV) for 75 % of the people - the rest act as strangers,
  2. imports face photos as a ZIP (first 2 photos per person),
  3. starts a session with liveness ON (anti-spoofing CNN when installed),
  4. streams each remaining photo as ~6 webcam-sized frames,
  5. checks: every registered person is marked with the RIGHT name, no stranger or spoof
     is ever marked, then closes the session (absentees) and exports the report.
"""
from __future__ import annotations

import base64
import io
import os
import zipfile
from datetime import date, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

REAL = os.environ.get("REAL_FACES_DIR")
pytestmark = pytest.mark.skipif(not REAL, reason="set REAL_FACES_DIR to run the real-data test")


@pytest.fixture()
def real_client(db_url, monkeypatch):
    from app.config import settings
    from app.vision import backends

    monkeypatch.setattr(settings, "vision_backend", "opencv")
    if os.environ.get("MODELS_DIR"):
        monkeypatch.setattr(settings, "models_dir", Path(os.environ["MODELS_DIR"]))
    backends.set_backend(None)
    backends._default_backend.cache_clear()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
    backends._default_backend.cache_clear()


def webcam_frames(path: Path, n: int, rng) -> list[str]:
    """Place the photo in a 1280x720 'classroom' frame with small jitter and sensor noise."""
    img = cv2.imread(str(path))
    s = 640 / max(img.shape[:2])
    img = cv2.resize(img, None, fx=s, fy=s)
    out = []
    for _ in range(n):
        canvas = np.full((720, 1280, 3), 60, np.uint8)
        h, w = img.shape[:2]
        x, y = 320 + rng.randint(-6, 7), 40 + rng.randint(-6, 7)
        canvas[y:y + h, x:x + w] = img
        noisy = np.clip(canvas + rng.normal(0, 2, canvas.shape), 0, 255).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", noisy, [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append("data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode())
    return out


def test_real_end_to_end(real_client):
    c = real_client
    root = Path(REAL)
    people = sorted(p for p in root.iterdir() if p.is_dir() and len(list(p.glob("*.jpg"))) >= 3)
    assert len(people) >= 4, "need at least 4 people with >= 3 photos"
    n_known = max(2, int(len(people) * 0.75))
    known, strangers = people[:n_known], people[n_known:]

    assert c.post("/login", data={"username": "admin", "password": "admin123"}, follow_redirects=False).status_code == 303
    c.post("/courses/new", data={"code": "CS-401", "name": "Artificial Intelligence", "semester": 7, "section": "A"})

    csv_text = "Student ID,Name,Semester,Section,Department,Consent\n" + "".join(
        f"{p.name},Student {p.name},7,A,Computer Science,yes\n" for p in known)
    r = c.post("/students/import", files={"file": ("class.csv", csv_text.encode())}, data={"auto_enroll": "on"})
    assert r.status_code == 200 and f"{len(known)} created" in r.text

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in known:
            for f in sorted(p.glob("*.jpg"))[:2]:
                zf.write(f, f"{p.name}/{f.name}")
    r = c.post("/students/import/faces", files={"file": ("faces.zip", buf.getvalue())})
    assert r.status_code == 200 and "template(s) registered" in r.text, r.text

    now = datetime.now()
    r = c.post("/sessions/new", data={"course_id": 1, "day": date.today().isoformat(), "start": now.strftime("%H:%M"),
                                      "duration": 60, "present_window": 10, "late_window": 20, "liveness": "on",
                                      "start_now": "1"}, follow_redirects=False)
    assert r.headers["location"] == "/sessions/1/live"

    rng = np.random.RandomState(0)
    name_of = {f"Student {p.name}" for p in known}
    results = {"correct": 0, "wrong": 0, "missed": 0, "stranger_ok": 0, "stranger_marked": 0,
               "spoof_rejected": 0, "spoof_accepted": 0}

    def show(photo: Path) -> dict:
        last = None
        for img in webcam_frames(photo, 6, rng):
            r = c.post("/api/sessions/1/frame", json={"image": img})
            assert r.status_code == 200, r.text
            faces = r.json()["faces"]
            if faces:
                last = max(faces, key=lambda f: f["bbox"][2] * f["bbox"][3])
        return last or {"state": "none", "label": ""}

    for p in known:
        for photo in sorted(p.glob("*.jpg"))[2:]:
            f = show(photo)
            if f["state"] in ("marked", "duplicate", "accepted") and f["label"] == f"Student {p.name}":
                results["correct"] += 1
            elif f["label"] in name_of and f["label"] != f"Student {p.name}":
                results["wrong"] += 1
            else:
                results["missed"] += 1
                print("missed:", photo, f)
    for p in strangers:
        for photo in sorted(p.glob("*.jpg")):
            f = show(photo)
            results["stranger_marked" if f["label"] in name_of else "stranger_ok"] += 1
    spoof_dir = os.environ.get("SPOOF_DIR")
    if spoof_dir:
        for photo in sorted(Path(spoof_dir).glob("*.jpg")):
            f = show(photo)
            results["spoof_rejected" if f["state"] in ("spoof", "unknown") else "spoof_accepted"] += 1
            print("spoof sample:", photo.name, f["state"], f.get("message"))

    print("\nREAL-DATA END-TO-END:", results)
    assert results["wrong"] == 0, "a registered student was marked as someone else"
    assert results["stranger_marked"] == 0, "an unregistered person was marked present"
    assert results["spoof_accepted"] == 0, "a spoof was accepted"
    assert results["correct"] >= 0.9 * (results["correct"] + results["missed"])

    c.post("/sessions/1/close")
    summary = c.get("/api/sessions/1/summary").json()
    assert summary["expected"] == len(known) and summary["not_yet"] == 0
    assert c.get("/reports/session/1?fmt=xlsx").content[:2] == b"PK"
