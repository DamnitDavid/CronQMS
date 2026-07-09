"""Page rendering routes for Proins."""

import os

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.auth import get_current_user_optional, get_current_admin_user
from app.database import get_db
from app.models import Event

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates"))


@router.get("/login")
async def login_page(
    request: Request,
    current_user=Depends(get_current_user_optional),
):
    if current_user:
        return templates.TemplateResponse(
            "admin/dashboard.html",
            {"request": request, "current_user": current_user, "stats": {}, "recent_events": []},
        )
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})


@router.get("/admin/events")
async def admin_events_page(
    request: Request,
    current_user=Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    events = (
        db.query(Event)
        .filter(Event.is_active.is_(True))
        .order_by(Event.updated_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        "admin/events/list.html",
        {"request": request, "current_user": current_user, "events": events},
    )


@router.get("/admin/events/create")
async def admin_events_create_page(
    request: Request,
    current_user=Depends(get_current_admin_user),
):
    return templates.TemplateResponse(
        "admin/events/create.html",
        {"request": request, "current_user": current_user},
    )
