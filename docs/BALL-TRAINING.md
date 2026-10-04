# Teach Pitchlens the ball

The ball is the weakest part of automatic analysis on amateur footage: it is a
few pixels across, it looks like a boot or a sock, and the public models were
not trained on your venue. Compute is cheap (a full match is well under a
dollar of GPU time); **labelled frames from your own footage are what is
missing**. This is the loop that collects them and turns them into a better
ball finder.

## 1. Label (about 10 minutes per match)

Open a finished match → **Ball accuracy and training → Label the ball**.
Pitchlens shows 150 frames spread over the whole match, with its guess in
yellow.

| Key | Action |
| --- | --- |
| Enter | the guess is right |
| click, then click again in the zoom | the ball is here (exact position) |
| N | the ball is not visible (behind a player, out of shot) |
| → / S | skip |

Each answer immediately updates the measured accuracy for that match:

- **Ball found when visible**: of the frames where you saw the ball, how often
  Pitchlens had it within 3 px (at 360p; scaled with resolution).
- **Ball positions that were right**: of the positions Pitchlens reported on
  labelled frames, how many were the ball.

These are the first real accuracy numbers for your footage, with a 95% range.
They count toward the release gate "visible-ball recall ≥ 90%"
(market-ready/RELEASE-GATES.md).

Label at least two matches (ideally different days and lighting) so one can be
held out for checking.

## 2. Train (about $1 of GPU, 15-30 minutes)

Training uses every labelled match on the worker. With Modal configured on the
worker it runs on a Modal GPU (`VISION_MODAL_TRAIN_GPU`, default L4);
otherwise on the worker itself (slow on CPU).

```bash
curl -X POST "$VISION_SERVICE_URL/ball-model/train" \
  -H "Authorization: Bearer $VISION_SERVICE_TOKEN" \
  -H "content-type: application/json" -d '{"epochs": 60}'
# progress and results
curl "$VISION_SERVICE_URL/ball-model" -H "Authorization: Bearer $VISION_SERVICE_TOKEN"
```

What happens:

1. Every labelled frame is cut into exactly the tiles the detector sees, with
   a box on the ball; tiles without the ball teach it what is *not* a ball.
2. Matches are split for training and checking **by match** (with only one
   labelled match: the last 30% of its frames, reported as a weaker check).
3. The current ball model is fine-tuned on the GPU.
4. Old and new models are both scored on the held-out frames. The new one is
   switched on **only** if it finds at least 3 points more balls without
   losing more than 2 points of precision. Every run, kept or not, is recorded
   in `.vision/models/ball-model.json` with both scores.

The new weights are named by their hash (`ball-<hash>.pt`), stored on the
worker volume, and copied to the Modal GPU (hash-checked) for later analyses.
Re-run an analysis from the saved footage to apply it to an older match.

To go back to the stock model, set `"active": null` in
`.vision/models/ball-model.json`, or set `VISION_BALL_WEIGHTS` to a weights
file.

## 3. Also free: upload the original recording

The engine detects at the video's own resolution (a 1080p frame is searched
in 12 tiles, a 360p frame in 2 upscaled ones). A ball that is 4 px in a
YouTube 360p copy is about 12 px in the original 1080p recording. Upload the
camera's original file whenever possible.

## Limits

- Fine-tuned on one venue, the model is validated for that venue only.
- Frame-level recall here is a conservative proxy; the engine's tracker can
  recover more balls between detections, and the per-match accuracy above
  measures the full pipeline.
- Labels are the reviewer's judgement; a ball hidden behind a player should be
  marked "not visible", not guessed.
