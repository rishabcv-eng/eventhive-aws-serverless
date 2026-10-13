import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor

os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"
os.environ["AWS_ACCESS_KEY_ID"] = "test"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test"

from moto import mock_aws
import boto3


def req(app, method, path, body=None, admin=False):
    r = app.handler({
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "headers": {"x-admin-key": app.ADMIN_KEY} if admin else {},
        "body": json.dumps(body) if body is not None else None,
    })
    return r["statusCode"], json.loads(r["body"])


class EventHiveTest(unittest.TestCase):
    def setUp(self):
        self.mock = mock_aws()
        self.mock.start()
        c = boto3.client("dynamodb")
        c.create_table(TableName="EventHiveEvents", BillingMode="PAY_PER_REQUEST",
                       KeySchema=[{"AttributeName": "eventId", "KeyType": "HASH"}],
                       AttributeDefinitions=[{"AttributeName": "eventId", "AttributeType": "S"}])
        c.create_table(TableName="EventHiveRegistrations", BillingMode="PAY_PER_REQUEST",
                       KeySchema=[{"AttributeName": "eventId", "KeyType": "HASH"},
                                  {"AttributeName": "email", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "eventId", "AttributeType": "S"},
                                             {"AttributeName": "email", "AttributeType": "S"}])
        import importlib
        import app
        self.app = importlib.reload(app)
        _, ev = req(self.app, "POST", "/events",
                    {"title": "Test", "venue": "AB1", "date": "2026-11-01", "capacity": 2}, admin=True)
        self.eid = ev["eventId"]

    def tearDown(self):
        self.mock.stop()

    def reg(self, email):
        return req(self.app, "POST", f"/events/{self.eid}/register",
                   {"name": "A", "email": email, "regno": "24BCE0001"})

    def test_create_needs_admin(self):
        code, _ = req(self.app, "POST", "/events", {"title": "x"})
        self.assertEqual(code, 403)

    def test_register_and_ticket(self):
        code, body = self.reg("a@vit.ac.in")
        self.assertEqual(code, 201)
        self.assertTrue(body["ticket"].startswith("EH-"))

    def test_duplicate_blocked(self):
        self.reg("a@vit.ac.in")
        code, body = self.reg("a@vit.ac.in")
        self.assertEqual(code, 409)
        self.assertIn("already", body["error"])

    def test_capacity_enforced(self):
        self.reg("a@vit.ac.in")
        self.reg("b@vit.ac.in")
        code, body = self.reg("c@vit.ac.in")
        self.assertEqual(code, 409)
        self.assertIn("full", body["error"])

    def test_no_overbooking_under_load(self):
        with ThreadPoolExecutor(10) as pool:
            results = list(pool.map(lambda i: self.reg(f"u{i}@vit.ac.in")[0], range(20)))
        self.assertEqual(results.count(201), 2)
        _, events = req(self.app, "GET", "/events")
        self.assertEqual(events[0]["registered"], 2)

    def test_bad_email(self):
        code, _ = self.reg("not-an-email")
        self.assertEqual(code, 400)

    def test_unknown_event(self):
        code, _ = req(self.app, "POST", "/events/nope/register",
                      {"name": "A", "email": "a@vit.ac.in", "regno": "1"})
        self.assertEqual(code, 404)

    def test_stats(self):
        self.reg("a@vit.ac.in")
        code, s = req(self.app, "GET", "/stats", admin=True)
        self.assertEqual(code, 200)
        self.assertEqual(s["fillRate"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
