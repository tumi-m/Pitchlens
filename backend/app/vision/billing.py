"""Paystack/ZAR test checkout and a transactional, persistent sandbox credit ledger.

Live keys are deliberately refused until account recovery, commercial terms and
refund operations have passed launch review. No card details enter this service.
"""

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request

MAX_BODY = 64 * 1024


def configuration():
    secret = os.getenv("PAYSTACK_SECRET_KEY", "")
    callback = os.getenv("PAYSTACK_CALLBACK_URL", "")
    try:
        amount = int(os.getenv("PAYSTACK_TEST_PRICE_CENTS", "0"))
    except ValueError:
        amount = 0
    try:
        url = urlparse(callback)
        callback_ok = (
            bool(url.hostname)
            and not url.username
            and not url.password
            and (
                url.scheme == "https"
                or (url.hostname in ("localhost", "127.0.0.1") and url.scheme == "http")
            )
        )
    except ValueError:
        callback_ok = False
    enabled = secret.startswith("sk_test_") and 100 <= amount <= 1000000 and callback_ok
    return {"secret": secret, "callback": callback, "amount": amount, "enabled": enabled}


def configured():
    config = configuration()
    if not config["enabled"]:
        raise HTTPException(
            503, "Paystack test checkout is not configured. Live payments are disabled."
        )
    return config


def owner_key(request):
    owner = request.headers.get("x-pitchlens-owner", "")
    if not re.fullmatch(r"[a-f0-9]{32}", owner):
        raise HTTPException(401, "Open billing in the browser that owns this workspace")
    # Do not duplicate the browser access capability in the accounting database.
    return hashlib.sha256(owner.encode()).hexdigest()


@contextmanager
def database(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "billing-test.sqlite3", timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
          CREATE TABLE IF NOT EXISTS orders (
            reference TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
            email_hash TEXT NOT NULL, amount INTEGER NOT NULL, credits INTEGER NOT NULL,
            state TEXT NOT NULL, url TEXT, created REAL NOT NULL,
            UNIQUE(owner, request_id));
          CREATE TABLE IF NOT EXISTS settlements (
            transaction_id TEXT PRIMARY KEY, reference TEXT NOT NULL UNIQUE,
            FOREIGN KEY(reference) REFERENCES orders(reference));
          CREATE TABLE IF NOT EXISTS ledger (
            entry TEXT PRIMARY KEY, owner TEXT NOT NULL, delta INTEGER NOT NULL,
            reference TEXT NOT NULL, created REAL NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


async def body_bytes(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise HTTPException(413, "Payment request is too large")
    return bytes(data)


def parse_body(raw):
    try:

        def invalid(value):
            raise ValueError(value)

        data = json.loads(raw, parse_constant=invalid)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid payment request") from None


async def provider(method, path, config, payload=None):
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(
                method,
                f"https://api.paystack.co/{path}",
                headers={"Authorization": f"Bearer {config['secret']}"},
                json=payload,
            )
        data = response.json()
        if (
            response.status_code >= 400
            or data.get("status") is not True
            or not isinstance(data.get("data"), dict)
        ):
            raise ValueError()
        return data["data"]
    except (httpx.HTTPError, ValueError, AttributeError):
        raise HTTPException(
            502, "Paystack could not confirm this request. Try again shortly."
        ) from None


def order_for(root, reference, owner=None):
    if not isinstance(reference, str) or not re.fullmatch(r"pltest-[a-f0-9]{32}", reference):
        raise HTTPException(404, "Payment not found")
    with database(root) as db:
        row = db.execute("SELECT * FROM orders WHERE reference=?", (reference,)).fetchone()
    if row is None or (owner is not None and row["owner"] != owner):
        raise HTTPException(404, "Payment not found")
    return dict(row)


def settle(root, order, data):
    """Validate the provider response, then grant credits atomically exactly once."""
    if data.get("status") != "success":
        return {"state": "pending", "reference": order["reference"], "mode": "test"}
    customer = data.get("customer")
    email = customer.get("email", "") if isinstance(customer, dict) else ""
    transaction = data.get("id")
    if (
        data.get("reference") != order["reference"]
        or data.get("currency") != "ZAR"
        or type(data.get("amount")) is not int
        or data["amount"] != order["amount"]
        or data.get("domain") != "test"
        or type(transaction) is not int
        or transaction <= 0
        or not isinstance(email, str)
        or hashlib.sha256(email.strip().lower().encode()).hexdigest() != order["email_hash"]
    ):
        raise HTTPException(409, "Payment details did not match this test order. No credits added.")
    with database(root) as db:
        previous = db.execute(
            "SELECT * FROM settlements WHERE transaction_id=? OR reference=?",
            (str(transaction), order["reference"]),
        ).fetchone()
        if previous and (
            previous["reference"] != order["reference"]
            or previous["transaction_id"] != str(transaction)
        ):
            raise HTTPException(409, "Payment was already allocated to another order")
        db.execute(
            "INSERT OR IGNORE INTO settlements VALUES (?, ?)",
            (str(transaction), order["reference"]),
        )
        db.execute(
            "INSERT OR IGNORE INTO ledger VALUES (?, ?, ?, ?, ?)",
            (
                f"payment:{order['reference']}",
                order["owner"],
                order["credits"],
                order["reference"],
                time.time(),
            ),
        )
        db.execute("UPDATE orders SET state='paid' WHERE reference=?", (order["reference"],))
    return {"state": "paid", "reference": order["reference"], "mode": "test"}


def router(root):
    routes = APIRouter(prefix="/billing")

    @routes.get("/summary")
    def summary(request: Request):
        owner = owner_key(request)
        config = configuration()
        with database(root()) as db:
            balance = db.execute(
                "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE owner=?", (owner,)
            ).fetchone()[0]
            orders = db.execute(
                "SELECT reference,amount,state,created FROM orders WHERE owner=? "
                "ORDER BY created DESC LIMIT 20",
                (owner,),
            ).fetchall()
        return {
            "mode": "test",
            "liveEnabled": False,
            "configured": config["enabled"],
            "currency": "ZAR",
            "priceCents": config["amount"] if config["enabled"] else None,
            "creditsPerOrder": 1,
            "balance": balance,
            "orders": [dict(row) for row in orders],
        }

    @routes.post("/checkout")
    async def checkout(request: Request):
        owner = owner_key(request)
        config = configured()
        body = parse_body(await body_bytes(request))
        email, request_id = body.get("email"), body.get("requestId")
        if (
            not isinstance(email, str)
            or len(email) > 254
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
        ):
            raise HTTPException(400, "Enter a valid email address")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9-]{32,36}", request_id):
            raise HTTPException(400, "Invalid checkout request ID")
        email = email.strip().lower()
        email_hash = hashlib.sha256(email.encode()).hexdigest()
        with database(root()) as db:
            existing = db.execute(
                "SELECT * FROM orders WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if existing:
                if existing["email_hash"] != email_hash:
                    raise HTTPException(409, "Checkout request ID already used")
                if existing["url"]:
                    return {
                        "url": existing["url"],
                        "reference": existing["reference"],
                        "mode": "test",
                    }
                raise HTTPException(
                    409,
                    "This checkout may still be initializing. "
                    "Check payment history before starting another.",
                )
            recent = db.execute(
                "SELECT COUNT(*) FROM orders WHERE owner=? AND created>?",
                (owner, time.time() - 3600),
            ).fetchone()[0]
            if recent >= 10:
                raise HTTPException(429, "Too many checkout attempts. Try again later.")
            reference = f"pltest-{uuid.uuid4().hex}"
            db.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?)",
                (reference, owner, request_id, email_hash, config["amount"], 1, time.time()),
            )
        data = await provider(
            "POST",
            "transaction/initialize",
            config,
            {
                "email": email,
                "amount": config["amount"],
                "currency": "ZAR",
                "reference": reference,
                "callback_url": config["callback"],
            },
        )
        url = data.get("authorization_url", "")
        try:
            target = urlparse(url) if isinstance(url, str) else None
        except ValueError:
            target = None
        if (
            data.get("reference") != reference
            or not target
            or target.scheme != "https"
            or target.netloc != "checkout.paystack.com"
        ):
            raise HTTPException(502, "Paystack returned an unexpected checkout destination")
        with database(root()) as db:
            db.execute("UPDATE orders SET url=? WHERE reference=?", (url, reference))
        return {"url": url, "reference": reference, "mode": "test"}

    @routes.post("/verify")
    async def verify(request: Request):
        owner = owner_key(request)
        config = configured()
        body = parse_body(await body_bytes(request))
        order = order_for(root(), body.get("reference"), owner)
        if order["state"] == "paid":
            return {"state": "paid", "reference": order["reference"], "mode": "test"}
        data = await provider("GET", f"transaction/verify/{order['reference']}", config)
        return settle(root(), order, data)

    @routes.post("/webhook")
    async def webhook(request: Request):
        config = configured()
        raw = await body_bytes(request)
        signature = request.headers.get("x-paystack-signature", "")
        expected = hmac.new(config["secret"].encode(), raw, hashlib.sha512).hexdigest()
        if not re.fullmatch(r"[a-f0-9]{128}", signature) or not hmac.compare_digest(
            signature, expected
        ):
            raise HTTPException(401, "Invalid payment signature")
        data = parse_body(raw)
        if data.get("event") != "charge.success":
            return {"received": True, "ignored": True}
        charge = data.get("data")
        if not isinstance(charge, dict):
            raise HTTPException(400, "Invalid payment event")
        try:
            order = order_for(root(), charge.get("reference"))
        except HTTPException as exc:
            if exc.status_code == 404:
                return {"received": True, "ignored": True}
            raise
        # Reconciliation and browser verification share the same transaction.
        settle(root(), order, charge)
        return {"received": True}

    return routes
