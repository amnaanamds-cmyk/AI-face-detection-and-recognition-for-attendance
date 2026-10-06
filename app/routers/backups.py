"""Backup & restore page (installations on the school's own computer)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.deps import flash, render, superadmin
from app.models import User
from app.services import backup

router = APIRouter()


@router.get("/backup")
def backup_page(request: Request, user: User = Depends(superadmin)):
    return render(request, "admin/backup.html", user, available=backup.available(), backups=backup.list_backups(),
                  folder=str(backup.BACKUP_DIR))


@router.get("/backup/download")
def download_now(user: User = Depends(superadmin)):
    if not backup.available():
        raise HTTPException(404)
    from datetime import datetime

    return Response(backup.make_backup(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="faceattend-backup-{datetime.now():%Y-%m-%d-%H%M}.zip"'})


@router.get("/backup/file/{name}")
def download_auto(name: str, user: User = Depends(superadmin)):
    if not re.fullmatch(r"faceattend-\d{4}-\d{2}-\d{2}\.zip", name):
        raise HTTPException(404)
    path = backup.BACKUP_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/zip", filename=name)


@router.post("/backup/restore")
async def restore(request: Request, file: UploadFile = File(...), confirm: str = Form(""),
                  user: User = Depends(superadmin)):
    if confirm.strip().upper() != "RESTORE":
        flash(request, "Type RESTORE to confirm: restoring replaces all current data.", "danger")
        return RedirectResponse("/backup", status_code=303)
    try:
        result = backup.restore(await file.read())
    except backup.BackupError as exc:
        flash(request, f"Not restored: {exc}", "danger")
        return RedirectResponse("/backup", status_code=303)
    request.session.clear()
    msg = "Backup restored. Log in again with the accounts from the backup."
    if result["kept"]:
        msg += f" The previous data was kept as {result['kept']}."
    if result["keys_changed"]:
        msg += " The backup came with other keys: restart FaceAttend once (tray icon > Quit, then open it again)."
    flash(request, msg, "success")
    return RedirectResponse("/login", status_code=303)
