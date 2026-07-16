"""Admin user management routes (JSON API).

Every route here is tenant-scoped: an admin only ever sees or mutates users in
their own organization, and newly created/updated users are pinned to the
caller's organization. The ``organization_id`` field on the request body is
ignored for this reason — it cannot be used to reach into another tenant.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.core.security import generate_temp_password, hash_password
from app.database import get_db
from app.models import User
from app.schemas.user import UserCreate, UserResponse

router = APIRouter(prefix="/api/users", tags=["Users"])


def _user_in_org(db: Session, user_id: int, current_user: User) -> User:
    """Fetch a user in the caller's organization or raise 404.

    Returning 404 (not 403) for out-of-org ids avoids confirming that a user
    with that id exists in another tenant.
    """
    user = (
        db.query(User)
        .filter(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/", response_model=list[UserResponse])
async def list_users(
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> list[User]:
    return (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .all()
    )


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> User:
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    new_user = User(
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        role=user_data.role.value,
        # Pin to the caller's org — the body's organization_id is not trusted.
        organization_id=current_user.organization_id,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> User:
    return _user_in_org(db, user_id, current_user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserCreate,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> User:
    user = _user_in_org(db, user_id, current_user)

    user.email = user_data.email
    user.hashed_password = hash_password(user_data.password)
    user.role = user_data.role.value
    # organization_id is intentionally not updatable here: a user cannot be
    # moved between tenants through this endpoint.
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    user = _user_in_org(db, user_id, current_user)
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )
    user.is_active = False
    db.add(user)
    db.commit()
    return {"detail": "User deactivated successfully"}


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    current_user: User = Depends(require_permission(Permission.USER_MANAGE)),
    db: Session = Depends(get_db),
) -> dict:
    user = _user_in_org(db, user_id, current_user)
    # Set a random one-time password rather than a shared, guessable default.
    # It is returned once so the admin can relay it; it is never stored in clear.
    temp_password = generate_temp_password()
    user.hashed_password = hash_password(temp_password)
    db.add(user)
    db.commit()
    return {
        "detail": "Password reset. Share the temporary password and have the user change it.",
        "temporary_password": temp_password,
    }
