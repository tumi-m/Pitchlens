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
