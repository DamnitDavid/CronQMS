"""Pydantic schemas for attachment metadata responses."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AttachmentResponse(BaseModel):
    """Metadata returned for an uploaded attachment."""

    id: int
    event_id: int
    filename: str
    content_type: Optional[str]
    size_bytes: int
    checksum: str
    uploaded_by: int
    created_at: datetime

    class Config:
        from_attributes = True
