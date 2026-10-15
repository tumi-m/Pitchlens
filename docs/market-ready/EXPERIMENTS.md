# Experiments

Hypothesis, data, configuration, outcome, decision. Newest first.
Development data only unless marked. Lines marked "held-out" (E8-E11,
E15-E20, E22-E23) are Metrica game 2, run only after tuning on game 1 stopped.

## E27 · GPU spend recorded per run and an optional daily budget (2026-10-15)
- Problem: a GPU run left no cost trace. `status.json` had no duration, card or
  cost, and the result's `performance.totalSeconds` leaves out container start,
  imports, the weight copy, the download and compression. Failed, timed-out,
  cancelled and CPU-takeover runs, and training, were invisible although Modal
  billed them (three reproductions against the round-8 worker). The only
  spend controls were `VISION_MAX_JOBS_PER_DAY` starts, one analysis at a time
  and Modal time limits of 3-24 h per run.
- Worst case under those controls (`worst_case.py`, COST.md prices with the
  assumed 1.5 cores and 4 GiB; estimates): one analysis at a time all day
  costs $21.65/day on an L4, $49.29 on an L40S and $97.25 on an H100, plus
  training (2 site runs of up to 2 h on an L4: $3.61; operator runs uncapped).
  Per run: a 20-second section could bill up to $2.71 on an L4 or $12.16 on an
  H100 (the 3 h floor); 90 minutes at 1080p 10 fps up to $6.35 / $28.55.
- Change: `modal_app.analyse` reports its phases on its last item (never in
  result.json); `gpu.run_on_modal` times the run from the call to the app's
  exit and hands a record (seconds, queueing, phases, estimated dollars, Modal
  app id) to the worker however the run ends. The record goes to the job's
  `status.json` (`gpuRuns`, stripped from the owner's view), to
  `gpu-spend.json` (opened before the call, heartbeat every 30 s, closed with
  the record, 8 days kept, a run open at a restart closed at its last
  heartbeat plus 300 s) and to `/ops` → `gpuSpend`. Training is timed and
  priced with `VISION_MODAL_TRAIN_GPU`. Apps are tagged `pitchlens-job` and
  `pitchlens-kind` for Modal's billing report. `VISION_DAILY_GPU_BUDGET_USD`
  refuses new GPU starts (a GPU-only analysis keeps its upload and its start;
  sections fall back to the CPU; Retry, full re-runs and training answer 429
  `gpu-budget`); uploads are never refused for it. Recording is best-effort:
  a full volume or an unreadable ledger never stops a run (the budget then
  fails open, with a warning). Estimate formula and reconciliation: COST.md.
- Measured, locally on the CPU: `modal_app.analyse` run in-process through
  `run_on_modal` on the 3-minute Tryzub clip (20-second section, 6 fps, no
  ball model; `e2e_cost.py`): worker wall 29.5 s, imports 1.34 s, download
  0.02 s for 7,378,217 B, engine 28.14 s, 72.6 vCPU-s, peak memory 1.23 GB.
  A re-run of the shipped code on a busier machine: 37.2 s wall, engine
  34.8 s, 87.8 vCPU-s, and no cost field in result.json. The queueing split assumes `/proc/uptime` inside Modal's sandbox is
  the container's uptime; locally it is the host's, so queueing reads 0.
- Not yet known: every dollar figure here is an estimate from list prices,
  not reconciled with a Modal invoice, and no real L4 full match has been
  recorded yet. Guards: `tests/test_gpu_cost.py`, the `/ops` key test.
- Decision: adopt. Keep Modal's workspace spend limit as the backstop; the
  daily budget can overshoot by at most one analysis plus one training run.

## E26 · Goal areas and penalty arcs anchor line re-alignment on full-size pitches (2026-10-15)
- Problem: `pitch.line_segments` drew the touchlines, goal lines, halfway
  line, centre circle and penalty box, not the goal areas (5.5 x 18.32 m) and
  penalty arcs painted on every full-size pitch. The line mask barely sees a
  broadcast goal line (7-11% of its samples hit), so `align_to_lines` started
  exactly at a correct manual calibration rotated the goal line onto the bright
  board edge behind it and scored 0.52-0.62, above calibrate.build's 0.45 for
  the once-a-second re-alignment on a moving camera.
- Data: my manual calibrations of 4 broadcast frames from 2 clips (messi_00000,
  iniesta_00000, _00072, _00144), at full resolution and at 640x360.
  `align_hold.py` starts at the manual view; `align_test.py` applies 6 small
  simulated camera moves per frame and resolution (48 trials).
- Change: a law-standard box (at least 13 m deep and 35 m wide, never
  seven-a-side's 9 x 18) also draws both goal areas and, when the spot plus
  9.15 m reaches past the box, the arc's part outside it. Derived from the box,
  so UI templates, stored calibrations and venues get them; every other
  template draws exactly what it drew. The report overlay (`pitchLines`)
  mirrors the rule. No new landmark names (31 would pass the 30-click limit).
- Hold, counting a move as accepted when its score reaches 0.45: before, 4 of
  8 cases moved more than 0.3 m (messi full 0.53 m; iniesta_00000 360p 0.51 m,
  max 6.89; iniesta_00072 1.84 m, max 8.06, and at 360p 2.13 m, max 9.74).
  After, 7 of 8 are declined or move at most 0.26 m; iniesta_00072 at 360p
  still moves 1.68 m (max 8.15) at score 0.46. It is bistable: over 12
  sub-sample placements of the arc samples it was accepted at 1.60-1.73 m in
  6 (score 0.46-0.47) and declined in the rest. iniesta_00000 at 360p moves
  0.53 m but at 0.39, which build declines.
- Perturbations: median error 0.73 → 0.83 m before, 0.73 → 0.40 m after (p90
  2.36 → 1.65 m; refinements worse than their start 22 → 12). Accepted
  refinements 30 → 26, of which more than 1 m off 16 → 9; good corrections
  (better than the start and within 1 m) 11 → 14. Wrong locks that remain:
  at 360p iniesta_00000 4/6 accepted at 1.63-1.69 m and iniesta_00072 4/6 at
  1.49-1.66 m; at full resolution one messi_00000 trial at 8.51 m (8.65 m
  before).
- Guard in build's non-strict branch, measured and not adopted. A tighter
  `max_shift` for search 0 (14 px x height/360 instead of 60 px) cut the
  accepted wrong locks 9 → 2 with all 14 good corrections kept, but the
  simulated moves (4 px sigma, 2% zoom, 0.01 rad) cap the start error by
  construction: the cost of refusing a larger genuine correction on a real
  moving camera (the view then drifts until the next shot start or motion
  gap) is unmeasured. Requiring the refined score to beat the start by up to
  0.15 removed no wrong lock (they gain 0.2-0.3). calibrate.py is unchanged.
- A synthetic regression (rendered penalty area, faint goal line, board edge
  1.5-3 m behind) was tried and not committed: the old lines drifted more than
  1 m in few views and the new ones did not reliably hold within 0.3 m.
- Saved 11-a-side venues' `selfScore` was computed with the old lines, so a
  venue check may now refuse one and ask for a new setup (fails safe).
- Guards: `tests/test_pitch.py` (goal areas and arcs on law-standard boxes,
  other templates unchanged), `frontend/tests/pitch-lines.test.cjs`.
- Decision: adopt. The evidence is 4 broadcast frames from 2 clips; an
  11-a-side moving-camera match with ground truth would settle the 360p cases.

## E25 · Learned pitch keypoints as a suggested setup (2026-10-15)
- Roboflow's 32-point pitch model (`football-pitch-detection.pt`, Drive id
  1Ma5Kt86tgpdjCTKfum79YMgNnSjcoOyf from roboflow/sports
  examples/soccer/setup.sh) could not be fetched because Google Drive is
  blocked, so no SHA-256 is pinned. Stand-in: NBJW HRNet-W48 keypoints
  (GPL-2.0, evaluation only, SV_kp SHA-256 7ea78fa7…5113).
- Ground truth: my own manual calibrations of 4 UEFA broadcast frames
  (residual 0.02-0.27 m; one, iniesta_00072, has a 7.16 px residual and
  quality 'check').
- RANSAC from confident keypoints plus gates: suggestions on 27/27 broadcast
  frames at full resolution and 19/27 at 640x360 JPEG q60. Accuracy was
  measured only on the 4 manual frames (6 with their 360p copies): median
  0.12-0.34 m, p90 ≤0.71 m, max ≤1.61 m. The other 23 suggestions are
  unverified.
- Indoor Tryzub court, 98 frames: 0 suggestions at every threshold (never 6
  agreeing keypoints).
- Worst case, a small court labelled with standard ids (synthetic views):
  naive gates accepted 34/40 seven-a-side, 29/40 futsal and 9/40 five-a-side
  views, all 1.7-3.4x too large. With per-line paint evidence (at most 1
  predicted line without paint, at least 2 lines of one penalty-area structure
  painted) and the players' median height within 1.2-2.5 m: 0/600. Fitting
  the lens term on 6-15 keypoints gave k1 from -0.13 to +0.12 and 3.7 m
  players, so suggestions use no distortion.
- CPU: 4.6 s per frame (HRNet-W48 at 960x540, 4 threads).
- Decision: not adopted (licence, weights unavailable, no gain on small-sided
  courts). The prototype is not committed. Before adopting: (1) download the
  Roboflow weights where Drive works and pin the SHA-256 in
  setup_football.MODELS; (2) check the real model on frames with known
  calibration, including what indices 10/11/18/19 mean; (3) offer it only for
  law-standard boxes (areaDepth about 16.5 and areaWidth about 40.32, or
  length ≥ 90 m), never seven-a-side; (4) make backend validate_request and
  the frontend landmarks() mirror accept goal-area names, so a suggestion
  carries at least 4 clickable points (today 2-6 per frame); (5) get legal
  review if any GPL code or ideas are involved. Only ever offer it as a
  suggestion the owner confirms.

## E24 · Owner team fix by tapped crops (2026-10-15)
- Problem: when kit clustering picks the wrong colour group, the owner could
  not fix it. The kit panel only took names, and review's "Other team" flips
  one event at a time and cannot recover events that were never detected.
  Clustering-failure case, Tryzub on engine 2.7 (k=4, analytics version 12):
  Kit B #262124 was spectators, so Kit B passed 0 times against 6, possession
  read 78.7/21.3 and Kit B "ran" 250.7 m (the spectators). E22 fixes Tryzub
  itself; this is the fallback for clips where clustering still fails (which
  of the 4 clusters becomes Kit B still depends on cluster size, E22).
- Change: GET /jobs/{id}/teams offers up to 4 crops per group from the 150
  ball-label frames (no new image endpoint): per kit, one per track, the tracks
  whose median colour sits nearest the kit's median, boxes at least 9% of the
  frame height; unsorted players spread over colour (farthest point), starting
  from the densest unsorted colour, so a missing kit shows first. The owner
  taps a crop and says which team. POST /teams takes each tapped track's
  median kit colour (at least 3 detections) as a reference, refuses two
  references closer than 25 in plain LAB (E22's gate), and relabels every
  stored detection with the engine's own assign_team and vote_teams. Index 0
  stays the engine's Kit A (align_to_reference); a crosswise answer returns
  `swapped` and the site swaps the names. Metrics are recomputed and
  result.json rewritten, which invalidates the cached analysis. The engine's
  labels are kept per detection on the first fix, for undo.
- Design, on the 2.7 result (analytics version 12, the round's research): one
  clear crop per kit (track median within 15 of its kit's median) put 97% of
  green detections in the right team (97.4% at worst over 10 picks), passes
  [6-7, 4], possession 53.2/46.8, Kit B distance 959.7 m. A tap on any red or
  green track measured much worse (red 63.5% and spectators 31% correctly
  unlabelled at worst), so only crops nearest a group median are offered.
  Not adopted: refining both references on the whole match (green 90.7%);
  6 colour clusters for the owner to mark (the referee became Kit B 45% of
  the time); a referee crop (no measured benefit).
- Through the shipped endpoints (pipeline 2.8 rules, analytics version 13,
  no pitch setup, label frames extracted from the clip). 2.7 result: crops
  offered Kit A 4 red players, Kit B 4 spectators, unsorted green, dark, red,
  yellow referee (checked on a contact sheet). Tapping the first red player
  and the green one: green detections right 6.8% → 95.8%, red 91.7% → 98.5%,
  spectators unlabelled 4.5% → 95.6%, referee 100% → 99.8%; passes [4, 0] →
  [4, 5], interceptions [0, 1] → [1, 1], possession 72.2/27.8 → 57.9/42.1,
  Kit B distance 250.7 → 960.1 m. 0.5 s per fix on the 3-minute clip; about
  5.6 s on the owner's 37-minute match, analysis included. 2.8 result
  (`tz-patched`, clustering right): neutral; green 96.0% → 96.2%, red 99.5% →
  99.2%, passes [4, 6] → [4, 5], possession 58.8/41.2 → 57.9/42.1, Kit B
  distance 975.2 → 950.2 m.
- Faithfulness: relabelling from the engine's own stored kit colours with the
  same calls reproduces 99.77% of the engine's labels on Tryzub 2.8
  (10116/10139) and 99.89% on the owner's match (101772/101887); the rest are
  detections whose role was corrected after the engine labelled them. Undo
  restores every label (10221/10221).
- Guards: `tests/test_team_fix.py`; the proxy and report tests.
- Decision: adopt as the owner's fallback. Persisting the owner's references
  across reruns is out of scope ("Analyse again" re-clusters).

## E23 · A kit-less player at the ball makes the frame unknown (2026-10-15)
- Problem: only labelled players could control the ball. When the dribbler's
  kit label dropped out for a frame or two, the nearest labelled player (often
  an opponent marking him) took the ball, and gave it back a frame later.
  Repro (`_kitless_dribble`): tackles by team 1 at 1.2 s and team 0 at 1.6 s,
  possession 2.8/0.4 s; interceptions instead without a pitch setup.
- Change: a player or keeper without a kit label still competes for the ball;
  when he is nearest, the frame's state is unknown, which neither ends nor
  confirms a control (like an unseen ball). Image-space ambiguity stays
  "loose". ANALYTICS_VERSION 13.
- Outcome: the repro has no events and possession 3.2/0.0 s. Real results:
  Tryzub 16 of 233 control frames become unknown (15 of 211 calibrated), the
  owner's match 3 of 1252. Tryzub passes 45 → 39, interceptions 23 → 20
  (calibrated 41 → 35, 20 → 17).
- Held-out game 2 (verifier, 18 paired configurations): pass F1 +0.018 (better
  in 18/18), pass recall +1.4 points (18/18), passComplete F1 +0.015, turnover
  F1 +0.011 (14/18), interception F1 +0.007 (16/18), possession agreement +0.8
  points (16/18).
- Rejected: a separate "unlabelled" state (pass recall -1.3 points on the
  broadcast-style camera).
- Guards: `test_a_kitless_frame_of_the_player_on_the_ball_does_not_hand_it_to_his_marker`.
- Decision: adopt.

## E22 · Kit colour compared with lightness down-weighted (2026-10-15)
- Problem: in plain LAB, lightness decided the team. Owner's match: the light
  kit reads L 180 in the middle of the picture and ~100 at the edges with a/b
  nearly unchanged, so 43% of edge light-kit detections went to Kit A and the
  red centre was pulled to a* ~161. Tryzub: k=4 split mint by lightness and
  made the spectators Kit B (#262124). And vote_teams gave a decided track's
  kit to observations whose own colour was neither kit (the referee).
- Change: KIT_WEIGHTS [0.35, 1, 1] in KMeans and in assignment (nearest kit
  under 40, margin over 8; rejected = too far from both or the wrong hue);
  the vote keeps rejected observations unknown. The separation gate stays in
  plain LAB (≥25): the weighted gate refused white/light grey and green/mint,
  which the old code labels, and a refusal loses the whole match's team
  stats. Pipeline local-vision-2.8.
- Owner's match, re-tracked: tracks 2125 → 1929, interceptions 120 → 111,
  completed passes 116 → 119, possession 59.7/40.3 → 53.0/47.0, pass share
  55.9/44.1 → 49.6/50.4. Jersey-chroma check: same-colour interceptions
  16 → 3, cross-colour passes 8 → 1. Edge light-kit detections labelled Kit A
  52.7%/51.2% → 0.2%/8.5% (verifier 0%); frames with more than 7 players on
  one team 382 → 103. The vote rule changed no owner event (511 more unknown
  detections).
- Tryzub (replay with real calibration features): Kit B #262124 → #68966f,
  possession 82.9/17.1 → 55.3/44.7, passes by team [6, 0] → [5, 6]; 11 passes
  (7 complete) and 4 interceptions, all kit-consistent on contact sheets;
  yellow-referee detections labelled Kit B 130 → 0.
- Held-out game 2 with synthetic shading (lightness 1.0 → 0.5 across the
  pitch): wrong-team detections 4.1% → 0.0%, possession agreement 0.866 →
  0.889, share error 4.0 → 1.1 points, turnover F1 0.232 → 0.264,
  interception F1 0.192 → 0.203; white/navy and white/black within 0.15%;
  white/grey 7.6% → 7.2%.
- Not adopted: chroma only (cannot separate kits that differ only in
  lightness); a full-covariance GMM (38% of edge detections still wrong);
  vote weighting, smoothing or "unknown" for undecided tracks (after the
  change only 0.41% of labelled detections disagree with their track).
- Open risk: which of the 4 clusters becomes the second kit still depends on
  cluster size (Tryzub: mint 87 against spectators 76), and an older 2.1 run
  of the same clip picked green, so a crowded sideline could still win.
  Possible follow-up: prefer clusters spread over the pitch. The owner's team
  fix (E24) is the fallback.
- Guards: `tests/test_kit_teams.py`.
- Decision: adopt.

## E21 · Inference overlapped with tracking; fewer per-frame CPU steps (2026-10-14)
- Problem: run_video decoded a batch, ran the player and ball models, then did
  all of the batch's per-frame CPU work before decoding the next, so GPU and
  CPU time added up. The owner's 37:34 match on a Modal L4 (`performance` in
  the owner's export `pitchlens-vision (8).json`): 989.2 s = model stages
  659.4 s (players 354.3, ball 305.1) + about 305 s of per-frame CPU work while
  the GPU waits + about 25 s of setup and ball recovery. Three smaller costs:
  numpy's bundled OpenBLAS (4 threads) woke its pool for each of the Kalman
  filter's 4x4 inversions (about 15 per frame) and the threads busy-waited
  (112.4 s of process CPU for 32.0 s of wall time; 32.3 s with one thread, same
  output); pitch_colour converted the whole frame to LAB to keep one surface
  pixel in 17 (4.9 ms per 360p frame, 33.3 ms per 1080p frame, single-threaded
  as the engine runs OpenCV); and per-frame work was repeated (two grey
  conversions, a second identical dilation, np.percentile partitioning 230k
  pixels for one value, the tracker rebuilding boxes and centres for each of
  126k track x detection pairs).
- Change: the model calls for batch n run on a helper thread while batch n-1
  is tracked (one batch in flight; adaptive ball search stays on the tracking
  thread). `VISION_PIPELINE` is on by default only with CUDA: on the CPU it
  measured neutral, and on Apple MPS (both models at once in adaptive mode) it
  is untested; `1` forces it on, `0` off. run_video limits BLAS to one thread,
  and both images set `OPENBLAS_NUM_THREADS=1`, which also covers pitch setup.
  pitch_colour converts only the sampled pixels; one grey frame per frame and
  the dilated surface are shared; a 256-bin histogram replaces np.percentile
  (it mirrors numpy 1.26's `_lerp`; numpy is pinned below 2.0); the tracker
  builds each track's and each detection's arrays once.
- Measured on this 4-vCPU host with the owner's GPU timings emulated: Tryzub,
  900 frames, 88.9 → 58.7 s (1 core 85.1 → 57.9 s; 2 cores 83.6 → 58.2 s). A
  replay that holds the GIL as the real model call does, 200 real frames:
  20.2 → 15.8 s. BLAS: process CPU 112.4 → 32.3 s for the same wall time.
  pitch_colour 4.9 → 1.05 ms (360p) and 33.3 → 7.2 ms (1080p). result.json
  identical in every mode (pipeline on and off, exhaustive and adaptive ball
  search; `tests/test_engine_speed.py`), and on 60 s of the Tryzub match with
  real CPU inference: the pre-change engine, `VISION_PIPELINE=0` and `=1`
  agree key by key apart from `performance`.
- Projected, not measured: the owner's match in about 685-700 s instead of
  989 s on L4, so engine GPU time falls from about $0.234 to about $0.16 per
  40-minute match at $0.000222/s. Confirm with one real Modal L4 run
  (compare `performance.totalSeconds` with the summed inference seconds)
  before quoting it elsewhere; if the real gain is clearly smaller, consider
  reserving `cpu=2` on the Modal function. Costs: docs/COST.md.
- Decision: adopt, on by default with CUDA.
  `test_histogram_percentile_matches_numpy_bit_for_bit` guards the percentile.

## E20 · Control needs two sightings at every sampling rate (2026-10-14)
- Problem: control_spells asked for max(2, ceil(0.3 s x rate)) sightings: 2
  at 5-6 fps, 3 at 8.33-10 fps and 4 at 12.5 fps. A bursty low-resolution
  ball is seen at the feet once or twice, so the dearer Detailed preset
  (8.33 fps on 25 fps video, about 10 on 30 fps; about two-thirds more GPU
  time than Balanced) discarded short controls and found fewer passes. In
  owner-like Metrica runs, paired against 5 fps, 8.33 fps scored pass F1 0.03
  to 0.06 lower and coverage 1-3 points lower.
- Setup: owner-like Metrica: a 40% bursty ball on a 25 Hz chain, subsampled to
  each rate; 20% of fragments given the wrong kit; the true rate given to the
  analytics; possession scored on all sequences, not the report's list capped
  at 400; 3 seeds; calibrated and uncalibrated, game 1 and held-out game 2.
- Change: `minControlSamples` = 2 at every rate (`minControlSeconds` removed).
- Outcome, held-out game 2 calibrated: pass F1 at 8.33 fps 0.709 ± 0.011
  against 0.644 at 5 fps (paired +0.065 ± 0.026); +0.100 at 12.5 fps.
  Uncalibrated +0.022. Game 1: +0.060 calibrated, +0.025 uncalibrated.
  Interceptions +0.01 to +0.07 F1, coverage +2 to +4 points, agreement roughly
  unchanged. The gain is recall (0.45 → 0.61) at unchanged precision (0.74
  calibrated; 0.654 → 0.643 uncalibrated).
- Independent repro (verifier's own runs, 2 seeds): game 2 calibrated,
  8.33 fps with two sightings against 5 fps, pass F1 +0.084 on both seeds
  (0.586 → 0.670, 0.564 → 0.648); recall 0.45 → 0.61 and precision 0.74 against
  8.33 fps with three.
- No-op at or below 6.67 fps, where the minimum was already 2: every Quick and
  Balanced rate (25 → 5, 29.97 → 5.994, 30 → 6, 24 → 6, 50 → 5.56 and
  60 → 6 fps, and all 3 fps variants). Analyses are byte-identical on harness
  game 2 at 5 fps and on the real Tryzub result at 5.994 fps (calibrated and
  not), so E8-E19 still reproduce.
- Caveat: simulated visibility only; there is no real event truth at 10 fps.
- Decision: adopt; Detailed is kept.

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
