import { test, expect, Page, Route } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const ID = "b".repeat(32);
const VIDEO = fs.readFileSync(path.join(__dirname, "fixtures/review.mp4"));

function result() {
  const frames = Array.from({ length: 20 }, (_, i) => ({
    t: i * 0.2,
    scene: 0,
    players: [
      { id: 1, team: 0, box: [100, 150, 112, 180], confidence: 0.9 },
      { id: 11, team: 1, box: [400, 160, 412, 190], confidence: 0.9 },
    ],
    ball: { x: 110 + i * 10, y: 178, box: [108 + i * 10, 176, 112 + i * 10, 180], confidence: 0.7 },
    camera: [1, 0, 0, 0, 1, 0],
  }));
  return {
    schemaVersion: 1,
    source: "computer-vision",
    model: "players.pt",
    modelSha256: "x",
    video: { duration: 4, width: 640, height: 360, fps: 25 },
    analysedDuration: 4,
    analysedStart: 0,
    sampleFps: 5,
    teams: [
      { id: 0, label: "Kit A", colour: "#dc2626" },
      { id: 1, label: "Kit B", colour: "#22c55e" },
    ],
    metrics: {
      sampledFrames: 20,
      playerFrames: 20,
      ballFrames: 20,
      teamSeconds: [2, 0],
      unknownSeconds: 2,
      possessionShare: [100, 0],
      possessionCoverage: 50,
      trackCount: 2,
      events: [],
    },
    frames,
    limitations: ["Test fixture."],
  };
}

const count = (value: number, confirmed = 0) => ({ value, confirmed, pending: value - confirmed });

function analysis(calibrated: boolean, shotStatus: "proposed" | "confirmed" = "proposed") {
  const template = { length: 40, width: 20, goalWidth: 3, centreRadius: 3, areaRadius: 6, areaDepth: null, areaWidth: null, penaltySpot: 6 };
  const team = (i: number) => ({
    controlSeconds: i ? 0.8 : 2.4,
    possessionSeconds: i ? 1 : 3,
    possession: i ? 25 : 75,
    possessions: i ? 1 : 2,
    averagePossession: i ? 1 : 1.5,
    passes: count(i ? 1 : 4),
    passesComplete: count(i ? 0 : 3),
    passAccuracy: i ? 0 : 75,
    shots: calibrated ? count(i ? 0 : 1, shotStatus === "confirmed" && !i ? 1 : 0) : null,
    shotsOnTarget: calibrated ? count(i ? 0 : 1, shotStatus === "confirmed" && !i ? 1 : 0) : null,
    goals: calibrated ? { value: 0, candidates: 0 } : null,
    interceptions: count(i ? 1 : 0),
    tackles: count(0),
    fieldTilt: calibrated ? (i ? 20 : 80) : null,
  });
  return {
    schemaVersion: 1,
    calibrated,
    template: calibrated ? template : null,
    directions: calibrated ? { segments: [{ start: 0, end: null, team0Attacks: "right" }], confidence: 0.9, source: "team-depth" } : null,
    events: calibrated
      ? [{ id: "ev-0", type: "shot", t: 2, team: 0, confidence: 0.6, status: shotStatus, outcome: "on-target", onTarget: true, x: 30, y: 10, needsReview: true }]
      : [],
    stats: {
      teams: [team(0), team(1)],
      coverage: { possessionPercent: 100, possessionShown: true, possessionInterval: [60, 90], controlPercent: 80, ballStatePercent: 95, calibratedPercent: calibrated ? 100 : 0, inPlaySeconds: 4, deadBallSeconds: 0, contestedSeconds: 0, looseSeconds: 0.6, unknownSeconds: 0 },
      momentum: [0.5],
      heatmaps: calibrated ? { "0": [[0.2, 0.3], [0.1, 0.4]], "1": [[0.5, 0.1], [0.3, 0.1]] } : null,
      averagePositions: calibrated ? [{ player: 1, team: 0, x: 12, y: 10, seconds: 30 }] : null,
      shotMap: calibrated ? [{ id: "ev-0", team: 0, x: 30, y: 10, outcome: "on-target", status: shotStatus, t: 2 }] : null,
      tracks: { fragments: 2, players: 2 },
    },
    review: { decisions: shotStatus === "confirmed" ? 1 : 0, confirmed: shotStatus === "confirmed" ? 1 : 0, rejected: 0, pending: calibrated && shotStatus === "proposed" ? 1 : 0 },
    positions: calibrated ? Array.from({ length: 20 }, (_, i) => [i * 0.2, [[1, 0, 10 + i * 0.2, 10], [11, 1, 30, 10]], [11 + i * 0.3, 10, 0]]) : null,
  };
}

async function mockReport(page: Page, opts: { calibrated: boolean }) {
  const state = { calibrated: opts.calibrated, shot: "proposed" as "proposed" | "confirmed", reviews: [] as unknown[], previews: 0, saves: [] as unknown[] };
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: false, analytics: true } }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}$`), (r) =>
    r.fulfill({ json: { id: ID, title: "Friday 5s", status: "completed", stage: "Analysis complete", progress: 100, createdAt: 0 } }),
  );
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: result() }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video`), (r) =>
    r.fulfill({ status: 200, body: VIDEO, headers: { "content-type": "video/mp4", "accept-ranges": "bytes" } }),
  );
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: analysis(state.calibrated, state.shot) }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration(\\?.*)?$`), async (r: Route) => {
    if (r.request().method() === "POST") {
      state.saves.push(r.request().postDataJSON());
      state.calibrated = true;
      return r.fulfill({ json: { state: "processing", fit: { quality: "good" } } });
    }
    return r.fulfill({
      json: state.calibrated
        ? { state: "ready", template: analysis(true).template, k1: 0, size: [640, 360], coverage: 100, static: true, frames: Array(20).fill({ H: [1 / 15, 0, -20 / 15, 0, 1 / 15, -20 / 15, 0, 0, 1], d: 0 }), job: { state: "done", progress: 100 } }
        : { state: "none" },
    });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration/preview$`), (r) => {
    state.previews++;
    return r.fulfill({
      json: {
        template: analysis(true).template,
        lines: [[[20, 20], [620, 20]], [[20, 320], [620, 320]]],
        fit: {
          H: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
          k1: 0,
          size: [640, 360],
          rms: 0.2,
          rmsPixels: 1.1,
          quality: "good",
          warnings: [],
          fieldOfView: 90,
          cameraHeight: 6,
          lineResiduals: [],
          residuals: [
            { name: "corner-far-left", metres: 0.1, pixels: 1, leftOut: 1.5 },
            { name: "corner-far-right", metres: 0.1, pixels: 1, leftOut: 1.5 },
          ],
        },
      },
    });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/review$`), (r) => {
    const body = r.request().postDataJSON();
    state.reviews.push(body);
    if (body.decisions?.[0]?.action === "accept") state.shot = "confirmed";
    return r.fulfill({ json: { decisions: state.reviews.length, analysis: analysis(state.calibrated, state.shot) } });
  });
  return state;
}

test("an uncalibrated match shows possession and passes, and withholds shots instead of showing zero", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  await page.goto(`/vision/${ID}`);
  await expect(page.getByRole("heading", { name: "Match stats" })).toBeVisible();
  await expect(page.getByText("Unlock shots, heatmaps and positions")).toBeVisible();
  await expect(page.locator('[data-stat="Shots"]')).toContainText("—");
  await expect(page.locator('[data-stat="Passes"]')).toContainText("4");
  await expect(page.getByText("75%").first()).toBeVisible();
});

test("high ball coverage without control is not presented as verified ball accuracy", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const data = analysis(false);
  data.stats.coverage.controlPercent = 0;
  data.stats.coverage.ballStatePercent = 90;
  data.stats.coverage.possessionPercent = 0;
  data.stats.coverage.possessionShown = false;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), r => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  await expect(page.getByText("Ball detections did not establish possession")).toBeVisible();
  await expect(page.getByText("Ball position coverage 90%")).toBeVisible();
  await expect(page.getByText(/Ball state known/)).toHaveCount(0);
});

test("saved footage can start a new section without uploading the file again", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const next = "d".repeat(32);
  const requests: URL[] = [];
  const uploads: string[] = [];
  page.on("request", r => { if (r.method() === "PUT") uploads.push(r.url()); });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/rerun\\?`), r => {
    expect(r.request().method()).toBe("POST");
    requests.push(new URL(r.request().url()));
    return r.fulfill({ json: { id: next, status: "processing" } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${next}$`), r => r.fulfill({ json: { id: next, status: "processing", progress: 5, stage: "Detecting players", title: "Friday 5s" } }));
  await page.goto(`/vision/${ID}`);
  await page.getByLabel("Saved video test start").fill("2");
  await page.getByRole("button", { name: "Test 20 seconds" }).click();
  await expect(page).toHaveURL(`/vision/${next}`);
  expect(requests).toHaveLength(1);
  expect(requests[0].searchParams.get("start")).toBe("2");
  expect(requests[0].searchParams.get("mode")).toBe("section");
  expect(requests[0].searchParams.get("requestId")).toMatch(/^[a-f0-9-]{36}$/);
  expect(uploads).toEqual([]);
});

test("full-match reruns start at zero and retry with the same reservation ID", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const next = "e".repeat(32);
  const requests: URL[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${ID}/rerun\\?`), r => {
    requests.push(new URL(r.request().url()));
    return requests.length === 1
      ? r.fulfill({ status: 503, json: { detail: "Worker restarting" } })
      : r.fulfill({ json: { id: next, status: "processing" } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${next}$`), r => r.fulfill({ json: { id: next, status: "processing", progress: 5, stage: "Detecting players", title: "Friday 5s" } }));
  await page.goto(`/vision/${ID}`);
  await page.getByLabel("Saved video test start").fill("2");
  await page.getByRole("button", { name: "Analyse full match" }).click();
  await expect(page).toHaveURL(`/vision/${next}`);
  expect(requests).toHaveLength(2);
  expect(requests[0].searchParams.get("start")).toBe("0");
  expect(requests[0].searchParams.get("mode")).toBe("full");
  expect(requests[1].searchParams.get("requestId")).toBe(requests[0].searchParams.get("requestId"));
});

test("pitch setup: landmarks are clicked on the video, the fit is checked, then applied to the match", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Set up the pitch" }).first().click();
  const overlay = page.getByTestId("calibration-overlay");
  await expect(overlay).toBeVisible();
  const clickAt = async (x: number, y: number) => {
    const box = (await overlay.boundingBox())!;
    await overlay.click({ position: { x: (x / 640) * box.width, y: (y / 360) * box.height } });
  };
  await expect(page.getByRole("button", { name: "Check fit" })).toBeDisabled();
  for (const [x, y] of [[20, 20], [620, 20], [620, 320], [20, 320], [320, 170]]) await clickAt(x, y);
  await expect(page.getByText("5 landmarks")).toBeVisible();
  await page.getByRole("button", { name: "Check fit" }).click();
  await expect(page.getByText("Good fit")).toBeVisible();
  await page.getByRole("button", { name: "Apply to the whole match" }).click();
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible({ timeout: 15000 });
  const saved = state.saves[0] as { points: { name: string; x: number; y: number }[]; template: { length: number } };
  expect(saved.points.map((p) => p.name).slice(0, 2)).toEqual(["corner-far-left", "corner-far-right"]);
  expect(Math.round(saved.points[0].x)).toBe(20);
  expect(state.previews).toBe(1);
});

test("pitch setup: a click between analysed frames moves to the nearest one and asks again", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Set up the pitch" }).first().click();
  const overlay = page.getByTestId("calibration-overlay");
  await expect(overlay).toBeVisible();
  // Land between the analysed frames at 1.0 s and 1.2 s (as a timeline click would).
  await page.evaluate(() => {
    const v = document.querySelector("video")!;
    v.pause();
    v.currentTime = 1.1;
  });
  await expect.poll(() => page.evaluate(() => document.querySelector("video")!.currentTime)).toBeCloseTo(1.1, 2);
  const clickAt = async (x: number, y: number) => {
    const box = (await overlay.boundingBox())!;
    await overlay.click({ position: { x: (x / 640) * box.width, y: (y / 360) * box.height } });
  };
  await clickAt(20, 20);
  await expect(page.getByText("Moved to the nearest analysed frame", { exact: false })).toBeVisible();
  const snapped = await page.evaluate(() => document.querySelector("video")!.currentTime);
  expect([1.0, 1.2].some((t) => Math.abs(snapped - t) < 0.01)).toBe(true);
  for (const [x, y] of [[20, 20], [620, 20], [620, 320], [20, 320], [320, 170]]) await clickAt(x, y);
  await expect(page.getByText("5 landmarks")).toBeVisible();
  await page.getByRole("button", { name: "Check fit" }).click();
  await expect(page.getByText("Good fit")).toBeVisible();
  await page.getByRole("button", { name: "Apply to the whole match" }).click();
  await expect.poll(() => state.saves.length).toBe(1);
  expect(Math.abs((state.saves[0] as { t: number }).t - snapped)).toBeLessThan(0.01);
});

test("review: confirming a shot is saved on the worker and updates the stats", async ({ page }) => {
  const state = await mockReport(page, { calibrated: true });
  await page.goto(`/vision/${ID}`);
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible();
  await expect(page.getByText("1 possible goal", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await expect(page.getByRole("heading", { name: "Review moments" })).toBeVisible();
  // Browser shortcuts are never taken over (Ctrl/Cmd+A select all, +G find, +R reload...).
  await page.keyboard.press("Control+a");
  await page.keyboard.press("Meta+g");
  await page.waitForTimeout(300);
  expect(state.reviews.length).toBe(0);
  await page.keyboard.press("a");
  await expect.poll(() => state.reviews.length).toBe(1);
  expect((state.reviews[0] as { decisions: { action: string; eventId: string }[] }).decisions[0]).toEqual({
    action: "accept",
    eventId: "ev-0",
    event: { type: "shot", t: 2, team: 0, outcome: "on-target" },
  });
  await expect(page.getByText("1 of 1 reviewed")).toBeVisible();
  await page.getByRole("button", { name: /Goal/ }).first().click();
  await expect.poll(() => state.reviews.length).toBe(2);
  const added = (state.reviews[1] as { decisions: { action: string; type: string; team: number }[] }).decisions[0];
  expect(added.action).toBe("add");
  expect(added.type).toBe("goal");
  expect(added.team).toBe(0);
  await page.getByLabel("Goals for Kit A").fill("3");
  await page.getByLabel("Goals for Kit B").fill("1");
  await page.getByRole("button", { name: "Save score" }).click();
  await expect.poll(() => state.reviews.length).toBe(3);
  expect((state.reviews[2] as { decisions: { action: string; value: number[] }[] }).decisions[0]).toEqual({ action: "score", value: [3, 1] });
});


test("a saved venue is applied without clicking landmarks", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  const applied: unknown[] = [];
  await page.route("**/api/vision/venues", (r) =>
    r.fulfill({ json: [{ id: "e".repeat(32), name: "Tekkerz Court 2", template: analysis(true).template, size: [640, 360], static: true, createdAt: 0 }] }),
  );
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration(\\?.*)?$`), async (r) => {
    if (r.request().method() === "POST") {
      applied.push(r.request().postDataJSON());
      state.calibrated = true;
      return r.fulfill({ json: { state: "processing" } });
    }
    return r.fulfill({
      json: state.calibrated
        ? { state: "ready", template: analysis(true).template, k1: 0, size: [640, 360], coverage: 100, static: true, venue: { id: "e".repeat(32), name: "Tekkerz Court 2", lineScore: 0.8 }, frames: Array(20).fill({ H: [1 / 15, 0, -20 / 15, 0, 1 / 15, -20 / 15, 0, 0, 1], d: 0 }), job: { state: "done", progress: 100 } }
        : { state: "none" },
    });
  });
  await page.goto(`/vision/${ID}`);
  await expect(page.getByText("Use a saved venue")).toBeVisible();
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible({ timeout: 15000 });
  expect(applied).toEqual([{ venue: "e".repeat(32) }]);
  await expect(page.getByText(/From saved venue "Tekkerz Court 2"/)).toBeVisible();
});


test("unreviewed events are excluded from headline counts and uncertainty is not an accuracy guarantee", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  await page.goto(`/vision/${ID}`);
  await expect(page.getByText("Assisted review", { exact: true })).toBeVisible();
  const shots = page.locator('[data-stat="Shots"]');
  await expect(shots).toContainText("0 confirmed · 1 to review");
  await expect(shots).toContainText("—");
  await expect(page.getByText(/^95%:/)).toHaveCount(0);
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await page.keyboard.press("a");
  await expect(shots).toContainText("confirmed in reviewed clips");
});

test("a lost review response retries the same request instead of duplicating a decision", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const requests: { requestId: string; expectedRevision: number }[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${ID}/review$`), (route) => {
    requests.push(route.request().postDataJSON());
    if (requests.length === 1) return route.fulfill({ status: 503, json: { detail: "Reply lost" } });
    return route.fulfill({ json: { decisions: 1, analysis: analysis(true, "confirmed") } });
  });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await page.keyboard.press("a");
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[0].requestId).toMatch(/^[a-f0-9-]{36}$/);
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[0].expectedRevision).toBe(0);
});

test("a stopped analysis retries its saved upload without sending video bytes", async ({ page }) => {
  let retried = false;
  await page.route(`**/api/vision/jobs/${ID}`, (route) => route.fulfill({ json: {
    id: ID, title: "Interrupted match", status: retried ? "processing" : "interrupted",
    stage: retried ? "Retrying saved upload" : "Worker restarted", progress: 0, createdAt: 0,
  } }));
  await page.route(`**/api/vision/jobs/${ID}/retry`, (route) => {
    expect(route.request().method()).toBe("POST");
    retried = true;
    return route.fulfill({ json: { status: "processing" } });
  });
  let videoWrites = 0;
  page.on("request", (request) => { if (request.method() === "PUT") videoWrites++; });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Retry saved upload" }).click();
  await expect(page.getByRole("button", { name: "Retry saved upload" })).toHaveCount(0);
  expect(retried).toBe(true);
  expect(videoWrites).toBe(0);
});

test("deleting a finished analysis confirms the scope and removes the dashboard entry", async ({ page }) => {
  let deleted = false;
  await page.route("**/api/vision/jobs", (route) => route.fulfill({ json: deleted ? [] : [{
    id: ID, title: "Private match", status: "completed", stage: "Analysis complete", progress: 100, createdAt: 0,
  }] }));
  await page.route(`**/api/vision/jobs/${ID}`, (route) => {
    expect(route.request().method()).toBe("DELETE");
    deleted = true;
    return route.fulfill({ json: { deleted: true } });
  });
  page.once("dialog", async (dialog) => {
    expect(dialog.message()).toContain("video, report, reviews");
    await dialog.accept();
  });
  await page.goto("/dashboard");
  await page.getByRole("button", { name: "Delete analysis and footage" }).click();
  await expect(page.getByText("Private match", { exact: true })).toHaveCount(0);
  expect(deleted).toBe(true);
});

test("completed player detection does not imply usable ball analytics", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const data = result();
  data.metrics.ballFrames = 0;
  data.metrics.possessionCoverage = 0;
  data.metrics.teamSeconds = [0, 0];
  data.metrics.unknownSeconds = 4;
  const withoutBall = { ...data, frames: data.frames.map((frame) => ({ ...frame, ball: null })) };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (route) => route.fulfill({ json: withoutBall }));
  await page.goto(`/vision/${ID}`);
  await expect(page.getByRole('heading', { name: 'Ball tracking unavailable for this section' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Inspect detections' })).toBeVisible();
});

test("checked passes turn unchecked detections into an estimate with a range", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const data = analysis(false);
  data.stats.teams[0].passes = { value: 48, confirmed: 8, pending: 40, checked: 20, checkedCorrect: 15, estimate: 38, estimateRange: [29, 44] } as never;
  data.stats.teams[1].passes = { value: 30, confirmed: 2, pending: 28, checked: 20 } as never;
  data.review = { ...data.review, pending: 70, pendingKey: 2, pendingByType: { pass: 68, shot: 2 } } as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const passes = page.locator('[data-stat="Passes"]');
  await expect(passes).toContainText("≈38");
  await expect(passes).toContainText("29–44");
  await expect(page.getByText("2 key moments to check", { exact: false })).toBeVisible();
  await expect(page.getByText("68 passes found", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pitch not set up · set it up" })).toBeVisible();
});

test("the ball can be labelled on frames: accept the guess, click it exactly, or mark it not visible", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const posts: Record<string, unknown>[] = [];
  const state = {
    frames: [
      { index: 2, t: 0.4, guess: { x: 320, y: 180, confidence: 0.6 } },
      { index: 9, t: 1.8, guess: null },
      { index: 15, t: 3.0, guess: null },
    ],
    labels: {} as Record<string, unknown>,
    metrics: {
      labelled: 0, visible: 0, tolerancePixels: 3,
      recall: { value: null, n: 0, interval95: null }, precision: { value: null, n: 0, interval95: null },
      falseDetections: { value: null, n: 0, interval95: null }, inferredAccuracy: { value: null, n: 0, interval95: null },
      medianHitErrorPixels: null,
    },
    videoAvailable: true,
    framesReady: true,
    size: [640, 360],
  };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), async (r) => {
    if (r.request().method() === "POST") {
      const body = r.request().postDataJSON();
      posts.push(body);
      state.labels[String(body.index)] = body.visible === false ? { visible: false } : { visible: true, x: body.x, y: body.y };
      if (posts.length === 1) state.metrics = { ...state.metrics, labelled: 1, visible: 1, recall: { value: 1, n: 1, interval95: [0.21, 1] } } as never;
    }
    return r.fulfill({ json: state });
  });
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYPgPAAEDAQAIicLsAAAAAElFTkSuQmCC",
    "base64",
  );
  await page.route(new RegExp(`/api/vision/jobs/${ID}/frames/\\d+$`), (r) => r.fulfill({ body: png, headers: { "content-type": "image/png" } }));
  const trainings: unknown[] = [];
  const model = {
    active: null as string | null,
    runs: [] as unknown[],
    training: { state: "idle" } as Record<string, unknown>,
  };
  await page.route(/\/api\/vision\/ball-model$/, (r) => r.fulfill({ json: model }));
  await page.route(/\/api\/vision\/ball-model\/train$/, (r) => {
    trainings.push(r.request().postDataJSON());
    model.training = { state: "done", stage: "Finished" };
    model.runs = [{ at: 1, weights: "ball-0123456789abcdef.pt", matches: 2, split: "by-match", counts: { train: { positive: 90, negative: 80 }, valFrames: 150 },
      baseline: { recall: 0.41, precision: 0.8, frames: 150 }, candidate: { recall: 0.67, precision: 0.84, frames: 150 }, kept: true }];
    return r.fulfill({ json: { started: true } });
  });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Label the ball" }).click();
  // The default open site cannot start training (the proxy refuses it): no
  // button that could only fail, and no operator instructions for the viewer.
  await expect(page.getByTestId("training-operator")).toHaveText("Training is run by the site operator.");
  await expect(page.getByRole("button", { name: "Train on my labels" })).toHaveCount(0);
  await expect(page.getByText(/VISION_ACCESS_CODE/)).toHaveCount(0);
  await expect(page.getByTestId("ball-labeller")).toBeVisible();
  await expect(page.getByText("Frame 1 of 3")).toBeVisible();
  // Focus moves into the labeller, so Enter accepts the guess instead of
  // pressing the button that opened it.
  await expect(page.getByTestId("ball-labeller")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ index: 2, x: 320, y: 180 });
  await expect(page.getByTestId("ball-recall")).toHaveText("100%");
  await expect(page.getByText("Frame 2 of 3")).toBeVisible();
  // First click zooms, the second places the ball exactly.
  const frame = page.getByAltText(/Analysed frame/);
  const box = (await frame.boundingBox())!;
  await frame.click({ position: { x: box.width / 4, y: box.height / 2 } });
  const zoom = page.getByTestId("ball-zoom");
  await expect(zoom).toBeVisible();
  const zbox = (await zoom.boundingBox())!;
  await zoom.click({ position: { x: zbox.width / 2, y: zbox.height / 2 } });
  await expect.poll(() => posts.length).toBe(2);
  const placed = posts[1] as { index: number; x: number; y: number };
  expect(placed.index).toBe(9);
  expect(Math.abs(placed.x - 160)).toBeLessThan(2);
  expect(Math.abs(placed.y - 180)).toBeLessThan(2);
  // Keys are ignored while a label is saving: wait for the next frame.
  await expect(page.getByText("Frame 3 of 3")).toBeVisible();
  await page.keyboard.press("n");
  await expect.poll(() => posts.length).toBe(3);
  expect(posts[2]).toEqual({ index: 15, visible: false });
});

test("training on the labels reports old and new ball recall and whether it was switched on", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  // A site with an access code whose worker allows training from the site.
  await page.route("**/api/vision/health", (r) => r.fulfill({ json: { available: true, hosted: true, analytics: true, accessRequired: true, siteTraining: true } }));
  let count = 149;
  const empty = { value: null, n: 0, interval95: null };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), (r) => {
    const frames = Array.from({ length: 150 }, (_, i) => ({ index: i, t: i, guess: null }));
    const labels = Object.fromEntries(frames.slice(0, count).map((f) => [String(f.index), { visible: false }]));
    return r.fulfill({ json: { frames, labels, metrics: { labelled: count, visible: 0, tolerancePixels: 3, recall: empty, precision: empty, falseDetections: empty, inferredAccuracy: empty, medianHitErrorPixels: null }, videoAvailable: true, framesReady: true, size: [640, 360] } });
  });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/frames/\\d+$`), (r) => r.fulfill({ status: 404 }));
  const model = { active: null as string | null, runs: [] as unknown[], training: { state: "idle" } as Record<string, unknown> };
  const started: unknown[] = [];
  await page.route(/\/api\/vision\/ball-model$/, (r) => r.fulfill({ json: model }));
  await page.route(/\/api\/vision\/ball-model\/train$/, (r) => {
    started.push(r.request().postDataJSON());
    model.training = { state: "done", stage: "Finished" };
    model.active = "ball-0123456789abcdef.pt";
    model.runs = [{ at: 1, weights: "ball-0123456789abcdef.pt", matches: 2, split: "by-match", counts: { train: { positive: 90, negative: 80 }, valFrames: 150 },
      baseline: { recall: 0.41, precision: 0.8, frames: 150 }, candidate: { recall: 0.67, precision: 0.84, frames: 150 }, kept: true }];
    return r.fulfill({ json: { started: true } });
  });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Label the ball" }).click();
  // A run needs about 150 labelled frames (30 visible balls held back); one match is enough.
  await expect(page.getByRole("button", { name: "Train on my labels" })).toBeDisabled();
  await expect(page.getByText(/One match is enough/)).toBeVisible();
  count = 150;
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Label the ball" }).click();
  await page.getByRole("button", { name: "Train on my labels" }).click();
  await expect.poll(() => started.length).toBe(1);
  expect(started[0]).toEqual({ epochs: 60 });
  const result = page.getByTestId("ball-training-result");
  await expect(result).toContainText("41% → 67%");
  await expect(result).toContainText("Switched on");
});

test("passes checked in the random-sample view are marked as sampled", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  const data = analysis(false);
  data.events = Array.from({ length: 12 }, (_, i) => ({ id: `ev-${i}`, type: "pass", t: i, team: i % 2, confidence: 0.6, status: "proposed", outcome: "complete" })) as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: /Enter the score and review|Review/ }).first().click();
  await page.getByRole("tab", { name: "Check a sample" }).click();
  await expect(page.getByTestId("sample-progress")).toContainText("passes 0/10");
  await page.keyboard.press("a");
  await expect.poll(() => state.reviews.length).toBe(1);
  const decision = (state.reviews[0] as { decisions: { action: string; sample?: boolean }[] }).decisions[0];
  expect(decision.action).toBe("accept");
  expect(decision.sample).toBe(true);
});

test("the labeller waits while the worker prepares the frames", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const empty = { value: null, n: 0, interval95: null };
  let calls = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), (r) => {
    calls++;
    return r.fulfill({ json: { frames: [{ index: 3, t: 0.6, guess: null }], labels: {}, metrics: { labelled: 0, visible: 0, tolerancePixels: 3, recall: empty, precision: empty, falseDetections: empty, inferredAccuracy: empty, medianHitErrorPixels: null }, videoAvailable: true, framesReady: calls > 1, size: [640, 360] } });
  });
  await page.route(/\/api\/vision\/ball-model$/, (r) => r.fulfill({ json: { active: null, runs: [], training: { state: "idle" } } }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/frames/\\d+$`), (r) => r.fulfill({ status: 404 }));
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Label the ball" }).click();
  await expect(page.getByTestId("ball-frames-preparing")).toBeVisible();
  await expect(page.getByText("Frame 1 of 1")).toBeVisible({ timeout: 8000 });
});

test("a refused action asks for the site's access code on the match page", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/review$`), (r) =>
    r.fulfill({ status: 401, json: { detail: "Enter the access code to continue.", code: "access" } }),
  );
  await page.goto(`/vision/${ID}`);
  await page.evaluate(() => localStorage.setItem("pitchlens-vision-access", "old-code"));
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await page.keyboard.press("a");
  const prompt = page.getByRole("dialog", { name: "Access code needed" });
  await expect(prompt).toBeVisible();
  // The refused code was dropped; the new one is saved for the next attempt.
  expect(await page.evaluate(() => localStorage.getItem("pitchlens-vision-access"))).toBeNull();
  await prompt.getByLabel("Site access code").fill("  new-code ");
  await prompt.getByRole("button", { name: "Save code" }).click();
  await expect(prompt).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem("pitchlens-vision-access"))).toBe("new-code");
});

test("highlights play the goals and shots back to back", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await bar.getByRole("button", { name: "Play highlights (1)" }).click();
  await expect(bar).toContainText("Highlight 1 of 1");
  await expect.poll(() => page.evaluate(() => !document.querySelector("video")!.paused)).toBe(true);
  await bar.getByRole("button", { name: "Stop" }).click();
  await expect(bar.getByRole("button", { name: "Play highlights (1)" })).toBeVisible();
  expect(await page.evaluate(() => document.querySelector("video")!.paused)).toBe(true);
});

/** Real compute_analysis output for a ball-free (player-load) result: nothing about the ball was measured. */
function playerLoadAnalysis() {
  const team = {
    controlSeconds: 0, possessionSeconds: 0, possession: null, possessions: null, averagePossession: null,
    passes: null, passesComplete: null, passAccuracy: null, passShare: null, shots: null, shotsOnTarget: null,
    goals: { value: 0, candidates: null }, interceptions: null, tackles: null, fieldTilt: null,
  };
  return {
    schemaVersion: 1, calibrated: false, template: null, directions: null, events: [], kickoffs: [], enteredScore: null,
    stats: {
      teams: [team, { ...team }],
      coverage: { possessionPercent: 0, possessionShown: false, possessionInterval: null, possessionMissingBounds: null, controlPercent: 0, ballStatePercent: 0, calibratedPercent: 0, inPlaySeconds: 180.2, deadBallSeconds: 0, contestedSeconds: 0, looseSeconds: 0, unknownSeconds: 180.2 },
      momentum: [null, null, null, null], heatmaps: null, averagePositions: null, shotMap: null, possessionSequences: [],
      tracks: { fragments: 336, players: 199 },
    },
    review: { byType: {}, decisions: 0, confirmed: 0, rejected: 0, pending: 0, pendingKey: 0, pendingByType: {} },
    positions: null,
  };
}

const playerLoad = (metres: (number | null)[]) => ({ metres, maxSpeed: null, steps: 1200, method: "feet-homography", playerHeightM: 1.8, footNoisePx: 3 });

test("a player-load job leads with the distance it measured, not dashes, unmeasured 0% or a review banner", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const base = result();
  const loadOnly = {
    ...base,
    ballSearch: "none",
    // Player-load results have no ball timing at all.
    performance: { totalSeconds: 12.5, playerInferenceSeconds: 10.1, device: "cpu" },
    metrics: { ...base.metrics, ballFrames: 0, playerLoad: playerLoad([4321.9, 3876.2]) },
    frames: base.frames.map((f) => ({ ...f, ball: null })),
  };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: loadOnly }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: playerLoadAnalysis() }));
  await page.goto(`/vision/${ID}`);
  const hero = page.getByTestId("report-hero");
  await expect(hero).toContainText("4321.9");
  await expect(hero).toContainText("3876.2");
  await expect(hero).not.toContainText("– : –");
  await expect(page.locator('[data-stat="Distance"]')).toContainText("4321.9 m");
  await expect(page.getByText("Assisted review", { exact: true })).toHaveCount(0);
  // The ball was not tracked: no coverage shown as 0%, no ball-only rows.
  await expect(page.getByText("Ball not tracked (player-load job)")).toBeVisible();
  await expect(page.getByText(/Ball position coverage/)).toHaveCount(0);
  await expect(page.getByText(/Possession followed/)).toHaveCount(0);
  await expect(page.getByText(/could only be followed for 0%/)).toHaveCount(0);
  for (const stat of ["Observed control share", "Passes", "Shots", "Interceptions", "Tackles / balls won", "Field tilt"])
    await expect(page.locator(`[data-stat="${stat}"]`)).toHaveCount(0);
  await expect(page.getByText(/ball model not run/)).toBeVisible();
  // Score entry and manual goals still work, and the pitch setup is still offered.
  await expect(page.getByRole("button", { name: /Enter the score and review/ })).toBeVisible();
  await expect(page.getByText("Unlock heatmaps and positions")).toBeVisible();
});

test("a ball job shows the distance too, keeps its coverage chips, and an unmeasured kit reads —", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const withLoad = result() as ReturnType<typeof result> & { metrics: { playerLoad?: unknown } };
  withLoad.metrics.playerLoad = playerLoad([1234.5, null]);
  const data = analysis(false);
  data.stats.coverage.deadBallSeconds = 42;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: withLoad }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const distance = page.locator('[data-stat="Distance"]');
  await expect(distance).toContainText("1234.5 m");
  await expect(distance).toContainText("—");
  await expect(distance).not.toContainText("0.0");
  await expect(page.getByText("Ball position coverage 95%")).toBeVisible();
  await expect(page.getByText("Possession followed at least 100% of play")).toBeVisible();
  await expect(page.getByText("Assisted review", { exact: true })).toBeVisible();
  // Stoppage detection finds only a sliver of real stoppages: not shown as a figure.
  await expect(page.getByText(/Ball out of play/)).toHaveCount(0);
});

test("the score says which possible goals are still to check instead of a hard 0", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  let reviewed = false;
  const scored = () => {
    const data = analysis(true);
    data.events = [
      { id: "g0", type: "goal-candidate", t: 1, team: 0, confidence: 0.4, status: "confirmed" },
      { id: "g1", type: "goal-candidate", t: 2, team: 1, confidence: 0.4, status: reviewed ? "rejected" : "proposed" },
      { id: "g2", type: "goal-candidate", t: 3.5, team: 1, confidence: 0.4, status: reviewed ? "rejected" : "proposed" },
    ] as never;
    data.stats.teams[0].goals = { value: 1, candidates: 1 };
    data.stats.teams[1].goals = { value: 0, candidates: reviewed ? 0 : 2 };
    return data;
  };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: scored() }));
  await page.goto(`/vision/${ID}`);
  await expect(page.getByTestId("report-hero")).toHaveText(/1\s*–\s*0/);
  await expect(page.getByText("1 confirmed · 2 possible goals still to check")).toBeVisible();
  await expect(page.getByText("Goals confirmed by review")).toHaveCount(0);
  // Team 0's candidate is confirmed (none left to check); team 1 still has two.
  await expect(page.locator('[data-stat="Possible goals"]')).toHaveText(/^0\s*1 confirmed\s*Possible goals\s*2$/, { useInnerText: true });
  reviewed = true;
  await page.reload();
  await expect(page.getByText("Goals confirmed by review")).toBeVisible();
  await expect(page.getByText(/still to check/)).toHaveCount(0);
});

test("a highlight is named by its most important moment: a confirmed possible goal beats its shot", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [
    { id: "s1", type: "shot", t: 2, team: 0, confidence: 0.6, status: "proposed", outcome: "goal-candidate", x: 39, y: 10 },
    { id: "g1", type: "goal-candidate", t: 2, team: 0, confidence: 0.45, status: "confirmed" },
  ] as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await bar.getByRole("button", { name: "Play highlights (1)" }).click();
  await expect(bar).toContainText("Highlight 1 of 1 · 0:02 · Goal");
  await expect(bar).not.toContainText("(to review)");
  await expect(bar).not.toContainText("Shot");
});

test("a reviewer's goal after an automatic shot is one highlight, labelled and timed as the goal", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [
    { id: "s1", type: "shot", t: 1, team: 0, confidence: 0.6, status: "proposed", outcome: "on-target", x: 35, y: 10 },
    { id: "added-1", type: "goal", t: 3, team: 0, confidence: 1, status: "confirmed", source: "manual" },
  ] as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await bar.getByRole("button", { name: "Play highlights (1)" }).click();
  await expect(bar).toContainText("0:03 · Goal");
  await expect(bar).not.toContainText("(to review)");
  // From before the shot to after the goal.
  await expect(bar).toHaveAttribute("data-clip", "0.0-5.0");
});

test("a run of close shots is one highlight that plays through every shot", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [0.5, 3, 5.5, 8].map((t, i) => ({ id: `s${i}`, type: "shot", t, team: 0, confidence: 0.6, status: "proposed", outcome: "on-target", x: 35, y: 10 })) as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await bar.getByRole("button", { name: "Play highlights (1)" }).click();
  await expect(bar).toHaveAttribute("data-clip", "0.0-10.0");
  // The old clip stopped 2 s after the first shot; this one keeps playing.
  await expect.poll(() => page.evaluate(() => document.querySelector("video")!.currentTime), { timeout: 10_000 }).toBeGreaterThan(3);
});

test("a review clip ending never hands the video back to the highlight reel", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [
    { id: "ev-0", type: "shot", t: 0.6, team: 0, confidence: 0.6, status: "proposed", outcome: "on-target", x: 30, y: 10 },
    { id: "ev-1", type: "shot", t: 3.9, team: 1, confidence: 0.6, status: "proposed", outcome: "off-target", x: 30, y: 10 },
  ] as never;
  data.review.pending = 2;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await expect(bar).toBeVisible();
  await page.waitForFunction(() => (document.querySelector("video")?.readyState ?? 0) >= 1);
  await bar.getByRole("button", { name: "Play highlights (2)" }).click();
  await expect(bar).toContainText("Highlight 1 of 2");
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await expect(page.getByRole("heading", { name: "Review moments" })).toBeVisible();
  // The review clip for the 0.6 s shot runs to 3.1 s and stops there.
  const samples: { t: number; bar: string }[] = [];
  for (let i = 0; i < 14; i++) {
    await page.waitForTimeout(350);
    samples.push({ t: await page.evaluate(() => document.querySelector("video")!.currentTime), bar: await bar.innerText() });
  }
  expect(samples.some((s) => s.bar.includes("Highlight 2 of 2"))).toBe(false);
  const late = samples.findIndex((s) => s.t > 2.9);
  expect(late).toBeGreaterThanOrEqual(0);
  for (const s of samples.slice(late)) expect(s.t).toBeGreaterThan(2.9);
});

test("a seek made before the video has loaded is kept when it loads", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  await page.route(new RegExp(`/api/vision/jobs/${ID}/video`), async (r) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await r.fulfill({ status: 200, body: VIDEO, headers: { "content-type": "video/mp4", "accept-ranges": "bytes" } }).catch(() => {});
  });
  await page.goto(`/vision/${ID}`);
  await expect(page.getByRole("heading", { name: "Match timeline" })).toBeVisible();
  expect(await page.evaluate(() => document.querySelector("video")!.readyState)).toBe(0);
  await page.getByRole("button", { name: "Seek to 0:03" }).first().click();
  await page.waitForFunction(() => document.querySelector("video")!.readyState >= 1, null, { timeout: 15_000 });
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => document.querySelector("video")!.currentTime)).toBeCloseTo(3, 1);
  await expect(page.getByText(/visible\s+tracks/)).toContainText("0:03 ·");
});

test("the labeller keeps polling through a worker restart, and stops on a refusal", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const empty = { value: null, n: 0, interval95: null };
  const state = (ready: boolean) => ({ frames: [{ index: 3, t: 0.6, guess: null }], labels: {}, metrics: { labelled: 0, visible: 0, tolerancePixels: 3, recall: empty, precision: empty, falseDetections: empty, inferredAccuracy: empty, medianHitErrorPixels: null }, videoAvailable: true, framesReady: ready, size: [640, 360] });
  let calls = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), (r) => {
    calls++;
    if (calls === 2) return r.fulfill({ status: 503, json: { detail: "Cannot reach the vision server. It may be starting up; retry in a minute.", available: false } });
    return r.fulfill({ json: state(calls > 2) });
  });
  await page.route(/\/api\/vision\/ball-model$/, (r) => r.fulfill({ json: { active: null, runs: [], training: { state: "idle" } } }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/frames/\\d+$`), (r) => r.fulfill({ status: 404 }));
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Label the ball" }).click();
  await expect(page.getByTestId("ball-frames-preparing")).toBeVisible();
  await expect(page.getByText(/Cannot reach the vision server/)).toBeVisible({ timeout: 8000 });
  await expect(page.getByText("Frame 1 of 1")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/Cannot reach the vision server/)).toHaveCount(0);

  // A 404 is final: shown, and not asked again.
  let refused = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), (r) => {
    refused++;
    return r.fulfill({ status: 404, json: { detail: "Job not found" } });
  });
  await page.getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Label the ball" }).click();
  await expect(page.getByText("Job not found")).toBeVisible();
  await page.waitForTimeout(4000);
  expect(refused).toBe(1);
});

test("after a reload a running pitch setup shows its progress, blocks a second one and refreshes the report", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  await page.route("**/api/vision/venues", (r) =>
    r.fulfill({ json: [{ id: "e".repeat(32), name: "Tekkerz Court 2", template: analysis(true).template, size: [640, 360], static: true, createdAt: 0 }] }),
  );
  let reads = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration(\\?.*)?$`), (r) => {
    reads++;
    if (reads >= 3) {
      state.calibrated = true;
      return r.fulfill({ json: { state: "ready", template: analysis(true).template, k1: 0, size: [640, 360], coverage: 100, static: true, frames: Array(20).fill({ H: [1 / 15, 0, -20 / 15, 0, 1 / 15, -20 / 15, 0, 0, 1], d: 0 }), job: { state: "done", progress: 100 } } });
    }
    // Applied by hand before the reload (not an automatic venue setup).
    return r.fulfill({ json: { state: "none", job: { state: "processing", progress: reads === 1 ? 12 : 58 } } });
  });
  await page.goto(`/vision/${ID}`);
  const panel = page.getByRole("region", { name: "Pitch setup" });
  await expect(panel.getByTestId("calibration-running")).toContainText("12%");
  await expect(panel.getByRole("button", { name: "Apply", exact: true })).toBeDisabled();
  await expect(panel.getByRole("button", { name: /Set up the pitch/ })).toBeDisabled();
  await expect(panel.getByTestId("calibration-running")).toContainText("58%", { timeout: 10_000 });
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible({ timeout: 20_000 });
  await expect(panel.getByTestId("calibration-running")).toHaveCount(0);
});

test("one failed status check does not abandon a pitch setup that is still running", async ({ page }) => {
  const state = await mockReport(page, { calibrated: false });
  let posted = false;
  let polls = 0;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration(\\?.*)?$`), (r) => {
    if (r.request().method() === "POST") {
      posted = true;
      return r.fulfill({ json: { state: "processing", fit: { quality: "good" } } });
    }
    if (!posted) return r.fulfill({ json: { state: "none" } });
    polls++;
    if (polls === 1) return r.fulfill({ json: { state: "none", job: { state: "processing", progress: 40 } } });
    if (polls === 2) return r.fulfill({ status: 503, json: { detail: "Cannot reach the vision server. It may be starting up; retry in a minute." } });
    state.calibrated = true;
    return r.fulfill({ json: { state: "ready", template: analysis(true).template, k1: 0, size: [640, 360], coverage: 100, static: true, frames: Array(20).fill({ H: [1 / 15, 0, -20 / 15, 0, 1 / 15, -20 / 15, 0, 0, 1], d: 0 }), job: { state: "done", progress: 100 } } });
  });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Set up the pitch" }).first().click();
  const overlay = page.getByTestId("calibration-overlay");
  await expect(overlay).toBeVisible();
  for (const [x, y] of [[20, 20], [620, 20], [620, 320], [20, 320]]) {
    const box = (await overlay.boundingBox())!;
    await overlay.click({ position: { x: (x / 640) * box.width, y: (y / 360) * box.height } });
  }
  await page.getByRole("button", { name: "Check fit" }).click();
  await page.getByRole("button", { name: "Apply to the whole match" }).click();
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible({ timeout: 20_000 });
  expect(polls).toBeGreaterThanOrEqual(3);
  await expect(page.getByText(/Cannot reach the vision server/)).toHaveCount(0);
});

test("changing the pitch type drops clicks the new pitch has no landmark for", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const previews: { points: { name: string }[] }[] = [];
  await page.route(new RegExp(`/api/vision/jobs/${ID}/calibration/preview$`), (r) => {
    previews.push(r.request().postDataJSON());
    return r.fulfill({ json: { template: analysis(true).template, lines: [], fit: { H: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], k1: 0, size: [640, 360], rms: 0.2, rmsPixels: 1.1, quality: "good", warnings: [], fieldOfView: null, cameraHeight: null, lineResiduals: [], residuals: [] } } });
  });
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Set up the pitch" }).first().click();
  const overlay = page.getByTestId("calibration-overlay");
  await expect(overlay).toBeVisible();
  const clickAt = async (x: number, y: number) => {
    const box = (await overlay.boundingBox())!;
    await overlay.click({ position: { x: (x / 640) * box.width, y: (y / 360) * box.height } });
  };
  await page.getByLabel("Pitch type").selectOption("eleven-a-side");
  await page.getByLabel("Landmark to place").selectOption("left-area-far");
  await clickAt(200, 100);
  await expect(page.getByText("1 landmark ·")).toBeVisible();
  await page.getByLabel("Pitch type").selectOption("futsal");
  await expect(page.getByText("1 point was removed", { exact: false })).toBeVisible();
  await expect(page.getByText("0 landmarks ·")).toBeVisible();
  for (const [x, y] of [[20, 20], [620, 20], [620, 320], [20, 320]]) await clickAt(x, y);
  await expect(page.getByText("4 landmarks ·")).toBeVisible();
  await page.getByRole("button", { name: "Check fit" }).click();
  await expect.poll(() => previews.length).toBe(1);
  expect(previews[0].points.map((p) => p.name)).toEqual(["corner-far-left", "corner-far-right", "corner-near-right", "corner-near-left"]);
  await expect(page.getByText(/Unknown landmark/)).toHaveCount(0);
});

test("the dashboard stops asking for its list after a refusal, but keeps checking through a worker restart", async ({ page }) => {
  await page.clock.install();
  let status = 401;
  let reads = 0;
  await page.route("**/api/vision/jobs", (r) => {
    reads++;
    return status === 401
      ? r.fulfill({ status, json: { detail: "Open this match in the browser that uploaded it.", code: "ownership" } })
      : r.fulfill({ status, json: { detail: "Cannot reach the vision server. It may be starting up; retry in a minute." } });
  });
  await page.goto("/dashboard");
  await expect.poll(() => reads).toBe(1);
  await page.clock.runFor(65_000);
  await page.waitForTimeout(500);
  expect(reads).toBe(1);
  // A worker that is restarting (5xx) is checked again, every 30 s.
  status = 503;
  await page.reload();
  await expect.poll(() => reads).toBe(2);
  await page.clock.runFor(31_000);
  await expect.poll(() => reads).toBe(3);
});

test("dragging the video's own scrubber stops the highlight reel", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [
    { id: "ev-0", type: "shot", t: 0.6, team: 0, confidence: 0.6, status: "proposed", outcome: "on-target", x: 30, y: 10 },
    { id: "ev-1", type: "shot", t: 3.9, team: 1, confidence: 0.6, status: "proposed", outcome: "off-target", x: 30, y: 10 },
  ] as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  const bar = page.getByTestId("highlights");
  await expect(bar).toBeVisible();
  await page.waitForFunction(() => (document.querySelector("video")?.readyState ?? 0) >= 1);
  await bar.getByRole("button", { name: "Play highlights (2)" }).click();
  await expect(bar).toContainText("Highlight 1 of 2");
  // A seek the page did not make (the native controls) hands the video back to the viewer.
  await page.evaluate(() => { document.querySelector("video")!.currentTime = 3.5; });
  await expect(bar.getByRole("button", { name: "Play highlights (2)" })).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(bar).not.toContainText("Highlight 2 of 2");
});

/** The performance block a real player-load run writes (engine 2.7 adds ballInferenceSeconds: null). */
const playerLoadTimings = {
  playerInferenceSeconds: 41.2, playerInferenceCalls: 68, setupSeconds: 3.1, decodeSeconds: 6.4, cameraMotionSeconds: 2.2,
  ballRecoverySeconds: 0, totalSeconds: 58.9, device: "cpu", batchSize: 8, analysedFrames: 540,
};

for (const [name, timings] of [
  ["without a ball time (results from before the fix)", playerLoadTimings],
  ["with a null ball time", { ...playerLoadTimings, ballInferenceSeconds: null }],
] as const) {
  test(`a player-load report renders ${name}, instead of crashing the page`, async ({ page }) => {
    const errors: Error[] = [];
    page.on("pageerror", (e) => errors.push(e));
    await mockReport(page, { calibrated: false });
    const base = result();
    const loadOnly = {
      ...base,
      ballSearch: "none",
      ballInference: "skipped",
      performance: timings,
      metrics: { ...base.metrics, ballFrames: 0, teamSeconds: null, possessionCoverage: null, possessionShare: [null, null], possessions: null, playerLoad: playerLoad([98.2, 84.6]) },
      frames: base.frames.map((f) => ({ ...f, ball: null })),
    };
    await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: loadOnly }));
    await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: playerLoadAnalysis() }));
    await page.goto(`/vision/${ID}`);
    await expect(page.getByTestId("report-hero")).toContainText("98.2");
    const times = page.getByTestId("processing-times");
    await expect(times).toHaveText("Processing took 58.9s on cpu · players 41.2s · ball model not run.");
    await expect(page.getByText("Application error")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test("a shot read as a possible goal says confirming it is not a goal, and Goal sends a goal outcome", async ({ page }) => {
  const state = await mockReport(page, { calibrated: true });
  const data = analysis(true);
  data.events = [
    { id: "s1", type: "shot", t: 2, team: 0, confidence: 0.6, status: "proposed", outcome: "goal-candidate", onTarget: true, x: 35, y: 10 },
    { id: "g1", type: "goal-candidate", t: 2, team: 0, confidence: 0.45, status: "proposed", x: 35, y: 10 },
  ] as never;
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Start reviewing" }).click();
  await expect(page.getByRole("heading", { name: "Review moments" })).toBeVisible();
  // Possible goals come first; the shot is next.
  await expect(page.getByTestId("shot-goal-note")).toHaveCount(0);
  await page.getByRole("button", { name: "Next moment" }).click();
  await expect(page.getByTestId("shot-goal-note")).toHaveText("Looks like a goal: confirm the Possible goal or choose Goal below to count it.");
  const outcome = page.getByLabel("Shot outcome");
  // The automatic reading is shown but cannot be chosen.
  await expect(outcome).toHaveValue("goal-candidate");
  await expect(outcome.locator('option[value="goal-candidate"]')).toHaveAttribute("disabled", "");
  await outcome.selectOption("goal");
  await expect.poll(() => state.reviews.length).toBe(1);
  expect((state.reviews[0] as { decisions: unknown[] }).decisions[0]).toEqual({
    action: "outcome",
    eventId: "s1",
    event: { type: "shot", t: 2, team: 0, outcome: "goal-candidate" },
    value: "goal",
  });
});

test("the labeller shows no ball accuracy for a job that never ran the ball model", async ({ page }) => {
  await mockReport(page, { calibrated: false });
  const unmeasured = { value: null, n: 0, interval95: null };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/ball-labels$`), (r) =>
    r.fulfill({ json: {
      frames: [{ index: 2, t: 0.4, guess: null }, { index: 9, t: 1.8, guess: null }],
      labels: { "2": { visible: true, x: 100, y: 100 } },
      metrics: { labelled: 1, visible: 1, tolerancePixels: 3, measured: false, recall: unmeasured, precision: unmeasured, falseDetections: unmeasured, inferredAccuracy: unmeasured, medianHitErrorPixels: null },
      videoAvailable: true, framesReady: true, size: [640, 360],
    } }),
  );
  await page.route(/\/api\/vision\/ball-model$/, (r) => r.fulfill({ json: { active: null, runs: [], training: { state: "idle" } } }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/frames/\\d+$`), (r) => r.fulfill({ status: 404 }));
  await page.goto(`/vision/${ID}`);
  await page.getByRole("button", { name: "Label the ball" }).click();
  const labeller = page.getByTestId("ball-labeller");
  await expect(page.getByTestId("ball-recall")).toHaveText("—");
  await expect(page.getByTestId("ball-precision")).toHaveText("—");
  await expect(page.getByTestId("ball-not-run")).toHaveText("The ball model was not run on this job; your labels still train the ball finder.");
  await expect(labeller).toContainText("ball not searched in this job");
  await expect(labeller).not.toContainText("0%");
  await expect(labeller).not.toContainText("Pitchlens found no ball");
});

test("the classic report shows a measured kit's distance and — for an unmeasured one, without crashing", async ({ page }) => {
  const errors: Error[] = [];
  page.on("pageerror", (e) => errors.push(e));
  await mockReport(page, { calibrated: false });
  // Analytics unavailable (an older worker): the classic match centre is shown.
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ status: 404, json: { detail: "Not found" } }));
  const base = result();
  const loadOnly = {
    ...base,
    ballSearch: "none",
    metrics: { ...base.metrics, ballFrames: 0, teamSeconds: null, possessionCoverage: null, possessionShare: [null, null], possessions: null, playerLoad: playerLoad([119.7, null]) },
    frames: base.frames.map((f) => ({ ...f, ball: null })),
  };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: loadOnly }));
  await page.goto(`/vision/${ID}`);
  await expect(page.getByText("119.7 m")).toBeVisible();
  const hero = page.getByTestId("centre-distance");
  await hero.scrollIntoViewIfNeeded(); // the figure counts up once it is on screen
  await expect(hero).toContainText("119.7");
  await expect(hero).toContainText("—");
  await expect(hero).not.toContainText("0.0");
  await expect(page.getByText("Application error")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("a set-up pitch on a player-load job says shots were not looked for, not 'no shots'", async ({ page }) => {
  await mockReport(page, { calibrated: true });
  const base = result();
  const loadOnly = { ...base, ballSearch: "none", metrics: { ...base.metrics, ballFrames: 0, playerLoad: playerLoad([98.2, 84.6]) }, frames: base.frames.map((f) => ({ ...f, ball: null })) };
  const pl = playerLoadAnalysis();
  const calibrated = analysis(true);
  const data = {
    ...pl,
    calibrated: true,
    template: calibrated.template,
    directions: calibrated.directions,
    positions: calibrated.positions,
    stats: { ...pl.stats, shotMap: [], heatmaps: calibrated.stats.heatmaps, coverage: { ...pl.stats.coverage, calibratedPercent: 100, shotsBasis: "no-ball" } },
  };
  await page.route(new RegExp(`/api/vision/jobs/${ID}/result$`), (r) => r.fulfill({ json: loadOnly }));
  await page.route(new RegExp(`/api/vision/jobs/${ID}/analysis$`), (r) => r.fulfill({ json: data }));
  await page.goto(`/vision/${ID}`);
  await expect(page.getByRole("heading", { name: /Shot map/ })).toBeVisible();
  await expect(page.getByTestId("shot-map-empty")).toHaveText("Shots are not looked for in a player-load job; add any you saw in the review.");
  await expect(page.getByText("No shots detected", { exact: false })).toHaveCount(0);
});
