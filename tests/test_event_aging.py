"""Tests for event due dates / SLA / aging and traceability fields."""

import os
import unittest
import uuid
from datetime import date, datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.core.sla import sla_days
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event, Organization, Role, User


class EventAgingTest(unittest.TestCase):
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

    def _auth(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_target_close_date_derived_from_priority_sla(self):
        resp = self.client.post(
            "/api/events/",
            json={"title": "Critical failure", "priority": "Critical", "event_type": "Defect"},
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        expected = (date.today() + timedelta(days=sla_days("Critical"))).isoformat()
        self.assertEqual(body["target_close_date"], expected)
        self.assertFalse(body["is_overdue"])

    def test_explicit_target_close_date_is_respected(self):
        due = (date.today() + timedelta(days=3)).isoformat()
        resp = self.client.post(
            "/api/events/",
            json={"title": "Custom due", "priority": "Low", "target_close_date": due},
            headers=self._auth(),
        )
        self.assertEqual(resp.json()["target_close_date"], due)

    def test_traceability_fields_round_trip(self):
        resp = self.client.post(
            "/api/events/",
            json={
                "title": "Lot trace",
                "priority": "High",
                "lot_batch": "4471",
                "product_part_number": "PN-900",
                "supplier": "Acme Supplier",
                "work_order": "WO-1",
                "machine": "CNC-7",
            },
            headers=self._auth(),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["lot_batch"], "4471")
        self.assertEqual(body["product_part_number"], "PN-900")

    def test_overdue_flag_and_stat(self):
        # Seed an event whose target close date is already in the past.
        db = SessionLocal()
        try:
            event = Event(
                title="Overdue item",
                event_type="Defect",
                status="Open",
                priority="High",
                organization_id=self.org_id,
                reported_by=self.user_id,
                target_close_date=date.today() - timedelta(days=1),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            self.assertTrue(event.is_overdue)
            event_id = event.id
        finally:
            db.close()

        detail = self.client.get(f"/api/events/{event_id}", headers=self._auth()).json()
        self.assertTrue(detail["is_overdue"])

        stats = self.client.get("/api/stats", headers=self._auth()).json()
        self.assertIn("overdue_events", stats)
        self.assertGreaterEqual(stats["overdue_events"], 1)

    def test_closed_event_is_not_overdue(self):
        db = SessionLocal()
        try:
            event = Event(
                title="Closed overdue",
                event_type="Defect",
                status="Closed",
                priority="High",
                organization_id=self.org_id,
                reported_by=self.user_id,
                target_close_date=date.today() - timedelta(days=10),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            self.assertFalse(event.is_overdue)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
