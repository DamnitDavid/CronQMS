"""Tests for the CAPA workflow: gated transitions, verify, reopen, cancel."""

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


class CapaWorkflowTest(unittest.TestCase):
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

            make_user("qm", Role.QUALITY_MANAGER)          # update, verify, reopen, cancel
            make_user("investigator", Role.INVESTIGATOR)   # update, no verify/reopen/cancel
            make_user("approver", Role.APPROVER)           # verify, no reopen/cancel
            make_user("viewer", Role.VIEWER)                # read only

            event = Event(
                title="Linked NC", event_type="Defect", status="Open",
                priority="High", organization_id=cls.org_id, reported_by=cls.ids["qm"],
            )
            db.add(event)
            db.commit()
            cls.event_id = event.id
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
        payload = {"title": "Reduce solder defects"}
        payload.update(overrides)
        resp = self.client.post("/api/capas/", json=payload, headers=self._auth(who))
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def _patch(self, capa_id, new_status, who="qm"):
        return self.client.patch(
            f"/api/capas/{capa_id}/status", json={"status": new_status}, headers=self._auth(who)
        )

    def _put(self, capa_id, fields, who="qm"):
        return self.client.put(f"/api/capas/{capa_id}", json=fields, headers=self._auth(who))

    def _advance_to_effectiveness_check(self, capa_id, owner_id=None, who="qm"):
        self._put(
            capa_id,
            {
                "initiating_cause": "Customer complaint",
                "root_cause": "Oven profile drift",
                "corrective_action": "Recalibrate oven",
                "owner_id": owner_id if owner_id is not None else self.ids["investigator"],
                "due_date": "2026-08-01",
            },
            who=who,
        )
        for target in ("Investigation", "Action_Plan", "Implementation", "Effectiveness_Check"):
            resp = self._patch(capa_id, target, who=who)
            self.assertEqual(resp.status_code, 200, resp.text)

    def _history(self, capa_id):
        db = SessionLocal()
        try:
            return (
                db.query(EventHistory)
                .filter(EventHistory.entity_type == "capa", EventHistory.entity_id == capa_id)
                .all()
            )
        finally:
            db.close()

    # --- happy path ----------------------------------------------------
    def test_happy_path_full_lifecycle_with_audit(self):
        capa_id = self._create_capa(owner_id=self.ids["investigator"])
        self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["investigator"])
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify",
            json={"outcome": "Effective"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Closed")

        status_rows = [h for h in self._history(capa_id) if h.field == "status"]
        # Draft->Investigation->Action_Plan->Implementation->Effectiveness_Check->Closed = 5 hops.
        self.assertGreaterEqual(len(status_rows), 5)

    # --- draft gate ------------------------------------------------------
    def test_draft_gate_requires_initiating_cause_or_linked_event(self):
        capa_id = self._create_capa()
        resp = self._patch(capa_id, "Investigation")
        self.assertEqual(resp.status_code, 400)

        self._put(capa_id, {"initiating_cause": "Field failure report"})
        resp = self._patch(capa_id, "Investigation")
        self.assertEqual(resp.status_code, 200, resp.text)

        capa_id2 = self._create_capa(event_ids=[self.event_id])
        resp = self._patch(capa_id2, "Investigation")
        self.assertEqual(resp.status_code, 200, resp.text)

    # --- investigation gate ----------------------------------------------
    def test_investigation_gate_requires_root_cause(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self._patch(capa_id, "Investigation")
        resp = self._patch(capa_id, "Action_Plan")
        self.assertEqual(resp.status_code, 400)

        self._put(capa_id, {"root_cause": "Worn tooling"})
        resp = self._patch(capa_id, "Action_Plan")
        self.assertEqual(resp.status_code, 200, resp.text)

    # --- action plan gate --------------------------------------------------
    def test_action_plan_gate_requires_action_owner_due_date(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self._patch(capa_id, "Investigation")
        self._put(capa_id, {"root_cause": "Worn tooling"})
        self._patch(capa_id, "Action_Plan")

        # Missing everything.
        self.assertEqual(self._patch(capa_id, "Implementation").status_code, 400)

        # Action only.
        self._put(capa_id, {"preventive_action": "Add tooling inspection"})
        self.assertEqual(self._patch(capa_id, "Implementation").status_code, 400)

        # + owner.
        self._put(capa_id, {"owner_id": self.ids["investigator"]})
        self.assertEqual(self._patch(capa_id, "Implementation").status_code, 400)

        # + due date -> passes.
        self._put(capa_id, {"due_date": "2026-09-01"})
        resp = self._patch(capa_id, "Implementation")
        self.assertEqual(resp.status_code, 200, resp.text)

    # --- implementation gate ----------------------------------------------
    def test_implementation_gate_requires_recorded_action(self):
        db = SessionLocal()
        try:
            capa = Capa(
                organization_id=self.org_id,
                title="Legacy in-progress CAPA",
                status="Implementation",
                created_by=self.ids["qm"],
            )
            db.add(capa)
            db.commit()
            db.refresh(capa)
            capa_id = capa.id
        finally:
            db.close()
        resp = self._patch(capa_id, "Effectiveness_Check")
        self.assertEqual(resp.status_code, 400)

    # --- skip / illegal transitions ---------------------------------------
    def test_cannot_skip_states(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self.assertEqual(self._patch(capa_id, "Action_Plan").status_code, 400)
        self.assertEqual(self._patch(capa_id, "Closed").status_code, 400)
        self._patch(capa_id, "Investigation")
        self.assertEqual(self._patch(capa_id, "Effectiveness_Check").status_code, 400)

    # --- backward transitions ----------------------------------------------
    def test_backward_transitions(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self._patch(capa_id, "Investigation")
        self.assertEqual(self._patch(capa_id, "Draft").status_code, 400)

        self._put(capa_id, {"root_cause": "Worn tooling"})
        self._patch(capa_id, "Action_Plan")
        resp = self._patch(capa_id, "Investigation")
        self.assertEqual(resp.status_code, 200, resp.text)

        self._patch(capa_id, "Action_Plan")
        self._put(
            capa_id,
            {
                "corrective_action": "Recalibrate",
                "owner_id": self.ids["investigator"],
                "due_date": "2026-09-01",
            },
        )
        self._patch(capa_id, "Implementation")
        resp = self._patch(capa_id, "Action_Plan")
        self.assertEqual(resp.status_code, 200, resp.text)

    # --- verify guards -------------------------------------------------
    def test_verify_only_in_effectiveness_check(self):
        capa_id = self._create_capa()
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify", json={"outcome": "Effective"}, headers=self._auth("approver")
        )
        self.assertEqual(resp.status_code, 400)

    def test_ineffective_verification_fails_capa(self):
        capa_id = self._create_capa(owner_id=self.ids["investigator"])
        self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["investigator"])
        resp = self.client.post(
            f"/api/capas/{capa_id}/verify",
            json={"outcome": "Ineffective", "reason": "Recurred within a week"},
            headers=self._auth("approver"),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "Failed_Effectiveness")
        self.assertEqual(body["verified_by"], self.ids["approver"])

        reason_rows = [h for h in self._history(capa_id) if h.reason == "Recurred within a week"]
        self.assertTrue(reason_rows)

    # --- reopen --------------------------------------------------------
    def test_reopen_from_closed_and_failed(self):
        for outcome, expected_status in (("Effective", "Closed"), ("Ineffective", "Failed_Effectiveness")):
            capa_id = self._create_capa(owner_id=self.ids["investigator"])
            self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["investigator"])
            verify_resp = self.client.post(
                f"/api/capas/{capa_id}/verify", json={"outcome": outcome}, headers=self._auth("approver")
            )
            self.assertEqual(verify_resp.json()["status"], expected_status)

            resp = self.client.post(
                f"/api/capas/{capa_id}/reopen",
                json={"reason": "New evidence surfaced"},
                headers=self._auth("qm"),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["status"], "Investigation")
            self.assertEqual(body["verification_outcome"], "Pending")
            self.assertIsNone(body["verification_date"])
            self.assertIsNone(body["verified_by"])

            reason_rows = [h for h in self._history(capa_id) if h.reason == "New evidence surfaced"]
            self.assertTrue(reason_rows, "reopen reason must be captured in the audit trail")

    def test_reopen_requires_reason_and_terminal_state(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self._patch(capa_id, "Investigation")

        resp = self.client.post(f"/api/capas/{capa_id}/reopen", json={}, headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 422)

        resp = self.client.post(
            f"/api/capas/{capa_id}/reopen", json={"reason": "test"}, headers=self._auth("qm")
        )
        self.assertEqual(resp.status_code, 400)

        cancel_resp = self.client.post(
            f"/api/capas/{capa_id}/cancel", json={"reason": "no longer needed"}, headers=self._auth("qm")
        )
        self.assertEqual(cancel_resp.status_code, 200, cancel_resp.text)
        reopen_resp = self.client.post(
            f"/api/capas/{capa_id}/reopen", json={"reason": "test"}, headers=self._auth("qm")
        )
        self.assertEqual(reopen_resp.status_code, 400)

    def test_reopen_permission(self):
        capa_id = self._create_capa(owner_id=self.ids["investigator"])
        self._advance_to_effectiveness_check(capa_id, owner_id=self.ids["investigator"])
        self.client.post(
            f"/api/capas/{capa_id}/verify", json={"outcome": "Effective"}, headers=self._auth("approver")
        )
        for who, expected in (("investigator", 403), ("approver", 403), ("qm", 200)):
            resp = self.client.post(
                f"/api/capas/{capa_id}/reopen", json={"reason": "retry"}, headers=self._auth(who)
            )
            if expected != 200:
                self.assertEqual(resp.status_code, expected, resp.text)

    # --- cancel --------------------------------------------------------
    def test_cancel_from_non_terminal_states(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        resp = self.client.post(
            f"/api/capas/{capa_id}/cancel", json={"reason": "duplicate"}, headers=self._auth("qm")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "Cancelled")

        reason_rows = [h for h in self._history(capa_id) if h.reason == "duplicate"]
        self.assertTrue(reason_rows)

        again = self.client.post(
            f"/api/capas/{capa_id}/cancel", json={"reason": "duplicate again"}, headers=self._auth("qm")
        )
        self.assertEqual(again.status_code, 400)

    def test_cancel_requires_reason(self):
        capa_id = self._create_capa()
        resp = self.client.post(f"/api/capas/{capa_id}/cancel", json={}, headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 422)

    def test_cancel_permission(self):
        capa_id = self._create_capa()
        for who in ("investigator", "viewer"):
            resp = self.client.post(
                f"/api/capas/{capa_id}/cancel", json={"reason": "test"}, headers=self._auth(who)
            )
            self.assertEqual(resp.status_code, 403)
        resp = self.client.post(
            f"/api/capas/{capa_id}/cancel", json={"reason": "test"}, headers=self._auth("qm")
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    # --- list filter ------------------------------------------------------
    def test_status_filter_accepts_new_values(self):
        capa_id = self._create_capa(initiating_cause="Field failure")
        self._patch(capa_id, "Investigation")
        resp = self.client.get("/api/capas/?status=Investigation", headers=self._auth("qm"))
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = {c["id"] for c in resp.json()}
        self.assertIn(capa_id, ids)


if __name__ == "__main__":
    unittest.main()
