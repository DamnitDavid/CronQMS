"""Self-service account settings for the signed-in user.

Any authenticated user (no special permission) can change their own password
and their theme preference here. This is distinct from the admin-only user
management under ``/admin/users``, which resets *other* users' passwords to a
fixed default. Theme is stored client-side (localStorage); only the password
change touches the database.
"""

import os
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.auth import get_current_user
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.models import User

router = APIRouter(tags=["Account"])

settings = get_settings()

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)


@router.get("/admin/account")
async def account_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    error: Optional[str] = None,
    saved: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/account.html",
        {
            "request": request,
            "current_user": current_user,
            "password_min_length": settings.password_min_length,
            "error": error,
            "saved": saved,
        },
    )


@router.post("/admin/account/password")
async def account_change_password(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    def redirect(error: Optional[str] = None, saved: bool = False) -> RedirectResponse:
        url = "/admin/account"
        if error:
            url += f"?error={quote(error)}"
        elif saved:
            url += "?saved=1"
        return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)

    if not verify_password(current_password, current_user.hashed_password):
        return redirect("Your current password is incorrect.")
    if len(new_password) < settings.password_min_length:
        return redirect(
            f"New password must be at least {settings.password_min_length} characters."
        )
    if new_password != confirm_password:
        return redirect("New password and confirmation do not match.")
    if verify_password(new_password, current_user.hashed_password):
        return redirect("New password must be different from your current one.")

    current_user.hashed_password = hash_password(new_password)
    db.add(current_user)
    db.commit()
    return redirect(saved=True)
