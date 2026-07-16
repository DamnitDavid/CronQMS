"""Document Control endpoints — versioned controlled documents with a two-stage
review/approval workflow and retention tracking.

A document's content lives in versions; the workflow (submit/review/approve/
reject/obsolete) operates on a single version and is shared with the browser UI
via ``app/services/document_workflow.py`` so both entry points enforce identical
rules (segregation of duties, single-effective-version supersession).
"""

import hashlib
import uuid
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.permissions import Permission, require_permission
from app.core.storage import get_storage
from app.database import get_db
from app.models import Document, DocumentVersion, User
from app.models.document import DocumentVersionStatus
from app.schemas.document import (
    DocumentCreate,
    DocumentObsolete,
    DocumentReject,
    DocumentResponse,
    DocumentUpdate,
    DocumentVersionResponse,
)
from app.services import document_workflow
from app.services.document_workflow import WorkflowError

router = APIRouter(prefix="/api/documents", tags=["Documents"])

settings = get_settings()

# Document fields settable straight from the update payload.
_SCALAR_FIELDS = {
    "document_number",
    "title",
    "description",
    "owner_id",
    "review_period_months",
    "retention_period_months",
}


def _require_organization(current_user: User) -> int:
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to an organization",
        )
    return current_user.organization_id


def _get_document_in_org(db: Session, document_id: int, current_user: User) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.is_active.is_(True))
        .first()
    )
    if not document or document.organization_id != current_user.organization_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


def _get_version_in_org(db: Session, version_id: int, current_user: User) -> DocumentVersion:
    version = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
    if not version or version.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found"
        )
    return version


def _store_upload(document: Document, file: UploadFile, data: bytes) -> str:
    """Persist an uploaded blob and return its storage key."""
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")
    if len(data) > settings.attachment_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size",
        )
    storage_key = f"documents/{document.id}/{uuid.uuid4().hex}"
    get_storage().save(storage_key, data)
    return storage_key


def _next_version_number(document: Document) -> int:
    return max((v.version_number for v in document.versions), default=0) + 1


@router.post("/", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_CREATE)),
    db: Session = Depends(get_db),
) -> Document:
    """Register a controlled document (metadata only; add a version to attach a file)."""
    organization_id = _require_organization(current_user)
    document = Document(
        organization_id=organization_id,
        document_number=payload.document_number,
        title=payload.title,
        category=payload.category.value,
        description=payload.description,
        owner_id=payload.owner_id,
        review_period_months=payload.review_period_months,
        retention_period_months=payload.retention_period_months,
        created_by=current_user.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.get("/", response_model=List[DocumentResponse])
async def list_documents(
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None),
    owner_id: Optional[int] = Query(None),
    due_for_review: bool = Query(False),
    past_retention: bool = Query(False),
) -> List[Document]:
    query = db.query(Document).filter(
        Document.organization_id == current_user.organization_id,
        Document.is_active.is_(True),
    )
    if category:
        query = query.filter(Document.category == category)
    if owner_id is not None:
        query = query.filter(Document.owner_id == owner_id)
    if due_for_review:
        query = query.filter(
            Document.next_review_date.isnot(None),
            Document.next_review_date <= date.today(),
        )
    if past_retention:
        query = query.filter(
            Document.retention_until.isnot(None),
            Document.retention_until <= date.today(),
        )
    return (
        query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
) -> Document:
    return _get_document_in_org(db, document_id, current_user)


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
) -> Document:
    document = _get_document_in_org(db, document_id, current_user)
    update_data = payload.model_dump(exclude_unset=True)
    if "category" in update_data and update_data["category"] is not None:
        document.category = update_data.pop("category").value
    for key, value in update_data.items():
        if key in _SCALAR_FIELDS:
            setattr(document, key, value)
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_DELETE)),
    db: Session = Depends(get_db),
) -> Response:
    document = _get_document_in_org(db, document_id, current_user)
    document.is_active = False
    db.add(document)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    document_id: int,
    file: UploadFile = File(...),
    change_summary: Optional[str] = Form(None),
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    """Upload a new Draft revision of a document.

    The revision enters the workflow at Draft; it becomes effective only after
    review and approval, at which point it supersedes the prior effective one.
    """
    document = _get_document_in_org(db, document_id, current_user)

    # Guard against two drafts in flight at once.
    for existing in document.versions:
        if existing.status in (
            DocumentVersionStatus.DRAFT.value,
            DocumentVersionStatus.IN_REVIEW.value,
            DocumentVersionStatus.PENDING_APPROVAL.value,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A revision is already in progress for this document",
            )

    data = await file.read()
    storage_key = _store_upload(document, file, data)
    version = DocumentVersion(
        organization_id=document.organization_id,
        document_id=document.id,
        version_number=_next_version_number(document),
        status=DocumentVersionStatus.DRAFT.value,
        change_summary=change_summary,
        filename=file.filename or "upload",
        content_type=file.content_type,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        storage_key=storage_key,
        author_id=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.get("/versions/{version_id}/download")
async def download_version(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_READ)),
    db: Session = Depends(get_db),
) -> Response:
    version = _get_version_in_org(db, version_id, current_user)
    data = get_storage().load(version.storage_key)
    return Response(
        content=data,
        media_type=version.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{version.filename}"'},
    )


@router.post("/versions/{version_id}/submit", response_model=DocumentVersionResponse)
async def submit_version(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_UPDATE)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    version = _get_version_in_org(db, version_id, current_user)
    try:
        document_workflow.submit_for_review(version)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.post("/versions/{version_id}/review", response_model=DocumentVersionResponse)
async def review_version(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_REVIEW)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    version = _get_version_in_org(db, version_id, current_user)
    try:
        document_workflow.sign_off_review(version, current_user)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.post("/versions/{version_id}/approve", response_model=DocumentVersionResponse)
async def approve_version(
    version_id: int,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_APPROVE)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    version = _get_version_in_org(db, version_id, current_user)
    document = _get_document_in_org(db, version.document_id, current_user)
    try:
        document_workflow.approve(db, document, version, current_user)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add_all([document, version])
    db.commit()
    db.refresh(version)
    return version


@router.post("/versions/{version_id}/reject", response_model=DocumentVersionResponse)
async def reject_version(
    version_id: int,
    payload: DocumentReject,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_REVIEW)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    version = _get_version_in_org(db, version_id, current_user)
    try:
        document_workflow.reject(db, version, current_user, payload.reason)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.post("/versions/{version_id}/obsolete", response_model=DocumentVersionResponse)
async def obsolete_version(
    version_id: int,
    payload: DocumentObsolete,
    current_user: User = Depends(require_permission(Permission.DOCUMENT_OBSOLETE)),
    db: Session = Depends(get_db),
) -> DocumentVersion:
    version = _get_version_in_org(db, version_id, current_user)
    document = _get_document_in_org(db, version.document_id, current_user)
    try:
        document_workflow.obsolete(db, document, version, payload.reason)
    except WorkflowError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    db.add_all([document, version])
    db.commit()
    db.refresh(version)
    return version
