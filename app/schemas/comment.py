"""Pydantic schemas for event comments."""

from datetime import datetime

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    """Schema for posting a comment."""

    body: str = Field(..., min_length=1, max_length=5000)


class CommentResponse(BaseModel):
    """Schema for a comment in the thread."""

    id: int
    event_id: int
    author_id: int
    body: str
    created_at: datetime

    class Config:
        from_attributes = True
