from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.core.security import decode_access_token, verify_password

# Name of the HttpOnly cookie that carries the JWT for browser sessions.
ACCESS_TOKEN_COOKIE = "access_token"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


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
