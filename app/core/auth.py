"""Authentication and authorization logic."""

from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthCredentials
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.core.security import decode_access_token, verify_password
from app.schemas.user import CurrentUser

security = HTTPBearer()


async def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> Optional[User]:
    """Authenticate a user by email and password.

    Args:
        db: Database session.
        email: User email.
        password: Plain text password.

    Returns:
        Optional[User]: User object if authentication successful, None otherwise.
    """
    user = db.query(User).filter(User.email == email).first()

    if not user:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    if not user.is_active:
        return None

    return user


async def get_current_user(
    credentials: HTTPAuthCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Get the current authenticated user from JWT token.

    Args:
        credentials: HTTP Bearer credentials from request.
        db: Database session.

    Returns:
        CurrentUser: Current user information.

    Raises:
        HTTPException: If token is invalid or user not found.
    """
    token = credentials.credentials

    # Decode token
    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id: str = payload.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user from database
    user = db.query(User).filter(User.id == int(user_id)).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is inactive",
        )

    return CurrentUser(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
    )


async def get_current_user_optional(
    credentials: Optional[HTTPAuthCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[CurrentUser]:
    """Get the current user if authenticated, otherwise return None.

    Args:
        credentials: Optional HTTP Bearer credentials.
        db: Database session.

    Returns:
        Optional[CurrentUser]: Current user or None if not authenticated.
    """
    if not credentials:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None
