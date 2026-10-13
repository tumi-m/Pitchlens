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
  return page.getByText("Keep this tab open until the upload finishes").locator("..").getByRole("button", { name: /^Cancel (upload|analysis)$/ });
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
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false } }));
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
  const lost = page.getByRole("alert").filter({ hasText: "Connection lost at 0%. Press Analyse with the same file to resume." });
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
  await page.getByLabel("Video for computer vision").setInputFiles({ name: "full-match.mp4", mimeType: "video/mp4", buffer: Buffer.alloc(2 * 1024 * 1024) });
  const alert = page.getByTestId("too-large");
  await expect(alert).toContainText("This video is 2 MB; this server accepts up to 1 MB.");
  await expect(alert).toContainText("Trim it into parts in Photos, or export at 720p");
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
