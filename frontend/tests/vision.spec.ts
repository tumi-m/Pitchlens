import { test, expect, Page } from "@playwright/test";
import path from "node:path";

const ID = "a".repeat(32);
const FIXTURE = path.join(__dirname, "fixtures/review.mp4");

async function mockChunkedWorker(page: Page) {
  const calls = { created: 0, chunks: [] as number[], started: 0, owner: "", diagnostic: "", start: "" };
  let received = 0;
  await page.route(/\/api\/vision\/jobs\?/, async (r) => {
    calls.created++;
    calls.diagnostic = new URL(r.request().url()).searchParams.get("diagnostic") || "";
    calls.start = new URL(r.request().url()).searchParams.get("start") || "";
    calls.owner = (await r.request().headerValue("x-pitchlens-owner")) || "";
    await r.fulfill({
      json: {
        id: ID,
        title: "Vision test",
        status: "uploading",
        stage: "Receiving video",
        progress: 0,
        createdAt: 0,
      },
    });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), (r) => {
    const offset = Number(new URL(r.request().url()).searchParams.get("offset"));
    const size = Number(r.request().headers()["content-length"] || 0) ||
      (r.request().postDataBuffer()?.length ?? 0);
    calls.chunks.push(offset);
    received = Math.max(received, offset + size);
    return r.fulfill({ json: { received } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), (r) => {
    calls.started++;
    return r.fulfill({
      json: {
        id: ID,
        title: "Vision test",
        status: "processing",
        stage: "Opening video",
        progress: 0,
        createdAt: 0,
      },
    });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) =>
    r.fulfill({
      json: {
        id: ID,
        title: "Vision test",
        status: "processing",
        stage: "Detecting players",
        progress: 25,
        createdAt: 0,
        processedSeconds: 1,
      },
    }),
  );
  return calls;
}

test("automatic upload sends the video in chunks and opens the worker job", async ({
  page,
}) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({ json: { available: true, hosted: false, diagnostics: true } }),
  );
  const calls = await mockChunkedWorker(page);
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await expect(page.getByRole("heading", { name: "Video checked on your device" })).toBeVisible();
  expect(calls.created).toBe(0);
  await page.getByLabel("Test start (seconds)").fill("1");
  await page
    .getByRole("button", { name: "Analyse video automatically", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Detecting players", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Tag a moment" })).toHaveCount(
    0,
  );
  expect(calls.created).toBe(1);
  expect(calls.diagnostic).toBe("true");
  expect(calls.start).toBe("1");
  expect(calls.chunks[0]).toBe(0);
  expect(calls.started).toBe(1);
  expect(calls.owner).toMatch(/^[a-f0-9]{32}$/);
});

test("hosted analysis asks for the access code and reports a rejected code", async ({
  page,
}) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({
      json: {
        available: true,
        hosted: true,
        accessRequired: true,
        retentionHours: 168,
      },
    }),
  );
  await page.route(/\/api\/vision\/jobs\?/, (r) =>
    r.fulfill({
      status: 401,
      json: { detail: "Enter the access code to analyse videos.", code: "access" },
    }),
  );
  await page.goto("/upload");
  await expect(
    page.getByText("Uploaded to the Pitchlens analysis server; footage deleted after 7 days"),
  ).toBeVisible();
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page
    .getByRole("button", { name: "Analyse video automatically", exact: true })
    .click();
  await expect(page.getByText("Enter the access code first.")).toBeVisible();
  await page.getByLabel("Access code").fill("wrong");
  await page
    .getByRole("button", { name: "Analyse video automatically", exact: true })
    .click();
  await expect(
    page.getByText("Enter the access code to analyse videos."),
  ).toBeVisible();
});

test("a site without a vision worker says so and keeps manual review", async ({
  page,
}) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({
      status: 503,
      json: { available: false, configured: false, detail: "Not configured" },
    }),
  );
  await page.goto("/upload");
  await expect(
    page.getByRole("button", { name: "Analyse video automatically", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Start with the footage." }),
  ).toBeVisible();
});

test("unavailable local vision worker does not silently fall back to a manual report", async ({
  page,
}) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({
      status: 503,
      json: { available: false, hosted: false, detail: "Unavailable" },
    }),
  );
  await page.goto("/upload");
  await expect(
    page.getByText("Start the local vision worker to analyse a match."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", {
      name: "Analyse video automatically",
      exact: true,
    }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Start with the footage." }),
  ).toBeVisible();
});

test("a YouTube link is sent to the worker only after the rights confirmation", async ({
  page,
}) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({ json: { available: true, hosted: false, diagnostics: true } }),
  );
  let requested = "";
  await page.route(/\/api\/vision\/jobs\/from-url\?/, (r) => {
    requested = r.request().url();
    return r.fulfill({
      json: { id: ID, title: "YouTube match", status: "uploading",
        stage: "Waiting to download from YouTube", progress: 0, createdAt: 0 },
    });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) =>
    r.fulfill({
      json: { id: ID, title: "Sunday final", status: "uploading",
        stage: "Downloading from YouTube", progress: 40, createdAt: 0 },
    }),
  );
  await page.goto("/upload");
  await page.getByRole("tab", { name: "YouTube link" }).click();
  const analyse = page.getByRole("button", { name: "Analyse video automatically", exact: true });
  await page.getByLabel("YouTube video link").fill("https://example.com/clip.mp4");
  await expect(page.getByText("Paste a link to a single YouTube video")).toBeVisible();
  await page.getByLabel("YouTube video link").fill("https://youtu.be/dQw4w9WgXcQ");
  await expect(analyse).toBeDisabled();
  await page.getByLabel(/I filmed this video/).check();
  await analyse.click();
  await expect(page.getByRole("heading", { name: "Downloading from YouTube" })).toBeVisible();
  expect(new URL(requested).searchParams.get("url")).toBe("https://youtu.be/dQw4w9WgXcQ");
});


test("undecodable footage warns, skips the short test and still allows the upload", async ({ page }) => {
  // A browser without the codec (HEVC in Firefox/Linux Chromium) looks exactly
  // like a corrupt file from here; the analysis server is the one that decides.
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, diagnostics: true } }));
  const calls = await mockChunkedWorker(page);
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles({name: "broken.mp4", mimeType: "video/mp4", buffer: Buffer.from("not a video")});
  await expect(page.getByRole("alert").filter({ hasText: "could not decode" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Video checked on your device" })).toHaveCount(0);
  expect(calls.created).toBe(0);
  await expect(page.getByRole("button", {name: "Analyse video automatically", exact: true})).toBeEnabled();
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(calls.created).toBe(1);
  expect(calls.diagnostic).not.toBe("true");
});

test("worker recovery keeps the selected file and enables upload without reloading", async ({ page }) => {
  let checks = 0;
  await page.route("**/api/vision/health", (route) => {
    checks++;
    return route.fulfill({ status: checks === 1 ? 503 : 200, json: checks === 1
      ? { available: false, hosted: true, configured: false, detail: "Worker awaiting setup" }
      : { available: true, hosted: true, diagnostics: true, profiles: ["general"] } });
  });
  await page.goto('/upload');
  await page.getByLabel('Video for computer vision').setInputFiles(FIXTURE);
  await expect(page.getByRole('heading', { name: 'Video checked on your device' })).toBeVisible();
  await page.getByRole('button', { name: 'Retry connection' }).click();
  await expect(page.getByRole('button', { name: 'Analyse video automatically', exact: true })).toBeEnabled();
  await expect(page.getByLabel('Analysis title')).toHaveValue('review');
  await expect(page.getByRole('heading', { name: 'Video checked on your device' })).toBeVisible();
});

test("transient reservation failure retries the same upload request", async ({ page }) => {
  await page.route('**/api/vision/health', (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const ids: string[] = [];
  await page.route(/\/api\/vision\/jobs\?/, async (route) => {
    ids.push(new URL(route.request().url()).searchParams.get('requestId') || '');
    if (ids.length === 1) return route.fulfill({ status: 503, json: { detail: 'Worker starting' } });
    return route.fallback();
  });
  await page.goto('/upload');
  await page.getByLabel('Video for computer vision').setInputFiles(FIXTURE);
  await page.getByRole('button', { name: 'Analyse video automatically', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Detecting players', exact: true })).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[0]).toMatch(/^[a-f0-9-]{36}$/);
  expect(ids[1]).toBe(ids[0]);
  expect(calls.created).toBe(1);
  expect(calls.started).toBe(1);
});

test("worker startup is retried automatically", async ({ page }) => {
  let attempts = 0;
  await page.route('**/api/vision/health', (route) => route.fulfill({ json: ++attempts === 1
    ? { available: false, hosted: true }
    : { available: true, hosted: true } }));
  await page.goto('/upload');
  const reconnect = page.getByRole('button', { name: 'Retry connection' });
  await expect(reconnect).toBeVisible();
  await expect(reconnect).toHaveCount(0, { timeout: 10000 });
  expect(attempts).toBe(2);
});

test("the upload page says possession and passes need the ball model", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true, diagnostics: true } }));
  await page.goto("/upload");
  await expect(page.getByLabel(/Player load only/)).toBeChecked();
  await expect(page.getByText(/Possession and passes\s+need the ball model/)).toBeVisible();
  await expect(page.getByText(/You get possession and passes automatically/)).toHaveCount(0);
});

test("once the upload is complete the analysis cannot be cancelled from the upload page", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const cancels: string[] = [];
  await page.route(/\/api\/vision\/jobs\/[a-f0-9]{32}\/cancel$/, (r) => {
    cancels.push(r.request().url());
    return r.fulfill({ json: { status: "cancelled" } });
  });
  // A slow /start reply (mobile network): the worker may already be analysing.
  let reply!: () => void;
  const replied = new Promise<void>((resolve) => (reply = resolve));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), async (r) => {
    calls.started++;
    await replied;
    await r.fulfill({ json: { id: ID, title: "Vision test", status: "processing", stage: "Opening video", progress: 0, createdAt: 0 } }).catch(() => {});
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByText("Checking the video…").first()).toBeVisible();
  await expect.poll(() => calls.started).toBe(1);
  // Still waiting for the reply, still on the upload page: no cancel offered.
  await page.waitForTimeout(1000);
  await expect(page).toHaveURL(/\/upload$/);
  expect(await page.getByRole("button", { name: "Cancel upload" }).count()).toBe(0);
  reply();
  await expect(page).toHaveURL(`/vision/${ID}`, { timeout: 15_000 });
  expect(calls.started).toBe(1);
  expect(cancels).toEqual([]);
});

test("a dropped connection says so in words, not the browser's raw fetch error", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true } }));
  let tries = 0;
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    tries++;
    return r.abort("internetdisconnected");
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByText("Connection lost. Check your signal and press Analyse again; your file is still selected.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Failed to fetch|Load failed|NetworkError/)).toHaveCount(0);
  expect(tries).toBeGreaterThan(1);
});

test("the daily analysis cap is shown at once instead of being retried", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true } }));
  let tries = 0;
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    tries++;
    return r.fulfill({ status: 429, json: { detail: "This server has reached its limit of 30 analyses in 24 hours. Try again later." } });
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByText(/reached its limit of 30 analyses/)).toBeVisible({ timeout: 2000 });
  await page.waitForTimeout(1500);
  expect(tries).toBe(1);
});

test("an undecodable file never turns on the 20-second test, even when the server answers late", async ({ page }) => {
  let checks = 0;
  await page.route("**/api/vision/health", (r) => {
    checks++;
    return checks === 1
      ? r.fulfill({ status: 503, json: { available: false, hosted: true, detail: "Cannot reach the vision server. It may be starting up; retry in a minute." } })
      : r.fulfill({ json: { available: true, hosted: true, diagnostics: true } });
  });
  const calls = await mockChunkedWorker(page);
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles({ name: "phone.mov", mimeType: "video/quicktime", buffer: Buffer.from("not decodable here") });
  await expect(page.getByRole("alert").filter({ hasText: "could not decode" })).toBeVisible();
  await page.getByRole("button", { name: "Retry connection" }).click();
  const analyse = page.getByRole("button", { name: "Analyse video automatically", exact: true });
  await expect(analyse).toBeEnabled();
  await analyse.click();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(calls.created).toBe(1);
  expect(calls.diagnostic).not.toBe("true");
  expect(calls.start).toBe("0");
});

test("closing the tab mid-upload frees the worker, but never cancels a started analysis", async ({ page }) => {
  // A request sent while a tab unloads is not reliably visible to Playwright, so
  // the tab's pagehide event is fired in place: the same handler a close, reload
  // or navigation runs, and the keepalive cancel it sends is intercepted as usual.
  const leave = () => page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false })));
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const cancels: string[] = [];
  await page.route(/\/api\/vision\/jobs\/[a-f0-9]{32}\/cancel$/, (r) => {
    cancels.push(r.request().method());
    return r.fulfill({ json: { status: "cancelled" } });
  });
  // The first chunk is held: the upload is in flight.
  let sending = false;
  let chunk!: () => void;
  const chunkReplied = new Promise<void>((resolve) => (chunk = resolve));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    sending = true;
    await chunkReplied;
    await r.fallback();
  });
  let start!: () => void;
  const startReplied = new Promise<void>((resolve) => (start = resolve));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), async (r) => {
    calls.started++;
    await startReplied;
    await r.fulfill({ json: { id: ID, title: "Vision test", status: "processing", stage: "Opening video", progress: 0, createdAt: 0 } }).catch(() => {});
  });
  await page.goto("/upload");
  // Before any reservation there is nothing to cancel.
  await leave();
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => sending).toBe(true);
  await leave();
  await expect.poll(() => cancels).toEqual(["POST"]);
  // Let the upload finish: once /start has been sent the analysis may be running.
  chunk();
  await expect.poll(() => calls.started).toBe(1);
  await leave();
  await page.waitForTimeout(1000);
  expect(cancels).toEqual(["POST"]);
  start();
  await expect(page).toHaveURL(`/vision/${ID}`);
  await leave();
  await page.waitForTimeout(500);
  expect(cancels).toEqual(["POST"]);
});

test("analysing a saved review defaults to player load and the worker's football models", async ({ page }) => {
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({ json: { available: true, hosted: false, diagnostics: true, profiles: ["general", "broadcast"] } }),
  );
  const reservations: URLSearchParams[] = [];
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    reservations.push(new URL(r.request().url()).searchParams);
    return r.fulfill({ status: 400, json: { detail: "Fixture: stop after the reservation." } });
  });
  await page.goto("/upload");
  await page.getByRole("button", { name: "Open manual review instead" }).click();
  await page.getByLabel("Match video").setInputFiles(FIXTURE);
  await page.getByLabel("Home team").fill("Reds");
  await page.getByLabel("Away team").fill("Blues");
  await page.getByRole("button", { name: "Open review room" }).click();
  const analyse = page.getByRole("button", { name: "Analyse this video automatically" });
  await expect(analyse).toBeEnabled();
  await analyse.click();
  await expect(page.getByText("Fixture: stop after the reservation.")).toBeVisible();
  expect(reservations[0].get("search")).toBe("none");
  expect(reservations[0].get("profile")).toBe("broadcast");
  // Opting in to the ball model sends what the upload page sends with it on.
  await page.getByLabel(/Run the ball model/).check();
  await analyse.click();
  await expect.poll(() => reservations.length).toBe(2);
  expect(reservations[1].get("search")).toBe("exhaustive");
  expect(reservations[1].get("profile")).toBe("broadcast");
});
