"""Per-event discussion comments."""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Comment(Base):
    """A single comment in an event's discussion thread."""

    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("Event", backref="comments")
    author = relationship("User", foreign_keys=[author_id], lazy="joined")

    def __repr__(self) -> str:
        return f"<Comment(id={self.id}, event_id={self.event_id}, author_id={self.author_id})>"
