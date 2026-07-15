import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_custom_fields.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import CustomField, Event, EventCustomValue, User


def _admin_client():
    """Fresh client that has completed setup and holds the admin cookie."""
    client = TestClient(app)
    resp = client.post(
        "/setup",
        data={
            "org_name": "Acme",
            "email": "admin@acme.test",
            "password": "AdminPass123!",
            "confirm_password": "AdminPass123!",
        },
    )
    assert resp.status_code == 204, resp.text
    return client


class CustomFieldsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = _admin_client()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_custom_fields.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _add_field(self, label, field_type, event_type="Non_Conformance", options="", required=False):
        data = {
            "event_type": event_type,
            "label": label,
            "field_type": field_type,
            "options": options,
        }
        if required:
            data["required"] = "true"
        return self.client.post(
            "/admin/settings/custom-fields", data=data, follow_redirects=False
        )

    def _field_id(self, label):
        db = SessionLocal()
        try:
            return db.query(CustomField).filter(CustomField.label == label).first().id
        finally:
            db.close()

    def test_admin_can_add_field_and_it_shows(self):
        r = self._add_field("Scrap quantity", "number")
        self.assertEqual(r.status_code, 303)
        page = self.client.get("/admin/settings/custom-fields?event_type=Non_Conformance")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Scrap quantity", page.text)

    def test_fragment_returns_inputs_for_type(self):
        self._add_field("Root cause code", "text")
        fid = self._field_id("Root cause code")
        frag = self.client.get("/admin/events/custom-fields?event_type=Non_Conformance")
        self.assertEqual(frag.status_code, 200)
        self.assertIn(f"cf_{fid}", frag.text)

    def test_fragment_is_inline_not_boxed(self):
        # The fields render inline in the main form, not inside a boxed sub-form.
        self._add_field("Root cause code", "text")
        frag = self.client.get("/admin/events/custom-fields?event_type=Non_Conformance")
        self.assertNotIn('class="card"', frag.text)
        self.assertNotIn("Custom Information", frag.text)

    def test_select_field_renders_dropdown_with_options(self):
        self._add_field("Customer", "select", options="Acme Corp\nGlobex\nInitech")
        fid = self._field_id("Customer")
        frag = self.client.get("/admin/events/custom-fields?event_type=Non_Conformance")
        self.assertIn(f'<select id="cf_{fid}"', frag.text)
        for opt in ("Acme Corp", "Globex", "Initech"):
            self.assertIn(opt, frag.text)

    def test_select_requires_options(self):
        r = self._add_field("Customer", "select", options="   ")
        self.assertEqual(r.status_code, 303)
        self.assertIn("error", r.headers["location"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(CustomField).count(), 0)
        finally:
            db.close()

    def test_select_value_must_be_in_options(self):
        self._add_field("Customer", "select", options="Acme Corp\nGlobex")
        fid = self._field_id("Customer")
        # Valid choice persists.
        ok = self.client.post(
            "/admin/events/create",
            data={"title": "Valid pick", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": "Globex"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertIn("Globex", ok.text)
        # Out-of-list value is rejected.
        bad = self.client.post(
            "/admin/events/create",
            data={"title": "Bad pick", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": "Umbrella Corp"},
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code, 303)
        self.assertIn("error", bad.headers["location"])

    def test_fields_are_scoped_to_event_type(self):
        self._add_field("NC only", "text", event_type="Non_Conformance")
        frag = self.client.get("/admin/events/custom-fields?event_type=CAPA")
        self.assertNotIn("NC only", frag.text)

    def test_event_create_persists_custom_values(self):
        self._add_field("Root cause code", "text")
        self._add_field("Scrap quantity", "number")
        self._add_field("Contained", "boolean")
        text_id = self._field_id("Root cause code")
        num_id = self._field_id("Scrap quantity")
        bool_id = self._field_id("Contained")

        resp = self.client.post(
            "/admin/events/create",
            data={
                "title": "Weld defect",
                "event_type": "Non_Conformance",
                "priority": "High",
                f"cf_{text_id}": "RC-42",
                f"cf_{num_id}": "17",
                f"cf_{bool_id}": "true",
            },
        )
        self.assertEqual(resp.status_code, 200)
        # Landed on the detail page; custom values are rendered.
        self.assertIn("Custom Information", resp.text)
        self.assertIn("RC-42", resp.text)
        self.assertIn("17", resp.text)

        db = SessionLocal()
        try:
            values = {v.custom_field_id: v.value for v in db.query(EventCustomValue).all()}
            self.assertEqual(values[text_id], "RC-42")
            self.assertEqual(values[num_id], "17")
            self.assertEqual(values[bool_id], "true")
        finally:
            db.close()

    def test_invalid_number_is_rejected(self):
        self._add_field("Scrap quantity", "number")
        num_id = self._field_id("Scrap quantity")
        resp = self.client.post(
            "/admin/events/create",
            data={
                "title": "Bad number",
                "event_type": "Non_Conformance",
                "priority": "Low",
                f"cf_{num_id}": "not-a-number",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertIn("/admin/events/create", resp.headers["location"])
        self.assertIn("error", resp.headers["location"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Event).count(), 0)
            self.assertEqual(db.query(EventCustomValue).count(), 0)
        finally:
            db.close()

    def test_delete_hides_field(self):
        self._add_field("Temporary", "text")
        fid = self._field_id("Temporary")
        self.client.post(f"/admin/settings/custom-fields/{fid}/delete", follow_redirects=False)
        frag = self.client.get("/admin/events/custom-fields?event_type=Non_Conformance")
        self.assertNotIn(f"cf_{fid}", frag.text)

    def test_required_field_blocks_empty_save(self):
        self._add_field("Root cause code", "text", required=True)
        fid = self._field_id("Root cause code")
        # Empty required field -> rejected.
        bad = self.client.post(
            "/admin/events/create",
            data={"title": "Missing rc", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": ""},
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code, 303)
        self.assertIn("error", bad.headers["location"])
        db = SessionLocal()
        try:
            self.assertEqual(db.query(Event).count(), 0)
        finally:
            db.close()
        # Provided -> saved.
        ok = self.client.post(
            "/admin/events/create",
            data={"title": "Has rc", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": "RC-1"},
        )
        self.assertEqual(ok.status_code, 200)

    def test_date_field_validates(self):
        self._add_field("Date detected", "date")
        fid = self._field_id("Date detected")
        bad = self.client.post(
            "/admin/events/create",
            data={"title": "Bad date", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": "not-a-date"},
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code, 303)
        self.assertIn("error", bad.headers["location"])
        ok = self.client.post(
            "/admin/events/create",
            data={"title": "Good date", "event_type": "Non_Conformance",
                  "priority": "Low", f"cf_{fid}": "2026-07-10"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertIn("2026-07-10", ok.text)

    def test_viewer_cannot_access_settings(self):
        # Admin creates a Viewer, who then logs in on a separate client.
        self.client.post(
            "/admin/users",
            data={"email": "viewer@acme.test", "password": "ViewerPass1!", "role": "Viewer"},
            follow_redirects=False,
        )
        viewer = TestClient(app)
        login = viewer.post(
            "/api/auth/browser-login",
            data={"email": "viewer@acme.test", "password": "ViewerPass1!"},
        )
        self.assertEqual(login.status_code, 204)
        resp = viewer.get("/admin/settings/custom-fields")
        self.assertEqual(resp.status_code, 403)
        create = viewer.post(
            "/admin/settings/custom-fields",
            data={"event_type": "Non_Conformance", "label": "x", "field_type": "text"},
        )
        self.assertEqual(create.status_code, 403)


if __name__ == "__main__":
    unittest.main()
