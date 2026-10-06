"""Smoke test: every page opens (no server error) for an administrator, a teacher and a student."""
import re
from datetime import date, datetime

from fastapi.testclient import TestClient

from tests.conftest import data_url, face_image, noisy


def _get_paths(app):
    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
            elif hasattr(r, "routes") and not hasattr(r, "methods"):
                yield from walk(r.routes)
            elif getattr(r, "methods", None) and "GET" in r.methods:
                yield r.path
    return sorted(set(walk(app.routes)))


def test_every_page_opens_for_every_role(db_url):
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        c.post("/login", data={"username": "admin", "password": "admin123"})
        c.post("/users/new", data={"username": "t1", "full_name": "T One", "password": "teacher123", "role": "teacher"})
        c.post("/courses/new", data={"code": "C1", "name": "Course", "semester": 7, "section": "A", "teacher_id": 2})
        r = c.post("/students/new", data={"student_code": "S1", "name": "Stu", "semester": 7, "section": "A",
                                          "department": "Computer Science", "consent": "on", "auto_enroll": "on",
                                          "create_account": "on", "password": "student123"}, follow_redirects=False)
        sid = r.headers["location"].split("/")[2]
        c.post(f"/api/students/{sid}/faces", json={"images": [data_url(noisy(face_image(1), k)) for k in range(3)]})
        c.post("/sessions/new", data={"course_id": 1, "day": date.today().isoformat(), "start": datetime.now().strftime("%H:%M"),
                                      "duration": 60, "present_window": 10, "late_window": 20, "start_now": "1"})
        paths = _get_paths(app)
        assert len(paths) > 50
        errors = []
        for who, (u, p) in {"admin": ("admin", "admin123"), "teacher": ("t1", "teacher123"),
                            "student": ("S1", "student123")}.items():
            c.get("/logout")
            c.post("/login", data={"username": u, "password": p})
            for path in paths:
                if path.startswith(("/logout", "/docs", "/redoc", "/openapi", "/static")):
                    continue
                url = re.sub(r"\{[^}]+\}", "1", path.replace("{sid}", sid) if "students" in path else path)
                status = c.get(url, follow_redirects=False).status_code
                if status >= 500:
                    errors.append(f"{who} {url} -> {status}")
        assert not errors, errors
