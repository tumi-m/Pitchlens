# Status

Read this first after any break. Updated 2026-09-28.

## Where things stand

Branch `claude/pitchlens-mvp-8qhpP` (not yet merged to `main`).

A match now produces a Sofascore-style report:
- **Automatic, with coverage shown:** possession (time-based, with a 95%
  interval, withheld under 60% coverage), possession by passes, passes and
  pass accuracy (20+ attempts), interceptions, tackles, field tilt, attack
  momentum.
- **After a two-minute pitch setup** (click landmarks on one frame, or reuse a
  saved venue): shots, shots on target, shot map, possible goals, heatmaps,
  average positions, a top-down live view, pitch lines drawn on the video.
- **Human-confirmed:** goals (never automatic), shot outcomes, anything the
  reviewer adds; the final score typed by the reviewer sets the scoreline.
- **Not offered:** player identities, distance/speed, xG, PPDA.

## Evidence so far (see EXPERIMENTS.md)

- Event logic on human-labelled open tracking data (Metrica), held-out match,
  degraded like one 360p camera: pass F1 0.62, possession share error
  1.2 points, possession agreement 91.5% on 77% of play; shots F1 0.20.
  This validates the rules, not the vision; vision accuracy is unmeasured.
- Calibration: synthetic panning video with motion gaps stays within 0.5 m.
- Tracking: track IDs on a 3-minute clip 329 → 199 (97.5% of observations
  kept); 151-199 player tracks after stitching.
- Owner's 20 s GPU diagnostic of the night 5-a-side: ball in 91% of frames,
  possession followed for 54% of play (pre-calibration).

## Current task

Adversarial review of the new code: calibration, analytics, worker and
tracker findings fixed; frontend findings pending.

## Blockers (owner)

1. Real footage with labels: 6-10 short clips and 2-3 full matches from the
   target venues (PUSHIT at Tekkerz), with final scores. Needed for any
   vision-level accuracy claim (RELEASE-GATES.md).
2. Venue facts: court dimensions, goal size, markings, camera height/lens.
3. Licensing decision on Ultralytics AGPL-3.0 before charging (DECISIONS.md).
4. Business details and payment account before billing is built.

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
