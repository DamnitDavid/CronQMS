"""Security utilities for password hashing and JWT token management."""

import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from passlib.context import CryptContext
from jose import JWTError, jwt

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


def generate_temp_password(length: int = 16) -> str:
    """Generate a cryptographically-random temporary password.

    Used for admin-initiated password resets so a reset account is never left on
    a shared, guessable value. The result is shown to the admin once (to relay
    to the user) and is never stored in plaintext.
    """
    alphabet = string.ascii_letters + string.digits
    # Guarantee at least one letter and one digit, then fill the rest randomly.
    core = [secrets.choice(string.ascii_letters), secrets.choice(string.digits)]
    core += [secrets.choice(alphabet) for _ in range(max(length, 12) - 2)]
    secrets.SystemRandom().shuffle(core)
    return "".join(core)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a JWT access token.

    Args:
        data: Dictionary of data to encode in token.
        expires_delta: Optional custom expiration time.

    Returns:
        str: Encoded JWT token.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=settings.jwt_expiration_hours))
    to_encode.update({"exp": expire})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    return encoded_jwt


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token.

    Args:
        token: JWT token string.

    Returns:
        Optional[Dict]: Token payload if valid, None if invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        return None


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
    expires_delta = timedelta(hours=settings.jwt_expiration_hours)
    access_token = create_access_token(data=token_data, expires_delta=expires_delta)
    expires_in = int(expires_delta.total_seconds())
    return access_token, expires_in
