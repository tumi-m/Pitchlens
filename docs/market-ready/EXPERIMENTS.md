# Experiments

Hypothesis, data, configuration, outcome, decision. Newest first.
Development data only unless marked; none of these is a held-out result.

## E8 · Event engine against human-labelled events (Metrica open data, 2026-09-28)
- Data: Metrica Sports sample games 1 and 2 (25 fps optical tracking with
  hand-labelled events; acknowledged, not redistributed). Game 1 = development
  (all tuning), game 2 = held out (run once per setting after tuning stopped).
- Harness: `backend/scripts/evaluate_on_tracking_data.py` converts tracking to
  Pitchlens frames and degrades it: 5 fps, position noise, ball missing at
  random and extra at players' feet, IDs fragmented; then runs the unchanged
  analytics and scores passes, shots, interceptions (one-to-one, ±1 s / ±2 s)
  and possession (per 0.2 s, event-derived labels).
- Changes found necessary on game 1: gain validation on either side of a
  frame (reception/release), duel zone = possession zone, single-frame
  touches (direction change) and hidden single sightings, noise-scaled
  relative-speed test, wider shot band with "teammate collects = not a shot",
  stitching solved per connected group (a dense matrix used 13 GB) and
  repeated until stable (a player split into 40 fragments stayed split).
- Held-out game 2:

| Degradation | Pass P / R / F1 | Shot F1 | Interception F1 | Possession agreement / coverage | Share error |
|---|---|---|---|---|---|
| none | 0.83 / 0.82 / 0.83 | 0.40 | 0.45 | 93.8% / 90% | 0.8 pts |
| moderate (0.3 m players, 0.5 m ball, 30% + 30% at feet dropped, new ID every ~5 s) | 0.75 / 0.52 / 0.62 | 0.20 | 0.21 | 91.5% / 77% | 1.2 pts |
| heavy (0.5 m / 0.8 m, 40% + 40%, ~3 s) | 0.65 / 0.29 / 0.40 | 0.08 | 0.10 | 86.5% / 48% (share withheld) | 3.4 pts |

- Reading: possession share meets the ≤5-point target at every level; pass
  detection is in the published range for independent data (F1 ~0.71);
  shots are weak under degradation (as the literature reports) and stay
  review-gated. Limits: 11-a-side professional data, positions are better than
  a 360p camera before degradation, only two matches.

## E7 · ByteTrack-style tracker and optimal stitching (2026-09-28)
- Replay of the 3-min indoor clip's recorded detections through both trackers.
- Legacy: 329 IDs, median track 2.7 s, 63 IDs with ≤2 sightings, 99.6% of
  observations kept. New (Kalman, two passes, 3.5 s lost buffer, 2-hit
  confirmation, soft kit cost, start threshold 0.4, pre-confirmation sightings
  restored): 192 IDs (-42%), median 4.8 s, 6 short IDs, 96.8% kept.
- After offline stitching: 151 player tracks (previously 209). Possession
  followed (41%) and event counts unchanged: no regression.
- Decision: new tracker is the default (`VISION_TRACKER=legacy` reverts).
- Side effect: with longer tracks the per-track kit vote covers 89% of player
  observations (legacy tracker: 80%). Accuracy of those labels is unmeasured.

## E6 · Research-grounded analytics on real results (2026-09-28)
- Data: owner's 20 s GPU diagnostic of the night 5-a-side (360p, uncalibrated);
  owner's earlier full-match export (pipeline 1.5); 3-min indoor clip with a
  guessed-dimension calibration.
- Outcome: possession followed for 54% of play on the 20 s diagnostic (control-only
  rule: 6%, with 14 px slack: 26%). Old full-match export: 4.6% (ball visible 15%
  in that engine version, the real bottleneck). Analytics run in 0.07 s (3 min)
  and 1.2 s (37 min).
- Decision: adopt team-possession sequences; withhold the share below 60%.

## E5 · Calibration through a pan (synthetic ground truth, 2026-09-28)
- Rendered 60-frame panning video with motion-estimate noise (σ 0.8 px) and two
  frames of failed motion estimation.
- Outcome: 100% of frames calibrated, max error < 0.5 m after line re-alignment.
  First version locked onto a wrong solution after the gap (27 m error); fixed
  by staged alignment (shift search → similarity → regularised homography) plus
  plausibility checks.

## E4 · Calibration on real indoor footage (2026-09-28)
- 3-min panning broadcast-style clip; venue dimensions unknown (guessed).
- Outcome: 30 frames re-aligned to lines; coverage 17% because the guessed
  template cannot match the painted lines elsewhere. Lesson: dimensions must be
  right; the UI makes them editable and the fit flags mismatches.

## E3 · Engine 2.0 faint-ball recovery (2026-09-28)
- 3-min indoor clip at 360p and 240p, CPU, stand-in ball model.
- Outcome (360p): frames with a ball 36% → 70% (153 marked inferred); possession
  coverage 4.9% → 17.1%; recovered positions move 7-13 px per frame (median).
- GPU diagnostic on the owner's night clip: ball in 91% of 100 frames; 23 s total.

## Next
- E7 tracker upgrade (Kalman, 3-4 s lost buffer, confirmation, soft team cost):
  measure IDs per minute and median track length on the 3-min clip.
- E8 WASB soccer heatmap candidates on Modal against labelled night frames.
