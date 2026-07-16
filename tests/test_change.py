"""Tests for Change Control: CRUD, impact assessment, implementation actions,
approval gating, closure gating, audit trail, permission gating, and org
scoping."""

import os
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_change.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Capa, EventHistory, Organization, Role, User


class ChangeControlTest(unittest.TestCase):
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

            # A CAPA in-org (for action linkage) and one cross-org.
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
        db_path = os.path.join(os.getcwd(), "test_change.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _create_change(self, who="qm", **overrides):
        payload = {"reference": f"CC-{uuid.uuid4().hex[:6]}", "title": "Reroute weld station"}
        payload.update(overrides)
        return self.client.post("/api/changes/", json=payload, headers=self._auth(who))

    # --- creation & audit trail -------------------------------------------
    def test_create_defaults_and_is_audited(self):
        resp = self._create_change(owner_id=self.ids["investigator"], risk_level="High")
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Draft")
        self.assertEqual(body["change_type"], "Process")
        self.assertEqual(body["risk_level"], "High")
        self.assertEqual(body["created_by"], self.ids["qm"])

        db = SessionLocal()
        try:
            history = db.query(EventHistory).filter(
                EventHistory.entity_type == "change_request",
                EventHistory.entity_id == body["id"],
            ).all()
            self.assertTrue(history, "Change creation must be audited")
            self.assertTrue(all(h.actor_id == self.ids["qm"] for h in history))
        finally:
            db.close()

    def test_viewer_cannot_create_but_can_read(self):
        self.assertEqual(self._create_change(who="viewer").status_code, 403)
        self.assertEqual(self.client.get("/api/changes/", headers=self._auth("viewer")).status_code, 200)

    def test_cross_org_change_not_found(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.get(f"/api/changes/{change_id}", headers=self._auth("other_admin"))
        self.assertEqual(resp.status_code, 404)

    # --- impact assessment -------------------------------------------------
    def test_impact_add_and_update(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.post(
            f"/api/changes/{change_id}/impacts",
            json={"area": "Regulatory", "impact_level": "Medium", "description": "New notified body filing"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        impact = resp.json()
        self.assertEqual(impact["area"], "Regulatory")
        self.assertEqual(impact["impact_level"], "Medium")

        resp = self.client.put(
            f"/api/changes/{change_id}/impacts/{impact['id']}",
            json={"impact_level": "High", "mitigation": "Pre-submission review"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["impact_level"], "High")
        self.assertEqual(resp.json()["mitigation"], "Pre-submission review")

    def test_approver_cannot_assess(self):
        # Approver has read/approve but not assess.
        change_id = self._create_change().json()["id"]
        resp = self.client.post(
            f"/api/changes/{change_id}/impacts",
            json={"area": "Quality"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 403)

    # --- implementation actions -------------------------------------------
    def test_action_lifecycle_and_capa_link(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.post(
            f"/api/changes/{change_id}/actions",
            json={"title": "Update work instruction WI-12"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        action = resp.json()
        self.assertEqual(action["status"], "Open")

        # Link a same-org CAPA and close it.
        resp = self.client.put(
            f"/api/changes/{change_id}/actions/{action['id']}",
            json={"status": "Closed", "capa_id": self.capa_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Closed")
        self.assertEqual(resp.json()["capa_id"], self.capa_id)

    def test_action_cross_org_capa_rejected(self):
        change_id = self._create_change().json()["id"]
        action_id = self.client.post(
            f"/api/changes/{change_id}/actions",
            json={"title": "Some action"},
            headers=self._auth("qm"),
        ).json()["id"]
        resp = self.client.put(
            f"/api/changes/{change_id}/actions/{action_id}",
            json={"capa_id": self.other_capa_id},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 400)

    # --- approval gating ---------------------------------------------------
    def test_approval_requires_permission(self):
        # Investigator has update but not approve — the approval decision is
        # blocked by the status gate even though the update endpoint is reachable.
        change_id = self._create_change(who="investigator").json()["id"]
        resp = self.client.put(
            f"/api/changes/{change_id}",
            json={"status": "Approved"},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_quality_manager_can_approve(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.put(
            f"/api/changes/{change_id}",
            json={"status": "Approved"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Approved")

    # --- closure gating ----------------------------------------------------
    def test_cannot_close_change_with_open_actions(self):
        change_id = self._create_change().json()["id"]
        self.client.post(
            f"/api/changes/{change_id}/actions",
            json={"title": "Still open action"},
            headers=self._auth("qm"),
        )
        resp = self.client.put(
            f"/api/changes/{change_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 409, resp.text)

    def test_close_change_after_actions_resolved(self):
        change_id = self._create_change().json()["id"]
        action_id = self.client.post(
            f"/api/changes/{change_id}/actions",
            json={"title": "Resolve me"},
            headers=self._auth("qm"),
        ).json()["id"]
        self.client.put(
            f"/api/changes/{change_id}/actions/{action_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        resp = self.client.put(
            f"/api/changes/{change_id}",
            json={"status": "Closed"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Closed")

    def test_update_status_submitted(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.put(
            f"/api/changes/{change_id}",
            json={"status": "Submitted", "reason": "Ready for review"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Submitted")

    def test_delete_soft_deletes(self):
        change_id = self._create_change().json()["id"]
        resp = self.client.delete(f"/api/changes/{change_id}", headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            self.client.get(f"/api/changes/{change_id}", headers=self._auth("qm")).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
