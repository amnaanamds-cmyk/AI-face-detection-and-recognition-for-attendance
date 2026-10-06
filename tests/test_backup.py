"""Backup and restore of a school's own installation."""
import io
import zipfile
from datetime import datetime, timedelta

from app import database
from app.models import Student
from app.services import backup
from tests.conftest import data_url, face_image, login, noisy


def _isolate(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(backup, "DATA_DIR", tmp_path)
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(settings, "license_file", tmp_path / "license.key")


def test_backup_restore_round_trip(client, monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    login(client)
    r = client.post("/students/new", data={"student_code": "S1", "name": "Amina", "semester": 1, "section": "A",
                                           "department": "CS", "consent": "on"}, follow_redirects=False)
    sid = r.headers["location"].split("/")[2]
    assert client.post(f"/api/students/{sid}/faces", json={"images": [data_url(noisy(face_image(3), k)) for k in range(3)]}).json()["accepted"] == 3

    z = client.get("/backup/download")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "attendance.db" in names and "keys/.secret_key" in names and "backup.json" in names

    client.post(f"/students/{sid}/delete")                     # a mistake: the student is gone
    with database.SessionLocal() as db:
        assert db.query(Student).count() == 0

    assert "Type RESTORE" in client.post("/backup/restore", files={"file": ("b.zip", z.content)},
                                         data={"confirm": "no"}, follow_redirects=True).text
    assert "not a FaceAttend backup" in client.post("/backup/restore", files={"file": ("b.zip", b"junk")},
                                                    data={"confirm": "RESTORE"}, follow_redirects=True).text
    r = client.post("/backup/restore", files={"file": ("b.zip", z.content)}, data={"confirm": "RESTORE"}, follow_redirects=True)
    assert "Backup restored" in r.text
    with database.SessionLocal() as db:
        st = db.query(Student).one()
        assert st.name == "Amina" and len(st.embeddings) == 3          # faces still readable
    kept = list(backup.db_path().parent.glob("*.before-restore-*"))
    assert kept, "the replaced data is kept aside"
    login(client)
    assert client.get("/backup").status_code == 200


def test_automatic_backups_keep_newest(client, monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    start = datetime(2026, 10, 1)
    for i in range(16):
        backup.auto_backup(start + timedelta(days=i))
    files = backup.list_backups()
    assert len(files) == backup.KEEP and files[0]["name"] == "faceattend-2026-10-16.zip"
    assert backup.auto_backup(start + timedelta(days=15)).name == "faceattend-2026-10-16.zip"   # once a day
    login(client)
    assert client.get(f"/backup/file/{files[0]['name']}").status_code == 200
    assert client.get("/backup/file/..%2F.secret_key").status_code == 404
