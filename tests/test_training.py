"""Tests for Training Management: employees, courses, records, certification,
expiry computation, audit trail, permission gating, and org scoping."""

import os
import unittest
import uuid
from datetime import date, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_training.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Document, EventHistory, Organization, Role, TrainingRecord, User


class TrainingTest(unittest.TestCase):
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
                    email=f"{role.value.lower()}+{uuid.uuid4().hex}@example.com",
                    hashed_password=hash_password("TestPassword123!"),
                    role=role.value,
                    organization_id=org_id,
                )
                db.add(u)
                db.commit()
                db.refresh(u)
                cls.ids[key] = u.id
                cls.tokens[key] = create_token_for_user(u.id, u.email)[0]

            make_user("qm", Role.QUALITY_MANAGER, cls.org_id)
            make_user("investigator", Role.INVESTIGATOR, cls.org_id)
            make_user("approver", Role.APPROVER, cls.org_id)
            make_user("viewer", Role.VIEWER, cls.org_id)
            make_user("other_admin", Role.ADMIN, cls.other_org_id)

            # An in-org SOP document to link a course to, and a cross-org one.
            doc = Document(
                organization_id=cls.org_id,
                document_number=f"SOP-{uuid.uuid4().hex[:6]}",
                title="Line 1 Operating Procedure",
                category="SOP",
                created_by=cls.ids["qm"],
            )
            other_doc = Document(
                organization_id=cls.other_org_id,
                document_number=f"SOP-{uuid.uuid4().hex[:6]}",
                title="Rival SOP",
                category="SOP",
                created_by=cls.ids["other_admin"],
            )
            db.add_all([doc, other_doc])
            db.commit()
            cls.doc_id, cls.other_doc_id = doc.id, other_doc.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_training.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _create_employee(self, who="qm", **overrides):
        payload = {"full_name": f"Operator {uuid.uuid4().hex[:6]}"}
        payload.update(overrides)
        return self.client.post("/api/training/employees", json=payload, headers=self._auth(who))

    def _create_course(self, who="qm", **overrides):
        payload = {"code": f"TRN-{uuid.uuid4().hex[:6]}", "title": "SOP Training"}
        payload.update(overrides)
        return self.client.post("/api/training/courses", json=payload, headers=self._auth(who))

    # --- employees ---------------------------------------------------------
    def test_employee_crud_and_is_audited(self):
        resp = self._create_employee(employee_number="B-100", department="Packaging")
        self.assertEqual(resp.status_code, 201, resp.text)
        emp = resp.json()
        self.assertEqual(emp["department"], "Packaging")
        self.assertTrue(emp["is_active"])

        db = SessionLocal()
        try:
            history = db.query(EventHistory).filter(
                EventHistory.entity_type == "employee",
                EventHistory.entity_id == emp["id"],
            ).all()
            self.assertTrue(history, "Employee creation must be audited")
            self.assertTrue(all(h.actor_id == self.ids["qm"] for h in history))
        finally:
            db.close()

        # Update + soft delete.
        resp = self.client.put(
            f"/api/training/employees/{emp['id']}",
            json={"job_title": "Line Lead"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["job_title"], "Line Lead")

        resp = self.client.delete(f"/api/training/employees/{emp['id']}", headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 204)
        # Soft-deleted -> now 404.
        self.assertEqual(
            self.client.get(f"/api/training/employees/{emp['id']}", headers=self._auth("qm")).status_code,
            404,
        )

    def test_viewer_cannot_create_employee_but_can_read(self):
        self.assertEqual(self._create_employee(who="viewer").status_code, 403)
        self.assertEqual(
            self.client.get("/api/training/employees", headers=self._auth("viewer")).status_code, 200
        )

    # --- courses -----------------------------------------------------------
    def test_course_with_sop_link(self):
        resp = self._create_course(document_id=self.doc_id, recertification_period_months=12)
        self.assertEqual(resp.status_code, 201, resp.text)
        course = resp.json()
        self.assertEqual(course["document_id"], self.doc_id)
        self.assertEqual(course["recertification_period_months"], 12)

    def test_course_rejects_cross_org_document(self):
        resp = self._create_course(document_id=self.other_doc_id)
        self.assertEqual(resp.status_code, 400, resp.text)

    # --- records: assignment & validation ----------------------------------
    def test_assign_requires_exactly_one_trainee(self):
        course_id = self._create_course().json()["id"]
        # Neither trainee.
        resp = self.client.post(
            "/api/training/records",
            json={"course_id": course_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        # Both trainees.
        emp_id = self._create_employee().json()["id"]
        resp = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id, "user_id": self.ids["viewer"]},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 422, resp.text)

    def test_assign_employee_and_user(self):
        course_id = self._create_course().json()["id"]
        emp_id = self._create_employee().json()["id"]

        resp = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertEqual(resp.json()["status"], "Assigned")

        resp = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "user_id": self.ids["investigator"]},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    # --- certification & expiry --------------------------------------------
    def test_certify_sets_trained_and_computes_expiry(self):
        course_id = self._create_course(recertification_period_months=6).json()["id"]
        emp_id = self._create_employee().json()["id"]
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]

        resp = self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "Jane Operator", "trained_date": "2026-01-15"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Trained")
        self.assertEqual(body["trained_by"], self.ids["qm"])
        self.assertEqual(body["trainee_acknowledgment"], "Jane Operator")
        self.assertEqual(body["trained_date"], "2026-01-15")
        self.assertEqual(body["expiry_date"], "2026-07-15")  # +6 months

    def test_certify_without_recert_has_no_expiry(self):
        course_id = self._create_course().json()["id"]
        emp_id = self._create_employee().json()["id"]
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]
        resp = self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "Bob"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["expiry_date"])

    def test_expired_effective_status(self):
        """A trained record past its expiry surfaces as Expired."""
        course_id = self._create_course(recertification_period_months=1).json()["id"]
        emp_id = self._create_employee().json()["id"]
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]
        past = (date.today() - timedelta(days=90)).isoformat()
        self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "Al", "trained_date": past},
            headers=self._auth("qm"),
        )
        db = SessionLocal()
        try:
            record = db.query(TrainingRecord).filter(TrainingRecord.id == record_id).first()
            self.assertTrue(record.is_expired)
            self.assertEqual(record.effective_status, "Expired")
        finally:
            db.close()

    def test_viewer_cannot_certify(self):
        course_id = self._create_course().json()["id"]
        emp_id = self._create_employee().json()["id"]
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]
        resp = self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "X"},
            headers=self._auth("viewer"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_approver_can_certify_but_not_assign(self):
        course_id = self._create_course().json()["id"]
        emp_id = self._create_employee().json()["id"]
        # Approver cannot assign (needs training:update).
        resp = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 403)
        # But can certify an existing record.
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]
        resp = self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "Sam"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_certify_still_works_after_course_soft_deleted(self):
        """An outstanding assignment can be certified even if its course was
        soft-deleted after assignment."""
        course_id = self._create_course(recertification_period_months=3).json()["id"]
        emp_id = self._create_employee().json()["id"]
        record_id = self.client.post(
            "/api/training/records",
            json={"course_id": course_id, "employee_id": emp_id},
            headers=self._auth("qm"),
        ).json()["id"]
        self.assertEqual(
            self.client.delete(f"/api/training/courses/{course_id}", headers=self._auth("qm")).status_code,
            204,
        )
        resp = self.client.post(
            f"/api/training/records/{record_id}/certify",
            json={"trainee_acknowledgment": "Kim"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Trained")
        self.assertIsNotNone(resp.json()["expiry_date"])

    # --- org scoping -------------------------------------------------------
    def test_cross_org_course_not_found(self):
        course_id = self._create_course().json()["id"]
        resp = self.client.get(f"/api/training/courses/{course_id}", headers=self._auth("other_admin"))
        self.assertEqual(resp.status_code, 404)

    def test_cross_org_cannot_assign_to_other_employee(self):
        emp_id = self._create_employee().json()["id"]  # in Acme
        # Other-org admin creates a course in their own org, then tries to
        # assign the Acme employee to it — employee must be 404/400.
        other_course = self.client.post(
            "/api/training/courses",
            json={"code": f"O-{uuid.uuid4().hex[:6]}", "title": "Rival course"},
            headers=self._auth("other_admin"),
        ).json()["id"]
        resp = self.client.post(
            "/api/training/records",
            json={"course_id": other_course, "employee_id": emp_id},
            headers=self._auth("other_admin"),
        )
        self.assertIn(resp.status_code, (400, 404), resp.text)


if __name__ == "__main__":
    unittest.main()
