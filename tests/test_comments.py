"""Tests for the per-event comment thread."""

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
from app.models import Event, Organization, Role, User


class CommentsTest(unittest.TestCase):
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

            cls.ids, cls.tokens = {}, {}

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

            make_user("investigator", Role.INVESTIGATOR, cls.org_id)
            make_user("viewer", Role.VIEWER, cls.org_id)
            make_user("other", Role.INVESTIGATOR, cls.other_org_id)

            event = Event(
                title="Discuss me", event_type="Defect", status="Open",
                priority="High", organization_id=cls.org_id, reported_by=cls.ids["investigator"],
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
        db_path = os.path.join(os.getcwd(), "test_proins.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def test_post_and_list_thread_in_order(self):
        for text in ("First observation", "Second observation"):
            resp = self.client.post(
                f"/api/events/{self.event_id}/comments",
                json={"body": text},
                headers=self._auth("investigator"),
            )
            self.assertEqual(resp.status_code, 201, resp.text)

        listing = self.client.get(
            f"/api/events/{self.event_id}/comments", headers=self._auth("viewer")
        )
        self.assertEqual(listing.status_code, 200)
        bodies = [c["body"] for c in listing.json()]
        self.assertEqual(bodies[:2], ["First observation", "Second observation"])
        self.assertTrue(all(c["author_id"] == self.ids["investigator"] for c in listing.json()))

    def test_viewer_cannot_comment(self):
        resp = self.client.post(
            f"/api/events/{self.event_id}/comments",
            json={"body": "I should not be allowed"},
            headers=self._auth("viewer"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_empty_comment_rejected(self):
        resp = self.client.post(
            f"/api/events/{self.event_id}/comments",
            json={"body": ""},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 422)

    def test_cross_org_event_comment_not_found(self):
        resp = self.client.post(
            f"/api/events/{self.event_id}/comments",
            json={"body": "outsider"},
            headers=self._auth("other"),
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
