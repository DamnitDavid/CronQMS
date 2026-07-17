import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_assignee_groups.db")

from fastapi.testclient import TestClient

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import AssigneeGroup, Event, User


class AssigneeGroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.dispose()

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
        # A second user to add to groups.
        self.client.post(
            "/admin/users",
            data={"email": "j.lee@acme.test", "password": "MemberPass1!", "role": "Investigator"},
            follow_redirects=False,
        )

    @classmethod
    def tearDownClass(cls):
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = os.path.join(os.getcwd(), "test_assignee_groups.db")
        if os.path.exists(db_path):
            os.remove(db_path)

    def _ids(self):
        db = SessionLocal()
        try:
            member = db.query(User).filter(User.email == "j.lee@acme.test").first()
            group = db.query(AssigneeGroup).first()
            return (group.id if group else None), member.id
        finally:
            db.close()

    def _make_group(self, name="Line 3 Team"):
        self.client.post("/admin/settings/groups", data={"name": name}, follow_redirects=False)
        return self._ids()[0]

    def test_create_group_and_manage_members(self):
        gid = self._make_group()
        self.assertIsNotNone(gid)
        _, uid = self._ids()
        # Add member.
        self.client.post(f"/admin/settings/groups/{gid}/members/add",
                         data={"user_id": uid}, follow_redirects=False)
        db = SessionLocal()
        try:
            g = db.query(AssigneeGroup).get(gid)
            self.assertIn(uid, [m.id for m in g.members])
        finally:
            db.close()
        # Remove member.
        self.client.post(f"/admin/settings/groups/{gid}/members/remove",
                         data={"user_id": uid}, follow_redirects=False)
        db = SessionLocal()
        try:
            g = db.query(AssigneeGroup).get(gid)
            self.assertNotIn(uid, [m.id for m in g.members])
        finally:
            db.close()

    def test_assign_event_to_group_and_to_user(self):
        gid = self._make_group()
        _, uid = self._ids()
        # Assign to group.
        r = self.client.post("/admin/events/create", data={
            "title": "Group event", "event_type": "Defect",
            "priority": "Low", "assignee": f"group:{gid}"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Line 3 Team", r.text)  # detail shows the group name
        db = SessionLocal()
        try:
            e = db.query(Event).filter(Event.title == "Group event").first()
            self.assertEqual(e.assigned_group_id, gid)
            self.assertIsNone(e.assigned_to)
        finally:
            db.close()
        # Assign a different event to a single user.
        self.client.post("/admin/events/create", data={
            "title": "User event", "event_type": "Defect",
            "priority": "Low", "assignee": f"user:{uid}"})
        db = SessionLocal()
        try:
            e = db.query(Event).filter(Event.title == "User event").first()
            self.assertEqual(e.assigned_to, uid)
            self.assertIsNone(e.assigned_group_id)
        finally:
            db.close()

    def test_groups_settings_admin_only(self):
        viewer = TestClient(app)
        self.client.post("/admin/users",
                         data={"email": "v@acme.test", "password": "ViewerPass1!", "role": "Viewer"},
                         follow_redirects=False)
        viewer.post("/api/auth/browser-login", data={"email": "v@acme.test", "password": "ViewerPass1!"})
        self.assertEqual(viewer.get("/admin/settings/groups").status_code, 403)
        self.assertEqual(
            viewer.post("/admin/settings/groups", data={"name": "x"}).status_code, 403
        )


if __name__ == "__main__":
    unittest.main()
