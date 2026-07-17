import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_events_filter.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event


class EventsFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        resp = cls.client.post(
            "/setup",
            data={"org_name": "Acme", "email": "admin@acme.test",
                  "password": "AdminPass123!", "confirm_password": "AdminPass123!"},
        )
        assert resp.status_code == 204, resp.text
        # Two defects with different status/priority.
        cls.client.post("/admin/defects/create", data={
            "title": "Weld porosity", "event_type": "Defect",
            "priority": "High"})
        cls.client.post("/admin/defects/create", data={
            "title": "Label misprint", "event_type": "Defect",
            "priority": "Low"})
        # Move the second to In_Progress so status filtering is testable.
        db = SessionLocal()
        try:
            e = db.query(Event).filter(Event.title == "Label misprint").first()
            e.status = "In_Progress"
            db.commit()
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_events_filter.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _titles(self, url):
        r = self.client.get(url, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        # htmx fragment should be just the table, not a full page.
        self.assertNotIn("<html", r.text)
        self.assertIn("eventTable", r.text)
        return r.text

    def test_no_filter_shows_all(self):
        text = self._titles("/admin/defects")
        self.assertIn("Weld porosity", text)
        self.assertIn("Label misprint", text)

    def test_status_filter(self):
        text = self._titles("/admin/defects?status=Open")
        self.assertIn("Weld porosity", text)
        self.assertNotIn("Label misprint", text)

    def test_priority_filter(self):
        text = self._titles("/admin/defects?priority=Low")
        self.assertIn("Label misprint", text)
        self.assertNotIn("Weld porosity", text)

    def test_search_filter(self):
        text = self._titles("/admin/defects?search=porosity")
        self.assertIn("Weld porosity", text)
        self.assertNotIn("Label misprint", text)

    def test_full_page_when_not_htmx(self):
        r = self.client.get("/admin/defects")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<html", r.text)  # full page includes the shell
        self.assertIn("filter-bar", r.text)


if __name__ == "__main__":
    unittest.main()
