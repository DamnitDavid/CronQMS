"""Tests for the append-only event_history audit trail."""

import os
import unittest
import uuid

# Configure an isolated SQLite database before importing the app. Each test
# module sets this itself because ``unittest discover`` imports test modules as
# top-level names, bypassing tests/__init__.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.config import get_settings

get_settings.cache_clear()

from app.core.audit import set_audit_actor, set_audit_reason
from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event, EventHistory, Organization, Role, User


class AuditTrailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        # create_all fires the metadata after_create hooks, installing the
        # append-only triggers for SQLite.
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)

        db = SessionLocal()
        try:
            org = Organization(name="Acme", code=f"acme-{uuid.uuid4().hex[:8]}")
            db.add(org)
            db.commit()
            cls.org_id = org.id

            user = User(
                email=f"inv+{uuid.uuid4().hex}@example.com",
                hashed_password=hash_password("TestPassword123!"),
                role=Role.INVESTIGATOR.value,
                organization_id=cls.org_id,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            cls.user_id = user.id
            cls.token, _ = create_token_for_user(user.id, user.email)
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_cronqms.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _history(self, db, entity_id, field=None):
        q = db.query(EventHistory).filter(
            EventHistory.entity_type == "event",
            EventHistory.entity_id == entity_id,
        )
        if field:
            q = q.filter(EventHistory.field == field)
        return q.all()

    def test_insert_is_audited_field_level(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Cracked housing",
                event_type="Defect",
                status="Open",
                priority="High",
                organization_id=self.org_id,
                reported_by=self.user_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            event_id = event.id

            rows = {r.field: r for r in self._history(db, event_id)}
            # Non-null audited fields are each recorded with no prior value.
            self.assertIn("status", rows)
            self.assertIsNone(rows["status"].old_value)
            self.assertEqual(rows["status"].new_value, "Open")
            self.assertEqual(rows["title"].new_value, "Cracked housing")
            self.assertEqual(rows["priority"].new_value, "High")
        finally:
            db.close()

    def test_update_records_old_and_new_with_reason_and_actor(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Loose fastener",
                event_type="Defect",
                status="Open",
                priority="Medium",
                organization_id=self.org_id,
                reported_by=self.user_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            event_id = event.id

            # Deposit actor + reason for this mutation, then change status.
            set_audit_actor(db, self.user_id)
            set_audit_reason(db, "Investigation started")
            event.status = "In_Progress"
            db.commit()

            status_rows = self._history(db, event_id, field="status")
            # One creation row (Open) + one transition row.
            transition = [r for r in status_rows if r.old_value == "Open"]
            self.assertEqual(len(transition), 1)
            row = transition[0]
            self.assertEqual(row.new_value, "In_Progress")
            self.assertEqual(row.reason, "Investigation started")
            self.assertEqual(row.actor_id, self.user_id)
        finally:
            db.close()

    def test_soft_delete_is_audited(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Scrap batch",
                event_type="Defect",
                status="Open",
                priority="Low",
                organization_id=self.org_id,
                reported_by=self.user_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            event_id = event.id

            event.is_active = False
            db.commit()

            rows = self._history(db, event_id, field="is_active")
            change = [r for r in rows if r.old_value == "True" and r.new_value == "False"]
            self.assertEqual(len(change), 1)
        finally:
            db.close()

    def test_actor_captured_through_api(self):
        # The authenticated request path must attribute the change to the user.
        resp = self.client.post(
            "/api/events/",
            json={"title": "API reported event", "priority": "Medium", "event_type": "Defect"},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(resp.status_code, 201)
        event_id = resp.json()["id"]

        db = SessionLocal()
        try:
            rows = self._history(db, event_id, field="status")
            self.assertTrue(rows)
            self.assertTrue(all(r.actor_id == self.user_id for r in rows))
        finally:
            db.close()

    def test_history_rejects_update_at_db_level(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Immutable check",
                event_type="Defect",
                status="Open",
                priority="Medium",
                organization_id=self.org_id,
                reported_by=self.user_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            history_id = self._history(db, event.id)[0].id
        finally:
            db.close()

        with engine.connect() as conn:
            with self.assertRaises(DatabaseError):
                conn.execute(
                    text("UPDATE event_history SET new_value = 'tampered' WHERE id = :id"),
                    {"id": history_id},
                )
                conn.commit()

    def test_history_rejects_delete_at_db_level(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Immutable delete check",
                event_type="Defect",
                status="Open",
                priority="Medium",
                organization_id=self.org_id,
                reported_by=self.user_id,
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            history_id = self._history(db, event.id)[0].id
        finally:
            db.close()

        with engine.connect() as conn:
            with self.assertRaises(DatabaseError):
                conn.execute(
                    text("DELETE FROM event_history WHERE id = :id"), {"id": history_id}
                )
                conn.commit()


if __name__ == "__main__":
    unittest.main()
