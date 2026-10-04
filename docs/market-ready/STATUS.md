# Status

Read this first after any break. Updated 2026-10-03.

## Where things stand

Claude implementation merged to main in `1b51ffb`. Commercial-hardening changes are recorded below; check current Git HEAD before resuming.

A match now produces a Sofascore-style report:
- **Automatic, with coverage shown:** observed control share (with missing-time sensitivity bounds, withheld under
  60% estimated coverage), candidate pass share, field tilt and attack momentum.
  Event counts are confirmed-only in the report; reviewed pass accuracy needs
  20+ confirmed attempts. Automatic proposals remain in the review queue.
- **After a two-minute pitch setup** (click landmarks on one frame, or reuse a
  saved venue): shots, shots on target, shot map, possible goals, heatmaps,
  average positions, a top-down live view, pitch lines drawn on the video.
- **Human-confirmed:** goals (never automatic), shot outcomes, anything the
  reviewer adds; the final score typed by the reviewer sets the scoreline.
- **Not offered:** player identities, distance/speed, xG, PPDA.

## Evidence so far (see EXPERIMENTS.md)

- Event logic on human-labelled open tracking data (Metrica), held-out match,
  degraded like one 360p camera: pass F1 0.62, possession share error
  0.1 points, possession agreement 89.6% on 85% of play; shot recall 67%
  (precision 34%, all shots go to review).
  This validates the rules, not the vision; vision accuracy is unmeasured.
- Calibration: synthetic panning video with motion gaps stays within 0.5 m.
- Tracking: track IDs on a 3-minute clip 329 → 199 (97.5% of observations
  kept); 151-199 player tracks after stitching.
- Owner's 20 s GPU diagnostic of the night 5-a-side: ball in 91% of frames,
  possession followed for 54% of play (pre-calibration).

## 4 October: what changed (Claude)

- The report no longer shows an empty page until every moment is reviewed:
  checking about ten random detections of a type turns the rest into an
  estimate (≈N with a 95% range). The review asks for key moments only.
- Ball data engine: label the ball on 150 frames per match in the app; the
  report shows measured ball recall/precision for that match; a Modal GPU job
  fine-tunes the ball model on all labels and switches it on only if it
  validates better on held-out matches (docs/BALL-TRAINING.md).
- Tried and not adopted (EXPERIMENTS E13, E14): automatic pitch calibration by
  pose search; full-frame-rate ball trajectories without an appearance model.

## Current task

**Core features first, per owner instruction.** Pause secondary commercial work.
Fix upload/recovery, run real detection on the supplied footage, and verify the
report and video playback. Read [CORE-WORKFLOW.md](../CORE-WORKFLOW.md) for the
acceptance order and the 3 October real-video results. That check completed the
upload/inference/report path, but retained no ball track in its 20-second section;
automatic ball analytics remain a core unresolved gap.

## Blockers (owner)

1. Real footage with labels: 6-10 short clips and 2-3 full matches from the
   target venues (PUSHIT at Tekkerz), with final scores. Needed for any
   vision-level accuracy claim (RELEASE-GATES.md).
2. Venue facts: court dimensions, goal size, markings, camera height/lens,
   and whether the camera is fixed (the fixed-camera thresholds are only
   checked on synthetic video so far; DECISIONS.md).
3. Licensing decision on Ultralytics AGPL-3.0 before charging (DECISIONS.md).
4. Market confirmed: South Africa. No merchant account yet. Paystack test checkout
   and a sandbox ledger are prepared; merchant signup, test key and provider smoke
   test remain with the owner. Live billing remains disabled.

## Next commands

```bash
# backend tests
cd backend && .venv312/bin/python -m pytest tests -q -p no:cacheprovider \
  --ignore=tests/test_contracts.py --ignore=tests/test_pipeline.py
# event logic against labelled tracking data (clone metrica-sports/sample-data)
.venv312/bin/python scripts/evaluate_on_tracking_data.py PATH/sample-data/data --game 2 \
  --noise 0.3 --ball-noise 0.5 --drop 0.3 --feet-drop 0.3 --fragment 5
# score a real analysed match against labels
.venv312/bin/python scripts/evaluate_match.py JOB_DIR labels.json --markdown report.md
# frontend
cd ../frontend && npx tsc --noEmit && npm run lint && npm run build && npx playwright test
```

## Next engineering steps (ordered)

1. Possession HMM (control / flight / dead states, forward-backward) tuned on
   degraded open data — recovers possession through ball dropouts.
2. Multi-frame heatmap ball detector (WASB soccer weights) as an extra
   candidate source on Modal; measure on labelled night frames.
3. Per-tracklet team posterior with keeper by penalty-area time; identity
   merge/split review so named player stats become possible.
4. Keyframe-homography camera motion for panning phone footage.


## Commercial hardening review — 28 September 2026

The calibration/review/evaluation work is a substantial improvement, but does not
pass the automatic-analytics launch gates. Tracking-data event tests are not
end-to-end video accuracy. Model coverage and a bootstrap interval do not validate
possession or shots. No commercial-ready claim is supported yet.

Implemented after reviewing main `1b51ffb`:

- Private match reads/writes require the existing browser ownership capability,
  including video, result, calibration, review, cancellation and venue creation.
  A match UUID/URL alone is no longer access. Proxy uses an HttpOnly SameSite
  cookie for media/export requests; GPU fetches have expiring per-video grants.
- Review requests have persisted idempotency IDs, retry safely after lost replies,
  and reject stale revisions from another tab. UI prevents concurrent submissions.
- Confirmed event counts are separated from pending candidates. Reviewed pass
  accuracy requires 20 confirmed passes. Possession shows observed control with
  missing-time sensitivity bounds, not a misleading accuracy interval.
- Customers can delete footage, detections, calibration, reviews, cached analysis
  and venue setups derived from that match. Active work must stop first.
- Failed/interrupted jobs can retry a complete saved upload, at most three total
  attempts. Hosted full matches require GPU by default; no silent slow CPU fallback.

Still required before commercial launch: account/organization identity and recovery,
transactional distributed jobs/quotas, account-bound live billing/refunds, model/data rights,
real held-out venue video evaluation, measured full-match GPU cost/latency, and a
customer pilot. Browser capabilities improve privacy but are not team accounts.
The filesystem worker remains single-process; do not scale replicas against the
same volume or call the review ledger a multi-node transactional database.

## South Africa payment preparation

Paystack/ZAR sandbox checkout, server verification and signed webhook settlement
are implemented with a persistent transactional SQLite test ledger. Duplicate
deliveries and browser returns cannot double-credit. No account/key has been
supplied and no provider transaction is verified yet. Live keys are refused;
sandbox credits do not purchase or unlock analysis. See [setup and launch gaps](PAYSTACK-SETUP.md).
