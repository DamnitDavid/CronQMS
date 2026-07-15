"""First-time setup wizard.

Bootstraps the very first Organization + Admin user on a fresh install and signs
them in, solving the chicken-and-egg problem where an Admin can otherwise only be
created by an existing Admin. The routes are unauthenticated but self-gate on
"no admin exists yet" so they cannot be used as an open admin-creation backdoor
once setup is complete.
"""

import os
import re

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.auth import set_auth_cookie
from app.core.security import create_token_for_user, hash_password
from app.database import get_db
from app.models import Organization, User
from app.models.user import Role

router = APIRouter(tags=["Setup"])

settings = get_settings()

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)


def admin_exists(db: Session) -> bool:
    """Whether the system has already been initialized with an Admin user."""
    return (
        db.query(User).filter(User.role == Role.ADMIN.value).first() is not None
    )


def _unique_org_code(db: Session, name: str) -> str:
    """Derive a unique, uppercase alphanumeric org code from its name."""
    base = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:12] or "ORG"
    code = base
    suffix = 1
    while db.query(Organization).filter(Organization.code == code).first() is not None:
        suffix += 1
        code = f"{base[:10]}-{suffix}"
    return code


def _error_fragment(message: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> Response:
    """Small HTML fragment htmx swaps into the wizard's #response target."""
    return Response(
        content=f'<p class="error">{message}</p>',
        media_type="text/html",
        status_code=status_code,
    )


@router.get("/setup")
async def setup_page(request: Request, db: Session = Depends(get_db)):
    """Render the first-time setup wizard, or bounce to login once set up."""
    if admin_exists(db):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse("auth/setup.html", {"request": request})


@router.post("/setup")
async def setup_submit(
    db: Session = Depends(get_db),
    org_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
) -> Response:
    """Create the first Organization + Admin user and sign the admin in.

    Self-gates on ``admin_exists`` so it is inert once setup is complete. On
    validation failure returns an htmx error fragment; on success sets the
    session cookie and asks htmx to redirect to the dashboard.
    """
    if admin_exists(db):
        return _error_fragment(
            "Setup has already been completed.", status.HTTP_403_FORBIDDEN
        )

    org_name = org_name.strip()
    email = email.strip().lower()

    if not org_name:
        return _error_fragment("Organization name is required.")
    if password != confirm_password:
        return _error_fragment("Passwords do not match.")
    if len(password) < settings.password_min_length:
        return _error_fragment(
            f"Password must be at least {settings.password_min_length} characters long."
        )
    if db.query(User).filter(User.email == email).first() is not None:
        return _error_fragment("That email is already registered.")

    organization = Organization(name=org_name, code=_unique_org_code(db, org_name))
    db.add(organization)
    db.flush()  # assign organization.id before creating the user

    admin = User(
        email=email,
        hashed_password=hash_password(password),
        role=Role.ADMIN.value,
        organization_id=organization.id,
        is_active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    access_token, expires_in = create_token_for_user(admin.id, admin.email)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Redirect"] = "/admin/dashboard"
    set_auth_cookie(response, access_token, expires_in)
    return response
