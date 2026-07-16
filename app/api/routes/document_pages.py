"""Server-rendered Document Control pages and their form/action handlers.

The browser UI counterpart to the /api/documents JSON API. Mutations are
Post/Redirect/Get form posts that reuse the same workflow service and permission
dependencies as the API, so document-control behavior can't drift between the
two surfaces.
"""

import hashlib
import os
import uuid
from datetime import date
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Permission, require_permission
from app.core.storage import get_storage
from app.database import get_db
from app.models import Document, DocumentVersion, EventHistory, User
from app.models.document import DocumentCategory, DocumentVersionStatus
from app.services import document_workflow
from app.services.document_workflow import WorkflowError

router = APIRouter(tags=["Pages"])

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "..", "templates")
)

settings = get_settings()

CATEGORY_VALUES = [c.value for c in DocumentCategory]


# --- helpers ---------------------------------------------------------------
def _document_or_404(db: Session, document_id: int, current_user: User) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_active.is_(True))
        .first()
    )
    if not document or document.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _version_or_404(db: Session, version_id: int, current_user: User) -> DocumentVersion:
    version = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
    if not version or version.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found"
        )
    return version


def _permission_flags(user: User) -> dict:
    """Which document action buttons the current user may see."""
    granted = getattr(user, "granted_permissions", set())
    checks = {
        "can_create": Permission.DOCUMENT_CREATE,
        "can_edit": Permission.DOCUMENT_UPDATE,
        "can_review": Permission.DOCUMENT_REVIEW,
        "can_approve": Permission.DOCUMENT_APPROVE,
        "can_obsolete": Permission.DOCUMENT_OBSOLETE,
        "can_delete": Permission.DOCUMENT_DELETE,
    }
    return {name: perm.value in granted for name, perm in checks.items()}


def _org_user_emails(db: Session, organization_id: int) -> dict[int, str]:
    users = db.query(User).filter(User.organization_id == organization_id).all()
    return {u.id: u.email for u in users}


def _to_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value not in (None, "") else None


def _redirect(document_id: int, error: Optional[str] = None) -> RedirectResponse:
    url = f"/admin/documents/{document_id}"
    if error:
        url += f"?error={quote(error)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


# --- list ------------------------------------------------------------------
@router.get("/admin/documents")
async def documents_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
    category: Optional[str] = None,
    review: Optional[str] = None,
):
    query = db.query(Document).filter(
        Document.organization_id == current_user.organization_id,
        Document.is_active.is_(True),
    )
    if category in CATEGORY_VALUES:
        query = query.filter(Document.category == category)
    if review == "due":
        query = query.filter(
            Document.next_review_date.isnot(None),
            Document.next_review_date <= date.today(),
        )
    elif review == "retention":
        query = query.filter(
            Document.retention_until.isnot(None),
            Document.retention_until <= date.today(),
        )
    documents = query.order_by(Document.updated_at.desc()).limit(200).all()
    context = {
        "request": request,
        "current_user": current_user,
        "documents": documents,
        "owner_emails": _org_user_emails(db, current_user.organization_id),
        "categories": CATEGORY_VALUES,
        "today": date.today(),
        "perms": _permission_flags(current_user),
        "filters": {"category": category or "", "review": review or ""},
    }
    template = (
        "admin/documents/_document_table.html"
        if "HX-Request" in request.headers
        else "admin/documents/list.html"
    )
    return templates.TemplateResponse(template, context)


# --- create ----------------------------------------------------------------
@router.get("/admin/documents/create")
async def document_create_page(
    request: Request,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_CREATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    users = db.query(User).filter(User.organization_id == current_user.organization_id).all()
    return templates.TemplateResponse(
        "admin/documents/create.html",
        {
            "request": request,
            "current_user": current_user,
            "users": users,
            "categories": CATEGORY_VALUES,
            "error": error,
        },
    )


@router.post("/admin/documents/create")
async def document_create_submit(
    request: Request,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_CREATE)),
    db: Session = Depends(get_db),
    document_number: str = Form(...),
    title: str = Form(...),
    category: str = Form(DocumentCategory.SOP.value),
    description: str = Form(""),
    owner_id: Optional[str] = Form(None),
    review_period_months: Optional[str] = Form(None),
    retention_period_months: Optional[str] = Form(None),
):
    if category not in CATEGORY_VALUES:
        category = DocumentCategory.SOP.value
    document = Document(
        organization_id=current_user.organization_id,
        document_number=document_number.strip(),
        title=title.strip(),
        category=category,
        description=description or None,
        owner_id=_to_int(owner_id),
        review_period_months=_to_int(review_period_months),
        retention_period_months=_to_int(retention_period_months),
        created_by=current_user.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return _redirect(document.id)


# --- edit ------------------------------------------------------------------
@router.get("/admin/documents/{document_id}/edit")
async def document_edit_page(
    document_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    document = _document_or_404(db, document_id, current_user)
    users = db.query(User).filter(User.organization_id == current_user.organization_id).all()
    return templates.TemplateResponse(
        "admin/documents/edit.html",
        {
            "request": request,
            "current_user": current_user,
            "document": document,
            "users": users,
            "categories": CATEGORY_VALUES,
            "error": error,
        },
    )


@router.post("/admin/documents/{document_id}/edit")
async def document_edit_submit(
    document_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
    document_number: str = Form(...),
    title: str = Form(...),
    category: str = Form(DocumentCategory.SOP.value),
    description: str = Form(""),
    owner_id: Optional[str] = Form(None),
    review_period_months: Optional[str] = Form(None),
    retention_period_months: Optional[str] = Form(None),
):
    document = _document_or_404(db, document_id, current_user)
    document.document_number = document_number.strip()
    document.title = title.strip()
    document.category = category if category in CATEGORY_VALUES else document.category
    document.description = description or None
    document.owner_id = _to_int(owner_id)
    document.review_period_months = _to_int(review_period_months)
    document.retention_period_months = _to_int(retention_period_months)
    db.add(document)
    db.commit()
    return _redirect(document.id)


# --- detail ----------------------------------------------------------------
@router.get("/admin/documents/{document_id}")
async def document_detail_page(
    document_id: int,
    request: Request,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
    error: Optional[str] = None,
):
    document = _document_or_404(db, document_id, current_user)
    version_ids = [v.id for v in document.versions]
    history = (
        db.query(EventHistory)
        .filter(
            (
                (EventHistory.entity_type == "document")
                & (EventHistory.entity_id == document.id)
            )
            | (
                (EventHistory.entity_type == "document_version")
                & (EventHistory.entity_id.in_(version_ids or [0]))
            )
        )
        .order_by(EventHistory.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/documents/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "document": document,
            "versions": sorted(document.versions, key=lambda v: v.version_number, reverse=True),
            "current_version": document.current_version,
            "history": history,
            "user_emails": _org_user_emails(db, current_user.organization_id),
            "perms": _permission_flags(current_user),
            "today": date.today(),
            "error": error,
        },
    )


# --- version upload --------------------------------------------------------
@router.post("/admin/documents/{document_id}/versions")
async def document_version_upload(
    document_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    change_summary: str = Form(""),
):
    document = _document_or_404(db, document_id, current_user)
    for existing in document.versions:
        if existing.status in (
            DocumentVersionStatus.DRAFT.value,
            DocumentVersionStatus.IN_REVIEW.value,
            DocumentVersionStatus.PENDING_APPROVAL.value,
        ):
            return _redirect(
                document.id, "A revision is already in progress for this document"
            )

    data = await file.read()
    if not data:
        return _redirect(document.id, "Empty file")
    if len(data) > settings.attachment_max_bytes:
        return _redirect(document.id, "File exceeds maximum allowed size")

    storage_key = f"documents/{document.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)
    next_number = max((v.version_number for v in document.versions), default=0) + 1
    version = DocumentVersion(
        organization_id=document.organization_id,
        document_id=document.id,
        version_number=next_number,
        status=DocumentVersionStatus.DRAFT.value,
        change_summary=change_summary or None,
        filename=file.filename or "upload",
        content_type=file.content_type,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        storage_key=storage_key,
        author_id=current_user.id,
    )
    db.add(version)
    db.commit()
    return _redirect(document.id)


@router.get("/admin/documents/versions/{version_id}/download")
async def document_version_download(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
) -> Response:
    version = _version_or_404(db, version_id, current_user)
    data = get_storage().load(version.storage_key)
    return Response(
        content=data,
        media_type=version.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{version.filename}"'},
    )


# --- workflow actions ------------------------------------------------------
@router.post("/admin/documents/versions/{version_id}/submit")
async def document_version_submit(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
):
    version = _version_or_404(db, version_id, current_user)
    try:
        document_workflow.submit_for_review(version)
    except WorkflowError as exc:
        return _redirect(version.document_id, exc.message)
    db.add(version)
    db.commit()
    return _redirect(version.document_id)


@router.post("/admin/documents/versions/{version_id}/review")
async def document_version_review(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_REVIEW)),
    db: Session = Depends(get_db),
):
    version = _version_or_404(db, version_id, current_user)
    try:
        document_workflow.sign_off_review(version, current_user)
    except WorkflowError as exc:
        return _redirect(version.document_id, exc.message)
    db.add(version)
    db.commit()
    return _redirect(version.document_id)


@router.post("/admin/documents/versions/{version_id}/approve")
async def document_version_approve(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_APPROVE)),
    db: Session = Depends(get_db),
):
    version = _version_or_404(db, version_id, current_user)
    document = _document_or_404(db, version.document_id, current_user)
    try:
        document_workflow.approve(db, document, version, current_user)
    except WorkflowError as exc:
        return _redirect(version.document_id, exc.message)
    db.add_all([document, version])
    db.commit()
    return _redirect(version.document_id)


@router.post("/admin/documents/versions/{version_id}/reject")
async def document_version_reject(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_REVIEW)),
    db: Session = Depends(get_db),
    reason: str = Form(...),
):
    version = _version_or_404(db, version_id, current_user)
    try:
        document_workflow.reject(db, version, current_user, reason)
    except WorkflowError as exc:
        return _redirect(version.document_id, exc.message)
    db.add(version)
    db.commit()
    return _redirect(version.document_id)


@router.post("/admin/documents/versions/{version_id}/obsolete")
async def document_version_obsolete(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_OBSOLETE)),
    db: Session = Depends(get_db),
    reason: str = Form(...),
):
    version = _version_or_404(db, version_id, current_user)
    document = _document_or_404(db, version.document_id, current_user)
    try:
        document_workflow.obsolete(db, document, version, reason)
    except WorkflowError as exc:
        return _redirect(version.document_id, exc.message)
    db.add_all([document, version])
    db.commit()
    return _redirect(version.document_id)


# --- delete ----------------------------------------------------------------
@router.post("/admin/documents/{document_id}/delete")
async def document_delete(
    document_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_DELETE)),
    db: Session = Depends(get_db),
):
    document = _document_or_404(db, document_id, current_user)
    document.is_active = False
    db.add(document)
    db.commit()
    return RedirectResponse("/admin/documents", status_code=status.HTTP_303_SEE_OTHER)
