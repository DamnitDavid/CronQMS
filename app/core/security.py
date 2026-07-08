"""Security utilities for password hashing and JWT token management."""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, status

from app.config import get_settings

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
settings = get_settings()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt.

    Args:
        password: Plain text password.

    Returns:
        str: Hashed password.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    Args:
        plain_password: Plain text password to verify.
        hashed_password: Previously hashed password.

    Returns:
        bool: True if password matches, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, int]:
    """Create a JWT access token.

    Args:
        data: Dictionary of data to encode in token.
        expires_delta: Optional custom expiration time.

    Returns:
        tuple: (token, expires_in_seconds)
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours)

    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    # Calculate expiration time in seconds
    expires_in = int((expire - datetime.utcnow()).total_seconds())

    return encoded_jwt, expires_in


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token.

    Args:
        token: JWT token string.

    Returns:
        Optional[Dict]: Token payload if valid, None if invalid/expired.

    Raises:
        HTTPException: If token is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


def create_token_for_user(user_id: int, email: str) -> tuple[str, int]:
    """Create a token for a specific user.

    Args:
        user_id: User ID.
        email: User email.

    Returns:
        tuple: (token, expires_in_seconds)
    """
    token_data = {
        "sub": str(user_id),
        "email": email,
    }
    return create_access_token(data=token_data)
