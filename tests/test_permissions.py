"""Role/permission matrix and organization-scoping tests.

Each protected endpoint is exercised against every role, asserting both the
allowed (non-403) and denied (403) cases, plus cross-organization isolation.
"""

import os
import unittest
import uuid

# Configure an isolated SQLite database before importing the app. Each test
# module sets this itself because ``unittest discover`` imports test modules as
# top-level names, bypassing tests/__init__.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_proins.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event, Organization, Role, User

# Which roles are granted each action. Everything not listed must get 403.
ALLOWED = {
    "event_create": {Role.ADMIN, Role.QUALITY_MANAGER, Role.INVESTIGATOR},
    "event_read": {Role.ADMIN, Role.QUALITY_MANAGER, Role.INVESTIGATOR, Role.APPROVER, Role.VIEWER},
    "event_update": {Role.ADMIN, Role.QUALITY_MANAGER, Role.INVESTIGATOR},
    "event_change_status": {Role.ADMIN, Role.QUALITY_MANAGER, Role.INVESTIGATOR, Role.APPROVER},
    "event_delete": {Role.ADMIN, Role.QUALITY_MANAGER},
    "user_manage": {Role.ADMIN},
    "dashboard_view": {Role.ADMIN, Role.QUALITY_MANAGER, Role.INVESTIGATOR, Role.APPROVER, Role.VIEWER},
}


class PermissionMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

        db = SessionLocal()
        try:
            org = Organization(name="Acme Manufacturing", code=f"acme-{uuid.uuid4().hex[:8]}")
            other_org = Organization(name="Rival Corp", code=f"rival-{uuid.uuid4().hex[:8]}")
            db.add_all([org, other_org])
            db.commit()
            # Capture ids as plain ints; the ORM objects detach once the
            # session closes.
            cls.org_id = org.id
            cls.other_org_id = other_org.id

            # One user per role, all in the primary org.
            cls.tokens = {}
            cls.user_ids = {}
            for role in Role:
                user = User(
                    email=f"{role.value.lower()}+{uuid.uuid4().hex}@example.com",
                    hashed_password=hash_password("TestPassword123!"),
                    role=role.value,
                    organization_id=cls.org_id,
                )
                db.add(user)
                db.commit()
                db.refresh(user)
                token, _ = create_token_for_user(user.id, user.email)
                cls.tokens[role] = token
                cls.user_ids[role] = user.id

            # An admin in the *other* org, for cross-org isolation checks.
            other = User(
                email=f"other-admin+{uuid.uuid4().hex}@example.com",
                hashed_password=hash_password("TestPassword123!"),
                role=Role.ADMIN.value,
                organization_id=cls.other_org_id,
            )
            db.add(other)
            db.commit()
            db.refresh(other)
            cls.other_admin_token, _ = create_token_for_user(other.id, other.email)
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_proins.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    # --- helpers -----------------------------------------------------------
    def _auth(self, role):
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    def _make_event(self, organization_id=None):
        """Insert an event directly and return its id."""
        db = SessionLocal()
        try:
            event = Event(
                title="Seed nonconformance",
                event_type="Non_Conformance",
                status="Open",
                priority="Medium",
                organization_id=organization_id or self.org_id,
                reported_by=self.user_ids[Role.ADMIN],
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            return event.id
        finally:
            db.close()

    def _assert_allowed_denied(self, action, call):
        """Run ``call(role)`` for every role and assert against the ALLOWED map.

        ``call`` returns an HTTP response. Allowed roles must not get 403;
        denied roles must get exactly 403.
        """
        for role in Role:
            with self.subTest(action=action, role=role.value):
                resp = call(role)
                if role in ALLOWED[action]:
                    self.assertNotEqual(
                        resp.status_code, 403,
                        f"{role.value} should be allowed to {action}, got {resp.status_code}",
                    )
                else:
                    self.assertEqual(
                        resp.status_code, 403,
                        f"{role.value} should be denied {action}, got {resp.status_code}",
                    )

    # --- event endpoints ---------------------------------------------------
    def test_event_create(self):
        payload = {"title": "New event", "priority": "Medium", "event_type": "Non_Conformance"}
        self._assert_allowed_denied(
            "event_create",
            lambda role: self.client.post("/api/events/", json=payload, headers=self._auth(role)),
        )

    def test_event_read_list(self):
        self._assert_allowed_denied(
            "event_read",
            lambda role: self.client.get("/api/events/", headers=self._auth(role)),
        )

    def test_event_read_detail(self):
        event_id = self._make_event()
        self._assert_allowed_denied(
            "event_read",
            lambda role: self.client.get(f"/api/events/{event_id}", headers=self._auth(role)),
        )

    def test_event_update(self):
        self._assert_allowed_denied(
            "event_update",
            lambda role: self.client.put(
                f"/api/events/{self._make_event()}",
                json={"title": "Edited title"},
                headers=self._auth(role),
            ),
        )

    def test_event_change_status(self):
        self._assert_allowed_denied(
            "event_change_status",
            lambda role: self.client.patch(
                f"/api/events/{self._make_event()}/status",
                json={"status": "In_Progress"},
                headers=self._auth(role),
            ),
        )

    def test_event_delete(self):
        self._assert_allowed_denied(
            "event_delete",
            lambda role: self.client.delete(
                f"/api/events/{self._make_event()}", headers=self._auth(role)
            ),
        )

    # --- user management ---------------------------------------------------
    def test_user_manage(self):
        self._assert_allowed_denied(
            "user_manage",
            lambda role: self.client.get("/api/users/", headers=self._auth(role)),
        )

    # --- dashboard ---------------------------------------------------------
    def test_dashboard_view(self):
        self._assert_allowed_denied(
            "dashboard_view",
            lambda role: self.client.get("/api/stats", headers=self._auth(role)),
        )

    # --- cross-organization isolation -------------------------------------
    def test_cross_org_event_is_not_found(self):
        event_id = self._make_event(organization_id=self.org_id)
        # An admin in another org has EVENT_READ permission but must not see it.
        resp = self.client.get(
            f"/api/events/{event_id}",
            headers={"Authorization": f"Bearer {self.other_admin_token}"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_list_events_is_org_scoped(self):
        self._make_event(organization_id=self.org_id)
        resp = self.client.get(
            "/api/events/",
            headers={"Authorization": f"Bearer {self.other_admin_token}"},
        )
        self.assertEqual(resp.status_code, 200)
        # The other org has no events of its own.
        body = resp.json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["total"], 0)

    def test_unauthenticated_is_rejected(self):
        self.assertIn(self.client.get("/api/events/").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
