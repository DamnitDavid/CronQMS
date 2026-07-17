import os
import unittest

# Configure an isolated SQLite database before importing the app.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_setup.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Organization, User
from app.models.user import Role


class FirstTimeSetupTest(unittest.TestCase):
    """Exercises the first-run admin/organization setup wizard end to end."""

    @classmethod
    def setUpClass(cls):
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.org_name = "Acme Manufacturing"
        cls.email = "admin@acme.test"
        cls.password = "TestPassword123!"

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_setup.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def setUp(self):
        # Each test starts from an empty schema so ordering doesn't matter.
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.client = TestClient(app)

    def _complete_setup(self):
        return self.client.post(
            "/setup",
            data={
                "org_name": self.org_name,
                "email": self.email,
                "password": self.password,
                "confirm_password": self.password,
            },
        )

    def test_setup_page_available_when_no_admin(self):
        response = self.client.get("/setup")
        self.assertEqual(response.status_code, 200)
        self.assertIn("setup", response.text.lower())

    def test_root_redirects_to_setup_on_fresh_install(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")

    def test_login_redirects_to_setup_on_fresh_install(self):
        response = self.client.get("/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")

    def test_setup_creates_org_and_admin_and_logs_in(self):
        response = self._complete_setup()
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Redirect"), "/admin/events")
        self.assertIn("access_token", self.client.cookies)

        db = SessionLocal()
        try:
            org = db.query(Organization).filter(Organization.name == self.org_name).first()
            self.assertIsNotNone(org)
            admin = db.query(User).filter(User.email == self.email).first()
            self.assertIsNotNone(admin)
            self.assertEqual(admin.role, Role.ADMIN.value)
            self.assertEqual(admin.organization_id, org.id)
        finally:
            db.close()

        # The session cookie set above should now load the landing page.
        landing = self.client.get("/admin/events")
        self.assertEqual(landing.status_code, 200)
        self.assertIn("events", landing.text.lower())

    def test_setup_is_inert_once_completed(self):
        self.assertEqual(self._complete_setup().status_code, 204)

        # GET now bounces to login.
        page = TestClient(app).get("/setup", follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertEqual(page.headers["location"], "/login")

        # POST is refused so it can't be an open admin-creation backdoor.
        second = TestClient(app).post(
            "/setup",
            data={
                "org_name": "Evil Corp",
                "email": "intruder@evil.test",
                "password": "AnotherPass123!",
                "confirm_password": "AnotherPass123!",
            },
        )
        self.assertEqual(second.status_code, 403)

        db = SessionLocal()
        try:
            admin_count = db.query(User).filter(User.role == Role.ADMIN.value).count()
            self.assertEqual(admin_count, 1)
        finally:
            db.close()

    def test_setup_rejects_mismatched_passwords(self):
        response = self.client.post(
            "/setup",
            data={
                "org_name": self.org_name,
                "email": self.email,
                "password": self.password,
                "confirm_password": "Different123!",
            },
        )
        self.assertEqual(response.status_code, 400)
        db = SessionLocal()
        try:
            self.assertEqual(db.query(User).count(), 0)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
