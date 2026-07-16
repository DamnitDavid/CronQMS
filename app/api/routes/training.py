"""Training Management endpoints — employees, courses, and training records.

The JSON API under ``/api/training`` is the machine interface; the browser UI in
``app/api/routes/training_pages.py`` reuses the same permissions and
certification rules so behavior can't drift between the two surfaces.

Baseline operation employees have no login accounts, so a training record points
at either an :class:`~app.models.training.Employee` (non-account operator) or an
existing :class:`~app.models.user.User`. Certifying a record records the trainer
sign-off, a typed trainee acknowledgment, and — when the course requires
periodic recertification — a computed expiry date.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import (
    Document,
    Employee,
    TrainingCourse,
    TrainingRecord,
    TrainingStatus,
    User,
)
from app.schemas.training import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeUpdate,
    TrainingCourseCreate,
    TrainingCourseResponse,
    TrainingCourseUpdate,
    TrainingRecordCertify,
    TrainingRecordCreate,
    TrainingRecordResponse,
    TrainingRecordUpdate,
)
from app.services.training_workflow import certify_record as certify_training_record

router = APIRouter(prefix="/api/training", tags=["Training"])

_EMPLOYEE_SCALAR_FIELDS = {"full_name", "employee_number", "department", "job_title"}
_COURSE_SCALAR_FIELDS = {"code", "title", "description", "recertification_period_months"}


def _require_organization(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_employee_in_org(db: Session, employee_id: int, current_user: User) -> Employee:
    employee = (
        db.query(Employee)
        .filter(Employee.id == employee_id, Employee.is_active.is_(True))
        .first()
    )
    if not employee or employee.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return employee


def _get_course_in_org(db: Session, course_id: int, current_user: User) -> TrainingCourse:
    course = (
        db.query(TrainingCourse)
        .filter(TrainingCourse.id == course_id, TrainingCourse.is_active.is_(True))
        .first()
    )
    if not course or course.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _get_record_in_org(db: Session, record_id: int, current_user: User) -> TrainingRecord:
    record = db.query(TrainingRecord).filter(TrainingRecord.id == record_id).first()
    if not record or record.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training record not found")
    return record


def _resolve_document(db: Session, document_id: Optional[int], organization_id: int) -> Optional[int]:
    """Validate that ``document_id`` (if set) is a document in the same org."""
    if document_id is None:
        return None
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.organization_id == organization_id,
            Document.is_active.is_(True),
        )
        .first()
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document not found in your organization",
        )
    return document.id


def _validate_trainee(
    db: Session,
    employee_id: Optional[int],
    user_id: Optional[int],
    current_user: User,
) -> None:
    """Ensure the referenced trainee (employee or user) is in the caller's org."""
    if employee_id is not None:
        _get_employee_in_org(db, employee_id, current_user)
    if user_id is not None:
        trainee = db.query(User).filter(User.id == user_id).first()
        if trainee is None or trainee.organization_id != current_user.organization_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User not found in your organization",
            )


# --- Employees -------------------------------------------------------------
@router.post("/employees", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    payload: EmployeeCreate,
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    db: Session = Depends(get_db),
) -> Employee:
    organization_id = _require_organization(current_user)
    employee = Employee(
        organization_id=organization_id,
        full_name=payload.full_name.strip(),
        employee_number=payload.employee_number,
        department=payload.department,
        job_title=payload.job_title,
        created_by=current_user.id,
    )
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@router.get("/employees", response_model=List[EmployeeResponse])
async def list_employees(
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> List[Employee]:
    return (
        db.query(Employee)
        .filter(
            Employee.organization_id == current_user.organization_id,
            Employee.is_active.is_(True),
        )
        .order_by(Employee.full_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/employees/{employee_id}", response_model=EmployeeResponse)
async def get_employee(
    employee_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
) -> Employee:
    return _get_employee_in_org(db, employee_id, current_user)


@router.put("/employees/{employee_id}", response_model=EmployeeResponse)
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
) -> Employee:
    employee = _get_employee_in_org(db, employee_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key in _EMPLOYEE_SCALAR_FIELDS:
            setattr(employee, key, value)
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


@router.delete("/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_employee(
    employee_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    employee = _get_employee_in_org(db, employee_id, current_user)
    employee.is_active = False
    db.add(employee)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Courses ---------------------------------------------------------------
@router.post("/courses", response_model=TrainingCourseResponse, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: TrainingCourseCreate,
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    db: Session = Depends(get_db),
) -> TrainingCourse:
    organization_id = _require_organization(current_user)
    course = TrainingCourse(
        organization_id=organization_id,
        code=payload.code.strip(),
        title=payload.title.strip(),
        description=payload.description,
        document_id=_resolve_document(db, payload.document_id, organization_id),
        recertification_period_months=payload.recertification_period_months,
        created_by=current_user.id,
    )
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


@router.get("/courses", response_model=List[TrainingCourseResponse])
async def list_courses(
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> List[TrainingCourse]:
    return (
        db.query(TrainingCourse)
        .filter(
            TrainingCourse.organization_id == current_user.organization_id,
            TrainingCourse.is_active.is_(True),
        )
        .order_by(TrainingCourse.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/courses/{course_id}", response_model=TrainingCourseResponse)
async def get_course(
    course_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
) -> TrainingCourse:
    return _get_course_in_org(db, course_id, current_user)


@router.put("/courses/{course_id}", response_model=TrainingCourseResponse)
async def update_course(
    course_id: int,
    payload: TrainingCourseUpdate,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
) -> TrainingCourse:
    course = _get_course_in_org(db, course_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)
    if "document_id" in update_data:
        course.document_id = _resolve_document(
            db, update_data.pop("document_id"), course.organization_id
        )
    for key, value in update_data.items():
        if key in _COURSE_SCALAR_FIELDS:
            setattr(course, key, value)
    db.add(course)
    db.commit()
    db.refresh(course)
    return course


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    course = _get_course_in_org(db, course_id, current_user)
    course.is_active = False
    db.add(course)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Training records ------------------------------------------------------
@router.post("/records", response_model=TrainingRecordResponse, status_code=status.HTTP_201_CREATED)
async def assign_record(
    payload: TrainingRecordCreate,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
) -> TrainingRecord:
    organization_id = _require_organization(current_user)
    course = _get_course_in_org(db, payload.course_id, current_user)
    _validate_trainee(db, payload.employee_id, payload.user_id, current_user)
    record = TrainingRecord(
        organization_id=organization_id,
        course_id=course.id,
        employee_id=payload.employee_id,
        user_id=payload.user_id,
        status=TrainingStatus.ASSIGNED.value,
        assigned_date=date.today(),
        notes=payload.notes,
        created_by=current_user.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("/records", response_model=List[TrainingRecordResponse])
async def list_records(
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    course_id: Optional[int] = Query(None),
    employee_id: Optional[int] = Query(None),
    status_filter: Optional[TrainingStatus] = Query(None, alias="status"),
) -> List[TrainingRecord]:
    query = db.query(TrainingRecord).filter(
        TrainingRecord.organization_id == current_user.organization_id
    )
    if course_id is not None:
        query = query.filter(TrainingRecord.course_id == course_id)
    if employee_id is not None:
        query = query.filter(TrainingRecord.employee_id == employee_id)
    if status_filter is not None:
        query = query.filter(TrainingRecord.status == status_filter.value)
    return (
        query.order_by(TrainingRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.put("/records/{record_id}", response_model=TrainingRecordResponse)
async def update_record(
    record_id: int,
    payload: TrainingRecordUpdate,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
) -> TrainingRecord:
    record = _get_record_in_org(db, record_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)
    if "status" in update_data and update_data["status"] is not None:
        record.status = update_data.pop("status").value
    if "notes" in update_data:
        record.notes = update_data.pop("notes")
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.post("/records/{record_id}/certify", response_model=TrainingRecordResponse)
async def certify_record(
    record_id: int,
    payload: TrainingRecordCertify,
    current_user: User = Depends(require_permission(Permission.TRAINING_CERTIFY)),
    db: Session = Depends(get_db),
) -> TrainingRecord:
    record = _get_record_in_org(db, record_id, current_user)
    # Use the record's own course (loaded regardless of the course's active
    # flag) so an outstanding assignment can still be certified after its course
    # was soft-deleted.
    certify_training_record(
        record,
        record.course,
        trainer_id=current_user.id,
        acknowledgment=payload.trainee_acknowledgment.strip(),
        trained_date=payload.trained_date,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(
    record_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    record = _get_record_in_org(db, record_id, current_user)
    db.delete(record)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
