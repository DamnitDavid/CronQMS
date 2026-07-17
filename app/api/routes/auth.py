"""Authentication endpoints."""

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse
from app.core.security import hash_password, create_token_for_user
from app.core.auth import (
    authenticate_user,
    clear_auth_cookie,
    get_current_user,
    set_auth_cookie,
)
from app.core.ratelimit import rate_limit

router = APIRouter(prefix="/api/auth", tags=["Authentication"])

settings = get_settings()

# Throttle credential submission to blunt online brute-force. Keyed by client IP
# and shared between the JSON and browser login paths.
login_rate_limit = rate_limit(max_hits=10, window_seconds=60, name="login")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    db: Session = Depends(get_db),
) -> User:
    """Register a new user.

    Args:
        user_data: User registration data.
        db: Database session.

    Returns:
        User: Created user object.

    Raises:
        HTTPException: If registration is disabled or the email already exists.
    """
    # Secure by default: users are provisioned by an admin (or the first admin
    # via /setup). Open sign-up is only available when explicitly enabled.
    if not settings.allow_public_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled. Contact an administrator for an account.",
        )

    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Create new user
    hashed_password = hash_password(user_data.password)
    new_user = User(
        email=user_data.email,
        hashed_password=hashed_password,
        is_active=True,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return new_user


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: UserLogin,
    response: Response,
    db: Session = Depends(get_db),
    _: None = Depends(login_rate_limit),
) -> TokenResponse:
    """Authenticate user (JSON API) and return a JWT token.

    The token is returned in the body for API/bearer clients and is *also* set
    as an HttpOnly session cookie so the same endpoint works for browsers.

    Args:
        credentials: Login credentials.
        response: Response used to attach the session cookie.
        db: Database session.

    Returns:
        TokenResponse: JWT token and metadata.

    Raises:
        HTTPException: If credentials are invalid.
    """
    user = await authenticate_user(db, credentials.email, credentials.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token, expires_in = create_token_for_user(user.id, user.email)
    set_auth_cookie(response, access_token, expires_in)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> User:
    """Get current authenticated user information."""
    return current_user


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Log out an API client by clearing the session cookie.

    JWTs are stateless, so bearer clients simply discard their token; this also
    clears the browser session cookie for symmetry with ``/login``.

    Returns:
        dict: Logout confirmation.
    """
    clear_auth_cookie(response)
    return {"message": "Logged out successfully"}


@router.post("/browser-login")
async def browser_login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
    _: None = Depends(login_rate_limit),
) -> Response:
    """Authenticate a form-encoded (htmx) browser submission.

    On success, sets the session cookie and asks htmx to redirect to the
    dashboard via the ``HX-Redirect`` header. On failure, returns a small HTML
    fragment that htmx swaps into the login form's response target.
    """
    user = await authenticate_user(db, email, password)

    if not user:
        return Response(
            content='<p class="error">Invalid email or password</p>',
            media_type="text/html",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    access_token, expires_in = create_token_for_user(user.id, user.email)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Redirect"] = "/admin/defects"
    set_auth_cookie(response, access_token, expires_in)
    return response


@router.post("/browser-logout")
async def browser_logout() -> Response:
    """Log out a browser session: clear the cookie and redirect to login."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Redirect"] = "/login"
    clear_auth_cookie(response)
    return response
