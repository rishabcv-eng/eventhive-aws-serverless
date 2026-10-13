import json
import os
import re
import time
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

ddb = boto3.resource("dynamodb")
events = ddb.Table(os.environ.get("EVENTS_TABLE", "EventHiveEvents"))
regs = ddb.Table(os.environ.get("REGS_TABLE", "EventHiveRegistrations"))
sns = boto3.client("sns")

TOPIC_ARN = os.environ.get("TOPIC_ARN", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "vit-admin")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def to_plain(obj):
    if isinstance(obj, list):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def reply(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,x-admin-key",
            "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
        },
        "body": json.dumps(to_plain(body)),
    }


def is_admin(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get("x-admin-key") == ADMIN_KEY


def list_events():
    items = events.scan().get("Items", [])
    items.sort(key=lambda e: e.get("date", ""))
    return reply(200, items)


def create_event(body):
    missing = [f for f in ("title", "venue", "date", "capacity") if not body.get(f)]
    if missing:
        return reply(400, {"error": "missing fields: " + ", ".join(missing)})
    cap = int(body["capacity"])
    if cap < 1 or cap > 5000:
        return reply(400, {"error": "capacity must be between 1 and 5000"})
    item = {
        "eventId": uuid.uuid4().hex[:8],
        "title": body["title"].strip(),
        "venue": body["venue"].strip(),
        "date": body["date"],
        "category": body.get("category", "General"),
        "description": body.get("description", ""),
        "capacity": cap,
        "registered": 0,
        "createdAt": int(time.time()),
    }
    events.put_item(Item=item)
    return reply(201, item)


def register(event_id, body):
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    regno = (body.get("regno") or "").strip().upper()
    if not name or not regno or not EMAIL_RE.match(email):
        return reply(400, {"error": "name, valid email and reg no are required"})

    ticket = "EH-" + uuid.uuid4().hex[:6].upper()
    try:
        ddb.meta.client.transact_write_items(TransactItems=[
            {
                "Update": {
                    "TableName": events.name,
                    "Key": {"eventId": event_id},
                    "UpdateExpression": "SET registered = registered + :one",
                    "ConditionExpression": "attribute_exists(eventId) AND registered < #cap",
                    "ExpressionAttributeNames": {"#cap": "capacity"},
                    "ExpressionAttributeValues": {":one": 1},
                }
            },
            {
                "Put": {
                    "TableName": regs.name,
                    "Item": {
                        "eventId": event_id,
                        "email": email,
                        "name": name,
                        "regno": regno,
                        "ticket": ticket,
                        "registeredAt": int(time.time()),
                    },
                    "ConditionExpression": "attribute_not_exists(email)",
                }
            },
        ])
    except ClientError as e:
        if e.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
        if len(reasons) > 1 and reasons[1] == "ConditionalCheckFailed":
            return reply(409, {"error": "you are already registered for this event"})
        found = events.get_item(Key={"eventId": event_id}).get("Item")
        if not found:
            return reply(404, {"error": "event not found"})
        return reply(409, {"error": "sorry, this event is full"})

    if TOPIC_ARN:
        title = events.get_item(Key={"eventId": event_id})["Item"]["title"]
        sns.publish(
            TopicArn=TOPIC_ARN,
            Subject=f"EventHive ticket {ticket}",
            Message=f"Hi {name}, you're registered for {title}. Ticket: {ticket}",
        )
    return reply(201, {"ticket": ticket, "eventId": event_id, "name": name})


def list_registrations(event_id):
    res = regs.query(KeyConditionExpression=Key("eventId").eq(event_id))
    return reply(200, res.get("Items", []))


def delete_event(event_id):
    res = regs.query(KeyConditionExpression=Key("eventId").eq(event_id))
    with regs.batch_writer() as batch:
        for r in res.get("Items", []):
            batch.delete_item(Key={"eventId": event_id, "email": r["email"]})
    events.delete_item(Key={"eventId": event_id})
    return reply(200, {"deleted": event_id})


def stats():
    items = events.scan().get("Items", [])
    total_cap = sum(int(e["capacity"]) for e in items)
    total_reg = sum(int(e["registered"]) for e in items)
    per_event = [
        {
            "title": e["title"],
            "registered": int(e["registered"]),
            "capacity": int(e["capacity"]),
            "fill": round(100 * int(e["registered"]) / int(e["capacity"]), 1),
        }
        for e in items
    ]
    per_event.sort(key=lambda x: -x["fill"])
    by_cat = {}
    for e in items:
        by_cat[e.get("category", "General")] = by_cat.get(e.get("category", "General"), 0) + int(e["registered"])
    return reply(200, {
        "events": len(items),
        "registrations": total_reg,
        "capacity": total_cap,
        "fillRate": round(100 * total_reg / total_cap, 1) if total_cap else 0,
        "perEvent": per_event,
        "byCategory": by_cat,
    })


def handler(event, context=None):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/").rstrip("/") or "/"
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return reply(400, {"error": "invalid json"})

    if method == "OPTIONS":
        return reply(204, {})

    parts = path.strip("/").split("/")
    try:
        if path == "/events" and method == "GET":
            return list_events()
        if path == "/events" and method == "POST":
            return create_event(body) if is_admin(event) else reply(403, {"error": "admin key required"})
        if path == "/stats" and method == "GET":
            return stats() if is_admin(event) else reply(403, {"error": "admin key required"})
        if len(parts) == 3 and parts[0] == "events" and parts[2] == "register" and method == "POST":
            return register(parts[1], body)
        if len(parts) == 3 and parts[0] == "events" and parts[2] == "registrations" and method == "GET":
            return list_registrations(parts[1]) if is_admin(event) else reply(403, {"error": "admin key required"})
        if len(parts) == 2 and parts[0] == "events" and method == "DELETE":
            return delete_event(parts[1]) if is_admin(event) else reply(403, {"error": "admin key required"})
    except (ValueError, KeyError) as e:
        return reply(400, {"error": str(e)})
    return reply(404, {"error": f"no route for {method} {path}"})
