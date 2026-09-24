import { test, expect, Page } from "@playwright/test";
import path from "node:path";

const ID = "a".repeat(32);
const FIXTURE = path.join(__dirname, "fixtures/review.mp4");

async function mockChunkedWorker(page: Page) {
  const calls = { created: 0, chunks: [] as number[], started: 0, owner: "" };
  let received = 0;
  await page.route(/\/api\/vision\/jobs\?/, async (r) => {
    calls.created++;
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
    r.fulfill({ json: { available: true, hosted: false } }),
  );
  const calls = await mockChunkedWorker(page);
  await page.goto("/upload");
  await page.getByLabel("Video for computer vision").setInputFiles(FIXTURE);
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
    r.fulfill({ json: { available: true, hosted: false } }),
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
