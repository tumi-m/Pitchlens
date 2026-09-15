import { test, expect } from "@playwright/test";
import path from "node:path";
import fs from "node:fs/promises";

test("video review survives reload, tags evidence, exports and deletes", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/upload");
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await page
    .getByLabel("Match video")
    .setInputFiles(path.join(__dirname, "fixtures/review.mp4"));
  await page.getByLabel("Home team").fill("Cape Town Reds");
  await page.getByLabel("Away team").fill("City Blues");
  await page.getByRole("button", { name: "Open review room" }).click();
  await expect(
    page.getByRole("heading", { name: "Cape Town Reds vs City Blues" }),
  ).toBeVisible();
  await expect(page.getByText("0:04 · 640 × 360")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "goal", exact: true }),
  ).toBeEnabled();
  await page.locator("video").evaluate((v: HTMLVideoElement) => {
    v.currentTime = 2;
  });
  await page.getByLabel("Event note").fill("Near-post finish");
  await page.getByRole("button", { name: "goal", exact: true }).click();
  await expect(page.getByText("Near-post finish")).toBeVisible();
  await page
    .getByLabel("Coach’s notes")
    .fill("Track the runner on the far side.");
  await page.getByRole("button", { name: "Save notes" }).click();
  await page.reload();
  await expect(page.getByText("Near-post finish")).toBeVisible();
  await expect(page.getByLabel("Coach’s notes")).toHaveValue(
    "Track the runner on the far side.",
  );
  await page.getByRole("button", { name: "Export review" }).click();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download JSON" }).click();
  const file = await downloaded;
  const exported = JSON.parse(await fs.readFile((await file.path())!, "utf8"));
  expect(exported.review.duration).toBeCloseTo(4);
  expect(exported.review.events[0]).toMatchObject({
    source: "manual",
    type: "goal",
    note: "Near-post finish",
  });
  expect(exported.summary[0]).toMatchObject({ goals: 1, shots: 1 });
  expect(exported.review).not.toHaveProperty("possession");
  await page.getByRole("button", { name: /Remove goal/ }).click();
  await expect(page.getByText("Near-post finish")).not.toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete review" }).click();
  await expect(page.getByText("No matches yet")).toBeVisible();
  expect(errors).toEqual([]);
});

test("invalid video fails visibly and does not create a fabricated match", async ({
  page,
}) => {
  await page.goto("/upload");
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await page.getByLabel("Match video").setInputFiles({
    name: "broken.mp4",
    mimeType: "video/mp4",
    buffer: Buffer.from("this is not a video"),
  });
  await page.getByRole("button", { name: "Open review room" }).click();
  await expect(page.getByRole("alert")).toContainText("cannot be decoded");
  await page.goto("/dashboard");
  await expect(page.getByText("No matches yet")).toBeVisible();
});

test("unconfigured AI returns a failure, never simulated predictions", async ({
  request,
}) => {
  const response = await request.post("/api/infer", { data: { frame: "bad" } });
  expect(response.status()).toBe(503);
  expect(await response.json()).toHaveProperty("error");
  expect(
    (await request.get("/api/fetch-video?type=gdrive&url=anything")).status(),
  ).toBe(410);
});

test("mobile navigation and review form fit viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Toggle mobile menu" }).click();
  await page.getByRole("link", { name: "Upload", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Let the footage do the talking." }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("AI frames show provider observations without fabricated statistics", async ({
  page,
}) => {
  await page.route("**/api/infer", async (route) => {
    await route.fulfill({
      json:
        route.request().method() === "GET"
          ? { configured: true }
          : {
              predictions: [
                {
                  x: 100,
                  y: 100,
                  width: 30,
                  height: 60,
                  confidence: 0.9,
                  class: "player",
                  class_id: 2,
                },
              ],
            },
    });
  });
  await page.goto("/upload");
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await page
    .getByLabel("Match video")
    .setInputFiles(path.join(__dirname, "fixtures/review.mp4"));
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Open review room" }).click();
  await expect(
    page.getByRole("heading", { name: "AI frame inspection" }),
  ).toBeVisible();
  await expect(page.getByText("player 90%")).toBeVisible();
  await expect(
    page.getByText("6 of 6 sample frames inspected.", { exact: false }),
  ).toBeVisible();
  const record = await page.evaluate(
    () =>
      Object.values(
        JSON.parse(localStorage.getItem("pitchlens_matches")!),
      )[0] as any,
  );
  expect(record).not.toHaveProperty("stats");
  expect(record.review.frames).toHaveLength(6);
  expect(record.review.events).toEqual([]);
});

test("AI service failure preserves usable video review and reports failure", async ({
  page,
}) => {
  await page.route("**/api/infer", async (route) => {
    await route.fulfill(
      route.request().method() === "GET"
        ? { json: { configured: true } }
        : { status: 502, json: { error: "Unavailable" } },
    );
  });
  await page.goto("/upload");
  await page
    .getByRole("button", { name: "Open manual review instead" })
    .click();
  await page
    .getByLabel("Match video")
    .setInputFiles(path.join(__dirname, "fixtures/review.mp4"));
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Open review room" }).click();
  await expect(
    page.getByText("0 of 6 sample frames inspected.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "goal", exact: true }),
  ).toBeEnabled();
  const record = await page.evaluate(
    () =>
      Object.values(
        JSON.parse(localStorage.getItem("pitchlens_matches")!),
      )[0] as any,
  );
  expect(record.review.aiStatus).toBe("failed");
  expect(record).not.toHaveProperty("stats");
});

test("import, reconnect, edit and undo preserve a portable review", async ({
  page,
}) => {
  await page.goto("/dashboard");
  await page
    .getByLabel("Import review file", { exact: true })
    .setInputFiles(path.join(__dirname, "fixtures/review.json"));
  await expect(
    page.getByRole("heading", {
      name: "Portable QA — Reds vs Blues",
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByText("Imported review.", { exact: false }),
  ).toBeVisible();
  await page
    .getByLabel("Reconnect video file", { exact: true })
    .setInputFiles(path.join(__dirname, "fixtures/review.mp4"));
  await expect(
    page.getByRole("button", { name: "goal", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Edit goal at 0:01", exact: true })
    .click();
  await page.getByLabel("Event timestamp in seconds").fill("2.5");
  await page.getByLabel("Event team", { exact: true }).selectOption("away");
  await page.getByLabel("Event type", { exact: true }).selectOption("shot");
  await page
    .getByLabel("Edit event note", { exact: true })
    .fill("Corrected QA observation");
  await page.getByRole("button", { name: "Save event", exact: true }).click();
  await expect(
    page.getByText("Corrected QA observation", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await page
    .getByRole("button", { name: "Remove shot at 0:02", exact: true })
    .click();
  await expect(
    page.getByText("Corrected QA observation", { exact: true }),
  ).not.toBeVisible();
  await page
    .getByRole("button", { name: "Undo last removal", exact: true })
    .click();
  await expect(
    page.getByText("Corrected QA observation", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Export review", exact: true })
    .click();
  await page.getByText("Preview exported data", { exact: true }).click();
  const data = JSON.parse(await page.getByLabel("Review JSON").inputValue());
  expect(data.review.events[0]).toMatchObject({
    timestamp: 2.5,
    team: "away",
    type: "shot",
  });
  expect(data.summary[0].goals).toBe(0);
  expect(data.summary[1].shots).toBe(1);
});

test("invalid imports leave the workspace unchanged", async ({ page }) => {
  await page.goto("/dashboard");
  await page.getByLabel("Import review file", { exact: true }).setInputFiles({
    name: "bad.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"schemaVersion": 99}'),
  });
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByText("No matches yet", { exact: true })).toBeVisible();
});
