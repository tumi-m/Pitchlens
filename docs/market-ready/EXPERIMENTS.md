# Experiments

Hypothesis, data, configuration, outcome, decision. Newest first.
Development data only unless marked; none of these is a held-out result.

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
