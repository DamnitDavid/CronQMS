import os
import unittest
import uuid

# Use a local SQLite file database for isolated test execution.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_proins.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, engine
from app.main import app


class AuthFlowIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        cls.test_email = f"test+{uuid.uuid4().hex}@example.com"
        cls.test_password = "TestPassword123!"

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        test_db_path = os.path.join(os.getcwd(), "test_proins.db")
        if os.path.exists(test_db_path):
            os.remove(test_db_path)

    def test_register_login_and_get_current_user(self):
        register_response = self.client.post(
            "/api/auth/register",
            json={"email": self.test_email, "password": self.test_password},
        )
        self.assertEqual(register_response.status_code, 201)
        self.assertEqual(register_response.json()["email"], self.test_email)

        login_response = self.client.post(
            "/api/auth/login",
            json={"email": self.test_email, "password": self.test_password},
        )
        self.assertEqual(login_response.status_code, 200)
        token_payload = login_response.json()
        self.assertIn("access_token", token_payload)
        self.assertEqual(token_payload["token_type"], "bearer")

        token = token_payload["access_token"]
        me_response = self.client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(me_response.json()["email"], self.test_email)


if __name__ == "__main__":
    unittest.main()
