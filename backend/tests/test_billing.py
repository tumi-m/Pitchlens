"""Test-mode payment integrity. No provider network or real funds are used."""

import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.vision import billing

OWNER = "b" * 32
EMAIL = "operator@example.com"
SECRET = "sk_test_fixture_not_a_real_key"


@pytest.fixture
def payments(tmp_path, monkeypatch):
    from app.vision import server

    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setattr(server, "TOKEN", "fixture-service")
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", SECRET)
    monkeypatch.setenv("PAYSTACK_CALLBACK_URL", "https://pitchlens.example/billing")
    monkeypatch.setenv("PAYSTACK_TEST_PRICE_CENTS", "1500")
    calls = []
    charges = {}

    async def provider(method, path, config, payload=None):
        calls.append((method, path, payload))
        if path == "transaction/initialize":
            ref = payload["reference"]
            charges[ref] = {
                "id": len(charges) + 1,
                "domain": "test",
                "status": "success",
                "reference": ref,
                "amount": payload["amount"],
                "currency": "ZAR",
                "customer": {"email": payload["email"]},
            }
            return {
                "reference": ref,
                "authorization_url": "https://checkout.paystack.com/test-fixture",
            }
        return charges[path.split("/")[-1]]

    monkeypatch.setattr(billing, "provider", provider)
    client = TestClient(server.app, raise_server_exceptions=False)
    client.headers.update({"Authorization": "Bearer fixture-service", "x-pitchlens-owner": OWNER})
    return client, tmp_path, calls, charges


def checkout(client, **updates):
    body = {"email": EMAIL, "requestId": uuid.uuid4().hex, **updates}
    return client.post("/billing/checkout", json=body)


def webhook(client, charge, *, event="charge.success", signature=None):
    # Whitespace is deliberate: a relay must preserve bytes, not reserialize JSON.
    raw = json.dumps({"event": event, "data": charge}, indent=2).encode()
    signature = signature or hmac.new(SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return client.post("/billing/webhook", content=raw, headers={"x-paystack-signature": signature})


def balance(client):
    return client.get("/billing/summary").json()["balance"]


def test_server_sets_price_and_checkout_id_is_idempotent(payments):
    client, root, calls, _ = payments
    request_id = uuid.uuid4().hex
    first = checkout(client, requestId=request_id, amount=1, currency="USD", credits=1000)
    second = checkout(client, requestId=request_id)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1
    assert calls[0][2]["amount"] == 1500 and calls[0][2]["currency"] == "ZAR"
    assert balance(client) == 0
    assert checkout(client, requestId=request_id, email="different@example.com").status_code == 409
    with billing.database(root) as db:
        order = db.execute("SELECT * FROM orders").fetchone()
        assert order["owner"] != OWNER and order["email_hash"] != EMAIL
        assert order["credits"] == 1


@pytest.mark.parametrize("secret", ["", "sk_live_fixture", "pk_test_fixture"])
def test_live_or_missing_secret_never_initializes(payments, monkeypatch, secret):
    client, _, calls, _ = payments
    monkeypatch.setenv("PAYSTACK_SECRET_KEY", secret)
    assert checkout(client).status_code == 503
    summary = client.get("/billing/summary").json()
    assert summary["configured"] is False and summary["liveEnabled"] is False
    assert summary["priceCents"] is None and not calls


@pytest.mark.parametrize(
    "setting,value",
    [
        ("PAYSTACK_TEST_PRICE_CENTS", ""),
        ("PAYSTACK_TEST_PRICE_CENTS", "-1"),
        ("PAYSTACK_TEST_PRICE_CENTS", "NaN"),
        ("PAYSTACK_TEST_PRICE_CENTS", "9999999999"),
        ("PAYSTACK_CALLBACK_URL", "http://public.example/billing"),
        ("PAYSTACK_CALLBACK_URL", "https://[bad"),
        ("PAYSTACK_CALLBACK_URL", "https://user:secret@example.com/billing"),
    ],
)
def test_invalid_configuration_stays_disabled(payments, monkeypatch, setting, value):
    client, _, calls, _ = payments
    monkeypatch.setenv(setting, value)
    assert checkout(client).status_code == 503 and not calls


def test_repeated_webhook_and_verify_grant_once_and_persist(payments):
    client, root, calls, charges = payments
    reference = checkout(client).json()["reference"]
    charge = charges[reference]
    assert client.post("/billing/verify", json={"reference": reference}).json()["state"] == "paid"
    for _ in range(3):
        assert webhook(client, charge).status_code == 200
        assert (
            client.post("/billing/verify", json={"reference": reference}).json()["state"] == "paid"
        )
    assert balance(client) == 1
    # New connections see the durable ledger, not a process-local balance.
    with billing.database(root) as db:
        assert db.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 1
    assert len(calls) == 2  # initialize + initial verify; already-paid reads need no provider call


def test_concurrent_duplicate_settlements_grant_once(payments):
    client, root, _, charges = payments
    reference = checkout(client).json()["reference"]
    order = billing.order_for(root, reference)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: billing.settle(root, order, charges[reference]), range(12)))
    assert balance(client) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount", 1),
        ("amount", 1500.0),
        ("amount", True),
        ("currency", "USD"),
        ("domain", "live"),
        ("reference", "pltest-" + "d" * 32),
        ("id", "123"),
        ("id", -1),
        ("customer", {"email": "wrong@example.com"}),
        ("customer", []),
        ("customer", None),
    ],
)
def test_mismatched_provider_details_do_not_grant(payments, field, value):
    client, _, _, charges = payments
    reference = checkout(client).json()["reference"]
    charges[reference][field] = value
    assert client.post("/billing/verify", json={"reference": reference}).status_code == 409
    assert balance(client) == 0


def test_unsigned_or_tampered_webhooks_do_not_grant(payments):
    client, _, _, charges = payments
    reference = checkout(client).json()["reference"]
    assert webhook(client, charges[reference], signature="0" * 128).status_code == 401
    assert (
        client.post(
            "/billing/webhook", json={"event": "charge.success", "data": charges[reference]}
        ).status_code
        == 401
    )
    assert balance(client) == 0


def test_pending_charge_and_callback_do_not_grant(payments):
    client, _, _, charges = payments
    reference = checkout(client).json()["reference"]
    charges[reference]["status"] = "pending"
    assert (
        client.post("/billing/verify", json={"reference": reference, "status": "success"}).json()[
            "state"
        ]
        == "pending"
    )
    assert balance(client) == 0


def test_unrelated_signed_events_acknowledged_without_credit(payments):
    client, _, _, _ = payments
    assert webhook(client, {"reference": "different-product"}).json()["ignored"]
    assert webhook(client, {}, event="unrelated.event").json()["ignored"]
    assert balance(client) == 0


def test_transaction_cannot_be_reused_for_another_order(payments):
    client, _, _, charges = payments
    first = checkout(client).json()["reference"]
    second = checkout(client).json()["reference"]
    assert webhook(client, charges[first]).status_code == 200
    charges[second]["id"] = charges[first]["id"]
    assert webhook(client, charges[second]).status_code == 409
    assert balance(client) == 1


def test_service_and_owner_authorization(payments):
    client, _, calls, _ = payments
    reference = checkout(client).json()["reference"]
    client.headers["x-pitchlens-owner"] = "c" * 32
    assert client.post("/billing/verify", json={"reference": reference}).status_code == 404
    assert client.get("/billing/summary").json()["orders"] == []
    assert len(calls) == 1
    del client.headers["x-pitchlens-owner"]
    assert client.get("/billing/summary").status_code == 401
    assert checkout(client).status_code == 401
    del client.headers["Authorization"]
    assert client.post("/billing/webhook").status_code == 401


def test_initialize_timeout_does_not_make_a_second_order_on_retry(payments, monkeypatch):
    client, root, _, _ = payments

    async def timeout(*args):
        raise HTTPException(502, "Request timed out")

    monkeypatch.setattr(billing, "provider", timeout)
    request_id = uuid.uuid4().hex
    assert checkout(client, requestId=request_id).status_code == 502
    assert checkout(client, requestId=request_id).status_code == 409
    assert len(client.get("/billing/summary").json()["orders"]) == 1
    assert balance(client) == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://checkout.paystack.com/a",
        "https://attacker.example/a",
        "https://checkout.paystack.com.attacker.example/a",
        "https://[bad",
    ],
)
def test_unexpected_checkout_url_rejected(payments, monkeypatch, url):
    client, _, _, _ = payments

    async def bad_url(method, path, config, payload):
        return {"reference": payload["reference"], "authorization_url": url}

    monkeypatch.setattr(billing, "provider", bad_url)
    assert checkout(client).status_code == 502
    assert balance(client) == 0


def test_bounded_and_well_formed_payment_requests(payments):
    client, _, _, _ = payments
    for raw in [b"[]", b"null", b'{"amount": NaN}', b"\xff"]:
        assert client.post("/billing/checkout", content=raw).status_code == 400
    assert (
        client.post("/billing/checkout", content=b"a" * (billing.MAX_BODY + 1)).status_code == 413
    )
    for _ in range(10):
        assert checkout(client).status_code == 200
    assert checkout(client).status_code == 429


def test_settlement_storage_failure_rolls_back_before_acknowledging(payments):
    client, root, _, charges = payments
    reference = checkout(client).json()["reference"]
    with billing.database(root) as db:
        db.execute("""CREATE TRIGGER simulate_full_disk BEFORE INSERT ON ledger
                      BEGIN SELECT RAISE(ABORT, 'storage unavailable'); END""")
    assert webhook(client, charges[reference]).status_code == 500
    with billing.database(root) as db:
        assert db.execute("SELECT COUNT(*) FROM settlements").fetchone()[0] == 0
        assert db.execute("SELECT state FROM orders").fetchone()[0] == "pending"
        db.execute("DROP TRIGGER simulate_full_disk")
    assert balance(client) == 0
    assert webhook(client, charges[reference]).status_code == 200
    assert balance(client) == 1
