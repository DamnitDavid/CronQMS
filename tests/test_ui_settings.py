"""Tests for the org-configurable navigation, the unified audit-log feed, and
document owner groups.
"""

import os
import unittest
import uuid

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_ui_settings.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import create_token_for_user, hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AssigneeGroup, Document, Organization, Role, User
from app.services import nav_config


def _mk_org(db, name):
    org = Organization(name=name, code=f"{name.lower()}-{uuid.uuid4().hex[:8]}")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org.id


def _mk_user(db, org_id, role):
    user = User(
        email=f"{role.value.lower()}+{uuid.uuid4().hex}@example.com",
        hashed_password=hash_password("TestPassword123!"),
        role=role.value,
        organization_id=org_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token, _ = create_token_for_user(user.id, user.email)
    return user.id, token


class NavConfigUnitTest(unittest.TestCase):
    """Pure-function coverage for the layout resolver."""

    def test_build_nav_hides_modules_without_privilege(self):
        layout = {
            "groups": [
                {"title": "Ops", "modules": ["events", "users"]},
            ]
        }
        # A user with only event:read sees Events but not Users.
        nav = nav_config.build_nav(layout, {"event:read"})
        self.assertEqual(len(nav), 1)
        keys = [m["key"] for m in nav[0]["modules"]]
        self.assertEqual(keys, ["events"])

    def test_build_nav_drops_empty_groups(self):
        layout = {"groups": [{"title": "Admin", "modules": ["users"]}]}
        nav = nav_config.build_nav(layout, {"event:read"})  # lacks user:manage
        self.assertEqual(nav, [])

    def test_sanitize_drops_unknown_modules_and_untitled_groups(self):
        raw = {"groups": [{"title": "", "modules": ["events"]},
                          {"title": "Good", "modules": ["events", "bogus"]}]}
        clean = nav_config._sanitize(raw)
        self.assertEqual(clean, {"groups": [{"title": "Good", "modules": ["events"]}]})


class NavigationEditorTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(app)
        resp = self.client.post(
            "/setup",
            data={"org_name": "Acme", "email": "admin@acme.test",
                  "password": "AdminPass123!", "confirm_password": "AdminPass123!"},
        )
        assert resp.status_code == 204, resp.text

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_ui_settings.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def test_save_custom_layout_reflected_in_sidebar(self):
        # Put Events under a custom group title; hide everything else.
        form = {"group_title__0": "Frontline", "group__events": "0", "order__events": "0"}
        r = self.client.post("/admin/settings/navigation", data=form, follow_redirects=False)
        self.assertEqual(r.status_code, 303)

        page = self.client.get("/admin/events")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Frontline", page.text)

        db = SessionLocal()
        try:
            org = db.query(Organization).first()
            layout = nav_config.get_layout(db, org.id)
        finally:
            db.close()
        self.assertEqual(layout["groups"][0]["title"], "Frontline")
        self.assertIn("events", layout["groups"][0]["modules"])

    def test_reset_restores_default(self):
        self.client.post(
            "/admin/settings/navigation",
            data={"group_title__0": "Only", "group__events": "0"},
            follow_redirects=False,
        )
        r = self.client.post(
            "/admin/settings/navigation", data={"reset": "1"}, follow_redirects=False
        )
        self.assertEqual(r.status_code, 303)
        db = SessionLocal()
        try:
            org = db.query(Organization).first()
            layout = nav_config.get_layout(db, org.id)
        finally:
            db.close()
        self.assertEqual(layout, nav_config.DEFAULT_LAYOUT)

    def test_navigation_editor_is_admin_only(self):
        self.client.post(
            "/admin/users",
            data={"email": "v@acme.test", "password": "ViewerPass1!", "role": "Viewer"},
            follow_redirects=False,
        )
        viewer = TestClient(app)
        viewer.post("/api/auth/browser-login",
                    data={"email": "v@acme.test", "password": "ViewerPass1!"})
        self.assertEqual(viewer.get("/admin/settings/navigation").status_code, 403)


class AuditLogFeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        db = SessionLocal()
        try:
            cls.org_a = _mk_org(db, "Acme")
            cls.org_b = _mk_org(db, "Rival")
            _, cls.token_a = _mk_user(db, cls.org_a, Role.ADMIN)
            _, cls.token_b = _mk_user(db, cls.org_b, Role.ADMIN)
        finally:
            db.close()
        # Each admin creates an event via the API so history is actor-attributed.
        cls.client.post("/api/events/",
                        json={"title": "Acme weld defect", "priority": "Medium", "event_type": "Defect"},
                        headers={"Authorization": f"Bearer {cls.token_a}"})
        cls.client.post("/api/events/",
                        json={"title": "Rival paint defect", "priority": "Medium", "event_type": "Defect"},
                        headers={"Authorization": f"Bearer {cls.token_b}"})

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_ui_settings.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_requires_authentication(self):
        self.assertIn(TestClient(app).get("/admin/audit-log").status_code, (401, 403))

    def test_feed_is_org_scoped(self):
        page = self.client.get("/admin/audit-log", headers=self._auth(self.token_a))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Acme weld defect", page.text)
        self.assertNotIn("Rival paint defect", page.text)

        other = self.client.get("/admin/audit-log", headers=self._auth(self.token_b))
        self.assertIn("Rival paint defect", other.text)
        self.assertNotIn("Acme weld defect", other.text)

    def test_entity_type_filter(self):
        # Filtering to documents yields no rows (only event changes exist).
        empty = self.client.get(
            "/admin/audit-log?entity_type=document", headers=self._auth(self.token_a)
        )
        self.assertEqual(empty.status_code, 200)
        self.assertNotIn("Acme weld defect", empty.text)
        # Filtering to events still shows the event change.
        events = self.client.get(
            "/admin/audit-log?entity_type=event", headers=self._auth(self.token_a)
        )
        self.assertIn("Acme weld defect", events.text)


class DocumentOwnerGroupTest(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(app)
        resp = self.client.post(
            "/setup",
            data={"org_name": "Acme", "email": "admin@acme.test",
                  "password": "AdminPass123!", "confirm_password": "AdminPass123!"},
        )
        assert resp.status_code == 204, resp.text
        self.client.post("/admin/settings/groups", data={"name": "Docs Team"},
                         follow_redirects=False)

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_ui_settings.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _group_id(self):
        db = SessionLocal()
        try:
            return db.query(AssigneeGroup).first().id
        finally:
            db.close()

    def test_create_with_owner_group_and_filter(self):
        gid = self._group_id()
        r = self.client.post(
            "/admin/documents/create",
            data={"document_number": "SOP-1", "title": "Cleaning SOP",
                  "category": "SOP", "owner_group_id": str(gid)},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.document_number == "SOP-1").first()
            self.assertEqual(doc.owner_group_id, gid)
        finally:
            db.close()

        # The owner-group filter narrows the list.
        match = self.client.get(f"/admin/documents?owner_group={gid}")
        self.assertIn("Cleaning SOP", match.text)
        # A different (non-existent) group id yields no rows.
        miss = self.client.get(f"/admin/documents?owner_group={gid + 999}")
        self.assertNotIn("Cleaning SOP", miss.text)


if __name__ == "__main__":
    unittest.main()
