# Decisions

Each entry: the decision, what else was considered, and why. Newest first.

## 2026-09-28 · South Africa: prepare Paystack in ZAR, sandbox only

The owner confirmed South Africa and approved Paystack preparation without an
existing merchant account. Implement hosted test checkout and durable, idempotent
sandbox settlement now; decline live keys until account identity, entitlements,
refunds and launch operations are implemented. No selling price is chosen from a
test amount. See PAYSTACK-SETUP.md for the flow and explicit limits.

The later commercial review also supersedes the bootstrap-interval presentation
below: report bounds now show sensitivity to unobserved time, not an accuracy
confidence interval. Earlier research decisions remain here as historical context.

## 2026-09-28 · How we used the "market-ready" implementation brief

The brief (prepared by another assistant) was treated as input, not a script.

**What it gets right, and we adopted**
- Measure before claiming. Every stat now carries a coverage figure, a review
  status, and is shown as "—" (not measured) rather than 0 when evidence is
  missing. An evaluation harness (`backend/app/evaluation`) scores ball,
  events, possession and calibration against labels, and turns a reviewer's
  decisions into precision/recall.
- Sell an assisted-review product first. Automatic events are proposals;
  goals only count once a person confirms them; the review queue is built
  into the report. Competitors do the same: Veo asks users to fix AI events,
  Hudl Assist and Spiideo "AutoData Advanced" use human analysts.
- Narrow operating domain: one continuous wide view of a known pitch.
  Fixed venue cameras (PUSHIT at Tekkerz) are the best case.
- Licensing risk is real (see Licences below).

**Where we disagreed, and why**
- *Sequencing.* The brief front-loads months of data acquisition (40-60
  rights-cleared matches, 30 held-out matches across 5 venues, 100 examples
  per event type) and enterprise infrastructure (tenant isolation, credit
  ledger, SSRF hardening) before anything user-visible changes. The owner's
  goal is a working Sofascore-style report now. We built the report,
  calibration, events and review first, with measurement built in, so that
  every reviewed match becomes evaluation data. The held-out gates stay as
  the bar for calling a stat "automatic".
- *Thresholds.* Targets such as 98% ball precision and 95% pass precision
  are higher than any published result for single-camera amateur footage
  (rule-based pass F1 0.71 and shot F1 0.65 on independent data, Bischofberger
  et al. 2024). We keep them as aspirations in RELEASE-GATES.md but gate the
  first release on the assisted-review targets.
- *Ledgers.* Five documents are kept, but short. STATUS.md is the one to
  read.
- *Payments/accounts.* Not built. They need the owner's business identity,
  jurisdiction and payment account; the shared access code remains the pilot
  gate. Listed as an owner dependency in STATUS.md.

## 2026-09-28 · Pitch calibration: user clicks now, automatic later

- Chosen: the user clicks 4+ named landmarks and optional points along
  straight lines on one paused frame; we fit a homography plus one radial
  distortion term (division model) by minimising image-pixel error, grid
  searching the distortion (strong fisheye included), keeping it only if
  held-out clicks agree better (leave-one-out). Carried through camera motion
  and re-aligned to painted lines every second (staged: shift search,
  pan/tilt/zoom, regularised homography, plausibility checks).
- Considered: automatic keypoint models (NBJW 2024, PnLCalib, TVCalib,
  Roboflow's 32-point model). All use full-size 105x68 m templates and assume
  little distortion; none has keypoints for 5-a-side cages. Revisit by
  training a small-sided keypoint model on frames from calibrated matches.
- Why lines as well as points: low venue cameras rarely see the near
  corners; points on lines keep the fit well-posed and make distortion
  observable (research simulation: ignoring a 120-150° fisheye costs 0.8-2.3 m
  median error; fitting it jointly brings calibration error to ~0.1 m).
- Quality is graded in pixels scaled to 360p: ≤2 px good, ≤4 px check,
  >4 px refused. Implied field of view and camera height are sanity checks.

## 2026-09-28 · Possession is team possession, not control time

- Chosen: possession runs from one team's won ball to the other team's,
  including the flight of passes, excluding dead-ball time; the share is shown
  only when possession can be followed for at least 60% of in-play time, with
  a bootstrap 95% interval.
- Why: player-on-ball control is only ~18 of ~56 minutes of team possession
  per match (Link & Hoernig 2017), and it is exactly the part a 3-6 px ball at
  5 fps loses most. A control-time share was biased and noisy.
- Possession zone 1.1 m + 1.5 × calibration error (cap 2 m); duel zone
  +0.5 m (Vidal-Codina et al. 2022 use 0.5-1.0 m on elite tracking; databallpy
  defaults to 1.5 m). The ball must move with the player (relative speed
  ≤ 4 m/s + 2σ), rejecting balls rolling past a player.

## 2026-09-28 · Shots judged by outcome; goals never automatic

- One camera at 5-6 fps cannot observe ball height, so "on target" is decided
  by what follows: keeper-area gain within 1.5 s of a ball heading between the
  posts = save (on target); an outfield player ≥1.5 m in front of the line =
  block; ball seen ≥0.2 m beyond the line between the posts = goal candidate.
- Goals need a reviewer. A centre-spot restart (both teams in their own
  half, a player on the spot, after a stoppage) corroborates a preceding shot
  or flags an unseen goal. Research: shots are the weakest automatic event
  even on better data (Mills et al. 2026; Bischofberger et al. 2024).

## 2026-09-28 · Rules settled by the adversarial code review

- A reviewer's decision records the moment they judged (type, time, team,
  outcome). After re-analysis it re-attaches to the matching event, and where
  the new analysis reads that moment differently the reviewer's reading
  stands. A stale "accept" can therefore never confirm a goal nobody saw.
- Unmeasured is null, never zero: passes, tackles, interceptions and
  possessions need kit labels; shots need an attacking direction (automatic
  or set by the reviewer). Reviewer-logged events still count.
- A fixed camera is judged on its whole motion chain (no 5-second window may
  drift 8 px or more), and a moment where motion was not measured is only
  bridged when the player tracks show the camera held still. Otherwise the
  view is found again from the painted lines, or that stretch stays
  unmapped. Thresholds come from synthetic checks; confirm them on the first
  real fixed-camera footage (the `static` flag in calibration.json).
- Pitch setup uses an analysed frame exactly (to within one video frame);
  the engine stores each sample's source frame number so the same frame is
  read again on variable-frame-rate video.
- With a role-aware detector, an outfield defender winning the ball after a
  shot is a block wherever it happens; the keeper-area rule is the fallback.

## 2026-09-28 · Not adopted yet (with reasons)

- Multi-frame heatmap ball detector (WASB, TrackNetV3): strongest published
  approach for tiny balls (MIT code; WASB ships soccer weights trained on
  ISSIA-CNR, whose data terms are unclear). Needs GPU work on Modal and
  labelled Pitchlens clips to validate; next ball experiment.
- xG: no validated small-sided model exists; 11v11 coefficients do not
  transfer to 3.66 × 1.22 m goals.
- Jersey-number recognition: impossible at 20-25 px players (a number is
  3-4 px tall).

## Licences (must be resolved before charging)

- Ultralytics YOLO (runtime and the Roboflow football weights) is AGPL-3.0.
  Serving it over a network to paying users requires releasing the service
  source under AGPL or buying an Ultralytics Enterprise licence.
- Avoid in the product: boxmot (AGPL), StrongSORT and sn-gamestate (GPL-3.0),
  PRTReID (Hippocratic), Deep-EIoU (no licence file). SoccerNet data is NDA /
  non-commercial; SportsMOT is CC BY-NC.
- Usable: ByteTrack, BoT-SORT, OC-SORT (MIT), roboflow/sports and trackers
  (MIT/Apache-2.0), TrackEval (MIT), WASB/TrackNetV3 code (MIT).
