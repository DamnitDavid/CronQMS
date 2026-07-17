"""Tests for reporting, CSV export, and paginated event listing."""

import os
import unittest
import uuid
from datetime import date, datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Capa, Event, Organization, Role, User


class ReportsTest(unittest.TestCase):
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

            qm = User(
                email=f"qm+{uuid.uuid4().hex}@example.com",
                hashed_password=hash_password("TestPassword123!"),
                role=Role.QUALITY_MANAGER.value,
                organization_id=cls.org_id,
            )
            assignee = User(
                email=f"inv+{uuid.uuid4().hex}@example.com",
                hashed_password=hash_password("TestPassword123!"),
                role=Role.INVESTIGATOR.value,
                organization_id=cls.org_id,
            )
            db.add_all([qm, assignee])
            db.commit()
            db.refresh(qm)
            db.refresh(assignee)
            cls.qm_id, cls.assignee_id = qm.id, assignee.id
            cls.token, _ = create_token_for_user(qm.id, qm.email)

            # CAPAs for Pareto and cycle time.
            db.add_all([
                Capa(organization_id=cls.org_id, title="c1", status="Open",
                     root_cause_category="Process", created_by=cls.qm_id),
                Capa(organization_id=cls.org_id, title="c2", status="Open",
                     root_cause_category="Process", created_by=cls.qm_id),
                Capa(organization_id=cls.org_id, title="c3", status="Open",
                     root_cause_category="Material", created_by=cls.qm_id),
                Capa(organization_id=cls.org_id, title="c4", status="Closed",
                     root_cause_category="Material", created_by=cls.qm_id,
                     created_at=datetime(2026, 1, 1), verification_date=date(2026, 1, 11)),
            ])

            # Events across months; one closed, some overdue with an assignee.
            db.add_all([
                Event(title="jan1", event_type="Defect", status="Open",
                      priority="High", organization_id=cls.org_id, reported_by=cls.qm_id,
                      created_at=datetime(2026, 1, 5)),
                Event(title="jan2", event_type="Defect", status="Closed",
                      priority="High", organization_id=cls.org_id, reported_by=cls.qm_id,
                      created_at=datetime(2026, 1, 20), closed_at=datetime(2026, 2, 2)),
                Event(title="overdue1", event_type="Defect", status="Open",
                      priority="High", organization_id=cls.org_id, reported_by=cls.qm_id,
                      assigned_to=cls.assignee_id, target_close_date=date.today() - timedelta(days=2)),
                Event(title="overdue2", event_type="Defect", status="In_Progress",
                      priority="High", organization_id=cls.org_id, reported_by=cls.qm_id,
                      assigned_to=cls.assignee_id, target_close_date=date.today() - timedelta(days=1)),
            ])
            db.commit()
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

    def test_list_events_is_paginated_with_total(self):
        resp = self.client.get("/api/events/?page=1&page_size=2", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 4)
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 2)
        self.assertEqual(len(body["items"]), 2)

    def test_pareto_root_cause_ordering(self):
        resp = self.client.get("/api/reports/pareto-root-cause", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data[0], {"category": "Process", "count": 2})
        self.assertEqual({d["category"] for d in data}, {"Process", "Material"})

    def test_events_by_month(self):
        resp = self.client.get("/api/reports/events-by-month", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        by_month = {row["month"]: row for row in resp.json()}
        self.assertEqual(by_month["2026-01"]["opened"], 2)
        self.assertEqual(by_month["2026-02"]["closed"], 1)

    def test_capa_cycle_time(self):
        resp = self.client.get("/api/reports/capa-cycle-time", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        summary = resp.json()[0]
        self.assertEqual(summary["closed_capas"], 1)
        self.assertEqual(summary["average_cycle_days"], 10.0)

    def test_overdue_by_owner(self):
        resp = self.client.get("/api/reports/overdue-by-owner", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data[0]["owner_id"], self.assignee_id)
        self.assertEqual(data[0]["count"], 2)

    def test_csv_export_events(self):
        resp = self.client.get("/api/reports/events.csv", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/csv"))
        self.assertIn("attachment; filename=", resp.headers.get("content-disposition", ""))
        lines = resp.text.strip().splitlines()
        self.assertTrue(lines[0].startswith("id,title,event_type"))
        self.assertEqual(len(lines), 1 + 4)  # header + 4 events

    def test_report_csv_format_param(self):
        resp = self.client.get("/api/reports/pareto-root-cause?format=csv", headers=self._auth())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("text/csv"))
        self.assertIn("category,count", resp.text)


if __name__ == "__main__":
    unittest.main()
