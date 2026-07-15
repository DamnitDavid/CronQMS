"""Tests for the approval / closure / reopen workflow."""

import os
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_proins.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event, EventHistory, Organization, Role, User


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

        db = SessionLocal()
        try:
            org = Organization(name="Acme", code=f"acme-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.commit()
            cls.org_id = org.id

            cls.ids, cls.tokens = {}, {}

            def make_user(key, role):
                u = User(
                    email=f"{key}+{uuid.uuid4().hex}@example.com",
                    hashed_password=hash_password("TestPassword123!"),
                    role=role.value,
                    organization_id=cls.org_id,
                )
                db.add(u)
                db.commit()
                db.refresh(u)
                cls.ids[key] = u.id
                cls.tokens[key] = create_token_for_user(u.id, u.email)[0]

            make_user("qm", Role.QUALITY_MANAGER)         # change_status, approve, reopen
            make_user("investigator", Role.INVESTIGATOR)  # change_status, no approve/reopen
            make_user("approver", Role.APPROVER)          # change_status, approve, no reopen
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_proins.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def _make_event(self, reporter="investigator", assigned=None, status="Open"):
        db = SessionLocal()
        try:
            event = Event(
                title="Workflow event",
                event_type="Non_Conformance",
                status=status,
                priority="High",
                organization_id=self.org_id,
                reported_by=self.ids[reporter],
                assigned_to=self.ids[assigned] if assigned else None,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return event.id
        finally:
            db.close()

    def _patch(self, event_id, status, who):
        return self.client.patch(
            f"/api/events/{event_id}/status", json={"status": status}, headers=self._auth(who)
        )

    # --- happy path --------------------------------------------------------
    def test_full_lifecycle_open_to_closed(self):
        event_id = self._make_event(reporter="investigator", assigned="investigator")
        self.assertEqual(self._patch(event_id, "In_Progress", "investigator").status_code, 200)
        self.assertEqual(self._patch(event_id, "Resolved", "investigator").status_code, 200)
        # Independent approver closes.
        resp = self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Closed")
        self.assertEqual(body["closed_by"], self.ids["approver"])
        self.assertIsNotNone(body["closed_at"])

    # --- workflow guards ---------------------------------------------------
    def test_cannot_jump_open_to_closed_via_status(self):
        event_id = self._make_event()
        self.assertEqual(self._patch(event_id, "Closed", "qm").status_code, 400)

    def test_cannot_skip_investigation_open_to_resolved(self):
        event_id = self._make_event()
        self.assertEqual(self._patch(event_id, "Resolved", "qm").status_code, 400)

    def test_close_requires_resolved(self):
        event_id = self._make_event(status="Open")
        resp = self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))
        self.assertEqual(resp.status_code, 400)

    # --- distinct-approver rule -------------------------------------------
    def test_reporter_cannot_close(self):
        # Approver is also the reporter -> must be blocked despite the permission.
        event_id = self._make_event(reporter="approver", status="Resolved")
        resp = self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))
        self.assertEqual(resp.status_code, 403)

    def test_investigator_assignee_cannot_close(self):
        # Approver is the assigned investigator -> blocked.
        event_id = self._make_event(reporter="investigator", assigned="approver", status="Resolved")
        resp = self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))
        self.assertEqual(resp.status_code, 403)

    def test_non_approver_cannot_close(self):
        event_id = self._make_event(reporter="qm", assigned="qm", status="Resolved")
        resp = self.client.post(f"/api/events/{event_id}/close", headers=self._auth("investigator"))
        self.assertEqual(resp.status_code, 403)

    # --- reopen ------------------------------------------------------------
    def test_reopen_requires_reason_and_is_audited(self):
        event_id = self._make_event(reporter="investigator", assigned="investigator", status="Resolved")
        self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))

        # Missing reason -> validation error.
        self.assertEqual(
            self.client.post(f"/api/events/{event_id}/reopen", json={}, headers=self._auth("qm")).status_code,
            422,
        )

        resp = self.client.post(
            f"/api/events/{event_id}/reopen",
            json={"reason": "New evidence from field returns"},
            headers=self._auth("qm"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Open")
        self.assertIsNone(body["closed_by"])

        db = SessionLocal()
        try:
            reason_rows = db.query(EventHistory).filter(
                EventHistory.entity_type == "event",
                EventHistory.entity_id == event_id,
                EventHistory.reason == "New evidence from field returns",
            ).all()
            self.assertTrue(reason_rows, "reopen reason must be captured in the audit trail")
        finally:
            db.close()

    def test_reopen_is_privileged(self):
        event_id = self._make_event(reporter="investigator", assigned="investigator", status="Resolved")
        self.client.post(f"/api/events/{event_id}/close", headers=self._auth("approver"))
        # Approver can close but not reopen.
        resp = self.client.post(
            f"/api/events/{event_id}/reopen",
            json={"reason": "trying to reopen"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
