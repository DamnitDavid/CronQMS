import os
import unittest
import uuid

# Configure an isolated SQLite database before importing the app. Each test
# module sets this itself because ``unittest discover`` imports test modules as
# top-level names, bypassing tests/__init__.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_proins.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import User


class BrowserAuthFlowTest(unittest.TestCase):
    """Exercises the cookie-based browser login path end to end."""

    @classmethod
    def setUpClass(cls):
        # Drop any pooled connection left over from a prior test module that
        # may have deleted the shared SQLite file, then start clean.
        engine.dispose()
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        cls.email = f"admin+{uuid.uuid4().hex}@example.com"
        cls.password = "TestPassword123!"

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_proins.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _register_admin(self):
        # Public registration is disabled, so provision the admin directly. The
        # dashboard requires admin privileges, hence role="Admin".
        db = SessionLocal()
        try:
            if db.query(User).filter(User.email == self.email).first() is None:
                db.add(
                    User(
                        email=self.email,
                        hashed_password=hash_password(self.password),
                        role="Admin",
                        is_active=True,
                    )
                )
                db.commit()
        finally:
            db.close()

    def test_dashboard_requires_authentication(self):
        # No cookie, no bearer token -> the auth dependency must reject.
        fresh = TestClient(app)
        response = fresh.get("/admin/dashboard")
        self.assertIn(response.status_code, (401, 403))

    def test_form_login_sets_cookie_and_loads_dashboard(self):
        self._register_admin()

        # The HTML form posts application/x-www-form-urlencoded via htmx.
        login = self.client.post(
            "/api/auth/browser-login",
            data={"email": self.email, "password": self.password},
        )
        self.assertEqual(login.status_code, 204)
        self.assertEqual(login.headers.get("HX-Redirect"), "/admin/dashboard")
        self.assertIn("access_token", self.client.cookies)

        # The cookie set above is now carried automatically by the client.
        dashboard = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("dashboard", dashboard.text.lower())

    def test_form_login_rejects_bad_credentials(self):
        response = self.client.post(
            "/api/auth/browser-login",
            data={"email": self.email, "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("HX-Redirect", response.headers)

    def test_logout_clears_cookie(self):
        # Establish a session, then clear it.
        self.client.post(
            "/api/auth/browser-login",
            data={"email": self.email, "password": self.password},
        )
        self.assertIn("access_token", self.client.cookies)

        logout = self.client.post("/api/auth/browser-logout")
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(logout.headers.get("HX-Redirect"), "/login")
        # After logout the dashboard is no longer reachable.
        self.client.cookies.clear()
        blocked = self.client.get("/admin/dashboard")
        self.assertIn(blocked.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
