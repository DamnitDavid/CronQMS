from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.core.security import decode_access_token, verify_password

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

async def get_current_user_email(token: str | None = Depends(oauth2_scheme)):
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
    token: str | None = Depends(oauth2_scheme),
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


async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    """Ensure the current user has admin privileges."""
    if getattr(current_user, "role", "Viewer") != "Admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def authenticate_user(db: Session, email: str, password: str) -> User | None:
    """Authenticate a user by email and password."""
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user
