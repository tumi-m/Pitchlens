# Pitchlens: a credible route to a much better product

## Assessment

The repository was a visually developed prototype with three competing processing paths. Its README described capabilities the running application did not deliver. The biggest opportunity is to make football insight traceable to video evidence, then automate a validated workflow incrementally. “100×” is an ambition, not a measured result.

## What was wrong and what changed

1. **Invented outcomes:** the browser seeded random goals, fouls, passes, xG and narratives from filename/size. Even a failed video could become a completed match. Removed that processing path; corrupt videos fail visibly. New reviews contain real metadata and user-tagged evidence.
2. **Misleading AI provenance:** six frame detections were mixed with simulated statistics and labelled live analytics. New AI output is restricted to actual sample-frame boxes, labels and confidence. It makes no claims about team identity or match outcomes.
3. **No usable playback:** uploaded footage was not retained in the primary flow. Added IndexedDB video persistence, playback after reload and timestamp navigation.
4. **No correction loop:** users could not fix generated claims. Added editable manual tags (time, team, type and note), undo for the last removal, notes, observation counts and structured JSON export/import with original-video reconnection.
5. **Fake authentication:** a forged guest object was presented as a Firebase user. Restored real auth subscriptions and made local guest review explicit without a fake account.
6. **Silent storage loss:** storage quota failures deleted older matches and could still silently fail. Removed eviction; persistence errors are surfaced. New videos are cleaned up if metadata saving fails.
7. **Conflicting sources:** remote snapshots could alternate with local null values; browser writes tried to bypass the intended server-owned stats model. Added explicit local subscriptions and removed background client stats uploads.
8. **False sharing:** device-local URLs were copied as share links. Removed misleading report sharing; use export. Historical PDFs now carry an unverified-data notice.
9. **Exposed inference budget:** the proxy accepted unrestricted input and hid provider errors. Added production token verification, bounded streamed input, JPEG validation, timeout, two-request concurrency and a process budget. Distributed account quotas remain necessary before scaling.
10. **Large-file proxy mismatch:** the Drive proxy streamed up to 500 MB through a short-lived web endpoint and the UI did not reliably validate the resulting media. The supported flow now asks for a local download and validates it through the decoder.
11. **Writable analytics:** Firestore rules blocked status changes but permitted modification of stats. Metadata-only updates now use an allowlist. User roles cannot be self-promoted. Storage uploads must reference a match owned by the uploader.
12. **Competing engines:** Cloud Functions had another fabricated fallback. Replaced it with one authenticated dispatch path, transactional ownership checks and explicit failure.
13. **Broken Python setup:** invalid Poetry metadata, unavailable SDK constraint and an import for a different SDK. Corrected metadata and SDK integration; removed unrelated fallback model and unused heavyweight dependencies.
14. **Cloud startup failure:** Firestore credentials were resolved at module import. Database initialization is lazy, allowing credential-free health checks.
15. **Lost background work:** the API returned before its thread completed. It now awaits processing so request-based compute remains active. A durable queue is still the correct production architecture.
16. **Backend correctness defects:** fixed corner detection's invalid `any` call, duplicate goal/shot aggregation, frontend/backend pass-edge names and tracker subsampling rate. Bounded retained jersey crops. Empty player detection cannot become a successful analysis.
17. **Unsupported claims:** removed guaranteed sub-minute professional analytics, unsupported subscriptions and retention promises from the homepage. The new design describes the working review room.
18. **Delivery quality:** added a repeatable browser suite, real synthetic video fixture, backend contract tests, noninteractive lint configuration, setup examples and ignored secrets/generated files. Updated the framework and vulnerable dependencies.

## Priority 1 — Trust and a complete coaching workflow

- **Evidence-first event editing:** Editable time/team/type/notes and single-removal undo now work. Next add player attribution, keyboard shortcuts, full undo/redo and bulk review. Acceptance: every exported event links to a playable source timestamp and has an author/source.
- **Import and portable projects:** Schema-validated JSON import and video relinking now work. New videos use a bounded first/last-chunk fingerprint; next add full-file hashing and ZIP project export. Acceptance: move a review between two browsers without losing tags, notes or timestamps.
- **Reliable storage:** move review metadata and frame images into IndexedDB as well; show disk usage and request persistent storage. Acceptance: quota exhaustion leaves every existing review intact and identifies the unsaved item.
- **Useful coach outputs:** generate a concise printable report, selected video clips and a practice checklist from reviewed moments. Acceptance: the report distinguishes reviewed counts from unknown match totals and includes source provenance.
- **Accessibility:** conduct keyboard-only and screen-reader passes, label video alternatives, support captions where available, test 200% zoom and touch targets. Acceptance: WCAG AA contrast and no blocked core operation without a mouse.
- **Usability:** observe five coaches completing an unassisted first review. Target: first saved tag in under two minutes after a playable file is selected; measure abandonment and error recovery before redesigning further.

## Priority 2 — Earn the right to automate

- **Define the filming contract:** start with a fixed elevated full-pitch camera and one five-a-side format. Record pitch dimensions, attack directions, halves and camera cuts. Avoid claiming support for arbitrary shaky phone footage.
- **Pitch calibration:** let users select at least four non-collinear landmarks; validate reprojection residuals and recalibrate on camera motion. Use player foot points. Target before release: held-out median pitch error below a pre-agreed metre threshold, reported by venue.
- **Team identity:** collect labelled jersey examples and map clusters to user-confirmed teams, including goalkeepers/referees. Track uncertainty through lighting changes and substitutions. Unknown must remain unknown.
- **Stable tracking:** maintain identities through occlusion; evaluate IDF1/HOTA and ID switches on annotated footage. Store timestamped trajectories separately from summary documents.
- **Ball tracking:** train/evaluate on small, blurred and occluded balls; distinguish absent observations from interpolated positions. Keep confidence and coverage with every derived feature.
- **Event detection:** use temporal sequences and hysteresis/cooldown, not isolated positions. Start with reviewable shot/pass candidates. Target precision/recall on an independently labelled test set and require human confirmation for score-affecting events.
- **Possession:** estimate duration with uncertainty, not number of changes or which half contains the ball. Unknown time must be visible and excluded transparently.
- **xG:** assemble a documented outcome-labelled dataset appropriate to five-a-side; train/calibrate and evaluate reliability/Brier score on held-out venues. The current handwritten logistic coefficients are not a validated model.
- **Spatial analytics:** distinguish occupancy heatmaps, nearest-player space partitions and actual pass edges. A proximity edge is not evidence of a completed pass.
- **Grounded summaries:** derive factual prose from reviewed events and explicit denominators. If an LLM is added, constrain output to evidence IDs and reject unsupported numerical claims.

## Priority 3 — Production architecture and security

- One versioned `Video → Job → Observation → ReviewedEvent → Report` contract, with schema migrations and contract tests across TypeScript/Python.
- Upload directly to object storage with resumable chunks; avoid large files through the web server. Store object identifiers, not expiring signed URLs; generate fresh URLs on authorized access.
- Use Cloud Tasks/PubSub plus a worker/job service with idempotency keys, leases, heartbeat, timeout, cancellation and dead-letter handling. Retries must not duplicate events or charges.
- Keep raw detections/trajectories in object storage or a suitable analytical store; Firestore holds bounded summaries and job state. Enforce document-size budgets.
- Add per-user distributed quotas, App Check where appropriate, request limits, billing caps and deletion/retention policies. The new process limiter is not a multi-instance billing control.
- Emulator tests for owner/non-owner reads/writes, forged stats, privilege escalation, cross-owner upload paths and deletion. Stage deployments before production and retain a rollback path.
- Instrument correlation/job IDs, structured error codes, processing-stage latency, detector coverage and cost per video minute. Do not log signed URLs, tokens or frames.
- Adopt supported runtimes and automated dependency updates; review license/model usage constraints and maintain a software bill of materials.

## Priority 4 — Performance and commercial focus

- Lazy-load historical chart/report code separately from the current video review room; avoid pulling D3/Recharts into simple routes.
- Extract frames in a worker where feasible, batch inference, cap resolution and sample adaptively around candidate events. Benchmark CPU, memory and bandwidth on low-end phones and laptops.
- Cache results by content hash + model/calibration version; avoid paying for duplicate video analysis.
- Pilot with a few five-a-side coaches, not many sports. Measure reviews/week, clips shared, correction rate, time saved and four-week retention.
- Price only after observing actual compute/storage cost and repeat usage. Do not sell “unlimited” unbounded inference. Gate by video minutes or clear job quotas with visible usage.
- Build team/club collaboration after personal review is reliable: invitations, roles, explicit report permissions and revocation. Never treat a guessed/local URL as public sharing.

## What still prevents validated automatic analytics

The repository does not include a representative match-video evaluation set, event labels, a calibrated pitch model, trained/validated xG model or production service configuration. Those are required inputs to demonstrate automatic correctness, accuracy, processing cost and latency. This change makes local review usable and stops false analytics; it does not pretend those missing foundations are solved.
