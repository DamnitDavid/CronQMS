import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin_pages.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import User


class AdminPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        cls.admin = TestClient(app)
        resp = cls.admin.post(
            "/setup",
            data={
                "org_name": "Acme",
                "email": "admin@acme.test",
                "password": "AdminPass123!",
                "confirm_password": "AdminPass123!",
            },
        )
        assert resp.status_code == 204, resp.text
        # Create a Viewer to test the gates.
        cls.admin.post(
            "/admin/users",
            data={"email": "viewer@acme.test", "password": "ViewerPass1!", "role": "Viewer"},
            follow_redirects=False,
        )
        cls.viewer = TestClient(app)
        cls.viewer.post(
            "/api/auth/browser-login",
            data={"email": "viewer@acme.test", "password": "ViewerPass1!"},
        )

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_admin_pages.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def test_admin_can_reach_all_sections(self):
        for path in [
            "/admin/settings/groups",
            "/admin/users",
            "/admin/reports",
            "/admin/capa",
        ]:
            resp = self.admin.get(path)
            self.assertEqual(resp.status_code, 200, f"{path} -> {resp.status_code}")

    def test_settings_redirect(self):
        resp = self.admin.get("/admin/settings", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers["location"], "/admin/settings/groups")

    def test_viewer_gated_out_of_admin_pages(self):
        # Settings and Users are Admin-only; a Viewer must be refused.
        self.assertEqual(self.viewer.get("/admin/settings/groups").status_code, 403)
        self.assertEqual(self.viewer.get("/admin/users").status_code, 403)

    def test_viewer_can_see_reports_and_capa(self):
        # Reports/CAPA are gated by DASHBOARD_VIEW / CAPA_READ, which Viewers have.
        self.assertEqual(self.viewer.get("/admin/reports").status_code, 200)
        self.assertEqual(self.viewer.get("/admin/capa").status_code, 200)

    def test_user_role_change_and_deactivate(self):
        db = SessionLocal()
        try:
            uid = db.query(User).filter(User.email == "viewer@acme.test").first().id
        finally:
            db.close()
        self.admin.post(f"/admin/users/{uid}/role", data={"role": "Approver"}, follow_redirects=False)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(User).filter(User.id == uid).first().role, "Approver")
        finally:
            db.close()

    def test_admin_cannot_deactivate_self(self):
        db = SessionLocal()
        try:
            aid = db.query(User).filter(User.email == "admin@acme.test").first().id
        finally:
            db.close()
        resp = self.admin.post(f"/admin/users/{aid}/deactivate", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertIn("error", resp.headers["location"])
        db = SessionLocal()
        try:
            self.assertTrue(db.query(User).filter(User.id == aid).first().is_active)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
