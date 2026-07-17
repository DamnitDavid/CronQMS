"""Tests for the CAPA entity: CRUD, linking, verification, audit, scoping."""

import os
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Capa, Event, EventHistory, Organization, Role, User


class CapaTest(unittest.TestCase):
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

            # Capture plain ints; ORM instances detach when the session closes.
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

            event = Event(
                title="Linked NC", event_type="Defect", status="Open",
                priority="High", organization_id=cls.org_id, reported_by=cls.ids["qm"],
            )
            other_event = Event(
                title="Other org NC", event_type="Defect", status="Open",
                priority="High", organization_id=cls.other_org_id, reported_by=cls.ids["other_admin"],
            )
            db.add_all([event, other_event])
            db.commit()
            cls.event_id, cls.other_event_id = event.id, other_event.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_cronqms.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _create_capa(self, who="qm", **overrides):
        payload = {"title": "Reduce solder defects", "root_cause_category": "Process"}
        payload.update(overrides)
        return self.client.post("/api/capas/", json=payload, headers=self._auth(who))

    def _advance_to_effectiveness_check(self, capa_id, who="qm", owner_id=None):
        """Drive a freshly created CAPA to Effectiveness_Check via the gated workflow."""
        self.client.put(
            f"/api/capas/{capa_id}",
            json={
                "initiating_cause": "Customer complaint",
                "root_cause": "Oven profile drift",
                "corrective_action": "Recalibrate oven",
                "owner_id": owner_id if owner_id is not None else self.ids["investigator"],
                "due_date": "2026-08-01",
            },
            headers=self._auth(who),
        )
        for target in ("Investigation", "Action_Plan", "Implementation", "Effectiveness_Check"):
            resp = self.client.patch(
                f"/api/capas/{capa_id}/status", json={"status": target}, headers=self._auth(who)
            )
            self.assertEqual(resp.status_code, 200, resp.text)

    def test_create_links_events_and_is_audited(self):
        resp = self._create_capa(event_ids=[self.event_id], owner_id=self.ids["investigator"])
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Draft")
        self.assertEqual(body["event_ids"], [self.event_id])
        self.assertEqual(body["created_by"], self.ids["qm"])

        db = SessionLocal()
        try:
            history = db.query(EventHistory).filter(
                EventHistory.entity_type == "capa",
                EventHistory.entity_id == body["id"],
            ).all()
            self.assertTrue(history, "CAPA creation must be audited")
            self.assertTrue(all(h.actor_id == self.ids["qm"] for h in history))
        finally:
            db.close()

    def test_viewer_cannot_create_but_can_read(self):
        self.assertEqual(self._create_capa(who="viewer").status_code, 403)
        self.assertEqual(self.client.get("/api/capas/", headers=self._auth("viewer")).status_code, 200)

    def test_linking_cross_org_event_is_rejected(self):
        resp = self._create_capa(event_ids=[self.other_event_id])
        self.assertEqual(resp.status_code, 400)

    def test_cross_org_capa_not_found(self):
        capa_id = self._create_capa().json()["id"]
        resp = self.client.get(f"/api/capas/{capa_id}", headers=self._auth("other_admin"))
        self.assertEqual(resp.status_code, 404)

    def test_update_ignores_status_field_and_relinks(self):
        capa_id = self._create_capa(event_ids=[self.event_id]).json()["id"]
        resp = self.client.put(
            f"/api/capas/{capa_id}",
            json={"status": "Implementation", "corrective_action": "Re-flow oven recalibrated", "event_ids": []},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Draft")
        self.assertEqual(body["event_ids"], [])
        self.assertEqual(body["corrective_action"], "Re-flow oven recalibrated")

    def test_effective_verification_closes_capa(self):
        # Owner is the investigator; approver (independent) verifies.
        capa_id = self._create_capa(owner_id=self.ids["investigator"]).json()["id"]
        self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["investigator"])
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify",
            json={"outcome": "Effective", "reason": "No recurrence over 3 lots"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Closed")
        self.assertEqual(body["verification_outcome"], "Effective")
        self.assertEqual(body["verified_by"], self.ids["approver"])
        self.assertIsNotNone(body["verification_date"])

        db = SessionLocal()
        try:
            reason_rows = db.query(EventHistory).filter(
                EventHistory.entity_type == "capa",
                EventHistory.entity_id == capa_id,
                EventHistory.reason == "No recurrence over 3 lots",
            ).all()
            self.assertTrue(reason_rows, "verification reason must be captured in the audit trail")
        finally:
            db.close()

    def test_owner_cannot_verify_own_capa(self):
        # QM owns the CAPA and also holds CAPA_VERIFY; independence must block it.
        capa_id = self._create_capa(who="qm", owner_id=self.ids["qm"]).json()["id"]
        self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["qm"])
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify",
            json={"outcome": "Effective"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_investigator_cannot_verify(self):
        capa_id = self._create_capa().json()["id"]
        self._advance_to_effectiveness_check(capa_id)
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify",
            json={"outcome": "Effective"},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
