import { test, expect, Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const ID = "a".repeat(32);
const FIXTURE = path.join(__dirname, "fixtures/review.mp4");
const PIECE = 4 * 1024 * 1024;

/** The fixture video (still decodable) grown to `total` bytes with a trailing MP4 "free" box. */
function paddedVideo(name: string, total: number) {
  const video = fs.readFileSync(FIXTURE);
  const free = Buffer.alloc(Math.max(8, total - video.length));
  free.writeUInt32BE(free.length, 0);
  free.write("free", 4, "latin1");
  return { name, mimeType: "video/mp4", buffer: Buffer.concat([video, free]) };
}

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

test("once the upload is complete, waiting for the start neither offers Cancel upload nor cancels the analysis", async ({ page }) => {
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
  // Still waiting for the reply, still on the upload page: the upload itself can no
  // longer be cancelled (only "Cancel analysis", which asks the worker), and nothing is cancelled.
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

test("leaving the page mid-upload never cancels the upload or a started analysis", async ({ page }) => {
  // Phones discard background tabs while the user switches apps: the upload must
  // survive so a reload can resume it. pagehide is fired in place (a request sent
  // while a tab unloads is not reliably visible to Playwright).
  const leave = () => page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: false })));
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const cancels: string[] = [];
  await page.route(/\/api\/vision\/jobs\/[a-f0-9]{32}\/cancel$/, (r) => {
    cancels.push(r.request().method());
    return r.fulfill({ json: { status: "cancelled" } });
  });
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
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => sending).toBe(true);
  await leave();
  chunk();
  await expect.poll(() => calls.started).toBe(1);
  await leave();
  await page.waitForTimeout(500);
  expect(cancels).toEqual([]);
  start();
  await expect(page).toHaveURL(`/vision/${ID}`);
  expect(cancels).toEqual([]);
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

/** The upload finishes, then /start is answered only after 3 s (the worker checks the video). */
async function mockSlowStart(page: Page, cancelReply: { requested: boolean }) {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const order: string[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), async (r) => {
    order.push("start");
    await new Promise((resolve) => setTimeout(resolve, 3000));
    // The page may have given up on this request by now.
    await r.fulfill({ json: { id: ID, title: "Vision test", status: "processing", stage: "Opening video", progress: 0, createdAt: 0 } }).catch(() => {});
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/cancel$`), (r) => {
    order.push("cancel");
    return r.fulfill({ json: cancelReply });
  });
  // Records whether the page ever claimed the upload was cancelled.
  await page.addInitScript(() => {
    const w = window as unknown as { saidCancelled: boolean };
    w.saidCancelled = false;
    new MutationObserver(() => {
      if (document.body?.textContent?.includes("Upload cancelled")) w.saidCancelled = true;
    }).observe(document, { subtree: true, childList: true, characterData: true });
  });
  return { calls, order };
}

/** Uploads the fixture and returns the upload card's cancel button once /start was sent. */
async function uploadAndWaitForStart(page: Page, order: string[]) {
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => order).toContain("start");
  // The upload card's button (the analysis page has its own "Cancel analysis").
  return page.getByText(/Keep this page open/).locator("..").getByRole("button", { name: /^Cancel (upload|analysis)$/ });
}

test("cancelling while the video is checked opens the analysis if the worker had already started it", async ({ page }) => {
  const { order } = await mockSlowStart(page, { requested: false });
  const cancel = await uploadAndWaitForStart(page, order);
  await cancel.click();
  await expect(page).toHaveURL(`/vision/${ID}`);
  expect(order).toEqual(["start", "cancel"]);
  expect(await page.evaluate(() => (window as unknown as { saidCancelled: boolean }).saidCancelled)).toBe(false);
});

test("cancelling while the video is checked says cancelled once the worker stopped it", async ({ page }) => {
  const { order } = await mockSlowStart(page, { requested: true });
  const cancel = await uploadAndWaitForStart(page, order);
  // The upload is done by now: what is being cancelled is the analysis.
  await expect(cancel).toHaveText("Cancel analysis");
  await cancel.click();
  await expect(page.getByText("Upload cancelled. Your file is still selected.")).toBeVisible();
  expect(order).toEqual(["start", "cancel"]);
  await expect(page).toHaveURL(/\/upload$/);
});

test("cancelling after the upload, while /start is being retried, asks the worker to stop", async ({ page }) => {
  // The worker was briefly unavailable: the cancel lands between /start attempts.
  const { order } = await mockSlowStart(page, { requested: true });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), (r) => {
    order.push("start");
    return r.fulfill({ status: 503, json: { detail: "Worker restarting" } });
  });
  const cancel = await uploadAndWaitForStart(page, order);
  await cancel.click();
  await expect(page.getByText("Upload cancelled. Your file is still selected.")).toBeVisible();
  expect(order.at(-1)).toBe("cancel");
  expect(order.indexOf("cancel")).toBeGreaterThan(order.indexOf("start"));
});

const processing = { id: ID, title: "Vision test", status: "processing", stage: "Detecting players", progress: 25, createdAt: 0 };
const uploading = { id: ID, title: "Vision test", status: "uploading", stage: "Receiving video", progress: 0, createdAt: 0 };
const broken = { name: "match.mp4", mimeType: "video/mp4", buffer: Buffer.from("not a decodable video") };

test("the upload copy matches the player-load default, and the ball-model copy when it is unticked", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true } }));
  await page.goto("/upload");
  const loadOnly = page.getByLabel(/Player load only/);
  await expect(loadOnly).toBeChecked();
  await expect(page.getByText("This job estimates how far each kit ran (camera pan removed).", { exact: false })).toBeVisible();
  await expect(page.getByText("distance and xG are not measured", { exact: false })).toHaveCount(0);
  await loadOnly.uncheck();
  await expect(page.getByText("You get possession and passes automatically", { exact: false })).toBeVisible();
  await expect(page.getByText("Speed and xG are not measured; running distance is a per-kit estimate.", { exact: false })).toBeVisible();
  await expect(page.getByText("This job estimates how far each kit ran", { exact: false })).toHaveCount(0);
});

test("a reloaded page resumes the same upload when the same file is chosen", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const ids: string[] = [];
  const puts: number[][] = [[], []];
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    ids.push(new URL(r.request().url()).searchParams.get("requestId") || "");
    return r.fulfill({ json: uploading });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    const offset = Number(new URL(r.request().url()).searchParams.get("offset"));
    const session = ids.length - 1;
    puts[session].push(offset);
    if (session === 0) {
      // The first page is reloaded while this chunk is on its way.
      await new Promise((done) => setTimeout(done, 5000));
      return r.fulfill({ json: { received: offset } }).catch(() => {});
    }
    if (offset === 0) return r.fulfill({ status: 409, json: { detail: "Expected offset 1000" } });
    return r.fulfill({ json: { received: offset + (r.request().postDataBuffer()?.length ?? 0) } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), (r) => r.fulfill({ json: processing }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) => r.fulfill({ json: processing }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => puts[0].length).toBe(1);
  await page.reload();
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(ids[1]).toBe(ids[0]);
  // The server already had the first 1000 bytes: the upload continues from there.
  expect(puts[1]).toEqual([0, 1000]);
  // Finished: the next upload starts afresh.
  expect(await page.evaluate(() => localStorage.getItem("pitchlens-vision-pending-upload"))).toBeNull();
});

test("this browser's earlier upload is named and can be cancelled before trying again", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  const earlier = "e".repeat(32);
  let posts = 0;
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    posts++;
    if (posts > 1) return r.fallback();
    return r.fulfill({ status: 409, json: { detail: "This browser's earlier upload is still reserved (28%). Choose the same file to resume, or cancel it.", code: "own-upload", jobId: earlier, receivedBytes: 28, expectedBytes: 100 } });
  });
  const cancels: string[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${earlier}/cancel$`), (r) => {
    cancels.push(r.request().method());
    return r.fulfill({ json: { requested: true } });
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "earlier upload is still reserved (28%)" })).toBeVisible();
  expect(posts).toBe(1);
  await page.getByRole("button", { name: "Cancel the earlier upload" }).click();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(cancels).toEqual(["POST"]);
  expect(posts).toBe(2);
  expect(calls.started).toBe(1);
});

test("a connection that stays down says how far the upload got and how to resume", async ({ page }) => {
  await page.clock.install();
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false, uploadIdleSeconds: 300, uploadAbandonSeconds: 7200 } }));
  const calls = await mockChunkedWorker(page);
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), (r) => r.abort("failed"));
  const cancels: string[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${ID}/cancel$`), (r) => {
    cancels.push(r.request().url());
    return r.fulfill({ json: { requested: true } });
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  const lost = page.getByRole("alert").filter({
    hasText: "Connection lost at 0%. Press Analyse with the same file to continue. What was sent is kept for about 2 hours unless someone else starts an analysis.",
  });
  await expect.poll(async () => {
    await page.clock.fastForward(60_000);
    return lost.count();
  }, { timeout: 30_000 }).toBe(1);
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
  // The reservation is kept for the resume: no cancel was sent.
  expect(cancels).toEqual([]);
  expect(calls.created).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem("pitchlens-vision-pending-upload"))).not.toBeNull();
});

test("an upload keeps going through a worker restart (the site answers 503 meanwhile)", async ({ page }) => {
  await page.clock.install();
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  let attempts = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), (r) => {
    attempts++;
    if (attempts <= 6) return r.fulfill({ status: 503, json: { detail: "Cannot reach the vision server. It may be starting up; retry in a minute." } });
    return r.fulfill({ json: { received: broken.buffer.length } });
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  const done = page.getByRole("heading", { name: "Detecting players", exact: true });
  await expect.poll(async () => {
    await page.clock.fastForward(16_000);
    return done.count();
  }, { timeout: 30_000 }).toBe(1);
  expect(attempts).toBe(7);
  expect(calls.started).toBe(1);
});

test("a file over the server's limit says how much fits and how to get under it", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true, maxBytes: 1024 * 1024 } }));
  await page.goto("/upload");
  await expect(page.getByText("Up to 1 MB", { exact: false })).toBeVisible();
  // Decodable, so the readiness card is shown: without its 20-second test, which cannot be sent either.
  await page.getByLabel("Video for computer vision").setInputFiles(paddedVideo("full-match.mp4", 2 * 1024 * 1024));
  const alert = page.getByTestId("too-large");
  await expect(alert).toContainText("This video is 2 MB; this server accepts up to 1 MB.");
  // Advice an iPhone can follow: Photos trims and saves a new clip; it has no 720p export.
  await expect(alert).toContainText("In Photos, tap Edit, trim, then Save Video as New Clip");
  await expect(alert).not.toContainText("export at 720p, then");
  await expect(page.getByRole("heading", { name: "Video checked on your device" })).toBeVisible();
  await expect(page.getByLabel("Test start (seconds)")).toHaveCount(0);
  await expect(page.getByLabel(/Only analyse a 20-second test/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Analyse video automatically", exact: true })).toBeDisabled();
});

test("the daily limit is not retried and says when analysis resumes", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  let posts = 0;
  const resetsAt = Math.round(Date.now() / 1000) + 3600;
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    posts++;
    return r.fulfill({ status: 429, json: { detail: "This server has reached its limit of 30 analyses in 24 hours.", code: "daily-limit", resetsAt } });
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Analysis is paused until" })).toBeVisible();
  await page.waitForTimeout(1500);
  expect(posts).toBe(1);
});

test("leaving for another page of the site keeps the upload going, and the upload page shows it again", async ({ page }) => {
  // Navbar links navigate inside the app: the upload page unmounts, the upload must not.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const file = paddedVideo("match.mp4", 4 * PIECE - 1000);
  let posts = 0;
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    posts++;
    return r.fulfill({ json: uploading });
  });
  const puts: number[] = [];
  let received = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    const offset = Number(new URL(r.request().url()).searchParams.get("offset"));
    puts.push(offset);
    await new Promise((done) => setTimeout(done, 1500));
    received = offset + (r.request().postDataBuffer()?.length ?? 0);
    await r.fulfill({ json: { received } }).catch(() => {});
  });
  const cancels: string[] = [];
  await page.route(/\/api\/vision\/jobs\/[a-f0-9]{32}\/cancel$/, (r) => {
    cancels.push(r.request().url());
    return r.fulfill({ json: { requested: true } });
  });
  let started = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), (r) => {
    started++;
    return r.fulfill({ json: processing });
  });
  await page.route("**/api/vision/jobs", (r) =>
    r.fulfill({ json: [{ ...uploading, receivedBytes: received, expectedBytes: file.buffer.length }] }),
  );
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) => r.fulfill({ json: processing }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(file);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => received, { timeout: 10_000 }).toBe(PIECE);
  const left = Date.now();
  await page.getByRole("button", { name: "Toggle mobile menu" }).click();
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByTestId("nav-upload")).toContainText("Uploading");
  // Back on the upload page: the same upload, not a new reservation.
  await page.getByTestId("nav-upload").click();
  await expect(page).toHaveURL(/\/upload$/);
  await expect(page.getByRole("button", { name: "Cancel upload" })).toBeVisible();
  await expect(page.getByTestId("upload-bytes")).toContainText("MB of");
  await expect(page.getByText("match.mp4")).toBeVisible();
  await expect.poll(() => puts, { timeout: 15_000 }).toContain(3 * PIECE);
  await expect(page).toHaveURL(`/vision/${ID}`, { timeout: 15_000 });
  await page.waitForTimeout(Math.max(0, 5000 - (Date.now() - left)));
  expect(cancels).toEqual([]);
  expect(posts).toBe(1);
  expect(puts).toEqual([0, PIECE, 2 * PIECE, 3 * PIECE]);
  expect(started).toBe(1);
});

test("an upload that finishes after the page was left links to its report from the navbar", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  let sending = false;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    sending = true;
    await new Promise((done) => setTimeout(done, 1500));
    await r.fallback();
  });
  await page.route("**/api/vision/jobs", (r) => r.fulfill({ json: [processing] }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => sending).toBe(true);
  await page.getByRole("button", { name: "Toggle mobile menu" }).click();
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  const link = page.getByTestId("nav-upload");
  await expect(link).toHaveText("Analysis started · open report", { timeout: 10_000 });
  await expect(link).toHaveAttribute("href", `/vision/${ID}`);
  expect(calls.started).toBe(1);
  await link.click();
  await expect(page).toHaveURL(`/vision/${ID}`);
  await expect(page.getByTestId("nav-upload")).toHaveCount(0);
});

test("after a reload, the same video continues its upload with the title and test it was started with", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false, diagnostics: true } }));
  const file = paddedVideo("match.mp4", 2 * PIECE + 1000);
  const posts: URLSearchParams[] = [];
  const puts: number[][] = [[], []];
  await page.route(/\/api\/vision\/jobs\?/, (r) => {
    posts.push(new URL(r.request().url()).searchParams);
    // The worker still holds the first 4 MB piece.
    return r.fulfill({ json: posts.length === 1 ? uploading : { ...uploading, receivedBytes: PIECE } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    const offset = Number(new URL(r.request().url()).searchParams.get("offset"));
    const session = posts.length - 1;
    puts[session].push(offset);
    if (session === 0 && offset > 0) {
      // The page is reloaded while the second piece is on its way.
      await new Promise((done) => setTimeout(done, 8000));
      return r.fulfill({ json: { received: PIECE } }).catch(() => {});
    }
    return r.fulfill({ json: { received: offset + (r.request().postDataBuffer()?.length ?? 0) } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/start$`), (r) => r.fulfill({ json: processing }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) => r.fulfill({ json: processing }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(file);
  await expect(page.getByRole("heading", { name: "Video checked on your device" })).toBeVisible();
  await page.getByLabel("Analysis title").fill("Tekkerz Tuesday vs Lions");
  await page.getByLabel("Test start (seconds)").fill("2");
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => puts[0]).toEqual([0, PIECE]);
  await page.reload();
  // The page names the unfinished upload before a file is chosen.
  await expect(page.getByTestId("unfinished-upload")).toContainText('Unfinished upload: "Tekkerz Tuesday vs Lions", test 0:02–0:22');
  // Choosing the video again resets the title and test start; continuing keeps the originals.
  await page.getByLabel("Video for computer vision").setInputFiles(file);
  await expect(page.getByRole("button", { name: "Start again with these options" })).toBeVisible();
  await page.getByRole("button", { name: "Continue this upload" }).click();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1].get("requestId")).toBe(posts[0].get("requestId"));
  expect(posts[1].get("title")).toBe("Tekkerz Tuesday vs Lions");
  expect(posts[1].get("start")).toBe("2");
  expect(posts[1].get("diagnostic")).toBe("true");
  expect(puts[1][0]).toBe(PIECE);
});

test("the short-test checkbox says it only analyses a test, and how the full match follows", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false, diagnostics: true } }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  const box = page.getByLabel(/Only analyse a 20-second test/);
  await expect(box).toBeChecked();
  await expect(page.getByText("After the test you can analyse the full match from its report, without uploading again.")).toBeVisible();
  await expect(page.getByText(/before analysing the full match/)).toHaveCount(0);
});

test("the readiness card shows the file size and the upload card how much has been sent", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  await mockChunkedWorker(page);
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  let pieces = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    // The first piece arrives; the second waits until the card has been read.
    if (++pieces > 1) await released;
    await r.fallback();
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(paddedVideo("match.mp4", 9 * 1024 * 1024));
  await expect(page.getByLabel("Video readiness")).toContainText("9.0 MB to upload");
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByTestId("upload-bytes")).toContainText("4.0 MB of 9.0 MB");
  release();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
});

test("the screen is kept awake while the upload runs, asked for again when the page shows, and released when it ends", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { wake: { requests: string[]; releases: number } };
    w.wake = { requests: [], releases: 0 };
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: {
        request: async (type: string) => {
          w.wake.requests.push(type);
          return { type, released: false, release: async () => void w.wake.releases++, addEventListener() {}, removeEventListener() {} };
        },
      },
    });
  });
  const wake = () => page.evaluate(() => (window as unknown as { wake: { requests: string[]; releases: number } }).wake);
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  await mockChunkedWorker(page);
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  let sending = false;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    sending = true;
    await released;
    await r.fallback();
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => sending).toBe(true);
  await expect.poll(async () => (await wake()).requests).toEqual(["screen"]);
  await expect(page.getByText(/we keep the screen awake while it uploads/)).toBeVisible();
  // Back from another app: the browser dropped the lock, so it is asked for again.
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect.poll(async () => (await wake()).requests).toEqual(["screen", "screen"]);
  const before = (await wake()).releases;
  release();
  await expect(page).toHaveURL(`/vision/${ID}`);
  expect((await wake()).releases).toBeGreaterThan(before);
});

test("a cancelled upload releases the screen", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { wake: { requests: number; releases: number } };
    w.wake = { requests: 0, releases: 0 };
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: { request: async () => (w.wake.requests++, { released: false, release: async () => void w.wake.releases++, addEventListener() {}, removeEventListener() {} }) },
    });
  });
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  await mockChunkedWorker(page);
  await page.route(/\/api\/vision\/jobs\/[a-f0-9]{32}\/cancel$/, (r) => r.fulfill({ json: { requested: true } }));
  let sending = false;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    sending = true;
    await new Promise((done) => setTimeout(done, 10_000));
    await r.fallback().catch(() => {});
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect.poll(() => sending).toBe(true);
  await page.getByRole("button", { name: "Cancel upload" }).click();
  await expect(page.getByText("Upload cancelled. Your file is still selected.")).toBeVisible();
  const wake = await page.evaluate(() => (window as unknown as { wake: { requests: number; releases: number } }).wake);
  expect(wake.requests).toBe(1);
  expect(wake.releases).toBe(1);
});

test("without Screen Wake Lock the upload still works and asks to keep the screen on", async ({ page }) => {
  await page.addInitScript(() => {
    delete (Navigator.prototype as unknown as { wakeLock?: unknown }).wakeLock;
  });
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  const calls = await mockChunkedWorker(page);
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video\\?offset=`), async (r) => {
    await released;
    await r.fallback();
  });
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(broken);
  await page.getByRole("button", { name: "Analyse video automatically", exact: true }).click();
  await expect(page.getByText("Keep this page open and the screen on until the upload finishes.", { exact: false })).toBeVisible();
  await expect(page.getByText(/we keep the screen awake/)).toHaveCount(0);
  release();
  await expect(page.getByRole("heading", { name: "Detecting players", exact: true })).toBeVisible();
  expect(calls.started).toBe(1);
});

test("a 360p video gets one note about the small ball, not two", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false, diagnostics: true } }));
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  await expect(page.getByRole("heading", { name: "Video checked on your device" })).toBeVisible();
  await expect(page.getByText(/about the size of a boot/)).toHaveCount(1);
  await expect(page.getByLabel(/Player load only/)).toHaveAccessibleName(/Player load only: running distance per team, quicker/);
});

test("on a phone the video picker is on the first screen; on a desktop the feature cards stay", async ({ page }) => {
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
  await page.setViewportSize({ width: 390, height: 664 });
  await page.goto("/upload");
  await expect(page.getByRole("heading", { name: "Let the footage do the talking." })).toBeVisible();
  const picker = page.getByLabel("Video for computer vision");
  expect((await picker.boundingBox())!.y).toBeLessThan(664);
  await expect(page.getByText("Player + ball detection")).toBeHidden();
  await page.setViewportSize({ width: 1280, height: 800 });
  for (const card of ["Player + ball detection", "Kit grouping + tracking", "Evidence-linked analytics"])
    await expect(page.getByText(card)).toBeVisible();
});

test("a missing or refused access code brings its field into view with the cursor in it", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/vision/health", (r) =>
    r.fulfill({ json: { available: true, hosted: true, accessRequired: true, retentionHours: 168 } }),
  );
  await page.route(/\/api\/vision\/jobs\?/, (r) =>
    r.fulfill({ status: 401, json: { detail: "Enter the access code to analyse videos.", code: "access" } }),
  );
  const field = page.getByLabel("Access code");
  const inView = async () => {
    const box = await field.boundingBox();
    return !!box && box.y >= 0 && box.y + box.height <= 844;
  };
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
  const analyse = page.getByRole("button", { name: "Analyse video automatically", exact: true });
  await analyse.click();
  await expect(page.getByText("Enter the access code first.")).toBeVisible();
  await expect(field).toBeFocused();
  await expect.poll(inView).toBe(true);
  await field.fill("wrong");
  await analyse.click();
  await expect(page.getByText("Enter the access code to analyse videos.")).toBeVisible();
  await expect(field).toHaveValue("");
  await expect(field).toBeFocused();
  await expect.poll(inView).toBe(true);
});
