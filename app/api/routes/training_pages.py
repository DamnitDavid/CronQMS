"""Server-rendered Training Management pages and their form/action handlers.

The browser UI counterpart to the /api/training JSON API. Mutations are
Post/Redirect/Get form posts that reuse the same permission dependencies and
certification rules as the API, so behavior can't drift between the two
surfaces.

The certify handler is the shop-floor flow: a supervisor pulls up the record on
a tablet, opens the linked SOP, walks the operator through it, then signs off —
recording themselves as the trainer plus the operator's typed acknowledgment.
"""

import os
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.permissions import Permission, require_permission
from app.database import get_db
from app.models import (
    Document,
    Employee,
    EventHistory,
    TrainingCourse,
    TrainingRecord,
    TrainingStatus,
    User,
)
from app.services.training_workflow import certify_record

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

STATUS_VALUES = [s.value for s in TrainingStatus]


# --- helpers ---------------------------------------------------------------
def _employee_or_404(db: Session, employee_id: int, current_user: User) -> Employee:
    employee = (
        db.query(Employee)
        .filter(Employee.id == employee_id, Employee.is_active.is_(True))
        .first()
    )
    if not employee or employee.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return employee


def _course_or_404(db: Session, course_id: int, current_user: User) -> TrainingCourse:
    course = (
        db.query(TrainingCourse)
        .filter(TrainingCourse.id == course_id, TrainingCourse.is_active.is_(True))
        .first()
    )
    if not course or course.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


def _record_or_404(db: Session, record_id: int, current_user: User) -> TrainingRecord:
    record = db.query(TrainingRecord).filter(TrainingRecord.id == record_id).first()
    if not record or record.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Training record not found")
    return record


def _permission_flags(user: User) -> dict:
    """Which training action buttons the current user may see."""
    granted = getattr(user, "granted_permissions", set())
    checks = {
        "can_create": Permission.TRAINING_CREATE,
        "can_edit": Permission.TRAINING_UPDATE,
        "can_certify": Permission.TRAINING_CERTIFY,
        "can_delete": Permission.TRAINING_DELETE,
    }
    return {name: perm.value in granted for name, perm in checks.items()}


def _org_users(db: Session, organization_id: int) -> list[User]:
    return (
        db.query(User)
        .filter(User.organization_id == organization_id, User.is_active.is_(True))
        .order_by(User.email)
        .all()
    )


def _org_employees(db: Session, organization_id: int) -> list[Employee]:
    return (
        db.query(Employee)
        .filter(Employee.organization_id == organization_id, Employee.is_active.is_(True))
        .order_by(Employee.full_name)
        .all()
    )


def _org_courses(db: Session, organization_id: int) -> list[TrainingCourse]:
    return (
        db.query(TrainingCourse)
        .filter(TrainingCourse.organization_id == organization_id, TrainingCourse.is_active.is_(True))
        .order_by(TrainingCourse.title)
        .all()
    )


def _org_documents(db: Session, organization_id: int) -> list[Document]:
    return (
        db.query(Document)
        .filter(Document.organization_id == organization_id, Document.is_active.is_(True))
        .order_by(Document.title)
        .all()
    )


def _to_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _to_date(value: Optional[str]):
    return date.fromisoformat(value) if value else None


def _redirect(path: str, error: Optional[str] = None) -> RedirectResponse:
    if error:
        path += ("&" if "?" in path else "?") + f"error={quote(error)}"
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


# --- records list (module landing) -----------------------------------------
@router.get("/admin/training")
async def training_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = None,
    course_id: Optional[str] = None,
):
    query = db.query(TrainingRecord).filter(
        TrainingRecord.organization_id == current_user.organization_id
    )
    course_pk = _to_int(course_id) if (course_id or "").isdigit() else None
    if course_pk is not None:
        query = query.filter(TrainingRecord.course_id == course_pk)
    records = query.order_by(TrainingRecord.created_at.desc()).limit(500).all()

    # The Expired filter is on the derived status, so apply it in Python.
    if status_filter == "Expired":
        records = [r for r in records if r.is_expired]
    elif status_filter in STATUS_VALUES:
        records = [r for r in records if r.status == status_filter and not r.is_expired]

    context = {
        "request": request,
        "current_user": current_user,
        "records": records,
        "courses": _org_courses(db, current_user.organization_id),
        "employees": _org_employees(db, current_user.organization_id),
        "users": _org_users(db, current_user.organization_id),
        "statuses": STATUS_VALUES,
        "perms": _permission_flags(current_user),
        "today": date.today(),
        "filters": {"status": status_filter or "", "course_id": course_id or ""},
        "error": request.query_params.get("error"),
    }
    template = (
        "admin/training/_records_table.html"
        if "HX-Request" in request.headers
        else "admin/training/list.html"
    )
    return templates.TemplateResponse(template, context)


# --- assign a record -------------------------------------------------------
@router.post("/admin/training/assign")
async def training_assign(
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    course_id: str = Form(...),
    trainee: str = Form(...),  # "emp:<id>" or "user:<id>"
    notes: str = Form(""),
):
    course_pk = _to_int(course_id) if course_id.isdigit() else None
    if course_pk is None:
        return _redirect("/admin/training", "Select a course.")
    course = _course_or_404(db, course_pk, current_user)
    kind, _, raw_id = trainee.partition(":")
    trainee_id = _to_int(raw_id)
    if trainee_id is None or kind not in ("emp", "user"):
        return _redirect("/admin/training", "Select a trainee.")

    employee_id = user_id = None
    if kind == "emp":
        _employee_or_404(db, trainee_id, current_user)
        employee_id = trainee_id
    else:
        trainee_user = db.query(User).filter(User.id == trainee_id).first()
        if trainee_user is None or trainee_user.organization_id != current_user.organization_id:
            return _redirect("/admin/training", "Trainee not found in your organization.")
        user_id = trainee_id

    db.add(
        TrainingRecord(
            organization_id=current_user.organization_id,
            course_id=course.id,
            employee_id=employee_id,
            user_id=user_id,
            status=TrainingStatus.ASSIGNED.value,
            assigned_date=date.today(),
            notes=notes or None,
            created_by=current_user.id,
        )
    )
    db.commit()
    return _redirect("/admin/training")


# --- certify (tablet sign-off) ---------------------------------------------
@router.post("/admin/training/records/{record_id}/certify")
async def training_certify(
    record_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_CERTIFY)),
    db: Session = Depends(get_db),
    trainee_acknowledgment: str = Form(...),
    trained_date: Optional[str] = Form(None),
    redirect_to: str = Form("/admin/training"),
):
    record = _record_or_404(db, record_id, current_user)
    if not trainee_acknowledgment.strip():
        return _redirect(redirect_to, "Trainee acknowledgment is required to certify.")
    # Use the record's own course (loaded regardless of its active flag) so an
    # outstanding assignment can still be certified after its course was
    # soft-deleted.
    certify_record(
        record,
        record.course,
        trainer_id=current_user.id,
        acknowledgment=trainee_acknowledgment.strip(),
        trained_date=_to_date(trained_date),
    )
    db.add(record)
    db.commit()
    return _redirect(redirect_to)


# --- record status / delete ------------------------------------------------
@router.post("/admin/training/records/{record_id}/status")
async def training_record_status(
    record_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    status_value: str = Form(..., alias="status"),
    redirect_to: str = Form("/admin/training"),
):
    record = _record_or_404(db, record_id, current_user)
    if status_value not in STATUS_VALUES:
        return _redirect(redirect_to, "Unknown status.")
    record.status = status_value
    db.add(record)
    db.commit()
    return _redirect(redirect_to)


@router.post("/admin/training/records/{record_id}/delete")
async def training_record_delete(
    record_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
    redirect_to: str = Form("/admin/training"),
):
    record = _record_or_404(db, record_id, current_user)
    db.delete(record)
    db.commit()
    return _redirect(redirect_to)


# --- employees -------------------------------------------------------------
@router.get("/admin/training/employees")
async def employees_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "admin/training/employees/list.html",
        {
            "request": request,
            "current_user": current_user,
            "employees": _org_employees(db, current_user.organization_id),
            "perms": _permission_flags(current_user),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/admin/training/employees/create")
async def employee_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/training/employees/create.html",
        {"request": request, "current_user": current_user, "error": error},
    )


@router.post("/admin/training/employees/create")
async def employee_create_submit(
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    employee_number: str = Form(""),
    department: str = Form(""),
    job_title: str = Form(""),
):
    if not full_name.strip():
        return _redirect("/admin/training/employees/create", "Name is required.")
    db.add(
        Employee(
            organization_id=current_user.organization_id,
            full_name=full_name.strip(),
            employee_number=employee_number.strip() or None,
            department=department.strip() or None,
            job_title=job_title.strip() or None,
            created_by=current_user.id,
        )
    )
    db.commit()
    return _redirect("/admin/training/employees")


@router.get("/admin/training/employees/{employee_id}/edit")
async def employee_edit_page(
    employee_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    employee = _employee_or_404(db, employee_id, current_user)
    return templates.TemplateResponse(
        "admin/training/employees/edit.html",
        {"request": request, "current_user": current_user, "employee": employee, "error": error},
    )


@router.post("/admin/training/employees/{employee_id}/edit")
async def employee_edit_submit(
    employee_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    employee_number: str = Form(""),
    department: str = Form(""),
    job_title: str = Form(""),
):
    employee = _employee_or_404(db, employee_id, current_user)
    if not full_name.strip():
        return _redirect(f"/admin/training/employees/{employee_id}/edit", "Name is required.")
    employee.full_name = full_name.strip()
    employee.employee_number = employee_number.strip() or None
    employee.department = department.strip() or None
    employee.job_title = job_title.strip() or None
    db.add(employee)
    db.commit()
    return _redirect("/admin/training/employees")


@router.post("/admin/training/employees/{employee_id}/delete")
async def employee_delete(
    employee_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
):
    employee = _employee_or_404(db, employee_id, current_user)
    employee.is_active = False
    db.add(employee)
    db.commit()
    return _redirect("/admin/training/employees")


# --- courses ---------------------------------------------------------------
@router.get("/admin/training/courses")
async def courses_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "admin/training/courses/list.html",
        {
            "request": request,
            "current_user": current_user,
            "courses": _org_courses(db, current_user.organization_id),
            "perms": _permission_flags(current_user),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/admin/training/courses/create")
async def course_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    return templates.TemplateResponse(
        "admin/training/courses/create.html",
        {
            "request": request,
            "current_user": current_user,
            "documents": _org_documents(db, current_user.organization_id),
            "error": error,
        },
    )


@router.post("/admin/training/courses/create")
async def course_create_submit(
    current_user: User = Depends(require_permission(Permission.TRAINING_CREATE)),
    db: Session = Depends(get_db),
    code: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    document_id: Optional[str] = Form(None),
    recertification_period_months: Optional[str] = Form(None),
):
    if not code.strip() or len(title.strip()) < 3:
        return _redirect("/admin/training/courses/create", "Code and a title (3+ chars) are required.")
    doc_id = _to_int(document_id)
    if doc_id is not None:
        doc = (
            db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.organization_id == current_user.organization_id,
                Document.is_active.is_(True),
            )
            .first()
        )
        if doc is None:
            return _redirect("/admin/training/courses/create", "Linked SOP not found in your organization.")
    db.add(
        TrainingCourse(
            organization_id=current_user.organization_id,
            code=code.strip(),
            title=title.strip(),
            description=description.strip() or None,
            document_id=doc_id,
            recertification_period_months=_to_int(recertification_period_months),
            created_by=current_user.id,
        )
    )
    db.commit()
    return _redirect("/admin/training/courses")


@router.get("/admin/training/courses/{course_id}/edit")
async def course_edit_page(
    course_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    course = _course_or_404(db, course_id, current_user)
    return templates.TemplateResponse(
        "admin/training/courses/edit.html",
        {
            "request": request,
            "current_user": current_user,
            "course": course,
            "documents": _org_documents(db, current_user.organization_id),
            "error": error,
        },
    )


@router.post("/admin/training/courses/{course_id}/edit")
async def course_edit_submit(
    course_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_UPDATE)),
    db: Session = Depends(get_db),
    code: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    document_id: Optional[str] = Form(None),
    recertification_period_months: Optional[str] = Form(None),
):
    course = _course_or_404(db, course_id, current_user)
    if not code.strip() or len(title.strip()) < 3:
        return _redirect(f"/admin/training/courses/{course_id}/edit", "Code and a title (3+ chars) are required.")
    doc_id = _to_int(document_id)
    if doc_id is not None:
        doc = (
            db.query(Document)
            .filter(
                Document.id == doc_id,
                Document.organization_id == current_user.organization_id,
                Document.is_active.is_(True),
            )
            .first()
        )
        if doc is None:
            return _redirect(f"/admin/training/courses/{course_id}/edit", "Linked SOP not found in your organization.")
    course.code = code.strip()
    course.title = title.strip()
    course.description = description.strip() or None
    course.document_id = doc_id
    course.recertification_period_months = _to_int(recertification_period_months)
    db.add(course)
    db.commit()
    return _redirect(f"/admin/training/courses/{course_id}")


@router.get("/admin/training/courses/{course_id}")
async def course_detail_page(
    course_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.TRAINING_READ)),
    db: Session = Depends(get_db),
):
    course = _course_or_404(db, course_id, current_user)
    history = (
        db.query(EventHistory)
        .filter(
            EventHistory.entity_type == "training_course",
            EventHistory.entity_id == course.id,
        )
        .order_by(EventHistory.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/training/courses/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "course": course,
            "records": course.records,
            "employees": _org_employees(db, current_user.organization_id),
            "users": _org_users(db, current_user.organization_id),
            "statuses": STATUS_VALUES,
            "history": history,
            "perms": _permission_flags(current_user),
            "today": date.today(),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/training/courses/{course_id}/delete")
async def course_delete(
    course_id: int,
    current_user: User = Depends(require_permission(Permission.TRAINING_DELETE)),
    db: Session = Depends(get_db),
):
    course = _course_or_404(db, course_id, current_user)
    course.is_active = False
    db.add(course)
    db.commit()
    return _redirect("/admin/training/courses")
