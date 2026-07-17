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
_STORAGE_DIR = os.path.join(tempfile.gettempdir(), f"cronqms_alert_test_{uuid.uuid4().hex}")
os.environ["ATTACHMENT_STORAGE_DIR"] = _STORAGE_DIR

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Alert, AlertImage, AssigneeGroup, Event, Notification, User
from app.models.event import EventType
from app.services import org_settings


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

    def _create_alert(self, title, gid, **extra):
        data = {"title": title, "alert_type": "Quality", "severity": "Medium",
                "recipient_group_ids": gid}
        data.update(extra)
        self.client.post(f"/admin/events/{self._make_capa_event()}/alerts", data=data)
        db = SessionLocal()
        try:
            return db.query(Alert).filter(Alert.title == title).first().id
        finally:
            db.close()

    def test_signage_poster_has_no_signature_table(self):
        gid = self._make_group_with_member()
        alert_id = self._create_alert("Poster", gid)
        resp = self.client.get(f"/admin/alerts/{alert_id}/print")
        self.assertEqual(resp.status_code, 200)
        # Signage is a clean poster: no signature column headers.
        self.assertNotIn("Signature", resp.text)
        self.assertIn("ALERT", resp.text)

    def test_blank_signoff_sheet_has_rows(self):
        gid = self._make_group_with_member()
        alert_id = self._create_alert("Signable sheet", gid)
        resp = self.client.get(f"/admin/alerts/{alert_id}/signoff")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Signature", resp.text)
        self.assertIn("Printed Name", resp.text)
        # 20 numbered blank rows.
        self.assertIn(">20<", resp.text)

    def test_expiry_defaults_and_override(self):
        from datetime import date, timedelta
        gid = self._make_group_with_member()
        # Default: today + 14 days when no expiry supplied.
        default_id = self._create_alert("DefaultExpiry", gid)
        override_id = self._create_alert("OverrideExpiry", gid, expires_at="2099-01-01")
        db = SessionLocal()
        try:
            default_alert = db.query(Alert).filter(Alert.id == default_id).first()
            override_alert = db.query(Alert).filter(Alert.id == override_id).first()
            self.assertEqual(default_alert.expires_at, date.today() + timedelta(days=14))
            self.assertEqual(override_alert.expires_at, date(2099, 1, 1))
        finally:
            db.close()

    def test_is_expired_flag(self):
        gid = self._make_group_with_member()
        alert_id = self._create_alert("PastDue", gid, expires_at="2000-01-01")
        db = SessionLocal()
        try:
            self.assertTrue(db.query(Alert).filter(Alert.id == alert_id).first().is_expired)
        finally:
            db.close()
        # Once closed, it's no longer flagged as expired.
        self.client.post(f"/admin/alerts/{alert_id}/close")
        db = SessionLocal()
        try:
            self.assertFalse(db.query(Alert).filter(Alert.id == alert_id).first().is_expired)
        finally:
            db.close()

    def test_image_upload_serves_and_enforces_two_slots(self):
        gid = self._make_group_with_member()
        alert_id = self._create_alert("WithPhotos", gid)
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"
        # Upload to slot 1, then re-upload to slot 1 (replace, not add).
        r1 = self.client.post(f"/admin/alerts/{alert_id}/images",
                              data={"position": "1"},
                              files={"file": ("a.png", png, "image/png")})
        self.assertEqual(r1.status_code, 200)
        r1b = self.client.post(f"/admin/alerts/{alert_id}/images",
                               data={"position": "1"},
                               files={"file": ("a2.png", png + b"x", "image/png")})
        self.assertEqual(r1b.status_code, 200)
        self.client.post(f"/admin/alerts/{alert_id}/images",
                         data={"position": "2"},
                         files={"file": ("b.png", png, "image/png")})
        db = SessionLocal()
        try:
            images = db.query(AlertImage).filter(AlertImage.alert_id == alert_id).all()
            self.assertEqual(len(images), 2)  # slot 1 replaced, not duplicated
            img1 = next(i for i in images if i.position == 1)
        finally:
            db.close()
        served = self.client.get(f"/api/alert-images/{img1.id}")
        self.assertEqual(served.status_code, 200)
        self.assertIn("inline", served.headers.get("content-disposition", ""))

    def test_standalone_alert_gated_by_config(self):
        gid = self._make_group_with_member()
        # Off by default -> the new-alert page 404s.
        self.assertEqual(self.client.get("/admin/alerts/new").status_code, 404)
        blocked = self.client.post("/admin/alerts/new", data={
            "title": "Nope", "alert_type": "Quality", "severity": "Low",
            "recipient_group_ids": gid}, follow_redirects=False)
        self.assertEqual(blocked.status_code, 404)
        # Enable in Config, then it works and the alert has no source event.
        self.client.post("/admin/settings/config",
                         data={"allow_standalone_alerts": "on", "default_expiry_days": "14"})
        self.assertEqual(self.client.get("/admin/alerts/new").status_code, 200)
        ok = self.client.post("/admin/alerts/new", data={
            "title": "Standalone", "alert_type": "Safety", "severity": "High",
            "recipient_group_ids": gid})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("N/A", ok.text)
        db = SessionLocal()
        try:
            alert = db.query(Alert).filter(Alert.title == "Standalone").first()
            self.assertIsNone(alert.event_id)
        finally:
            db.close()

    def test_config_page_persists_settings(self):
        self.client.post("/admin/settings/config",
                         data={"allow_standalone_alerts": "on", "default_expiry_days": "30"})
        db = SessionLocal()
        try:
            self.assertTrue(org_settings.standalone_alerts_enabled(db, 1))
            self.assertEqual(org_settings.default_expiry_days(db, 1), 30)
        finally:
            db.close()

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
