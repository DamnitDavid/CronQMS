"""Tests for event attachments: upload, checksum, download, scoping."""

import hashlib
import os
import shutil
import tempfile
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_cronqms.db")
# Isolate attachment storage in a throwaway directory.
_STORAGE_DIR = os.path.join(tempfile.gettempdir(), f"cronqms_attach_test_{uuid.uuid4().hex}")
os.environ["ATTACHMENT_STORAGE_DIR"] = _STORAGE_DIR

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Event, Organization, Role, User


class AttachmentsTest(unittest.TestCase):
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
                title="With attachments", event_type="Defect", status="Open",
                priority="High", organization_id=cls.org_id, reported_by=cls.ids["investigator"],
            )
            other_event = Event(
                title="Other org", event_type="Defect", status="Open",
                priority="High", organization_id=cls.other_org_id, reported_by=cls.ids["other"],
            )
            db.add_all([event, other_event])
            db.commit()
            cls.event_id, cls.other_event_id = event.id, other_event.id
        finally:
            db.close()

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_cronqms.db")
        if os.path.exists(db_path):
            os.remove(db_path)
        shutil.rmtree(_STORAGE_DIR, ignore_errors=True)

    def _auth(self, who):
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def test_upload_records_checksum_and_downloads_identically(self):
        payload = b"quality record contents"
        resp = self.client.post(
            f"/api/events/{self.event_id}/attachments",
            files={"file": ("report.txt", payload, "text/plain")},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        body = resp.json()
        self.assertEqual(body["checksum"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(body["size_bytes"], len(payload))
        self.assertEqual(body["uploaded_by"], self.ids["investigator"])

        download = self.client.get(
            f"/api/attachments/{body['id']}/download", headers=self._auth("viewer")
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, payload)
        self.assertIn("report.txt", download.headers.get("content-disposition", ""))

    def test_list_attachments(self):
        self.client.post(
            f"/api/events/{self.event_id}/attachments",
            files={"file": ("a.bin", b"12345", "application/octet-stream")},
            headers=self._auth("investigator"),
        )
        resp = self.client.get(
            f"/api/events/{self.event_id}/attachments", headers=self._auth("viewer")
        )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.json()), 1)

    def test_viewer_cannot_upload(self):
        resp = self.client.post(
            f"/api/events/{self.event_id}/attachments",
            files={"file": ("x.txt", b"data", "text/plain")},
            headers=self._auth("viewer"),
        )
        self.assertEqual(resp.status_code, 403)

    def test_empty_file_rejected(self):
        resp = self.client.post(
            f"/api/events/{self.event_id}/attachments",
            files={"file": ("empty.txt", b"", "text/plain")},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 400)

    def test_cross_org_event_upload_not_found(self):
        resp = self.client.post(
            f"/api/events/{self.other_event_id}/attachments",
            files={"file": ("x.txt", b"data", "text/plain")},
            headers=self._auth("investigator"),
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
