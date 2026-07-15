"""Per-event comment thread endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import Comment, Event, User
from app.schemas.comment import CommentCreate, CommentResponse

router = APIRouter(tags=["Comments"])


def _get_event_in_org(db: Session, event_id: int, current_user: User) -> Event:
    event = (
        db.query(Event)
        .filter(Event.id == event_id, Event.is_active.is_(True))
        .first()
    )
    if not event or event.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


@router.post(
    "/api/events/{event_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    event_id: int,
    comment_data: CommentCreate,
    current_user: User = Depends(require_permission(Permission.EVENT_COMMENT)),
    db: Session = Depends(get_db),
) -> Comment:
    event = _get_event_in_org(db, event_id, current_user)
    comment = Comment(event_id=event.id, author_id=current_user.id, body=comment_data.body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@router.get("/api/events/{event_id}/comments", response_model=list[CommentResponse])
async def list_comments(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
) -> list[Comment]:
    event = _get_event_in_org(db, event_id, current_user)
    return (
        db.query(Comment)
        .filter(Comment.event_id == event.id)
        .order_by(Comment.created_at.asc())
        .all()
    )
