# Release gates

Targets are frozen before evaluation. "Unknown" stays visible until measured
on held-out matches split by match and venue (never by adjacent frames).
Measure with `python backend/scripts/evaluate_match.py JOB_DIR labels.json`.

## Gate 1 · Paid assisted-review pilot

| Gate | Target | Current |
|---|---|---|
| Report opens with coverage and review status for every stat | Always | Met (code) |
| Every event opens its clip; confirm, reject, switch team, add missed | Always | Met (code; browser tests) |
| Goals only from reviewer confirmation | Always | Met (code) |
| Median review time per 60-min match | ≤ 10 min | Unknown (needs pilot) |
| Coach time saved vs their current workflow | ≥ 50% | Unknown (needs pilot) |
| Pilot teams completing 3 match cycles, ≥3 paying | 5 teams | Not started (owner) |
| Upload-to-report p95 for a 60-min match on the Modal L4 | ≤ 20 min | Unknown (estimate from a 20 s run: ~20-25 min) |
| Accepted jobs completing | ≥ 99% | Unknown |
| No critical security / data-loss / cross-tenant defects | None open | Shared access code only; accounts not built |

## Gate 2 · Automatic team analytics (per metric, within the supported domain)

Supported domain for the first release: one continuous wide view of a
known pitch (fixed venue camera or steady wide pan), 360p or better, two
distinguishable kits.

| Metric | Target | Current |
|---|---|---|
| Ball: precision of emitted positions | ≥ 98% | Unknown on held-out labels (retrospective: 8/8 on one night clip) |
| Ball: visible-ball recall at max(3 px, ½ diameter) | ≥ 90% | Unknown |
| Players: recall at IoU ≥ 0.5 | ≥ 95% | Unknown |
| Team: accuracy on emitted labels / coverage | ≥ 98% / ≥ 90% | Unknown (coverage 74-84% of observations on local clips) |
| Calibration: held-out landmark error | median ≤ 1 m, p95 ≤ 2 m | Synthetic: ≤ 0.5 m through a pan with motion gaps; real footage unknown |
| Possession share error vs adjudicated labels | MAE ≤ 5 pts, p95 ≤ 10 pts, coverage ≥ 85% | Unknown; share withheld below 60% coverage |
| Passes | precision ≥ 95%, recall ≥ 85% | Unknown (literature: F1 0.71-0.90 depending on data) |
| Shots | precision ≥ 95%, recall ≥ 90% | Unknown; always reviewed until met |
| Held-out sample | ≥ 30 matches, ≥ 5 venues, ≥ 100 examples per event | 0 (owner to supply footage) |

## Gate 3 · Advanced player analytics

Player identity, distances, speeds: not offered. Requires persistent
identity (not feasible from shirt numbers at 20-25 px) and validated
calibration. Revisit after tracker and stitching work plus user naming.
