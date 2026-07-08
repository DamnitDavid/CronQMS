"""Quality event model for manufacturing events tracking."""

from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text

from app.database import Base


class Event(Base):
    """Quality event model representing manufacturing events.

    Attributes:
        id: Primary key, auto-incrementing integer
        title: Event title/description
        description: Detailed event description
        event_type: Type of event (defect, nonconformance, audit, etc.)
        user_id: Foreign key to user who reported the event
        created_at: Timestamp of event creation (audit trail)
        updated_at: Timestamp of last update (audit trail)
    """

    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    event_type = Column(String(50), nullable=False, default="defect")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        """String representation of Event."""
        return f"<Event(id={self.id}, title={self.title}, type={self.event_type})>"
