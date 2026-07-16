"""Tests for Document Control: versioning, two-stage approval workflow with
segregation of duties, retention tracking, audit trail, and org scoping."""

import os
import unittest
import uuid
from datetime import date, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_documents.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Document, DocumentVersion, EventHistory, Organization, Role, User


class DocumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

        db = SessionLocal()
        try:
            org = Organization(name="Acme", code=f"acme-{uuid.uuid4().hex[:8]}")
            other = Organization(name="Rival", code=f"rival-{uuid.uuid4().hex[:8]}")
            db.add_all([org, other])
            db.commit()
            cls.org_id, cls.other_org_id = org.id, other.id

            cls.ids = {}
            cls.tokens = {}

            def make_user(key, role, org_id):
                u = User(
                    email=f"{key}+{uuid.uuid4().hex}@example.com",
                    hashed_password=hash_password("TestPassword123!"),
                    role=role.value,
                    organization_id=org_id,
                )
                db.add(u)
                db.commit()
                db.refresh(u)
                cls.ids[key] = u.id
                cls.tokens[key] = create_token_for_user(u.id, u.email)[0]

            # author uploads/submits; reviewer signs off; approver approves.
            # Three distinct people, so full segregation of duties can pass.
            make_user("author", Role.QUALITY_MANAGER, cls.org_id)
            make_user("reviewer", Role.APPROVER, cls.org_id)
            make_user("approver", Role.QUALITY_MANAGER, cls.org_id)
            make_user("viewer", Role.VIEWER, cls.org_id)
            make_user("other_admin", Role.ADMIN, cls.other_org_id)
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_documents.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _create_document(self, who="author", **overrides):
        payload = {
            "document_number": f"SOP-{uuid.uuid4().hex[:6]}",
            "title": "Cleaning procedure",
            "category": "SOP",
            "review_period_months": 12,
            "retention_period_months": 24,
        }
        payload.update(overrides)
        return self.client.post("/api/documents/", json=payload, headers=self._auth(who))

    def _upload_version(self, document_id, who="author", summary="Initial draft"):
        return self.client.post(
            f"/api/documents/{document_id}/versions",
            files={"file": ("proc.pdf", b"%PDF-1.4 content", "application/pdf")},
            data={"change_summary": summary},
            headers=self._auth(who),
        )

    def _drive_to_effective(self, document_id):
        """Author uploads + submits, reviewer signs off, approver approves."""
        version_id = self._upload_version(document_id).json()["id"]
        self.client.post(
            f"/api/documents/versions/{version_id}/submit", headers=self._auth("author")
        )
        self.client.post(
            f"/api/documents/versions/{version_id}/review", headers=self._auth("reviewer")
        )
        resp = self.client.post(
            f"/api/documents/versions/{version_id}/approve", headers=self._auth("approver")
        )
        return version_id, resp

    # --- happy path --------------------------------------------------------
    def test_full_workflow_makes_effective_and_is_audited(self):
        doc_id = self._create_document().json()["id"]
        version_id, resp = self._drive_to_effective(doc_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Effective")
        self.assertEqual(body["reviewed_by"], self.ids["reviewer"])
        self.assertEqual(body["approved_by"], self.ids["approver"])
        self.assertIsNotNone(body["effective_date"])

        doc = self.client.get(f"/api/documents/{doc_id}", headers=self._auth("author")).json()
        self.assertEqual(doc["status"], "Effective")
        # Review period of 12 months schedules the next review a year out.
        self.assertEqual(doc["next_review_date"], str(_add_months(date.today(), 12)))

        db = SessionLocal()
        try:
            history = db.query(EventHistory).filter(
                EventHistory.entity_type == "document_version",
                EventHistory.entity_id == version_id,
            ).all()
            self.assertTrue(history, "version transitions must be audited")
        finally:
            db.close()

    def test_new_revision_supersedes_previous(self):
        doc_id = self._create_document().json()["id"]
        v1_id, _ = self._drive_to_effective(doc_id)
        v2_id, resp = self._drive_to_effective(doc_id)
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["version_number"], 2)

        db = SessionLocal()
        try:
            v1 = db.get(DocumentVersion, v1_id)
            v2 = db.get(DocumentVersion, v2_id)
            self.assertEqual(v1.status, "Obsolete", "prior effective version must be superseded")
            self.assertEqual(v2.status, "Effective")
        finally:
            db.close()

    def test_obsolete_sets_retention_and_is_audited(self):
        doc_id = self._create_document().json()["id"]
        version_id, _ = self._drive_to_effective(doc_id)
        resp = self.client.post(
            f"/api/documents/versions/{version_id}/obsolete",
            json={"reason": "Superseded by external standard"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Obsolete")

        doc = self.client.get(f"/api/documents/{doc_id}", headers=self._auth("author")).json()
        self.assertEqual(doc["retention_until"], str(_add_months(date.today(), 24)))

        db = SessionLocal()
        try:
            reason_rows = db.query(EventHistory).filter(
                EventHistory.entity_type == "document_version",
                EventHistory.entity_id == version_id,
                EventHistory.reason == "Superseded by external standard",
            ).all()
            self.assertTrue(reason_rows, "obsolete reason must be captured in the audit trail")
        finally:
            db.close()

    # --- segregation of duties --------------------------------------------
    def test_author_cannot_review_own_version(self):
        doc_id = self._create_document().json()["id"]
        version_id = self._upload_version(doc_id).json()["id"]
        self.client.post(
            f"/api/documents/versions/{version_id}/submit", headers=self._auth("author")
        )
        # The author holds DOCUMENT_REVIEW (Quality Manager) but is the author.
        resp = self.client.post(
            f"/api/documents/versions/{version_id}/review", headers=self._auth("author")
        )
        self.assertEqual(resp.status_code, 403)

    def test_reviewer_cannot_also_approve(self):
        doc_id = self._create_document().json()["id"]
        version_id = self._upload_version(doc_id).json()["id"]
        self.client.post(
            f"/api/documents/versions/{version_id}/submit", headers=self._auth("author")
        )
        self.client.post(
            f"/api/documents/versions/{version_id}/review", headers=self._auth("reviewer")
        )
        # The reviewer (Approver role) also holds DOCUMENT_APPROVE, but approving
        # a version they reviewed breaks segregation of duties.
        resp = self.client.post(
            f"/api/documents/versions/{version_id}/approve", headers=self._auth("reviewer")
        )
        self.assertEqual(resp.status_code, 403)

    def test_reject_returns_to_draft_with_reason(self):
        doc_id = self._create_document().json()["id"]
        version_id = self._upload_version(doc_id).json()["id"]
        self.client.post(
            f"/api/documents/versions/{version_id}/submit", headers=self._auth("author")
        )
        resp = self.client.post(
            f"/api/documents/versions/{version_id}/reject",
            json={"reason": "Missing safety section"},
            headers=self._auth("reviewer"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Draft")

    # --- permissions & scoping --------------------------------------------
    def test_viewer_cannot_create_but_can_read(self):
        self.assertEqual(self._create_document(who="viewer").status_code, 403)
        self.assertEqual(
            self.client.get("/api/documents/", headers=self._auth("viewer")).status_code, 200
        )

    def test_cross_org_document_not_found(self):
        doc_id = self._create_document().json()["id"]
        resp = self.client.get(f"/api/documents/{doc_id}", headers=self._auth("other_admin"))
        self.assertEqual(resp.status_code, 404)

    def test_second_draft_blocked_while_one_in_progress(self):
        doc_id = self._create_document().json()["id"]
        self._upload_version(doc_id)
        resp = self._upload_version(doc_id)
        self.assertEqual(resp.status_code, 409)

    def test_due_for_review_and_past_retention_filters(self):
        doc_id = self._create_document().json()["id"]
        # Backdate the review/retention dates to make the document appear in the
        # respective "overdue" list filters.
        db = SessionLocal()
        try:
            doc = db.get(Document, doc_id)
            doc.next_review_date = date.today() - timedelta(days=1)
            doc.retention_until = date.today() - timedelta(days=1)
            db.commit()
        finally:
            db.close()

        due = self.client.get(
            "/api/documents/?due_for_review=true", headers=self._auth("author")
        ).json()
        self.assertIn(doc_id, [d["id"] for d in due])

        past = self.client.get(
            "/api/documents/?past_retention=true", headers=self._auth("author")
        ).json()
        self.assertIn(doc_id, [d["id"] for d in past])


def _add_months(start: date, months: int) -> date:
    import calendar

    zero_based = start.month - 1 + months
    year = start.year + zero_based // 12
    month = zero_based % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


if __name__ == "__main__":
    unittest.main()
