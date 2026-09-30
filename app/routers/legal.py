"""Legal pages. The texts are TEMPLATES that must be reviewed by a lawyer before selling the service."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.deps import render

router = APIRouter()
PAGES = {"privacy": "Privacy policy", "terms": "Terms of service", "dpa": "Data processing agreement",
         "consent-form": "Biometric consent form"}


@router.get("/legal/{page}")
def legal(page: str, request: Request):
    if page not in PAGES:
        raise HTTPException(404)
    return render(request, f"legal/{page}.html", None, title=PAGES[page], pages=PAGES, s=settings)
