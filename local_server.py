import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")

from moto import mock_aws

mock = mock_aws()
mock.start()

import boto3

ddb = boto3.client("dynamodb")
ddb.create_table(
    TableName="EventHiveEvents",
    KeySchema=[{"AttributeName": "eventId", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "eventId", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)
ddb.create_table(
    TableName="EventHiveRegistrations",
    KeySchema=[
        {"AttributeName": "eventId", "KeyType": "HASH"},
        {"AttributeName": "email", "KeyType": "RANGE"},
    ],
    AttributeDefinitions=[
        {"AttributeName": "eventId", "AttributeType": "S"},
        {"AttributeName": "email", "AttributeType": "S"},
    ],
    BillingMode="PAY_PER_REQUEST",
)
os.environ["TOPIC_ARN"] = boto3.client("sns").create_topic(Name="EventHiveTickets")["TopicArn"]

import app

HERE = Path(__file__).parent
ADMIN = {"x-admin-key": app.ADMIN_KEY}

seed = [
    ("AWS Cloud Practitioner Bootcamp", "AB1 - 405", "2026-10-24", 60, "Workshop"),
    ("Serverless Hackathon 24h", "Netaji Auditorium", "2026-10-31", 120, "Hackathon"),
    ("Intro to DynamoDB Data Modelling", "AB2 - 112", "2026-11-04", 40, "Workshop"),
    ("Cloud Careers Panel", "MG Auditorium", "2026-11-08", 200, "Talk"),
    ("Inter-hostel Cricket League", "VIT Ground", "2026-11-12", 22, "Sports"),
]
ids = []
for title, venue, date, cap, cat in seed:
    r = app.handler({
        "requestContext": {"http": {"method": "POST"}},
        "rawPath": "/events",
        "headers": ADMIN,
        "body": json.dumps({"title": title, "venue": venue, "date": date, "capacity": cap, "category": cat}),
    })
    ids.append(json.loads(r["body"])["eventId"])

demo_people = [("Arjun", "24BCE1001"), ("Meera", "24BCE1002"), ("Karthik", "24BCE1003"), ("Divya", "24BCE1004"),
               ("Sanjay", "24BCE1005"), ("Priya", "24BCE1006"), ("Rahul", "24BCE1007"), ("Ananya", "24BCE1008")]
plan = {0: 8, 1: 6, 2: 3, 3: 5, 4: 8}
for idx, count in plan.items():
    for name, reg in demo_people[:count]:
        app.handler({
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": f"/events/{ids[idx]}/register",
            "body": json.dumps({"name": name, "email": f"{name.lower()}@vitstudent.ac.in", "regno": reg}),
        })


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    def _api(self):
        length = int(self.headers.get("Content-Length") or 0)
        event = {
            "requestContext": {"http": {"method": self.command}},
            "rawPath": self.path[len("/api"):].split("?")[0] or "/",
            "headers": dict(self.headers),
            "body": self.rfile.read(length).decode() if length else None,
        }
        res = app.handler(event)
        self.send_response(res["statusCode"])
        for k, v in res["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(res["body"].encode())

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api()
        name = "index.html" if self.path in ("/", "/index.html") else self.path.lstrip("/")
        f = HERE / name
        if name not in ("index.html", "config.js") or not f.exists():
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html" if name.endswith(".html") else "application/javascript")
        self.end_headers()
        self.wfile.write(f.read_bytes())

    do_POST = _api
    do_DELETE = _api
    do_OPTIONS = _api


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"EventHive running locally on http://localhost:{port}  (admin key: {app.ADMIN_KEY})")
    ThreadingHTTPServer(("", port), Handler).serve_forever()
