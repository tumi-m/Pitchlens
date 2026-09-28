# Pitchlens: implementation design for Sofascore-style stats from one amateur camera

Date: 2026-09-28. Baseline: repository HEAD `289a867`.

## 0. Read this first

**The research input was incomplete.**
- Ball, calibration and events arrived in full, with their fact-checks.
- Tracking was cut off partway through a finding (inside the Deep-EIoU entry). Its fact-check, detailed team-classification findings and parameter list are missing.
- The last two of the six topics never arrived.

As a result:
- Team classification (sections 2.7 and 3.9) rests only on the tracking summary.
- Section 7 (competitors) has no competitor research behind it. It lists what the other topics mention in passing and names the research still needed.

**Many numbers could not be re-checked.** The fact-checkers could reach GitHub and PyPI but not arXiv, Springer, PLOS, Nature, CVF or Hugging Face, so figures that appear only in papers are unverified. Where a fact-check corrected a finding, this document uses the corrected value.

**The code is further along than the research brief describes.** HEAD already contains:
- **`480a087`: tracker.** A ByteTrack-style tracker and optimal tracklet stitching. On the 3-minute clip, IDs fell from 329 to 192 and the median track grew from 2.7 s to 4.8 s.
- **`342fc74`: possession and events.**
  - Possession is now time-based sequences with a bootstrap interval and a 0.6 coverage gate.
  - The control radius scales with noise.
  - Also added: keeper-zone saves, kick-off detection, pass-accuracy abstention and an 8 m/s shot threshold.
- **`94d1b67`: calibration fit.** A division-model plus homography fit measured in image pixels, with line clicks and leave-one-out checks.
- **`1e86bc4` and `289a867`: review and report.** Final-score entry in review, and field tilt withheld until enough data exists.
- **Uncommitted: evaluation harness.** A Metrica degradation harness at `/home/user/Pitchlens/backend/scripts/evaluate_on_tracking_data.py`.

This design builds on HEAD and says where HEAD disagrees with the research.

**Evidence tags used below:**
- **[code]**: read in a primary repository or notebook output by a fact-checker.
- **[paper?]**: a paper figure seen only in search extracts; not re-verified.
- **[sim]**: the calibration researcher's own Monte Carlo simulation (flat pitch, correct click labels, known dimensions).
- **[derived]**: a researcher's own arithmetic or recommendation. It is a starting value to tune, not a published value.
- **[HEAD]**: current Pitchlens code.

---

## 1. Verdict

**Partly achievable.** From one amateur camera, Pitchlens can produce automatically, with honest intervals, the positional and possession half of a Sofascore page. It can produce pass counts marked as "detected". It cannot produce trustworthy shots, shots on target, saves or goals without a fast human check.

No finding reports any system measured on night-time, 360p, small-sided footage from one low camera. Every accuracy figure below comes from better data: professional 11-a-side, 25 Hz, persistent player identities, a visible ball. Treat those figures as ceilings, not predictions.

### 1.1 Which stats

| Stat | Verdict | Expected quality and evidence |
|---|---|---|
| Team heatmaps, average positions | **Automatic** when the calibration checks pass | See note 1 below. Label as "about 1 m quality". |
| Possession % (share of time) | **Automatic**, shown with an interval; withheld if coverage < 0.6 | See note 2 below. Possession must bridge the ball's flight between players, as HEAD now does. |
| Territory, field tilt, momentum | **Automatic** (built from possession probabilities and positions) | Inherits the possession and calibration error. Needs the correct attacking direction per half. HEAD withholds field tilt until it has seen 20 s of attacking-third control. |
| Passes (count) | **Automatic, labelled "detected"**; will undercount | See note 3 below. |
| Pass accuracy | Shown only with **≥20 attempts per team** at confidence ≥0.5 | The missed passes are not random: short controls at the feet are lost most often. Until measured, the bias in accuracy has unknown direction. |
| Shots, shots on target | **Review-gated**: detected automatically, published only after review | See note 4 below. |
| Saves | **Review-gated** | Needs the keeper identified and a keeper-area gain within 1.5 s. The evidence is as weak as for shots. |
| Goals | **Never automatic** | A candidate comes from a grounded ball over the line or from the kick-off restart pattern. It is confirmed by a reviewer or matched to the user-entered final score (HEAD has score entry). |
| Timeline | Follows events | Shows only reviewed or high-confidence events. |
| Distance covered, speed | **Withheld** unless calibration and identity checks pass | 2 px of foot jitter on the far side is 1-3 m [derived]. Fragmented identities break per-player totals. |
| Player-level stats | **Withheld** | After `480a087` there are still about 64 IDs per minute (192 in 180 s) for 10-14 real players. |
| xG | **Experimental label only** | No validated small-sided xG model was found. 11-a-side models (Anzer & Bauer 2021, with distance the strongest feature [paper?]) do not transfer to 3.66 x 1.22 m goals. |
| PPDA | **Omit** | Needs counts of defensive actions, which Pitchlens cannot detect reliably. |

**Notes on the evidence**

1. **Heatmaps and positions.**
   - Simulated foot-position error at 640x360, with 1 px click noise and 2 px foot jitter: median 0.25-0.52 m, 90th percentile 0.7-2.3 m [sim].
   - The far side is coarse [sim]:
     - side camera 4.5 m high: 0.69 m per vertical pixel at the far touchline;
     - end camera: 1.32 m per pixel at the far goal.
   - Real footage adds wrong clicks, pitch-size error and hidden feet. None of this is measured.
2. **Possession %.**
   - Best anchor: PathCRF reaches 92.4% per-frame team possession accuracy from player trajectories alone at 5 fps. That is one held-out DFL match, with complete player identities [code, tutorial output] https://github.com/hyunsungkim-ds/pathcrf.
   - Ball Radar's notebook reports 0.745 [code] https://github.com/hyunsungkim-ds/ballradar.
   - Link & Hoernig: a player actually has the ball for only about 32% of team possession time (17:49 of 56:04 minutes per match) [paper?] https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0179953.
3. **Passes.**
   - Pass F1 is 0.71 when positions and events come from independent sources (Bischofberger 2024) [paper?] https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0298107.
   - F1 is ≥0.90 on broadcast-derived tracking of a single World Cup match (Mills 2026) [paper?] https://link.springer.com/article/10.1007/s12283-026-00549-4.
   - PathCRF kick F1 is 0.770 at a 1 s tolerance with the same player [code].
   - At 5 fps with 40% of ball samples missing, a 0.4 s control is fully seen only 36% of the time [derived].
4. **Shots.**
   - Shot F1 is 0.65 on independent data [paper?].
   - Mills 2026 finds shots and saves weak even on broadcast tracking [paper?].
   - At 5 fps a shot spans 1-3 ball samples.
   - A ground-plane homography cannot see ball height. A ball 1.5 m up and 20 m from a camera 5 m high projects 8.6 m too far [derived; checked].
   - So "on target" can only be decided from the outcome (goal or save), never from the ball's flight.

### 1.2 Under which footage conditions

| Footage | What is achievable | Why |
|---|---|---|
| Fixed venue camera (PUSHIT-style), 640x360, floodlights, court calibrated once | Everything in 1.1 at the stated level | The camera does not move, so a median background, per-venue calibration reuse and pitch masks all work. |
| Same camera at 426x240 | Positional stats likely; passes and shots weaker (unmeasured) | Calibration thresholds scale by 0.67. The ball stage must pad frames to 432x240, because HRNet needs sizes divisible by 8 [code]. The ball shrinks with the resolution. |
| Phone or panning camera | Positional stats only on segments with valid calibration; events mostly go to review | See the note below. The fix is keyframe homographies (2.6). |
| Unknown pitch dimensions | Heatmaps, average positions, thirds and territory in normalised units | Metric distance and speed are withheld. |
| Slow shutter at night (blurred ball) | Unknown | BlurBall models motion blur explicitly, but only for table tennis [code] https://github.com/cogsys-tuebingen/blurball. |

**Note on panning cameras.**
- The current camera-motion model is a 4-parameter similarity. At pans of about 5° per sample, only 27-36% of feature matches fit it. That is below the code's 50% threshold, so the code returns "no motion" and camera motion is silently dropped [derived, recomputed].
- Even a 2° pan leaves 3.4-4.9 px median and 9.9-14.3 px maximum residual error under the best similarity fit [derived].

### 1.3 The binding constraints, in order

1. **Ball sampling rate.**
   - Every published ball tracker (TrackNet V1-V5, WASB, TOTNet) assumes consecutive frames at 25-30 fps.
   - At 5-6 fps, a 10 m/s pass moves about 30 px (5-10 ball diameters) between samples at 360p [derived].
   - Frame-to-frame cues then disappear, and socks, pitch lines and lights get picked instead of the ball.
2. **Identity fragmentation.**
   - The old tracker produced 5,347 IDs in 37 minutes; after `480a087` there are still about 64 per minute.
   - At 20-25 px, appearance re-identification and shirt numbers are close to useless: a back number would be about 3 px tall (tracking summary).
3. **Far-side resolution and ball height.** Coarse far-side metres per pixel and the unobservable ball height cap shot and far-side accuracy, whatever detector is used.
4. **No in-domain labels.** None of the published models or thresholds has been measured on this footage.

---

## 2. Architecture

```
video ─┬─ 5-6 fps ─ player/GK/ref detector ─ ByteTrack (Kalman, 2-pass) ─ tracklets ──────┐
       └─ native 25/30 fps, repeated frames removed ─ ball candidates:                    │
            YOLO tiles + WASB heatmap peaks + motion blobs                                 │
            ─ median-background + pitch-polygon gate ──────────────────────────────────────┤
calibration: clicks or venue fingerprint → H + λ (division model) → validity monitors      │
            → keyframe homographies for moving cameras ────────────────────────────────────┤
pitch space: foot points + covariance → global tracklet stitching (cannot-link, speed,     │
            team) → per-tracklet team posterior → keeper by penalty-area dwell ─────────────┤
ball decode: offline Viterbi over {free ball at candidate k, at feet of player j, missing} │
            + RTS smoother + on-ground/airborne flag ─────────────────────────────────────┤
possession: team-level HMM, forward-backward → per-frame team probabilities                │
events: rules on posteriors → stats with Monte Carlo intervals → review queue ◄───────────┘
```

Each stage below gives the algorithm, the reason for it, what HEAD does now, and what to change.

### 2.1 Decode and sampling

- **Players** stay at 5-6 fps.
- **The ball** runs at the native frame rate (25/30 fps).
- **Repeated frames** are removed before any multi-frame model. Re-encoded online and phone videos contain them, and multi-frame heatmap models fail on them (BlurBall README) [code] https://github.com/cogsys-tuebingen/blurball.
- **Cost:** roughly 5x more ball inference. TrackNetV2 at the same 512x288 input runs at about 157-162 FPS in TensorFlow on an unstated GPU [code] https://github.com/TrackNetV4/TrackNetV4. WASB throughput on a Modal L4 is unmeasured.
- **Around each shot candidate**, re-run the ball detector at full frame rate for about 1 s either side [derived].

### 2.2 Player detection

- Keep the Roboflow YOLO model for now.
- The licence-safe alternative is RF-DETR (Apache-2.0). How it handles small balls in football is unmeasured (ball fact-check).

### 2.3 Ball candidates and gating

**Algorithm**
1. **Candidates** come from three sources:
   - the current tiled YOLO;
   - WASB-soccer HRNet heatmap peaks;
   - the motion blobs already in `faint.py`.
2. **WASB settings** [code] https://github.com/nttcom/WASB-SBDT:
   - **Input size:** run at native 640x360. That size is valid (360 = 8 x 45), while downscaling to 512x288 would shrink the ball to 2.4-4.8 px.
   - **Frames:** 3 frames in, step 1.
   - **Threshold:** start from WASB's 0.5 and sweep it on labelled clips. The 0.7 in the BlurBall README is for table tennis, not WASB.
   - **Blobs:** connected components, scored by summed heatmap mass.
3. **Background gate on fixed cameras.**
   - Take the per-pixel median of 50-100 frames spread across each few-minute segment. This is TrackNetV3's background idea (`np.median` over frames) [code] https://github.com/qaz812345/TrackNetV3.
   - Reject candidates whose 5x5 patch barely differs from the background, or that fall outside the pitch polygon.
   - Exempt high-confidence detections near players, so a stationary ball at a restart survives.
   - On panning cameras, warp the background with the camera motion or skip this gate.

**Why this approach**
- Heatmap models that keep full-resolution output beat single-frame detectors on tiny balls: on the shuttlecock test set, YOLOv7 scores F1 68.0 against 98.56 for TrackNetV3 [code README].
- WASB's soccer model was trained on ISSIA fixed cameras, where players appear about 17-38 px tall at the model's scale. That covers Pitchlens' 20-25 px [paper? for the ISSIA range].

**Weaknesses**
- ISSIA is daytime professional football, and its licence is unknown.
- WASB's soccer F1 is unverified: the extracts give 83.6 or 88.3.
- That F1 uses a 4 px tolerance at 1080p (about 1.3 px at 360p) and counts hidden balls as absent. It is not comparable with anything Pitchlens will measure.

**HEAD:**
- Tiled YOLO (480 px tiles, 48 px overlap, imgsz 640), so a 3-6 px ball reaches the model at about 5-10 px.
- Track-before-detect in `faint.py`.
- `drop_static_balls` needs 3-20 s of evidence before it removes a static false ball.

### 2.4 Ball decode (new engineering)

Replace greedy per-frame choice with an offline Viterbi / forward-backward decode over the grid of candidates.

- **Hidden states per ball frame:**
  - free ball at candidate k;
  - at the feet of player j (allowed only within the existing possession rule of 0.55 body heights + 14 px in the image, or R_pz on the pitch);
  - missing or out of play.
- **Free-to-free transitions** use a constant-acceleration prediction:
  - acc = (p1−p2) − (p2−p3);
  - vel = (p1−p2) + acc;
  - pred = p1 + vel + acc/2.
- **The gate grows with elapsed time.** Starting value at 25 fps: about 0.05 x frame diagonal (~37 px at 640x360), from a 30 m/s maximum ball speed [derived].
- **Emissions:** candidate score (heatmap mass or detector confidence) and difference from the background.
- **Afterwards:**
  - smooth free-ball segments with an RTS smoother;
  - flag interpolated gaps of up to 0.5 s.
- **Airborne flag:** the implied speed between samples is above 32 m/s, or the ball projects off the pitch while it is inside the image. Pitch-space ball positions are used only when the ball is judged to be on the ground [derived].

**Why:**
- Post-match analysis can use future frames.
- Modelling the ball and players together beats tracking the ball alone (Maksai et al.) https://arxiv.org/abs/1511.06181. That paper solves a mixed-integer program, so an HMM is an adaptation of the idea.

**This has to be built and measured, not ported:**
- The fact-check found that WASB's released tracker has no motion model: `predict()` is never called. It just takes the highest-scoring blob within 300 px [code] https://github.com/nttcom/WASB-SBDT/blob/main/src/trackers/online.py.
- Compute is small enough for a CPU: 37 min x 25 fps is about 55k steps with a few dozen states.

### 2.5 Player tracking

**HEAD (`480a087`)** already follows the ByteTrack/BoT-SORT recipe the tracking research recommends:
- a constant-velocity Kalman filter with camera-motion compensation;
- a first matching pass on confident detections (≥0.4) and a second on weak ones (≥0.1);
- a 3.5 s buffer before a lost track is dropped;
- confirmation after two sightings within 0.8 s;
- kit colour as a soft +0.35 cost;
- a gate of min(3, 1 + 0.9·gap) body heights.

**Benchmark context:** HOTA at default settings [code] https://github.com/roboflow/trackers/blob/develop/docs/evaluations/results.md.

| Benchmark | ByteTrack | BoT-SORT | McByte |
|---|---|---|---|
| SportsMOT | 73.0 | 73.8 | 76.5 |
| SoccerNet (ground-truth boxes) | 84.0 | 84.5 | 85.0 |

Frame-to-frame association is no longer the bottleneck.

**Remaining change: camera motion.**
- `tracking.py` uses `estimateAffinePartial2D`, which is a 4-parameter similarity, not a full affine.
- It runs at 320 px width with RANSAC at 2 px, and rejects results with inlier ratio < 0.5 or zoom outside 0.85-1.18 [HEAD].
- For moving cameras, replace it with homographies to a keyframe (2.6).
- Mark as untrusted every segment where the gate made it return "no motion".

### 2.6 Calibration

**HEAD (`94d1b67`)** implements the fit the research recommends:
- a grid over the division-model distortion λ in [−1.2, 0.05] with step 0.05;
- residuals in image pixels;
- point-on-line clicks and leave-one-out checks;
- a refined-k1 bound of −1.5 < k1 < 0.2, with 1 + k1·r² ≥ 0.25.

It also has per-frame homographies from anchors, with alignment to the painted lines.

**Why this fit** [sim, re-run by the fact-checker]:
- The old fit measured error in metres and started from zero distortion. It rejected distortion in 78-99% of simulated end-camera trials, giving 0.20-1.45 m median error, against 0.08-0.16 m for the pixel-residual grid fit.
- Ignoring a 120-150° fisheye costs 0.70-2.25 m median.

**Why not the published automatic methods:** NBJW, PnLCalib, TVCalib, the 2023 SoccerNet calibration winner (Sportlight) and Roboflow's 32-keypoint model all use full-size pitch templates. They ignore distortion or hold it fixed. None will transfer to cages or futsal courts without retraining https://github.com/mguti97/PnLCalib https://github.com/roboflow/sports.

**Add:**
1. **Checks at calibration time:**
   - click RMS error;
   - leave-one-out outlier flags;
   - horizon above every landmark;
   - consistent orientation;
   - soft checks on focal length and camera height recovered from H;
   - error on 2 held-out check points.

   Values are in 3.7.
2. **Runtime checks, per segment:**
   - **Player height:** predicted pixel height of a 1.75 m person against the detected box height.
   - **Feet in the cage:** share of feet outside the cage.
   - **Line overlay:** chamfer score of the projected lines against the painted lines.
   - **Speed:** sustained player speed ≤9 m/s.
   - **Drift:** fixed-camera drift against the median frame, every 2-5 s. Dense `cv2.findTransformECC` with MOTION_HOMOGRAPHY suits 360p floodlit frames, which have few corners to track.
3. **Moving cameras.**
   - Compute homographies to a keyframe on undistorted frames with players masked out: USAC_MAGSAC at 2 px, ≥30 inliers, inlier ratio ≥0.4.
   - Start a new keyframe when overlap falls below 50%, then refine against the painted lines.
   - Feature matchers with safe licences: LightGlue with DISK or ALIKED. Avoid SuperPoint (weights are non-commercial).
4. **Per-venue reuse for fixed courts.**
   - Store each calibration with a background fingerprint (descriptors plus the line mask of the median frame).
   - On a new upload, match against stored fingerprints, verify with the line score, and reuse the calibration with no clicks.
   - Each such match also yields auto-labelled frames for a future keypoint model.
5. **Recover the full camera, not just the homography.**
   - Decompose H with the principal point at the image centre, or add clicks off the ground plane (crossbar corners at the known goal height, the top of the boards) and solve P = K[R|t].
   - This projects the 3D goal mouth, which shots on target and goals need, and it supports the player-height check.
6. **Foot-point uncertainty.**
   - Use σx ≈ 1.5 px and σy ≈ max(1.5 px, 0.08 x box height).
   - Carry it to pitch space as J Σ Jᵀ (J is the Jacobian of the image-to-pitch map) and feed it to a Kalman filter.
   - Heatmap smoothing width is at least the local σ.
7. **Unknown-dimensions mode.** Fit in normalised units, using only landmarks defined as fractions of length and width. Output only stats that do not depend on scale.
8. **Optional initialisers** for zero-click and phone footage, to estimate λ and field of view:
   - GeoCalib: Apache-2.0 code, CC-BY-4.0 weights.
   - AnyCalib: Apache-2.0 code and weights.

   Neither has been tested on 360p night football.

### 2.7 Team classification

The evidence here is the tracking summary only; the detailed findings did not arrive.

- **Decide per tracklet, not per detection**, as SoccerNet's sn-gamestate does.
- **Features:**
  - correct the floodlight colour cast using the pitch colour;
  - use torso and shorts colour;
  - cluster with 3-4 clusters. HEAD uses 4 clusters and keeps the two largest.
- **Decision rule:**
  - accumulate per-detection log-likelihoods per tracklet;
  - decide only when the posterior reaches **0.99**; below that, the tracklet stays "unknown".
  - HEAD instead relabels a track after 3 confident votes with a 60% majority, which is much looser.
- **Cap:** no more players per team per frame than the roster on the pitch.
- **Goalkeeper:** per team and half, the player who spends the most time inside the penalty area. 5-a-side outfield players may not enter it (events research), which makes this more reliable than the detector's goalkeeper class at 20 px.
- **Referees and bystanders:** use the detector class plus colour outliers. Exclude them from team stats and from the calibration checks.
- **Target:** coverage above 90% at more than 98% accuracy. This is plausible but unmeasured; no published result covers 20 px players.

### 2.8 Tracklet stitching

**HEAD** stitches offline with multi-pass optimal matching, tightest gaps first (0.6 s, 1.5 s, 3.0 s). A join needs:
- a start where the ending track's recent motion predicts it (at most 8.5 m/s + 1.5 m);
- no clear kit disagreement;
- no overlap in time.

It works in metres when the pitch is calibrated, and in body heights otherwise.

**Change.** The strongest systems (SoccerNet game-state winners, SportsSUSHI, GTATrack) combine pitch coordinates, team, a physical speed limit and appearance.
1. Solve globally across all gaps, with hard constraints:
   - two tracklets seen at the same time are different players;
   - the per-team roster cap;
   - the team posterior from 2.7, used as a constraint, not a vote.
2. Give appearance a low weight; at 20-25 px it adds little.
3. Send long gaps (>3 s) and ambiguous joins to a human merge/split review, ordered by how much player-level stats depend on them.

**Evidence for the gain:**
- Offline tracklet association adds about +3.7 HOTA to Deep-EIoU on SoccerNet (GTA).
- It raises McByte++ IDF1 from 84.5 to 87.2.

Both figures come from the tracking summary; their source URLs were in the part of the input that was cut off.

**Goal:** 10-20 IDs per team per match after review.

### 2.9 Possession

**HEAD** is rule-based:
- per-sample control with radius 1.1 m + 1.5σ, capped at 2.0 m;
- a relative-speed check of 4 + 2σ m/s;
- single-sample touches only with a ≥30° turn at ≥2 m/s;
- bridging of up to 3 unseen samples;
- control spells of at least 0.3 s;
- possession sequences that survive 5 s of unseen or loose ball, with a 400-iteration bootstrap interval and a 0.6 coverage gate.

**Change: a team-level hidden Markov model decoded with forward-backward.** This follows PathCRF's idea of one possession state per step plus forbidden transitions. It is defined over teams and the nearest detection in each frame, not over persistent player IDs, because PathCRF and Ball Radar both need complete identities that Pitchlens does not have.

- **States per 0.2 s step:**
  - C_A, C_B: team controls, through its nearest player;
  - F_A, F_B: ball in flight, last touched by that team;
  - D: dead ball.
- **Emission when the ball is seen:**
  - control states: a Gaussian on the distance from ball to nearest foot, with σ = √(0.6² + σ_ball² + σ_player²);
  - flight states: a uniform likelihood over speed-plausible positions.
- **Emission when the ball is unseen:** P(unseen | control) ≈ 0.5, P(unseen | flight) ≈ 0.3.
- **Transitions:**

  | Transition | Probability per step |
  |---|---|
  | stay | 0.85 |
  | control → flight | 0.12 |
  | direct control change (only with an opponent inside the duel zone) | 0.02 |
  | → dead ball | 0.01 |

  The mean dwell is 1.33 s, consistent with the 1.28 s mean control spell implied by Link & Hoernig (unverified).
- **Outputs:**
  - per-frame team probabilities;
  - possession % as expected time share;
  - event confidences.
- **Gain validation must scale with position noise** [derived, fact-check]:
  - At dt = 0.2 s and σ = 0.5 m, a velocity from two intervals has about 1.77 m/s noise per axis, and the difference of two such velocities about 2.5 m/s.
  - So require Δv ≥ max(3 m/s, 2.5·σ_dv), and test a heading change only when ball speed ≥ 3σ_v.
  - Do not use acceleration features. Second-difference noise is 18-31 m/s², above both ELASTIC's 10 m/s² peak threshold and Link & Hoernig's 4 m/s².
- **All priors are starting values.** Fit them on SkillCorner possession labels and on reviewed Pitchlens matches.

### 2.10 Events

Rules run on the HMM posteriors; thresholds are in section 3. This is the two-step design of Vidal-Codina et al.: decide possession first, then read events from changes of possession, the laws and pitch geometry https://arxiv.org/abs/2202.00804. Their own thresholds could not be verified; the small-sided values are derived.

- **Pass:** a team's control ends, then a teammate makes a validated gain within the transfer window, with enough ball travel.
- **Incomplete pass:** the next gain is by an opponent, or the ball goes out or over the netting.
- **Tackle:** an opponent gain that is short and quick.
- **Clearance:** a hard ball in the defensive third with no teammate target. Kept out of pass accuracy.
- **Shot:** passes the speed, origin and goal-crossing gates.
- **Inferred shot:** the ball is lost after release, and then within 2.0 s one of these happens:
  - the keeper gains it in his area;
  - the ball is seen at the goal line;
  - it goes over the end netting;
  - a kick-off follows.

  Confidence at most 0.5, and it must be reviewed.
- **On target:** decided from the outcome only (goal or save).
- **Save:** a keeper-area gain within 1.5 s of the shot. It is a parry if the keeper loses the ball within 1.0 s, otherwise a hold.
- **Goal:** a grounded ball past the line between the posts, or the kick-off pattern. Always confirmed by review or the entered score.
- **Dead ball and restarts:** players standing still plus a stationary or unseen ball; restarts classified by tolerance zones around the lines, corners and centre.
- **Caged courts:** the ball rarely leaves play. Over the netting it goes to the opposing keeper, so "keeper restart after a dead ball" replaces line-based out-of-play. Make restart rules a per-venue setting.

### 2.11 Stats and uncertainty

- **Possession %** is the time share T_A/(T_A+T_B), shown with its interval and coverage.
  - Also show possession by share of passes as a labelled cross-check. Flag the match if the two differ by more than 10 percentage points.
  - Sofascore's and Opta's headline possession is widely described as pass-share based (unverified), so the UI must say which definition it shows.
- **Intervals:** resample each event 500 times, keeping it with probability equal to its confidence, and report the 10th-90th percentile of each stat.
- **Momentum:**
  - per minute, sum P(team control) · exp(−d_goal / 8 m) · dt, where d_goal is distance to the attacked goal;
  - take the difference between teams;
  - smooth with an exponential moving average, half-life 1.5 min;
  - add spikes for shots.

  HEAD has this.
- **Field tilt** is each team's share of control time in the final third (L/3), withheld below 20 s.
- **Robustness levels** from section 1.1 are shown next to every stat.

### 2.12 Human review

- **Event confidence** = s_start · s_end · (0.5 + 0.5 · share of samples with the ball visible) · team confidence · identity confidence.
  - s is a touch evidence score built from distance, visibility and how well the ball segments fit.
  - It does not use acceleration (see 2.9).
- **Triage thresholds:**
  - auto-accept at ≥0.85;
  - queue 0.3-0.85;
  - always queue shots and goals;
  - auto-reject below 0.2.

  Fit an isotonic calibration per event type once about 200 reviewed labels exist.
- **Queue order:** by impact on the headline stats.
- **Goals** must reconcile with the user-entered score (HEAD has score entry).
- **Identity:** tracklet merge/split review for long gaps.
- **Correction tools:** for click-and-propagate corrections of ball or player positions, use SAM 2 (Apache-2.0). Do not use CoTracker (CC BY-NC 4.0).

---

## 3. Parameters

The status column says where each value stands:
- **HEAD**: in the code now.
- **proposed**: to implement.
- **conflict**: HEAD and the research disagree; settle it on labelled data.
- **reference**: a literature value for comparison, not a Pitchlens setting.

### 3.1 Possession

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Possession-zone radius (literature) | 0.5 (provider A), 1.0 (providers B, C) | m | Vidal-Codina 2022 [paper?] https://arxiv.org/abs/2202.00804 | reference |
| databallpy defaults (25 Hz) | radius 1.5; speed change 5.0; angle 10; min frames 0 | m, m/s, deg | databallpy 0.8.1 [code] https://github.com/Alek050/databallpy | reference |
| Pitchlens possession radius R_pz | 1.1 + 1.5·σ_cal, capped at 2.0 | m | [derived]; HEAD `controlRadius`, `maxControlRadius` | HEAD |
| Duel zone | R_pz + 0.5 (research) vs R_pz + 0 (HEAD) | m | events research [derived]; HEAD `duelExtra` cites Vidal-Codina's 1.0 m for both zones | conflict |
| Relative-speed gain check | ball-player speed difference ≤ 4 + 2σ | m/s | [derived]; HEAD `maxRelativeSpeed` | HEAD |
| Speed-change gain check | Δv ≥ max(3, 2.5·σ_dv); σ_dv ≈ 2.5 at σ_pos 0.5 m | m/s | fact-check error propagation [derived] | proposed |
| Heading-change gain check | ≥20°, tested only when ball speed ≥ 3σ_v | deg | [derived] | proposed (HEAD single-sample touch: ≥30° at ≥2 m/s) |
| Acceleration features | not used at 5-6 fps (noise 18-31 m/s²) | m/s² | [derived] | proposed |
| Minimum control dwell | 2 samples (0.3 s) | s | [derived]; HEAD `minControlSeconds` | HEAD |
| Same-player bridging | ≤3 unseen samples (≤0.6 s) | samples | [derived]; HEAD `bridgeSamples` | HEAD |
| How long possession survives an unseen or loose ball | 5.0 (HEAD); research: gaps >1.5 s decided by posterior or marked "unknown" | s | HEAD `maxPossessionGap`; events research | conflict (the HMM replaces it) |
| Coverage needed to show possession % | ≥0.6 of in-play time | ratio | [derived]; HEAD | HEAD |
| Interval method | bootstrap 400 (HEAD); Monte Carlo 500, 10th-90th percentile (proposed) | iterations | [derived] | HEAD / proposed |
| HMM transitions per 0.2 s | stay 0.85; control→flight 0.12; control→control 0.02; →dead 0.01 | probability | [derived], fact-checked | proposed |
| HMM emissions | P(unseen \| control) 0.5; P(unseen \| flight) 0.3; reach σ 0.6 m | probability, m | [derived] | proposed |
| Time-share vs pass-share flag | difference > 10 | percentage points | [derived] | proposed |

### 3.2 Passes

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Max time from loss to gain | 4.0 (pitches ≤45 m); 5.0 (larger) | s | [derived]; HEAD | HEAD |
| Minimum ball travel | 2.0 (implied speed 2-30 m/s) | m | [derived]; HEAD | HEAD |
| Tackle rather than interception | opponent gain with travel < 2.0 and gap < 0.8 | m, s | [derived]; HEAD | HEAD |
| Clearance (kept out of accuracy) | defensive third, ≥10 m/s, no teammate target | m/s | [derived] | proposed |
| Out of play / over the netting | counts as an incomplete pass | – | events research | proposed (check HEAD) |
| Pass accuracy shown | ≥20 attempts per team at confidence ≥0.5 | count | [derived]; HEAD `minPassesForAccuracy` | HEAD (confidence part proposed) |
| Reference touch gates | ETSY: pass-like distance ≤2.5 m, foot height ≤1.5 m. ELASTIC: distance ≤3.0 m, boundary ≤1.0 m | m | [code] https://github.com/ML-KULeuven/ETSY https://github.com/hyunsungkim-ds/elastic | reference |

### 3.3 Shots

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Minimum release speed | 8 (6-8 kept as low confidence) | m/s | [derived]; HEAD | HEAD |
| Shot origin | ≤0.6 x pitch length from the target goal line (HEAD also ≤32 m) | fraction, m | [derived]; HEAD | HEAD |
| Extrapolated crossing | within g/2 + 2.0 m of the goal centre, g = goal width (HEAD: or 0.12 x pitch width) | m | [derived]; HEAD | HEAD |
| Time for the ball to reach the line | 1.5 (research) vs 2.5 (HEAD) | s | [derived]; HEAD `shotTimeToLine` | conflict (HEAD is looser; fine only while every shot is reviewed) |
| Ball samples after release | ≥2 within 0.6 | samples, s | [derived] | proposed |
| Inferred shot | ball unseen, then within 2.0 s: keeper gain in area, ball within 1.5 m of the line, over the end netting, or a kick-off. Confidence ≤0.5 | s, m | [derived] | proposed |
| Full-frame-rate ball re-run | ±1 around each candidate | s | [derived] | proposed |
| Goal-size presets | 3.66x1.22 or 4.88x1.22 (5-a-side); 3.66x1.83 (FA 5v5); 3x2 (futsal) | m | events and calibration research (unverified; user-editable) | preset |

### 3.4 Saves

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| On-target band | \|y_cross − y_centre\| ≤ g/2 + 0.3 | m | [derived] (ball radius 0.11 m + noise); HEAD `onTargetMargin` | HEAD |
| Save window | keeper or keeper-area gain or deflection within 1.5 | s | [derived]; HEAD `saveWindow` | HEAD |
| Parry vs hold | keeper loses the ball within 1.0 s = parry | s | Vidal-Codina [paper?] | proposed |
| Block | outfield defender stops the ball ≥1.5 m in front of the line | m | [derived] | proposed |
| Keeper identity | player with most time in the penalty area, per team and half | – | events research | proposed |

### 3.5 Goals

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Direct evidence | grounded ball ≥0.2 m beyond the goal line, within the posts + 0.1 m | m | [derived] | proposed |
| Kick-off pattern | 10-90 s after the candidate: dead ball (mean player speed <1.5 m/s for ≥3 s); ≥80% of each team in its own half; a player within 1.5 m of the centre for ≥1 s | s, m/s, m | [derived]; HEAD kick-off parameters | HEAD |
| Publication | only after a reviewer or the user-entered score confirms it | – | events research | HEAD (score entry) |

### 3.6 Out of play and dead ball

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Ball out (lined pitch) | ≥2 consecutive grounded samples beyond a line by > max(0.5 m, 2σ_ball) | m | [derived] (old code: 0.75 m) | proposed |
| Dead ball from player movement | mean player speed < 1.2-1.5 m/s for ≥2-3 s, plus ball stationary or unseen | m/s, s | [derived]; HEAD 1.5 m/s, 3.0 s | HEAD |
| Stationary ball | < 1 m/s for ≥0.6 s | m/s, s | [derived] | proposed |
| Restart zones | touchline ≤1.0 m + σ; corner ≤1.5 m; centre spot ≤1.5 m | m | [derived], following the Vidal-Codina tolerance-zone design | proposed |
| Airborne flag | implied speed > 32 m/s, or projects off the pitch while inside the image | m/s | [derived] | proposed |
| Literature anchor | in-play status from players alone: up to 92% frame accuracy | % | Lang et al. 2022 [paper?] https://www.nature.com/articles/s41598-022-19948-1 | reference |

### 3.7 Calibration validity

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Minimum clicks | 4 non-collinear; recommend 6-10 named landmarks + 3-6 points on each visible line | points | OpenCV findHomography docs; [sim] | HEAD supports line clicks |
| Distortion search | grid λ in [−1.2, 0.05], step 0.05, radius normalised by the half-diagonal; refined −1.5 < k1 < 0.2 with 1 + k1·r² ≥ 0.25 | – | [sim]; HEAD `pitch.py` | HEAD |
| Keep distortion only if | leave-one-out RMS improves ≥10% | % | [derived] | proposed (check HEAD) |
| Click RMS at 640x360 | ≤2 good; 2-4 warn; >4 reject; scale x0.67 at 240p | px | [derived]; HEAD quality gate | HEAD |
| Click outlier | leave-one-out residual > max(3 px, 3 x median) | px | [derived] | proposed |
| Line-overlay (JaC-style) tolerance | 3.3 at 640x360; 2.2 at 426x240 (SoccerNet's 5 px at 960x540, scaled) | px | https://github.com/SoccerNet/sn-calibration [code], scaled | evaluation |
| Plausibility (soft warnings) | field of view 40-170°; camera height 1.5-20 m; horizon above all landmarks | deg, m | [derived] | proposed |
| Player-height ratio | median 0.8-1.25, with no trend across the image | ratio | [derived] | proposed |
| Feet outside the cage | < 5% more than 1.5 m outside | %, m | [derived] | proposed |
| Sustained player speed | ≤9 | m/s | [derived] | proposed |
| Fixed-camera drift | check every 2-5 s; <1.5 px keep; 1.5-15 px update; otherwise invalid | px | [derived] | proposed |
| Keyframe motion | USAC_MAGSAC 2 px; ≥30 inliers; inlier ratio ≥0.4; new keyframe below 50% overlap | px, count | [derived] | proposed |
| Current camera-motion model | similarity; RANSAC 2 px at 320 px width; rejects inlier ratio <0.5 or zoom outside 0.85-1.18 (returns "no motion") | – | HEAD `tracking.py` | flag those segments |
| Foot-point noise | σx 1.5 px; σy max(1.5 px, 0.08 x box height) | px | [derived] | proposed |

### 3.8 Tracking and tracklet stitching

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Detection thresholds | high 0.4, low 0.1 | score | HEAD `ByteTracker` | HEAD |
| Lost-track buffer | 3.5 (research: 3-4) | s | HEAD; tracking summary | HEAD |
| Confirmation | 2 sightings within 0.8 s (research: 2-3 frames) | sightings, s | HEAD; tracking summary | HEAD |
| Kit disagreement | +0.35 soft cost | cost | HEAD | HEAD |
| Association gate | min(3, 1 + 0.9·gap) | body heights | HEAD | HEAD |
| Stitching passes | gaps of 0.6, 1.5, 3.0 | s | HEAD `stitch_tracks` | HEAD |
| Stitching speed limit | 8.5 m/s + 1.5 m | m/s, m | HEAD `maxPlayerSpeed` | HEAD |
| Cannot-link | any overlap in time forbids a merge | – | tracking summary | HEAD (per pair); extend to a global solve |
| Team cap | ≤ on-pitch roster per team per frame | players | tracking summary | proposed |
| Human review | gaps >3 s and ambiguous joins | s | [derived] | proposed |
| Deep-EIoU reference | high 0.6, low 0.1, new track 0.7; buffer 60 frames x fps/30; interpolate ≤20 frames | – | tracking research (Deep-EIoU has no licence file: do not copy) | reference |

### 3.9 Team classification

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| Colour clusters | 3-4 (HEAD: 4, keep the two largest) | clusters | tracking summary; HEAD `train_colours` | HEAD |
| Features | torso + shorts colour in LAB after pitch-colour cast correction | – | tracking summary | proposed |
| Decision rule | per tracklet; posterior ≥0.99, else "unknown" (HEAD: ≥3 votes and ≥60%) | probability | tracking summary; HEAD `vote_teams` | conflict (HEAD too loose) |
| Per-frame cap | ≤ roster on the pitch per team | players | tracking summary | proposed |
| Acceptance target | coverage >90% at accuracy >98% | % | tracking summary (unmeasured) | target |

### 3.10 Ball stage

| Name | Value | Unit | Source | Status |
|---|---|---|---|---|
| WASB input size | native 640x360 (divisible by 8); 240p padded to 432x240 | px | [code] `src/models/hrnet.py` | proposed |
| Frames and step | 3 in, 3 out; step 1 | frames | [code] | proposed |
| Heatmap threshold | 0.5 to start, then sweep on labelled clips | score | [code] WASB `detector/tracknetv2.yaml` | proposed |
| Weights and annotations | WASB soccer weights: Drive `1pg0MpMtKZ6ziYEr4oyfKYPOO3hjLw94l`; annotations: `1Oqr33EXOEjERothXvVAqAeKiKuB-per1` | – | WASB `MODEL_ZOO.md`, `setup_soccer.sh` | Modal only |
| Median background | 50-100 frames per few-minute segment | frames | [derived]; TrackNetV3 | proposed |
| Gate for a free ball at 25 fps | about 37 px (0.05 x diagonal), growing with elapsed time | px | [derived] | proposed |
| Flag interpolated gaps up to | 0.5 | s | [derived]; HEAD `max_bridge` | HEAD / proposed |
| Evaluation tolerance | 3 and 6 at 360p | px | [derived] | evaluation |

---

## 4. Prioritised build list

### 4.1 Now: CPU-only dev sandbox, no training, no Hugging Face or Google Drive

Ordered by value per unit of effort. Effort estimates come from the research.

1. **Measurement first (highest priority).** Effort: low-medium.
   - Commit and extend the Metrica degradation harness.
   - Add SkillCorner open data https://github.com/SkillCorner/opendata:
     - MIT licence;
     - per-frame possession labels (player_id and team);
     - 10 fps broadcast tracking with extrapolated players.
   - Add Sportec Open DFL:
     - CC BY 4.0; 7 matches (2 Bundesliga + 5 2. Bundesliga);
     - loaded through databallpy `get_open_game` or kloppy.
   - Both give possession ground truth under realistic noise. GitHub-hosted data should be reachable from the sandbox (raw.githubusercontent.com was reachable for the fact-checkers). Sportec's download host is not confirmed, so fall back to Modal if needed.
   - Write the CVAT label schema for ball point tracks. Use WASB's attributes (outside, occluded, used_in_game) plus a visibility class. Write the event-label schema and the scorers in section 5.
2. **Team-level possession HMM** with forward-backward decoding (2.9). Effort: 2-4 days.
   - Includes noise-scaled gain validation and removes acceleration features.
   - Tune the priors on SkillCorner and Sportec data degraded to 5 fps.
3. **Per-tracklet team posterior**, with colour-cast correction, the roster cap, and keeper identified by penalty-area time. Effort: about 1-2 days [derived].
4. **Offline ball Viterbi with a "feet" state** over the existing candidates (YOLO + faint blobs), plus RTS smoothing and the airborne flag. Effort: 3-5 days. Develop on cached candidates, but accept it only once the labelled ball set exists (4.4, item 1).
5. **Median-background and pitch-polygon gate, and duplicate-frame removal.** Effort: low.
6. **Calibration checks and runtime monitors** (3.7), and flag segments where camera motion fell back to "no motion". Effort: 2-3 days.
7. **Global tracklet stitching** with cannot-link and team constraints, plus a merge/split review UI. Effort: medium.
8. **Keyframe-homography camera motion** for panning footage. Effort: 3-5 days.
   - Start with ORB + USAC_MAGSAC, or ECC for fixed-camera drift; neither needs downloaded weights.
   - Add LightGlue + DISK later.
9. **Per-venue calibration reuse** (fingerprint + verification). Effort: medium.
10. **Stats presentation.** Effort: small-medium.
    - robustness labels;
    - the possession-definition label and pass-share cross-check;
    - Monte Carlo intervals;
    - a review queue ordered by confidence.
11. **Unknown-dimensions mode.** Effort: low.
12. **Full camera recovery** from H plus crossbar clicks, so the goal mouth can be projected. Effort: medium [derived].

### 4.2 Needs the Modal GPU, but no training

1. **Native-frame-rate ball stage**, with WASB-soccer zero-shot at 640x360 as an extra candidate source.
   - About 1-2 days to wrap `src/models/hrnet` and the postprocessor.
   - Measure throughput on an L4; it is unknown.
2. **Reproduce WASB's soccer evaluation** on ISSIA with the repo's eval script (`dataset=soccer`), to settle whether its F1 is 83.6 or 88.3.
3. **Full-frame-rate ball re-run around shot candidates.**
4. **End-to-end test on SoccerTrack v2**, downscaled to 640x360 and sampled at 5 fps https://github.com/AtomScott/SoccerTrack-v2.
   - CC BY 4.0: 10 amateur university matches filmed by fixed panoramic 4K cameras.
   - Per-frame pitch coordinates and 12-class ball-action labels with team.
   - It is the closest open match to Pitchlens footage, and it tests video-to-events, not just tracking-to-events.
   - Also use its pitch coordinates, and SoccerTrack v1's GNSS positions https://github.com/keisuke198619/SoccerTrack, to measure calibration at low resolution.
5. **GeoCalib / AnyCalib** as initialisers for distortion and field of view.
6. **SoccerNet-Tracking** (public on Hugging Face as SoccerNet/SN-Tracking-2023) for research-only evaluation.

### 4.3 Needs GPU training later

| Item | Data | Model | Cost | Notes |
|---|---|---|---|---|
| Ball detector fine-tune | 20-30k Pitchlens frames labelled at native fps in CVAT, split by match and venue. Optional: ISSIA; SoccerTrack v1 wide view downscaled; SoccerNet-Tracking ball boxes (research only). Degrade all to 360p/240p, H.264, blur, night gamma | WASB-HRNet from the soccer checkpoint, trained with the BlurBall fork (MIT). Ablations: median-background input channel; TOTNet occlusion augmentation (p 0.1-0.25) with visibility-weighted BCE; 5 frames instead of 3 | Labelling dominates. 30 epochs on one GPU is "hours, not days" (researcher estimate, not published) | Target F1 ≥0.8 on visible-ball frames at 3 px. Training at the target scale beats inference tricks: on VisDrone, slice fine-tuning gave +12.3 AP50 against +5.2 for inference-only slicing [code] https://github.com/fcakyon/small-object-detection-benchmark. For shippable weights use only own data plus CC BY sources; starting from the ISSIA-trained checkpoint inherits ISSIA's unknown terms |
| Small-sided pitch keypoint and line model | Frames auto-labelled from calibrated fixed-camera matches (4.1 item 9) | HRNet-W18/W32 or a pose model on a 5-a-side/futsal template, with NBJW/PnLCalib-style voting (DLT on subsets, RANSAC at several thresholds, keep the lowest error, point+line refinement) plus distortion | 2-4 weeks | Write original code; NBJW and PnLCalib are GPL-2.0 |
| Ball-action spotting as a second opinion | SoccerTrack v2 ball-action labels (CC BY 4.0) + reviewed Pitchlens events | T-DEED-style model (code is GPL-3.0, so reimplement) | Medium | The 2025 T-DEED baseline scores Team-mAP@1 47.18 / mAP@1 53.59 on the broadcast test set [code README] https://github.com/SoccerNet/sn-teamspotting. The gap to 640x360 fixed cameras is large |
| Detector licence swap | Pitchlens + CC BY data | RF-DETR (Apache-2.0) | Medium | Removes the AGPL dependency; small-ball performance unmeasured |
| Confidence calibration | About 200 reviewed labels per event type | Isotonic regression | CPU only | Needs review volume, not a GPU |
| Optional: P2-head YOLO | Pitchlens tiles | yolov8-p2 / yolo11-p2 | Medium | AGPL; the claimed 2-3 mAP gain has no primary source |

### 4.4 Needs the owner

1. **Footage.**
   - 6-10 held-out clips of 20-30 s at native frame rate, as originals rather than re-encodes. They should cover several venues, night lighting, 360p and 240p, and fixed and phone cameras.
   - 2-3 full matches for event and possession labels, with their final scores.
2. **Labelling time or budget.**
   - About 5-7k frames for the ball evaluation set.
   - 20-50 calibration frames per venue.
   - Events for 2-3 matches.
   - Later, about 20-30k ball frames for training.
3. **Venue facts (PUSHIT at Tekkerz).**
   - Camera: lens and field of view, mount height and position, native resolution, whether the export is already undistorted or cropped, and how often cameras move.
   - Courts: dimensions; markings (painted lines or board bases); goal size; whether there is a keeper area; restart rules after goals and when the ball goes over the netting. Tekkerz advertises "FIFA-standard ... fully netted courts" but publishes no dimensions.
4. **Rights and consent** to use customer footage for evaluation and training.
5. **Downloads on Modal:**
   - Google Drive via gdown: WASB weights and annotations, TrackNetV3 checkpoints;
   - Hugging Face: SoccerTrack v2, SoccerNet-Tracking, GeoCalib;
   - Dropbox: ISSIA videos.
6. **Legal decisions** (see section 6):
   - Ultralytics AGPL (runtime, and in Ultralytics' view the trained weights);
   - GPL-2.0 for NBJW/PnLCalib;
   - ISSIA terms and SoccerNet terms;
   - MPL-2.0 obligations.
7. **Product decisions:**
   - which possession definition to headline;
   - whether xG appears at all;
   - what review turnaround the product promises.

---

## 5. Measurement

**Rules for every metric:**
- Split by match and venue, never by frame.
- Report bootstrap confidence intervals.
- Test at both 5-6 fps and native frame rate where relevant.

**Small sets are noisy.** A proportion near 0.8 measured on 100 items has a 95% interval of about ±0.08 [derived arithmetic], so about 100 items per metric per condition is the practical minimum.

**Acceptance thresholds.** Thresholds marked [derived] are proposals placed between literature anchors that come from much better data.

**Ball detection**
- **Labelled set:** 6-10 clips of 20-30 s at native fps (about 5-7k frames), as CVAT point tracks.
  - Visibility classes: {visible, partial, occluded-at-feet, occluded-other, out-of-frame, not-in-play}.
  - Plus used_in_game, to separate the match ball from other balls.
- **Metrics:**
  - TrackNet-style TP/FP1/FP2/FN;
  - precision, recall and F1 at 3 px and 6 px, and AP over a threshold sweep;
  - RMSE of true positives and recall per visibility class;
  - false positives per minute when no ball is in play;
  - share of in-play time with a known ball, and jump count.
- **Acceptance:**
  - F1 ≥0.8 on visible-ball frames at 3 px;
  - no change may raise false positives per minute.
- **Justification:**
  - Research target: in-domain heatmap models reach 0.84-0.99 F1 on their own benchmarks.
  - Not comparable with WASB's soccer F1, which uses about 1.3 px tolerance at 360p and counts hidden balls as absent.

**Calibration**
- **Labelled set:** per venue, 20-50 frames with ≥12 points plus line points, and 2 held-out check points.
- **Metrics:**
  - held-out landmark error (median and 90th percentile, by near/middle/far third);
  - projection error in metres;
  - IoU of the visible pitch area;
  - line-overlay score at 3.3 px;
  - completeness.
- **Acceptance:** calibration-only median ≤0.3 m [derived].
- **Justification:** about 2.5x the simulated 0.05-0.12 m, to allow for real clicks, pitch-size and lens error.
- Keep the calibration Monte Carlo as a regression test.

**Foot positions**
- **Labelled set:** clicked foot positions on the calibration frames.
- **Metrics:** error in metres per third of the pitch.
- **Acceptance:** median ≤0.6 m and 90th percentile ≤1.7 m [derived].
- **Justification:**
  - The simulation with 2 px foot noise gives a median of 0.25-0.52 m.
  - 1.7 m is SoccerNet's game-state tolerance (τ = 5 m) scaled to a 36 m pitch (researcher's scaling) https://github.com/SoccerNet/sn-gamestate.

**Tracking and identity**
- **Labelled set:** 3-5 clips of 1-2 minutes with every player boxed and identified, plus identity links over one full match from review.
- **Metrics:**
  - HOTA and IDF1 (TrackEval);
  - IDs per minute per team;
  - share of player-time in tracklets of ≥20 s (the length HEAD requires for average positions).
- **Acceptance:** 10-20 IDs per team per match after review.
- **Justification:** HOTA of 73-85 on SportsMOT/SoccerNet is not comparable (professional footage, larger players). Only the trend matters until a small-sided baseline exists.

**Team classification**
- **Labelled set:** a few hundred tracklets across venues and lighting.
- **Metrics:** coverage and accuracy.
- **Acceptance:** coverage ≥90% at accuracy ≥98%.
- **Justification:** tracking-research target; unmeasured at 20 px.

**Possession**
- **Labelled set:**
  - 2-3 matches (or at least 30 minutes) with per-second team-control labels and dead-ball marks;
  - SkillCorner and Sportec data degraded to 5 fps.
- **Metrics:**
  - per-frame team accuracy on in-play frames;
  - absolute error of possession %;
  - interval coverage (the 80% interval should contain the truth about 80% of the time);
  - data coverage.
- **Acceptance:**
  - per-frame accuracy ≥0.85;
  - |error| ≤5 percentage points;
  - intervals calibrated.

  All three are [derived].
- **Justification:** PathCRF's 0.924 at 5 fps on professional data is the ceiling; Ball Radar's 0.745 is the floor.

**Passes**
- **Labelled set:** the same matches, with each pass's time, team, passer and receiver.
- **Metrics:** team-level precision, recall and F1 within ±1 s.
- **Acceptance:**
  - precision ≥0.8;
  - recall reported but not gated;
  - accuracy % error ≤10 points when shown.

  All three are [derived].
- **Justification:**
  - PathCRF also scores within 1.0 s.
  - Literature F1 is 0.71-0.90 on far better data [paper?].
  - The gate is on precision because the counts are shown as "detected".

**Shots and saves**
- **Labelled set:** every shot in the labelled matches, plus SoccerTrack v2 "Shot" labels.
- **Metrics:**
  - recall of the candidate list;
  - false candidates per match;
  - on-target and save correctness after review.
- **Acceptance:** candidate recall ≥0.9, with a review load of a few minutes per match [derived].
- **Justification:** every shot is reviewed, so what matters is that the list misses nothing. Literature shot F1 is 0.63-0.65 [paper?].

**Goals**
- **Labelled set:** every goal plus the user-entered scores.
- **Metrics:**
  - recall of goal candidates;
  - rate of reconciliation with the entered score.
- **Acceptance:** 100% after review; automatic candidate recall reported.
- **Justification:** goals are never published automatically.

**Dead ball / in play**
- **Labelled set:** the labelled matches.
- **Metrics:**
  - frame accuracy;
  - error in stoppage start and end times.
- **Acceptance:** reported, no gate.
- **Justification:** anchor of 92% frame accuracy (Lang 2022) [paper?].

**Confidence and review**
- **Labelled set:** every reviewed event.
- **Metrics:**
  - per-type reliability curve and expected calibration error, after about 200 labels;
  - share of queued items that reviewers changed;
  - reviewer minutes per match.
- **Acceptance:** events auto-accepted at ≥0.85 are correct at least 85% of the time.
- **Justification:** that is what a calibrated confidence means.

---

## 6. Licence risks and mitigations

**Ultralytics YOLO runtime and the Roboflow ball, player and pitch weights**
- **Licence:** AGPL-3.0. That it covers trained weights is Ultralytics' stated position, not settled law.
- **Risk:** copyleft that applies to network use, which matters for a hosted service.
- **Mitigation:** legal review; buy an Ultralytics commercial licence, or move to RF-DETR (Apache-2.0) and an MIT WASB-HRNet trained on own data.

**WASB / FootAndBall soccer weights**
- **Licence:** the code is MIT, but the weights were trained on ISSIA-CNR, whose terms are unconfirmed.
- **Risk:** shipping weights derived from possibly research-only data.
- **Mitigation:** use them for evaluation only until ISSIA's terms are known. Retrain on own data plus CC BY data for production.

**Other ball code and weights**

| Component | Licence | Use |
|---|---|---|
| Roboflow football-ball-detection dataset | CC BY 4.0 (not re-verified) | Credit it |
| WASB-SBDT code, BlurBall | MIT | Keep notices |
| TrackNetV3 code and checkpoints | MIT, commercial use explicitly allowed | Usable |
| TOTNet | MIT code; its TTA dataset is research-only | Do not ship weights trained on TTA |
| TrackNetV5 SDK | Proprietary | Do not use |
| MotionPosterior | No licence | Do not use |

**Datasets**

| Dataset | Licence | Use |
|---|---|---|
| SoccerNet (tracking, v3, calibration; action videos need an NDA) | Research terms; the tracking licence could not be read | Evaluation only |
| SoccerTrack v2 | Data CC BY 4.0, code MIT | Training and evaluation, with attribution |
| SoccerTrack v1 | Code MIT; data licence unverified | Evaluation until confirmed |
| Sportec Open DFL | CC BY 4.0 | Usable, with attribution |
| SkillCorner open data | MIT | Usable; credit SkillCorner |
| Metrica sample data | No formal licence, "acknowledge the source" | Evaluation only, not redistributed (the harness already does this) |

**Calibration code**

| Component | Licence | Use |
|---|---|---|
| NBJW, PnLCalib | GPL-2.0 | Reimplement the ideas; legal review before any server-side use |
| BroadTrack | Non-commercial research only | Do not use |
| Sportlight | No licence file | Ideas only |
| TVCalib, KpSFR | MIT | Usable |
| roboflow/sports | MIT | Usable |
| GeoCalib | Apache-2.0 code, CC-BY-4.0 weights | Usable, with attribution |
| AnyCalib | Apache-2.0 code and weights | Usable |

**Tracking code**
- sn-gamestate, StrongSORT and T-DEED are GPL-3.0: reimplement rather than copy.
- boxmot is AGPL: avoid.
- PRTReID uses the Hippocratic licence, which restricts usage: avoid.
- Deep-EIoU and Ball Radar have no licence file, so all rights are reserved: ideas only.
- ByteTrack, BoT-SORT, OC-SORT, GTA-link, UCMCTrack, TrackEval, roboflow trackers and supervision, TrackLab, CAMELTrack and McByte++ are permissive (tracking summary). Still treat any weights trained on SportsMOT or SoccerNet as non-commercial.

**Event code**
- PathCRF and ELASTIC are MPL-2.0, a per-file copyleft. Reimplement them, or keep changes to their files separate and publish those changes; get legal review.
- ETSY (Apache-2.0), databallpy (MIT) and kloppy (BSD-3) are permissive.

**Review tools and feature matching**
- SAM 2 (Apache-2.0) is fine.
- CoTracker (CC BY-NC 4.0) must be avoided.
- LightGlue (Apache-2.0), DISK (Apache-2.0) and ALIKED (BSD-3) are fine.
- SuperPoint's weights are non-commercial: avoid.

**Customer footage**
- **Risk:** consent and terms.
- **Mitigation:** the owner decides (4.4).

---

## 7. What competitors do, and the product implication

**No competitor research was received** (the input was cut off). The received findings mention these points only in passing:

- **Sofascore's Attack Momentum** was reportedly developed with Opta. It shows per-minute bars valuing possessions, dangerous attacks, box entries and set pieces [unverified] https://www.sofascore.com/news/how-sofascores-attack-momentum-changed-sport-analysis. So the reference product builds on professional event data, not on video alone.
- **The Opta/Sofascore possession definition is unclear.** One claim says it has been based on possessions since 2017; it is also widely described as pass-share based. Neither is verified.
- **FIFA** reportedly uses the Vidal-Codina method internally, with "default hyperparameters", to derive events from tracking data (Mills 2026) [paper?].
- **SkillCorner** sells broadcast-derived tracking. Its open data carries explicit `is_detected` flags and extrapolated-player files https://github.com/SkillCorner/opendata: a precedent for telling users what was seen and what was inferred.
- **Camera and venue vendors named in the research:**
  - PUSHIT, the replay system at Tekkerz (pushitreplays.com, seen via search only);
  - Spiideo, runner-up in the 2023 SoccerNet calibration challenge with 99.96% completeness, and author of the SynLoc benchmark with fixed elevated cameras;
  - BePro, whose panoramic cameras recorded SoccerTrack v2.

  The research did not establish what stats these products deliver, which are automatic, or whether humans review them.

**What the available evidence supports for the product:**
1. **Do not promise fully automatic events.** Even on professional tracking, shots are the weakest category. Be automatic where the evidence is strong (positions, possession, territory) and human-verified where it is weak (shots, saves, goals). Label each stat accordingly, following SkillCorner's seen-versus-inferred precedent.
2. **Make fast review the thing to design and measure.** With confidence triage, the per-match queue should be shots, goals and mid-confidence turnovers. Track reviewer minutes per match as a product KPI.
3. **Venue partnerships with fixed cameras are the favourable case.** They allow calibration reuse, background models, and a growing stock of auto-labelled training data. Position phone footage as a reduced product.
4. **Sofascore comparability.** Label the possession definition. Say that Pitchlens momentum is based on territory, not on Opta-style event values.

**Research still to commission:** per competitor (PUSHIT, Spiideo, BePro and others), which outputs are automatic and which are human-reviewed, the accuracy claimed, review turnaround and price.

---

## Appendix: where the evidence is weakest

- **No labelled Pitchlens data exists.** Every threshold marked [derived] in section 3 is a starting value.
- **Ball model:** WASB's soccer F1 is unverified and not comparable with Pitchlens metrics. How well it transfers zero-shot to night 5-a-side at 360p/240p is unknown.
- **Event papers:** the thresholds and scores of Vidal-Codina, Link & Hoernig, Bischofberger, Mills and Lang come from papers only and are unverified.
- **New engineering:** the ball Viterbi decode and the team-level possession HMM have no published soccer result for this setting.
- **Calibration accuracy is simulated only** (flat pitch, correct labels, known dimensions, distortion centred in the image). The first real check is SoccerTrack v1/v2 downscaled, then labelled Tekkerz frames.
- **Tracking and team:** the detailed findings and fact-check did not arrive. The team-classification targets are unmeasured at 20 px.
- **Unresolved licences:** ISSIA, SoccerTrack v1 data, SoccerNet-Tracking, and AGPL applied to trained weights.
- **Competitors:** no research received.