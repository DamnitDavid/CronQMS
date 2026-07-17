from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.core.security import decode_access_token, verify_password

# Name of the HttpOnly cookie that carries the JWT for browser sessions.
ACCESS_TOKEN_COOKIE = "access_token"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def set_auth_cookie(response: Response, token: str, expires_in: int) -> None:
    """Attach the JWT as an HttpOnly, SameSite=Lax session cookie.

    ``Secure`` is enabled outside development so the cookie is never sent over
    plain HTTP in production; ``SameSite=Lax`` blocks it on cross-site POSTs
    while still allowing top-level navigations. Shared by the login and
    first-time-setup flows so browser sessions are established identically.
    """
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=expires_in,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment != "development",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Remove the session cookie."""
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/")


def get_token_from_request(
    request: Request,
    bearer_token: str | None = Depends(oauth2_scheme),
) -> str | None:
    """Resolve the JWT from either the Authorization header or the session cookie.

    API clients send ``Authorization: Bearer <token>``; browsers carry the token
    in an HttpOnly cookie set at login. Header wins if both are present.
    """
    if bearer_token:
        return bearer_token
    return request.cookies.get(ACCESS_TOKEN_COOKIE)


async def get_current_user_email(token: str | None = Depends(get_token_from_request)):
    """Extract and validate JWT token, return user email."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    email = payload.get("email")
    if email is None:
        raise credentials_exception
    return email

async def get_current_user(email: str = Depends(get_current_user_email), db: Session = Depends(get_db)):
    """Fetch full user object from database."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # Attribute any mutations made during this request to the authenticated
    # user so the audit trail records the actor. Imported lazily to avoid a
    # circular import (audit -> models -> core).
    from app.core.audit import set_audit_actor

    set_audit_actor(db, user.id)

    # Resolve the user's granted permissions once and expose them to templates
    # (the sidebar gates nav items on this set). Lazy import avoids the
    # auth -> permissions import cycle, mirroring set_audit_actor above.
    from app.core.permissions import granted_permissions

    user.granted_permissions = {p.value for p in granted_permissions(db, user)}

    # Build the org-configurable sidebar for this user, gated by the grants just
    # resolved. Same per-request, attach-to-instance pattern as above.
    from app.services import nav_config

    user.nav = nav_config.visible_nav(db, user)
    return user

async def get_current_user_optional(
    token: str | None = Depends(get_token_from_request),
    db: Session = Depends(get_db),
):
    """Return current user if authenticated, otherwise None."""
    if not token:
        return None

    try:
        email = await get_current_user_email(token)
    except HTTPException:
        return None

    return db.query(User).filter(User.email == email).first()


async def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Authenticate a user by email and password."""
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user
