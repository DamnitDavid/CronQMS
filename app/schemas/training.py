"""Pydantic schemas for Training Management requests and responses."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.training import TrainingStatus

__all__ = [
    "TrainingStatus",
    "EmployeeCreate",
    "EmployeeUpdate",
    "EmployeeResponse",
    "TrainingCourseCreate",
    "TrainingCourseUpdate",
    "TrainingCourseResponse",
    "TrainingRecordCreate",
    "TrainingRecordUpdate",
    "TrainingRecordCertify",
    "TrainingRecordResponse",
]


# --- Employee --------------------------------------------------------------
class EmployeeCreate(BaseModel):
    """Schema for registering a non-account (shop-floor) employee."""

    full_name: str = Field(..., min_length=1, max_length=255)
    employee_number: Optional[str] = Field(default=None, max_length=50)
    department: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)


class EmployeeUpdate(BaseModel):
    """Partial update schema for an employee."""

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    employee_number: Optional[str] = Field(default=None, max_length=50)
    department: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)


class EmployeeResponse(BaseModel):
    id: int
    organization_id: int
    full_name: str
    employee_number: Optional[str]
    department: Optional[str]
    job_title: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Training course -------------------------------------------------------
class TrainingCourseCreate(BaseModel):
    """Schema for defining a training course."""

    code: str = Field(..., min_length=1, max_length=50)
    title: str = Field(..., min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    document_id: Optional[int] = None
    recertification_period_months: Optional[int] = Field(default=None, ge=1, le=600)


class TrainingCourseUpdate(BaseModel):
    """Partial update schema for a training course."""

    code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    document_id: Optional[int] = None
    recertification_period_months: Optional[int] = Field(default=None, ge=1, le=600)


class TrainingCourseResponse(BaseModel):
    id: int
    organization_id: int
    code: str
    title: str
    description: Optional[str]
    document_id: Optional[int]
    recertification_period_months: Optional[int]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Training record -------------------------------------------------------
class TrainingRecordCreate(BaseModel):
    """Schema for assigning a course to one trainee.

    Exactly one of ``employee_id`` / ``user_id`` must be provided — a record
    trains a non-account employee or an existing system user, not both.
    """

    course_id: int
    employee_id: Optional[int] = None
    user_id: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _exactly_one_trainee(self) -> "TrainingRecordCreate":
        if (self.employee_id is None) == (self.user_id is None):
            raise ValueError("Provide exactly one of employee_id or user_id")
        return self


class TrainingRecordUpdate(BaseModel):
    """Partial update schema for a training record (non-certify fields)."""

    status: Optional[TrainingStatus] = None
    notes: Optional[str] = Field(default=None, max_length=5000)


class TrainingRecordCertify(BaseModel):
    """Schema for certifying (marking trained) a record with sign-off."""

    trainee_acknowledgment: str = Field(..., min_length=1, max_length=255)
    trained_date: Optional[date] = None


class TrainingRecordResponse(BaseModel):
    id: int
    organization_id: int
    course_id: int
    employee_id: Optional[int]
    user_id: Optional[int]
    status: TrainingStatus
    assigned_date: date
    trained_date: Optional[date]
    trained_by: Optional[int]
    trainee_acknowledgment: Optional[str]
    expiry_date: Optional[date]
    notes: Optional[str]
    created_by: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
