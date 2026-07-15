"""Tests for the Quality/Safety Alert workflow: creation (requires a recipient
group + permission), notification fan-out, signed-document acknowledgement
upload/download, and org scoping."""

import os
import shutil
import tempfile
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_alerts.db")
# Isolate acknowledgement storage in a throwaway directory.
_STORAGE_DIR = os.path.join(tempfile.gettempdir(), f"proins_alert_test_{uuid.uuid4().hex}")
os.environ["ATTACHMENT_STORAGE_DIR"] = _STORAGE_DIR

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Alert, AssigneeGroup, Event, Notification, User
from app.models.event import EventType


class AlertsTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(app)  # admin session (cookie) after /setup
        resp = self.client.post(
            "/setup",
            data={"org_name": "Acme", "email": "admin@acme.test",
                  "password": "AdminPass123!", "confirm_password": "AdminPass123!"},
        )
        assert resp.status_code == 204, resp.text
        # A member user to receive alerts.
        self.client.post(
            "/admin/users",
            data={"email": "member@acme.test", "password": "MemberPass1!", "role": "Investigator"},
            follow_redirects=False,
        )

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_alerts.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        shutil.rmtree(_STORAGE_DIR, ignore_errors=True)

    # --- helpers -----------------------------------------------------------
    def _member_id(self) -> int:
        db = SessionLocal()
        try:
            return db.query(User).filter(User.email == "member@acme.test").first().id
        finally:
            db.close()

    def _make_group_with_member(self, name="Line 3 Team") -> int:
        self.client.post("/admin/settings/groups", data={"name": name}, follow_redirects=False)
        db = SessionLocal()
        try:
            gid = db.query(AssigneeGroup).filter(AssigneeGroup.name == name).first().id
        finally:
            db.close()
        self.client.post(
            f"/admin/settings/groups/{gid}/members/add",
            data={"user_id": self._member_id()}, follow_redirects=False,
        )
        return gid

    def _make_capa_event(self) -> int:
        self.client.post("/admin/events/create", data={
            "title": "Housing crack", "event_type": EventType.CAPA.value, "priority": "High"})
        db = SessionLocal()
        try:
            return db.query(Event).filter(Event.title == "Housing crack").first().id
        finally:
            db.close()

    # --- tests -------------------------------------------------------------
    def test_create_alert_notifies_group_members(self):
        gid = self._make_group_with_member()
        eid = self._make_capa_event()
        resp = self.client.post(f"/admin/events/{eid}/alerts", data={
            "title": "Do not ship lot 4471", "alert_type": "Quality", "severity": "High",
            "required_actions": "Quarantine affected lots", "recipient_group_ids": gid})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("Do not ship lot 4471", resp.text)

        db = SessionLocal()
        try:
            alert = db.query(Alert).filter(Alert.title == "Do not ship lot 4471").first()
            self.assertIsNotNone(alert)
            self.assertEqual([g.id for g in alert.recipient_groups], [gid])
            # The one member has a notification linked to the alert.
            notes = db.query(Notification).filter(
                Notification.user_id == self._member_id(),
                Notification.alert_id == alert.id).all()
            self.assertEqual(len(notes), 1)
            self.assertFalse(notes[0].is_read)
        finally:
            db.close()

    def test_create_alert_requires_a_group(self):
        eid = self._make_capa_event()
        # No recipient_group_ids -> redirected back to the form, no alert created.
        resp = self.client.post(f"/admin/events/{eid}/alerts", data={
            "title": "Missing group", "alert_type": "Quality", "severity": "Low"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Select at least one recipient group", resp.text)
        db = SessionLocal()
        try:
            self.assertIsNone(db.query(Alert).filter(Alert.title == "Missing group").first())
        finally:
            db.close()

    def test_acknowledgement_upload_and_download(self):
        gid = self._make_group_with_member()
        eid = self._make_capa_event()
        self.client.post(f"/admin/events/{eid}/alerts", data={
            "title": "Signable", "alert_type": "Safety", "severity": "Critical",
            "recipient_group_ids": gid})
        db = SessionLocal()
        try:
            alert_id = db.query(Alert).filter(Alert.title == "Signable").first().id
        finally:
            db.close()

        payload = b"scanned signed acknowledgement"
        resp = self.client.post(
            f"/admin/alerts/{alert_id}/acknowledgements",
            data={"group_id": str(gid), "note": "Signed by shift lead"},
            files={"file": ("signed.pdf", payload, "application/pdf")},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("signed.pdf", resp.text)

        db = SessionLocal()
        try:
            alert = db.query(Alert).filter(Alert.id == alert_id).first()
            self.assertEqual(len(alert.acknowledgements), 1)
            ack_id = alert.acknowledgements[0].id
            # All recipient groups responded -> status flips to Acknowledged.
            self.assertEqual(alert.status, "Acknowledged")
        finally:
            db.close()

        download = self.client.get(f"/api/alert-acknowledgements/{ack_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, payload)
        self.assertIn("signed.pdf", download.headers.get("content-disposition", ""))

    def test_empty_acknowledgement_rejected(self):
        gid = self._make_group_with_member()
        eid = self._make_capa_event()
        self.client.post(f"/admin/events/{eid}/alerts", data={
            "title": "NoEmpty", "alert_type": "Quality", "severity": "Low",
            "recipient_group_ids": gid})
        db = SessionLocal()
        try:
            alert_id = db.query(Alert).filter(Alert.title == "NoEmpty").first().id
        finally:
            db.close()
        resp = self.client.post(
            f"/admin/alerts/{alert_id}/acknowledgements",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Empty file", resp.text)

    def test_print_view_has_signature_page(self):
        gid = self._make_group_with_member()
        eid = self._make_capa_event()
        self.client.post(f"/admin/events/{eid}/alerts", data={
            "title": "Printable", "alert_type": "Quality", "severity": "Medium",
            "recipient_group_ids": gid})
        db = SessionLocal()
        try:
            alert_id = db.query(Alert).filter(Alert.title == "Printable").first().id
        finally:
            db.close()
        resp = self.client.get(f"/admin/alerts/{alert_id}/print")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Signatures", resp.text)
        self.assertIn("Line 3 Team", resp.text)
        self.assertIn("member@acme.test", resp.text)

    def test_viewer_cannot_create_alert(self):
        gid = self._make_group_with_member()
        eid = self._make_capa_event()
        self.client.post("/admin/users",
                         data={"email": "v@acme.test", "password": "ViewerPass1!", "role": "Viewer"},
                         follow_redirects=False)
        viewer = TestClient(app)
        viewer.post("/api/auth/browser-login",
                    data={"email": "v@acme.test", "password": "ViewerPass1!"})
        resp = viewer.post(f"/admin/events/{eid}/alerts", data={
            "title": "Nope", "alert_type": "Quality", "severity": "Low",
            "recipient_group_ids": gid}, follow_redirects=False)
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
