# Core workflow first

Priority reset, 3 October 2026: no further payment, pricing, account or marketing
features until the video workflow is useful. Existing sandbox billing stays
disabled. This document tracks engineering work, not a commercial-ready claim.

## Acceptance order

1. **Upload:** reserve once, transfer every byte, retry temporary connection
   failures, cancel cleanly, and recover without losing the selected file.
2. **Real inference:** decode the actual video and detect/track players and the
   ball. Completion alone is not success: measure the observations and inspect
   overlays. A section with no ball track cannot support automatic ball analytics.
3. **Usable report:** open the real result, seek/play the original footage with
   aligned overlays, review events and export measured data. Unknown stays unknown.
4. **Repeatability:** evaluate labelled sections from the supplied footage and
   held-out matches, recording precision, recall, runtime and failure cases before
   changing defaults or claiming improvement.

## Reproduced and fixed

- An unavailable first health response previously disabled upload until a page
  reload. The page now retries startup failures, provides a manual reconnect
  button, bounds the health request, and retains the selected video.
- A reservation reply lost after the worker accepted it previously stranded the
  client behind “another video” until timeout. Owner-scoped persisted request IDs
  now make reservation retries return the same upload. Conflicting options and
  stopped uploads are rejected instead of silently duplicating work.
- A report with zero retained ball observations now explicitly states that ball
  analytics are unavailable and links to the detection overlays.

These are reproduced reliability gaps. The user's original upload error message
has not been supplied, so do not claim its precise cause has been established.

## Real video check, 3 October

Input: the supplied `videoplayback.mp4`, 70,178,983 bytes, 640×360 at 25 fps,
37:34 duration. Transferred the complete file through the actual Next.js proxy
and FastAPI worker. Ran real local CPU inference on 02:00–02:20 with the
broadcast profile, requested 3 fps. No inference mock was used.

Observed: 56 sampled frames, players in 56, 26 track fragments, **zero retained
ball frames**. Result and analytics endpoints returned 200; video range playback
returned 206 with the requested bytes. An isolated browser opened the real report,
decoded the video, and sought to 02:00 using the detection-inspection button with
no page errors. CPU processing took 69.9 seconds for those 20 seconds of footage.
This verifies the mechanics, not accuracy
or full-match performance. Track fragments are not unique player identities.

Ball-model comparison on ten sampled images from that section:

- Existing 480-pixel tiling: 18.75 seconds for ten images.
- 240-pixel tiling: 54.18 seconds; changed candidates mainly remained stationary
  background objects. No validated ball-recall improvement was established.
- Whole-frame ONNX: 1.76 seconds; candidates were also predominantly stationary.

No detector threshold or model default was changed based on candidate count.
Next detection work: establish visible-ball labels on representative near/far,
moving/occluded and night sections; compare candidate recall and false positives,
then tune or train against those labels. Keep evaluation sections separate.

## Checks

Backend upload tests cover duplicate reservations, owner isolation, changed
options, cancellation and a fresh restart. Browser tests cover reconnecting while
retaining the chosen file, automatic startup retries, retrying the same reservation
ID, and warning when completed inference retained no ball track.

Normal checks: `pytest tests`, frontend `test:unit`, `type-check`, `lint`, `build`,
and Playwright. Real video inference remains a separate measured smoke run;
mocked browser tests are not evidence of computer-vision accuracy.

## Fixed-speck investigation, 4 October

The supplied `pitchlens-vision (5).json` covers only 00:00–00:20. Its 90
observed ball frames mostly follow a bright speck around source pixel (77, 52),
above the pitch. The advertised coverage was not ball accuracy. The first two
seconds also include a hand obstructing the camera.

Pipeline 2.4 rejects persistent unattended neural candidates before selecting
the ball path. It tests both camera-compensated and image-fixed positions, so
optical-flow drift no longer protects an unmoving background object. Weak or
off-pitch candidates need three seconds of evidence; confident on-pitch
candidates keep the existing conservative twenty-second allowance. Moving
airborne balls, scene changes, and attended set pieces (including keepers with
an unknown kit) are preserved. Retained observations are then reassociated.

Actual re-inference of the exact opening section on CPU: 100 sampled frames,
129 static candidate observations rejected, **zero retained positions at the
known sky speck**. The result contains 32 observed ball frames plus six inferred
positions. Inspection also found residual background mistakes; these counts
are not 38 correct detections. The engine found 1.4 seconds of possible control
and no event candidates. That is still insufficient for full-match statistics.
Runtime was 133.4 seconds on CPU. The exported JSON and inspection montage are
local diagnostic artifacts, not production match data or a held-out benchmark.

The report now calls its percentage “Ball position coverage”, explains that
coverage is not verified accuracy, and warns when ball detections establish no
control. Saved-video actions create a separate 20-second section or a full-match
report without uploading again. They retain ownership, leave the previous report
and reviews intact, and reuse the same request ID on transient retries. Expired
footage, a busy worker, invalid sections and failed scheduling return errors.

Local startup now selects CUDA or Apple MPS when available, with an explicit
`VISION_DEVICE` override respected. Kit-colour samples are batched. A prior
measured run on 02:00–02:20 fell from 131.8 seconds on CPU to 37.6 seconds on
MPS with batching. This is a hardware-and-pipeline comparison, not an isolated
algorithm speedup or a hosted CUDA performance claim. Player model calls on
the MPS run fell from 74 to 31. An intermediate 05:00–05:20 run preserved all
eight retrospective visible-ball labels within three pixels. A final CPU rerun
of 05:00–05:20, including the off-pitch rejection, also preserved all eight
labels (128.5 seconds, 39 observed plus three inferred ball positions). Eight
examples are insufficient to establish general recall or precision.

Also evaluated the official [WASB multi-frame soccer detector](https://github.com/nttcom/WASB-SBDT)
outside the repository. Neither the tested whole-frame nor tiled samples
produced candidates above its tested 0.5 threshold on this footage. It was not
adopted, and no detector threshold was lowered to inflate coverage.

Remaining detection work: label representative visible balls and hard negatives
on separate training and evaluation sections, improve ball recall without
restoring these distractors, and validate end-to-end events on held-out matches.
The current system must not be described as solved or commercially validated.

## Full-match export and pipeline 2.5

The next supplied export, `pitchlens-vision (6).json`, contains the full 37:34
video, not a diagnostic. CUDA processing took 840.1 seconds, with 2,847 observed
ball frames out of 11,272 (25.3%) and 2,125 player track fragments. Its legacy
raw `metrics.possessionCoverage` is 5.5%. The separate report analytics algorithm
produces 10.2% control coverage and 48.4% estimated possession coverage, still
below its display threshold, with 290 unconfirmed event candidates. These
different measures must not be compared as if they were detection accuracy.

Two further failures were reproduced:

- Role classifications fluctuate within tracked people. Treating each referee
  or goalkeeper classification as definitive dropped a known player's team
  assignment. A colour-aware association cost now helps prevent an unknown
  role from stealing an opposing kit's ID. A confidence-weighted role consensus
  needs six observations and a 75% majority before correcting a role; the
  original is retained in `detectedRole`. Ambiguous tracks stay unresolved.
  This is short-term tracking, not reliable full-match player identification.
- At 05:01.4 and 05:01.6 the tracker selected a pitch object while the visible
  ball moved across the frame. Nearby strong detections now resolve existing
  weak candidates backwards and forwards over at most 0.6 seconds. Motion
  candidates need bracketing neural observations. This pass never inserts a
  position without a candidate, never overwrites a strong observation, and
  stops at cuts or missing camera motion. It adds no model inference calls.

Real re-inference of 05:00–05:20 matches 10 of the 11 diagnostic centres within
three pixels, up from 8 in the user's export; both labelled pitch-object errors
are gone. The remaining ball at 05:05.6 is still missed. All eight earlier
positive labels remain matched. A separate 10:00–10:20 run adds five temporal
promotions; native-pixel crops of those five were visually inspected and show
the ball. This is a small diagnostic check, **not held-out match accuracy**.
The two final CPU runs executed concurrently and took about 245–247 seconds
each; those timings are not a serial CPU or hosted GPU performance benchmark.
One intermediate GPU run completed, but automatic approval-review quota blocked
the later GPU comparison. Final integrated checks therefore used CPU.

128-, 192- and 256-pixel crop searches with the existing ball and player models
were also tried. They did not consistently recover the three inspected misses
and introduced background candidates. No additional crop-inference pass or
lower detector threshold was adopted.

The versioned diagnostic labels live in
`backend/tests/fixtures/vision/videoplayback-diagnostic-labels.json`.
`backend/scripts/evaluate_ball_tracks.py` separately reports matched observed
positions, inferred positions, candidate availability and hits in labelled
background regions. Keep these debugging labels out of training and do not
describe their hit rate as a general accuracy score. A larger independent set
of visible balls, occlusions and hard negatives is still required before model
fine-tuning or a full-match quality claim.
