import { test, expect, Page } from "@playwright/test";

const REFERENCE = `pltest-${"a".repeat(32)}`;

async function setup(page: Page, configured = true) {
  const state = { balance: 0, paid: false, verifies: 0 };
  await page.addInitScript(() => localStorage.setItem("pitchlens-vision-access", "fixture-access"));
  await page.route("**/api/vision/billing/summary", (route) => route.fulfill({ json: {
    mode: "test", liveEnabled: false, configured, currency: "ZAR", priceCents: configured ? 1500 : null,
    balance: state.balance, orders: state.paid ? [{ reference: REFERENCE, amount: 1500, state: "paid", created: 1000 }] : [],
  } }));
  return state;
}

test("unconfigured merchant shows setup state without offering a payment", async ({ page }) => {
  await setup(page, false);
  await page.goto("/billing");
  await expect(page.getByRole("heading", { name: "Payment sandbox" })).toBeVisible();
  await expect(page.getByText(/Awaiting merchant setup/)).toBeVisible();
  await expect(page.getByTestId("sandbox-balance")).toHaveText("0 test credits");
  await expect(page.getByRole("button", { name: "Open Paystack test checkout" })).toHaveCount(0);
  await expect(page.getByText(/Live payments are disabled/)).toBeVisible();
});

test("callback success query never grants credit without server verification", async ({ page }) => {
  const state = await setup(page);
  await page.route("**/api/vision/billing/verify", (route) => {
    state.verifies++;
    expect(route.request().postDataJSON()).toEqual({ reference: REFERENCE });
    return route.fulfill({ json: { state: "pending", mode: "test", reference: REFERENCE } });
  });
  await page.goto(`/billing?reference=${REFERENCE}&status=success`);
  await expect(page.getByText(/Payment is not confirmed yet/)).toBeVisible();
  await expect(page.getByTestId("sandbox-balance")).toHaveText("0 test credits");
  expect(state.verifies).toBe(1);
  await expect(page.getByRole("button", { name: "Check returned payment" })).toBeVisible();
});

test("verified return refreshes durable balance and clears callback", async ({ page }) => {
  const state = await setup(page);
  await page.route("**/api/vision/billing/verify", (route) => {
    state.balance = 1; state.paid = true;
    return route.fulfill({ json: { state: "paid", mode: "test", reference: REFERENCE } });
  });
  await page.goto(`/billing?reference=${REFERENCE}`);
  await expect(page.getByText(/Test payment verified/)).toBeVisible();
  await expect(page.getByTestId("sandbox-balance")).toHaveText("1 test credit");
  await expect(page.getByText(/R\s*15,00 · Verified test payment/)).toBeVisible();
  await expect(page).toHaveURL(/\/billing$/);
});

test("checkout displays ZAR and retries lost responses with the same request ID", async ({ page }) => {
  await setup(page);
  const bodies: { email: string; requestId: string }[] = [];
  await page.route("**/api/vision/billing/checkout", (route) => {
    bodies.push(route.request().postDataJSON());
    expect(route.request().headers()["x-pitchlens-access"]).toBe("fixture-access");
    expect(route.request().headers()["x-pitchlens-owner"]).toMatch(/^[a-f0-9]{32}$/);
    return route.fulfill({ status: 502, json: { detail: "Temporary payment error" } });
  });
  await page.goto("/billing");
  await expect(page.getByText(/One sandbox credit/)).toContainText(/R\s*15,00/);
  await page.getByLabel("Test email").fill("operator@example.com");
  await page.getByRole("button", { name: "Open Paystack test checkout" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("Temporary payment error");
  await page.getByRole("button", { name: "Open Paystack test checkout" }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(Object.keys(bodies[0]).sort()).toEqual(["email", "requestId"]);
});

test("checkout only navigates to Paystack hosted checkout", async ({ page }) => {
  await setup(page);
  await page.route("**/api/vision/billing/checkout", (route) => route.fulfill({ json: { url: "https://checkout.paystack.com/pitchlens-test" } }));
  await page.route("https://checkout.paystack.com/pitchlens-test", (route) => route.fulfill({ contentType: "text/html", body: "<h1>Mock Paystack checkout</h1>" }));
  await page.goto("/billing");
  await page.getByLabel("Test email").fill("operator@example.com");
  await page.getByRole("button", { name: "Open Paystack test checkout" }).click();
  await expect(page).toHaveURL("https://checkout.paystack.com/pitchlens-test");
});

test("untrusted checkout destinations leave the browser on Pitchlens", async ({ page }) => {
  await setup(page);
  await page.route("**/api/vision/billing/checkout", (route) => route.fulfill({ json: { url: "https://attacker.example/checkout" } }));
  await page.goto("/billing");
  await page.getByLabel("Test email").fill("operator@example.com");
  await page.getByRole("button", { name: "Open Paystack test checkout" }).click();
  await expect(page.getByRole("main").getByRole("alert")).toHaveText("The checkout destination could not be verified.");
  await expect(page).toHaveURL(/\/billing$/);
});
