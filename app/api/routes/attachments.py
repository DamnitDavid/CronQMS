"""Attachment upload/download endpoints, scoped to events."""

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Permission, require_permission
from app.core.storage import get_storage
from app.database import get_db
from app.models import Attachment, Event, User
from app.schemas.attachment import AttachmentResponse

router = APIRouter(tags=["Attachments"])

settings = get_settings()


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
    "/api/events/{event_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    event_id: int,
    file: UploadFile,
    current_user: User = Depends(require_permission(Permission.EVENT_UPDATE)),
    db: Session = Depends(get_db),
) -> Attachment:
    """Upload a file against an event, recording checksum, size and uploader."""
    event = _get_event_in_org(db, event_id, current_user)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > settings.attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size",
        )

    checksum = hashlib.sha256(data).hexdigest()
    storage_key = f"{event.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)

    attachment = Attachment(
        event_id=event.id,
        filename=file.filename or "upload",
        content_type=file.content_type,
        size_bytes=len(data),
        checksum=checksum,
        storage_key=storage_key,
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    return attachment


@router.get("/api/events/{event_id}/attachments", response_model=list[AttachmentResponse])
async def list_attachments(
    event_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
) -> list[Attachment]:
    event = _get_event_in_org(db, event_id, current_user)
    return (
        db.query(Attachment)
        .filter(Attachment.event_id == event.id)
        .order_by(Attachment.created_at.desc())
        .all()
    )


@router.get("/api/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: int,
    current_user: User = Depends(require_permission(Permission.EVENT_READ)),
    db: Session = Depends(get_db),
) -> Response:
    attachment = db.query(Attachment).filter(Attachment.id == attachment_id).first()
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    # Enforce org scope via the parent event.
    _get_event_in_org(db, attachment.event_id, current_user)

    data = get_storage().load(attachment.storage_key)
    return Response(
        content=data,
        media_type=attachment.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )
