"""Tests for Audit Management: CRUD, checklists, findings, closure gating,
audit trail, permission gating, and org scoping."""

import os
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_audit.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Capa, EventHistory, Organization, Role, User


class AuditTest(unittest.TestCase):
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

            # A CAPA in-org (for finding linkage) and one cross-org.
            capa = Capa(title="Fix process", status="Open", organization_id=cls.org_id, created_by=cls.ids["qm"])
            other_capa = Capa(title="Rival CAPA", status="Open", organization_id=cls.other_org_id, created_by=cls.ids["other_admin"])
            db.add_all([capa, other_capa])
            db.commit()
            cls.capa_id, cls.other_capa_id = capa.id, other_capa.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_audit.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _create_audit(self, who="qm", **overrides):
        payload = {"reference": f"AUD-{uuid.uuid4().hex[:6]}", "title": "Internal QMS audit"}
        payload.update(overrides)
        return self.client.post("/api/audits/", json=payload, headers=self._auth(who))

    # --- creation & audit trail -------------------------------------------
    def test_create_defaults_and_is_audited(self):
        resp = self._create_audit(lead_auditor_id=self.ids["investigator"], standard="ISO 9001:2015")
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Planned")
        self.assertEqual(body["audit_type"], "Internal")
        self.assertEqual(body["created_by"], self.ids["qm"])

        db = SessionLocal()
        try:
            history = db.query(EventHistory).filter(
                EventHistory.entity_type == "audit",
                EventHistory.entity_id == body["id"],
            ).all()
            self.assertTrue(history, "Audit creation must be audited")
            self.assertTrue(all(h.actor_id == self.ids["qm"] for h in history))
        finally:
            db.close()

    def test_viewer_cannot_create_but_can_read(self):
        self.assertEqual(self._create_audit(who="viewer").status_code, 403)
        self.assertEqual(self.client.get("/api/audits/", headers=self._auth("viewer")).status_code, 200)

    def test_cross_org_audit_not_found(self):
        audit_id = self._create_audit().json()["id"]
        resp = self.client.get(f"/api/audits/{audit_id}", headers=self._auth("other_admin"))
        self.assertEqual(resp.status_code, 404)

    # --- checklist ---------------------------------------------------------
    def test_checklist_add_and_update(self):
        audit_id = self._create_audit().json()["id"]
        resp = self.client.post(
            f"/api/audits/{audit_id}/checklist",
            json={"question": "Are records controlled?", "clause": "7.5"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        item = resp.json()
        self.assertEqual(item["result"], "Pending")

        resp = self.client.put(
            f"/api/audits/{audit_id}/checklist/{item['id']}",
            json={"result": "Minor_NC", "notes": "Two forms unsigned"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["result"], "Minor_NC")

    def test_approver_cannot_conduct(self):
        # Approver has read/close but not conduct.
        audit_id = self._create_audit().json()["id"]
        resp = self.client.post(
            f"/api/audits/{audit_id}/checklist",
            json={"question": "X?"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 403)

    # --- findings ----------------------------------------------------------
    def test_finding_lifecycle_and_capa_link(self):
        audit_id = self._create_audit().json()["id"]
        resp = self.client.post(
            f"/api/audits/{audit_id}/findings",
            json={"title": "Uncontrolled document", "severity": "Major"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        finding = resp.json()
        self.assertEqual(finding["status"], "Open")
        self.assertEqual(finding["severity"], "Major")

        # Link a same-org CAPA and close it.
        resp = self.client.put(
            f"/api/audits/{audit_id}/findings/{finding['id']}",
            json={"status": "Closed", "capa_id": self.capa_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Closed")
        self.assertEqual(resp.json()["capa_id"], self.capa_id)

    def test_finding_cross_org_capa_rejected(self):
        audit_id = self._create_audit().json()["id"]
        finding_id = self.client.post(
            f"/api/audits/{audit_id}/findings",
            json={"title": "Some finding"},
            headers=self._auth("qm"),
        ).json()["id"]
        resp = self.client.put(
            f"/api/audits/{audit_id}/findings/{finding_id}",
            json={"capa_id": self.other_capa_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 400)

    # --- closure gating ----------------------------------------------------
    def test_cannot_close_audit_with_open_findings(self):
        audit_id = self._create_audit().json()["id"]
        self.client.post(
            f"/api/audits/{audit_id}/findings",
            json={"title": "Still open finding"},
            headers=self._auth("qm"),
        )
        resp = self.client.put(
            f"/api/audits/{audit_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_close_audit_after_findings_resolved(self):
        audit_id = self._create_audit().json()["id"]
        finding_id = self.client.post(
            f"/api/audits/{audit_id}/findings",
            json={"title": "Resolve me"},
            headers=self._auth("qm"),
        ).json()["id"]
        self.client.put(
            f"/api/audits/{audit_id}/findings/{finding_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        resp = self.client.put(
            f"/api/audits/{audit_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Closed")

    def test_update_status_in_progress(self):
        audit_id = self._create_audit().json()["id"]
        resp = self.client.put(
            f"/api/audits/{audit_id}",
            json={"status": "In_Progress", "summary": "Fieldwork underway"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "In_Progress")

    def test_delete_soft_deletes(self):
        audit_id = self._create_audit().json()["id"]
        resp = self.client.delete(f"/api/audits/{audit_id}", headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            self.client.get(f"/api/audits/{audit_id}", headers=self._auth("qm")).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
