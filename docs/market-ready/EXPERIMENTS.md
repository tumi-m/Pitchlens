# Experiments

Hypothesis, data, configuration, outcome, decision. Newest first.
Development data only unless marked. Lines marked "held-out" (E8-E11,
E15-E19) are Metrica game 2, run only after tuning on game 1 stopped.

## E19 · Stitching gate capped at 5 m / 0.9 body heights, rounds 0.6/1.5/3/5 s (2026-10-14)
- Problem: stitch_tracks joined a chain end to any start within a
  running-speed radius (8.5 m/s × gap + 1.5 m, so 27 m at 3 s; 1.2 × gap + 0.8
  body heights without a pitch setup), for gaps up to 3 s. The tracker already
  bridges 3.5 s with camera-compensated prediction, so fragments still apart
  are often ones it had reason to refuse. A player missed for 4-5 s on one spot
  was never rejoined.
- Harness: the event harness cannot measure stitching (its fragments change ID
  with no gap, so any stitcher rejoins everything: 24495 fragments → 49 players
  on game 2). New identity mode: `evaluate_on_tracking_data.py DATA --game 2
  --identity fixed|pan [--speed-miss 2]` films Metrica's known players with a
  640x360 camera (fixed, or panning after the ball), adds bursty detector
  misses, occlusion and box noise, runs the real ByteTracker, then stitch_tracks
  in image space and with the exact calibration.
- Change: a start must lie within min(running-speed radius, 5 m) of where the
  chain ended or where its motion predicts (0.9 body heights without a pitch
  setup); rounds of 0.6, 1.5, 3 and 5 s. Tuned on game 1 (~70 settings).
- Main metric, held-out game 2: correct / wrong junction links on tracker-made
  fragments (link precision), production → capped: fixed image 480/731 (0.40)
  → 706/196 (0.78); pan image 674/1192 (0.36) → 854/510 (0.63); fixed
  calibrated 502/1108 (0.31) → 904/270 (0.77); pan calibrated 715/1611 (0.31)
  → 1204/558 (0.68). Misses rising with player speed (`--speed-miss 2`), same
  order: 513/779 → 673/225, 678/1348 → 867/511, 536/1214 → 928/348,
  709/1803 → 1215/626. Purity (no-stitching ceiling 0.949 fixed, 0.911 pan):
  0.898 → 0.929, 0.833 → 0.878, 0.857 → 0.922, 0.797 → 0.866; on tracks of
  20 s or more (the average-position dots) fixed image 0.872 → 0.909, pan
  calibrated 0.753 → 0.834.
- Rejected: an 8 s round. Its links were only 56-63% correct and it cost 1.6-2.6
  purity points on tracks of 20 s or more. The 8 s variant still beat
  production on both counts in 8 robustness runs (new seeds, game 1, heavier
  occlusion and box noise, longer and speed-related misses); the 5 s version is
  the same minus its last round.
- Purity and IDF1 alone mislead: no stitching scores the best purity (0.949),
  and an uncapped 8 s stitcher the best IDF1 (0.075 at 41% link precision, in
  the round's simulation). In the harness IDF1 reads ~0.03 for every stitcher
  (each true player matches one stitched track), so it is reported last.
- Events and possession unchanged: the event harness output is identical, and
  so are the Tryzub clip's events and possession with this change alone. The
  report's "track segments" count rises because fewer different players are
  merged: Tryzub 199 → 241, owner's match 1417 → 1638 (its 235 passes and 119
  interceptions unchanged).
- Decision: adopt, stopping at 5 s: a missed link is preferred to a wrong one.

## E18 · Possession changes hands mid-way between opposing controls (2026-10-14)
- Problem: between a control by one team and the next by the other, the whole
  unseen stretch went to the first team; with a bursty ball the winner's first
  sighted control comes well after it won the ball.
- Change: the hand-over is put at the middle of the stretch (same-team and
  long gaps unchanged). Tuned on game 1 (25/40/50/60/100% of the stretch to the
  winner; 50% best or equal-best in every condition).
- Data: Metrica, 5 fps, ball dropped in bursts (two-state Markov, mean sighting
  2 frames, 25% visible), 0.3 m / 0.5 m noise, a new ID every ~5 s, whole
  fragments given the wrong kit (10-30%); calibrated, uncalibrated top-down
  and a panning broadcast-style perspective camera.
- Outcome, held-out game 2, agreement at unchanged coverage: calibrated 83.9% →
  84.8% (10% kit errors), 66.7% → 66.9% (30%); uncalibrated top-down 79.2% →
  80.5%; perspective 79.6% → 81.4%; 16% visible 85.5% → 86.5% (calibrated),
  80.3% → 81.5% (perspective); 40% visible with 20% kit errors 75.3% → 77.3%;
  ball always seen 91.2% → 92.4%. Game 1: +0.8 to +3.4 points. Share error
  worse by at most 0.5 points, better by up to 1.7; events and possession
  counts unchanged. Owner's match: 59.6% → 59.5%.
- Repository harness, held-out game 2 at E8 defaults
  (`evaluate_on_tracking_data.py DATA --game 2`): agreement 89.4% → 92.3% at
  unchanged coverage (89.3%); share error 0.1 → 0.8 points (52.8 vs true
  52.0). Game 1: 88.9% → 91.7% (share error 0.4 → 0.9). Events identical.
- Decision: adopt.

## E17 · In-play time from stoppages measured over 2 s (2026-10-14)
- Problem: dead_ball() read speeds from positions 0.4 s apart; 0.3 m of
  position noise alone reads ~1.3 m/s. On Metrica with 0.3 m noise it found 7%
  (game 1) and 5% (game 2) of 2459 s / 2250 s of stoppages (last action to
  restart, from the event data, goal kicks included); without noise 85% / 82%.
  In-play time therefore included the stoppages: on game 2 (25% ball visible)
  "possession followed" read 53.8% while 80.4% of live play was followed, and
  the share was withheld.
- Change: stoppages() applies the same rule to speeds measured over 2 s and
  sets the in-play time only (coverage, bounds, in-play and dead-ball seconds;
  possession seconds count live moments). Sequence lengths (average possession,
  the share's interval) also count live frames only, and a possession lying
  wholly inside a stoppage is not counted. Found 83% / 82% of stoppages at
  84% / 83% precision (0.3 m noise); 80% / 80% at 0.5 m noise (old: 0%).
- Not adopted: also cutting possessions and passes at these stoppages (game 2
  coverage 83.7% → 70.9%, pass F1 0.50 → 0.47; stoppages of 12 s or more
  only: coverage -2.8 points for +0.3 agreement).
- Outcome, held-out game 2 (calibrated): followed 53.8% → 75.0% (true 80.4%),
  share now shown (error 1.4 points with E18), unseen-play bounds 26.6-72.9% →
  38.0-62.9% (true share 52.0%). Game 1: 55.7% → 77.7% (true 80.5%), bounds
  29.9-74.2% → 41.5-63.8%. Agreement, coverage and events unchanged. On 45
  overlapping 37-minute windows of both games the true share stayed inside the
  bounds in 45 of 45 (mean width 48 → 36 points); the share was shown in 26
  windows instead of 13, with one over 5 points off in each case.
- Repository harness, held-out game 2 at E8 defaults
  (`evaluate_on_tracking_data.py DATA --game 2`): dead-ball time 123 → 1964 s,
  possession followed 57.4% → 79.8%, share now shown at 52.7 with E18 (51.8
  without; true 52.0), unseen-play bounds 28.9-71.5 → 42.1-62.3. Game 1:
  followed 61.7% → 84.5%. Pass, shot, interception and goal scores identical.
- Possession counts and average length keep a stricter gate (80% followed): with
  10% kit errors at 75-78% followed they were off by 17-32% and 35-49%. The
  gate is a conservative hide, not a validated threshold: game 2 with a 40%
  bursty ball and 10% kit errors reads 85% followed, yet possessions are 17%
  low and the average possession 42% off.
- Uncalibrated footage is unchanged (no stoppage detection without the pitch).
  The owner's match (76% followed) now shows its share but no possession counts.

## E16 · Event times when the ball is mostly unseen (2026-10-14)
- Problem: with the ball seen in 25% of frames in bursts of ~2 samples, the
  first sighted control after an unseen stretch comes after the real gain
  (calibrated: median 0.52 s, 90th percentile 1.75 s late), and the passer's
  last sighted control comes before the release. Correct interceptions fell
  outside the 1 s matching window, and the review timeline showed them late.
- Change: a calibrated interception is stamped 0.5 s before the first sighted
  control, never before the release (the clamp is to the release, so an
  interception never precedes the pass it intercepts); a pass 0.2 s after the
  last control, never after the reception. Image-space gains are unchanged
  (control there starts earlier). Tackles unchanged. Tuned on game 1:
  interception shifts of 0.4/0.5/0.6 s identical; pass 0.1/0.2/0.3 s, 0.2
  chosen; image space 0/0.2/0.5 s, 0 chosen.
- Data: Metrica game 1 (tuning) and game 2 (held out); bursty Markov ball,
  0.3/0.5 m noise, IDs every ~5 s, whole-fragment kit flips 0-30%, calibrated
  and uncalibrated broadcast-perspective input; also 15% visible, a second
  seed, 20% false ball detections.
- Held-out game 2 (precision/recall, round-3 simulation), calibrated, correct
  kits: passes 0.86/0.51 → 0.88/0.52; interceptions 0.22/0.21 → 0.31/0.30.
  With 20% kit flips: interceptions 0.067/0.145 → 0.087/0.186. Uncalibrated:
  passes slightly better, interceptions unchanged. Shots, tackles and
  possession identical everywhere. (Measured with the gain clamped to the
  passer's last control; the clamp to the release scores the same at 25%
  visibility and at E8 defaults, and better with the ball always seen:
  interceptions matched 69 → 71 of 145.)
- Repository harness, held-out game 2 at E8 defaults
  (`evaluate_on_tracking_data.py DATA --game 2`): interception F1 0.21 → 0.27
  (matched 33 → 42 of 145), pass F1 0.62 → 0.64 (matched 504 → 520); shots,
  goals and possession unchanged. Game 1: interception F1 0.34 → 0.36, pass
  F1 0.62 → 0.65.
- The harness gained owner-like degradations (`--visible`/`--run` bursty
  ball, `--team-flip` whole-fragment kit errors, `--camera broadcast` panning
  uncalibrated 640x360 view, `--ball-fp` boots taken for the ball, `--seed`)
  and metrics that compare like with like: `turnover` (interceptions and
  tackles against every recovery), `passAttempt` (every pass against completed
  passes plus pass-like ball losses) and `passComplete`; `pass` keeps the E8
  scoring. Game 2 at E8 defaults: passAttempt F1 0.61 → 0.63, turnover F1
  0.27 → 0.33. Owner-like (`--game 2 --visible 0.4 --run 2 --team-flip 0.2
  --camera broadcast`): passAttempt F1 0.63, interception precision 0.09
  (recall 0.26), turnover precision 0.16.
- Interception false positives under kit-label noise (game 2 calibrated,
  before): 78/87/93/96% at 0/10/20/30% flips; 138 → 353 detections for 145
  real interceptions.
- Also measured, not adopted:
  - Dropping a ≤2-sample opponent control between two controls of one team
    within 3 s: precision up, but held-out recall -1 to -4.5 points (it does
    halve interceptions when 20% of ball detections are false).
  - A two-state HMM over spell teams: interception recall -45%.
  - Flipping kit labels from ball exchanges: 102 of 174 flips wrong.
  - An image-space "contested" state: pass recall -30%.
  - Minimum control duration, ball travel and minimum gap: no separation
    under kit noise; single-sample gains were the most precise.
- Owner's match: the same-team share of exchanges is ~50% at every gap and in
  every slice (kit agreement, ball confidence, detected vs recovered, spell
  length), so its interception count is not something the event rules can
  tighten. Its counts are unchanged (235 passes, 119 interceptions); passes
  move 0.2 s.
- Decision: adopt.

## E15 · Possession across long unseen stretches (2026-10-13)
- Problem: the owner's 37-minute 360p match had the ball in 25% of frames
  (median sighting 2 frames), so possession was followed for only 53% of
  play and the report hid it (60% needed).
- Hypothesis: when the same team controls the ball on both sides of an
  unseen stretch, it usually kept possession.
- Data: Metrica games 1 (dev) and 2 (held out), ball dropped in bursts (a
  two-state Markov model, mean sighting 2 frames, 16% of frames visible:
  harsher than the owner's footage), with and without pitch calibration.
- Outcome, held-out game 2 (8 s current → 30 s same-team bridge):
  calibrated agreement 90.6% → 89.8%, coverage 73% → 82%, share error
  2.7 → 1.3 points; uncalibrated agreement 84.9% → 84.7%, coverage
  85% → 90%. Game 1 the same within a point. 60 s added little more.
- On the owner's match: possession followed 53% → 76%; now shown as
  60/40 with unseen-play bounds 45-69%.
- Decision: adopt 30 s (maxSameTeamGap). Opponent-to-opponent gaps keep the
  8 s limit; no bridge across stoppages or cuts.

## E14 · Full-frame-rate ball trajectories at 360p (2026-10-04)
- Hypothesis: at the full 30 fps a ball moves a few pixels per frame on a
  smooth path, so difference-image candidates linked by a constant-velocity
  model would find it without a detector (trajectory-based detection).
- Data: Tryzub indoor clip, 640x360, frames 1700-1900.
- Cost: 1.7 s of CPU per 200 frames (about 8 minutes for a 40-minute match).
- Outcome: 31 smooth trajectories in 200 frames; the inspected ones were legs,
  boots and a shirt number, not the ball. At 360p the ball is 3-5 px and
  indistinguishable from a boot by motion alone (also by eye in single frames).
- Decision: not adopted. The missing ingredient is an appearance model trained
  on labelled frames from the venue, so the in-app ball labelling and training
  loop was built instead (docs/BALL-TRAINING.md).

## E13 · Automatic pitch calibration by camera-pose search (2026-10-04)
- Hypothesis: search a dictionary of ~6,500 camera poses around the pitch,
  score projected template lines against the painted-line mask, refine with
  the existing line alignment.
- Outcome: 0 of 8 synthetic views (known template, clean lines) within 0.5 m;
  on a real frame with unknown court dimensions the fit chose a far too small
  pitch. Sparse pose dictionaries do not reach the narrow basin of the
  chamfer fit, and amateur courts do not have standard dimensions.
- Decision: not adopted. Clicking landmarks once per venue (reused for every
  match from a fixed camera) stays the method. A learned keypoint detector or
  line-correspondence search with metric rectification is the next attempt.

## E12 · Camera field of view for a side-on halfway camera (2026-09-28)
- Synthetic 1280×720 camera 30 m from a futsal court, level with halfway,
  facing straight across; 13 visible landmarks with 0.7-2 px click noise,
  100 seeds each.
- Old estimate (median of two focal candidates): 14-17 of 100 fits more than
  10° off. Least-squares solve of both constraints: 0 of 100.
- Decision: adopt (display only; the homography was never affected).

## E11 · Adversarial review of the new code (2026-09-28)
- Two review rounds by independent agents, each finding reproduced and then
  challenged by a skeptic before it counted. Final round: 49 claims,
  13 confirmed (most already fixed by the first round's fixes).
- All confirmed defects fixed, each with a regression test that fails on the
  old code: stale review decisions confirming goals, defender blocks counted
  as saves, a warm-up kick-off flagged as a goal, zeros shown for unmeasured
  counts, stitching mixing metres with pixels, a re-aimed fixed camera kept
  on its old calibration, masks from the wrong frame on variable-frame-rate
  video, clicks between analysed frames accepted, 500s on malformed input.
- Backend tests 150 → 163; browser tests 10 → 11 on the vision pages.
- Held-out Metrica game 2 (moderate degradation) re-run after the fixes:
  passes, shots and possession unchanged (pass F1 0.62, shot F1 0.45,
  agreement 89.6%); goal-candidate precision 2/3 → 2/2 (the opening
  kick-off no longer raises a false candidate).

## E10 · Possession through longer unseen gaps (2026-09-28)
- Possession gap (ball unseen or loose between two controls) 5 s → 8 s, tuned
  on game 1 (12 s raised coverage further but cost more agreement).
- Held-out game 2: moderate degradation coverage 77% → 85%, agreement
  91.5% → 89.6%, share error 1.2 → 0.1 points; heavy degradation coverage
  48% → 68% (share now shown), agreement 86.5% → 87.6%, share error
  3.4 → 1.6 points.
- Decision: adopt 8 s.

## E9 · Shots inferred from the ball's flight (2026-09-28)
- Hypothesis: most missed shots have the release hidden at the feet; a fast
  ball heading for goal from range, not collected by the attacking side, is a
  shot by the team attacking that goal.
- Tuned on game 1 (speed bar 8 m/s kept: 10-12 m/s cost more recall than they
  saved in false shots). Held-out game 2, shot recall / F1:
  none 33% → 92% / 0.40 → 0.54; moderate 13% → 67% / 0.20 → 0.45;
  heavy 4% → 46% / 0.08 → 0.37. Precision ~0.31-0.39: about two proposals per
  real shot, which the reviewer confirms or rejects. Goal candidates on clean
  tracking: 5 of 5 real goals flagged, no false ones.
- Decision: adopt, marked `inferred`, confidence 0.3, always reviewed.

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
