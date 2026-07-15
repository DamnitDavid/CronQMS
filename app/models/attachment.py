"""File attachment metadata.

The blob itself lives in the configured storage backend (see
app/core/storage.py); this row records the metadata: original filename, content
type, size, SHA-256 checksum, uploader, and timestamp.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class Attachment(Base):
    """Metadata for a file attached to an event."""

    __tablename__ = "attachments"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=False)  # SHA-256 hex digest
    storage_key = Column(String(255), nullable=False, unique=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="attachments")
    uploader = relationship("User", foreign_keys=[uploaded_by], lazy="joined")

    def __repr__(self) -> str:
        return f"<Attachment(id={self.id}, event_id={self.event_id}, filename={self.filename})>"
