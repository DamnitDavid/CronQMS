"""Authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse, CurrentUser
from app.core.security import hash_password, create_token_for_user
from app.core.auth import authenticate_user, get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


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
        HTTPException: If email already exists.
    """
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
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticate user and return JWT token.

    Args:
        credentials: Login credentials.
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

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Get current authenticated user information.

    Args:
        current_user: Current authenticated user.
        db: Database session.

    Returns:
        User: Current user object.
    """
    user = db.query(User).filter(User.id == current_user.id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return user


@router.post("/logout")
async def logout() -> dict:
    """Logout endpoint.

    Note: JWT tokens are stateless, so logout is handled client-side
    by removing the token. This endpoint exists for API completeness.

    Returns:
        dict: Logout confirmation.
    """
    return {"message": "Logged out successfully"}
