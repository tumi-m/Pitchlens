# Paystack preparation for South Africa

Updated 28 September 2026. **Sandbox only; no merchant account or keys supplied.**

## What is implemented

Open `/billing` on the website. It shows a ZAR test amount, hosted Paystack
checkout, server verification, the browser's test payment history and a sandbox
credit balance. With no configuration it shows “Awaiting merchant setup” and
never offers a payment. It is deliberately not advertised as a purchase plan.

- The worker sets the amount and currency. Browser-supplied prices are ignored.
- Checkout initialization has an owner-scoped idempotency ID. A retry after a lost
  browser response reuses the saved checkout; an uncertain provider initialization
  remains pending and is not silently initialized a second time.
- Redirects are restricted to `https://checkout.paystack.com`.
- Returning to the callback does not prove payment. The server verifies the
  reference, successful status, test domain, exact integer amount, ZAR currency,
  customer email hash and provider transaction ID before crediting.
- Webhooks require HMAC-SHA512 over the original request bytes. Browser verification
  and webhook settlement share one database transaction. Unique order, transaction
  and ledger constraints prevent duplicate credits, including concurrent delivery.
- The worker refuses live keys. Sandbox credits have no cash value, do not unlock
  analysis, and are not a commercial entitlement system.

This follows Paystack's [accept-payment flow](https://paystack.com/docs/payments/accept-payments/),
[verification API](https://paystack.com/docs/api/transaction/#verify) and
[webhook signature guidance](https://paystack.com/docs/payments/webhooks/).
Paystack's [API currency reference](https://paystack.com/docs/api/) specifies ZAR
for South Africa, with amounts sent in cents.

## Configure after creating the merchant account

1. Create your business's Paystack account and select South Africa. Obtain its
   **test secret key** from the merchant dashboard. Do not paste it into chat,
   commit it, or use a `NEXT_PUBLIC_` environment variable.
2. On the **vision worker** (Railway), set these environment variables and redeploy:

   ```dotenv
   PAYSTACK_SECRET_KEY=<your sk_test_ key>
   PAYSTACK_CALLBACK_URL=https://pitchlens1.vercel.app/billing
   PAYSTACK_TEST_PRICE_CENTS=1500
   ```

   `1500` is an illustrative **R15 test amount**, not a selected selling price.
   Choose a test amount between 100 and 1,000,000 cents. No amount is configured
   by default. Both the secret and callback must be present for checkout to open.
   Do not use the public key (`pk_test_`) or a live key (`sk_live_`).
3. The website uses its existing server-only `VISION_SERVICE_URL`,
   `VISION_SERVICE_TOKEN` and `VISION_ACCESS_CODE`. No Paystack key is needed on
   Vercel. Checkout and verification require the pilot access code and the same
   browser ownership capability as private vision data.
4. In the Paystack dashboard's **test webhook** settings, use:

   ```text
   https://pitchlens1.vercel.app/api/payments/paystack/webhook
   ```

   This endpoint accepts Paystack deliveries without browser cookies or the pilot
   code. It forwards the exact signed bytes to the authenticated worker; the
   worker validates the signature. It acknowledges only after the worker commits.
   Invalid signatures are rejected; worker failures remain retryable. The normal
   `/api/vision` browser proxy cannot relay webhook submissions.
5. Visit `/billing` in the same browser used for the pilot. Set the pilot access
   code if needed, use a test email, and open test checkout. Use the test payment
   details supplied by Paystack on its hosted checkout page.
6. Confirm the return shows one verified test payment and one sandbox credit.
   Redeliver the event and reload the callback: the balance must remain one.
   Verify the same result when closing checkout before returning, then checking
   payment history after webhook delivery.

If you change the production domain, update the worker callback and provider
webhook settings together. Use one environment's worker, key and callback per
sandbox; do not mix preview and production configuration.

## Persistence and recovery

The worker stores `billing-test.sqlite3` under `VISION_DATA_DIR` using SQLite WAL
and transactions. Mount that directory on persistent storage. It must survive
worker restarts and redeploys. Use SQLite's online backup API or stop the worker
before taking a consistent backup; copying the main file alone while WAL writes
are active is not a complete backup. Test restoration before collecting money.

The sandbox supports the current single-worker deployment. Do not add replicas
with separate disks or put the SQLite database on a shared network filesystem.
Move to a managed transactional database before a distributed paid service.

Only hashed browser capabilities and email addresses are stored in the sandbox
ledger; the email is sent to Paystack for initialization. Hashing is not account
recovery or anonymisation. Clearing browser storage loses self-service access to
that history. Real account/organization identity is required for live billing.
Deleting a match does not delete payment records. No card details enter Pitchlens.

If initialization times out, the order stays unconfirmed. Check it in payment
history and the merchant dashboard before starting another attempt. Provider
verification or an authenticated success webhook can settle it later even if the
original initialization response was lost. Never manually change a balance to
make the UI look successful. Unknown references and unrelated signed events are
acknowledged without credits.

## Validation and remaining live-launch work

Automated tests use provider fixtures, including amount/currency/domain mismatch,
wrong owner, malformed signatures, initialization failure, concurrent duplicate
settlement, transaction reuse, raw-byte forwarding, blocked live keys and browser
return handling. **No real Paystack sandbox transaction has been run yet** because
no merchant account/test key is available. Complete steps 5–6 before claiming that
provider integration is verified.

Before enabling real money, implement and validate:

- Durable customer/organization accounts, recovery and account-bound payment
  history; migrate from browser capabilities.
- A reviewed product/price catalogue and ZAR checkout terms. No subscription,
  auto-renewal, VAT invoice or live price exists in this sandbox.
- Analysis entitlement reservation, consumption and release on failure. The
  sandbox balance must never be treated as a paid analysis entitlement.
- Refunds, reversals, disputes, reconciliation jobs and a support/audit workflow.
  These events currently do not alter sandbox balances; the implementation is
  intentionally limited to successful test charges.
- Production database backups and restore evidence, operational alerts, provider
  timeout recovery, authenticated per-account limits and measured GPU costs.
- Business activation, customer terms/privacy and retention decisions for the
  South African launch, and the model/data and accuracy gates in RELEASE-GATES.md.

Live support must be an explicit reviewed implementation with its own tests.
Replacing a test key with a live key will **not** enable payments.
