# Low-resolution football analysis: implementation and validation plan

The immediate target is useful, measurable analysis of real 360p/240p footage, with
bounded experiments before a full match. Resolution alone is not an eligibility rule.
A five-pixel ball, glare, compression, occlusion, fisheye distortion and a low camera
are different problems and need separate measurements.

## Evidence from the reported failure

The supplied `videoplayback.mp4` is 640×360, 25 fps, 37:34, filmed at night from a
low wide-angle camera. The supplied export analysed 11,272 frames using the two
football models. It reports 1,674 observed-ball frames (14.85%), 1.5% stable
ball/player proximity, 5,347 short player tracks, and no pass candidates. None of
these is a labelled accuracy measurement. Only 2,254 frames contain any ball
candidate: the detector itself is a major bottleneck, before temporal tracking.
The export also contains almost identical person boxes with different role labels.

## Implemented in this change

1. **Inspect before upload.** Read dimensions and duration, decode three preview
   frames locally, reject undecodable/unsupported files before reserving a server,
   and explain low-resolution limitations without rejecting 360p/240p video.
2. **Bound the first run.** File uploads default to a 20-second diagnostic. Select
   a preview or enter a source timestamp. Keep original video timestamps in boxes,
   event links, timelines and possession charts. The entire file is still uploaded;
   browser-side trimming/transcoding is not implemented. YouTube submission retains
   its existing behaviour and does not claim to have a browser preflight.
3. **Remove avoidable work.** Reuse calibration detections on the exact same sampled
   frames; cap calibration samples on short videos. Keep existing GPU offload and
   forward all diagnostic settings to Modal. Report model/decode/motion timings and
   actual device. Stream file checksums rather than allocating the whole video.
4. **Improve robustness.** Keep detections if kit clustering fails, with unknown
   teams and withheld possession. Suppress duplicate player/referee/keeper boxes
   across classes. Correct velocity updates after player-detection gaps.
5. **Offer a cheaper model combination.** The experimental `small-ball` profile
   pairs the lightweight general person detector with the tiled football ball
   model. This avoids the large broadcast player model and its role assumptions;
   it is not a newly trained ball model and may miss distant players.
6. **Test focused search.** Optional adaptive ball search uses the previous observed
   ball, camera compensation and fixed-scale crops. It sweeps the whole image at
   least every 0.5 seconds and reacquires on misses, ambiguous detections, cuts or
   uncertain camera motion. It never outputs a predicted ball as an observation.
   Exhaustive search remains the default: a smaller search can hide distractors.
7. **Make evaluation reproducible.** `scripts/benchmark_vision.py` measures a bounded
   source section. `scripts/benchmark_resolution.py` compares both ball models on
   labelled centres at 360p and 240p, recording false positives as well as hits.

## Early measurements and their limits

On an eight-second existing indoor clip at six requested samples/sec, removing
repeated calibration inference changed CPU wall time from 26.5s to 19.4s in one
sequential run; observed-ball frames stayed 8/48. Warm-up and system load affect
this comparison: it is not a guaranteed 27% full-match improvement.

On an eight-second broadcast excerpt, focused search used 121 tile calls rather
than 160. Observed-ball coverage remained 31/40; ball inference took 29.0s rather
than 44.4s. This tiny excerpt cannot establish accuracy equivalence or general speed.

On nine previously labelled stills, the tiled football ball detector matched 7/9
centres at 360p and 8/9 at 240p, with three unmatched detections at each resolution.
The baseline matched 2/9 and 0/9, respectively, with one unmatched detection each.
These are small diagnostic counts, NOT percentages of real-world accuracy.
Downsampled stills are not native compressed 240p video; broadcast sources may
also overlap training data. The surprising 240p result illustrates small-sample
variance and scale sensitivity, not evidence that reducing resolution improves vision.

## Next accuracy work: gates, not promises

1. Label multiple independent matches, including this night/wide-angle domain.
   Include visible-ball centres, occluded/ambiguous frames, hard negatives (lines,
   lamps, spectators, spare balls), player boxes and identity segments. Split by
   match/camera **before** tuning; do not split adjacent frames between train/test.
2. Compare crop sizes and lightweight detectors at fixed inference budgets. Fine-tune
   with realistic H.264 compression, downsampling, blur, glare and lens-distortion
   augmentation. Keep an untouched native-240p/native-360p test set.
3. Add a genuinely multi-frame ball model (short aligned frame windows), trained
   with moving-ball and hard-negative labels. Compare against single-frame detection
   plus tracking. Temporal integration helps only when motion and occlusion are
   handled: simply averaging moving objects can erase the ball.
4. Evaluate player association independently (HOTA/IDF1 and fragmentation), stabilise
   kit classification across tracks, and validate perspective/lens calibration before
   reporting physical distances or speeds. Pixel coordinates cannot provide metres.
5. Run the same pinned models on the actual deployed GPU. Compare FP32/FP16, tile
   batching, runtime exports and warm/cold starts. Require repeatable latency and
   cost per video minute alongside accuracy, and cancellation/resource tests.
6. Promote a model/search policy only if held-out ball precision/recall/localisation
   and event precision remain acceptable at the agreed compute budget. Report
   confidence intervals by match, not millions of correlated adjacent frames.
   Leave unsupported possessions/passes unknown rather than filling gaps with guesses.

## Why astronomical imaging is a useful but limited analogy

Repeated observations, noise modelling, calibration and motion-aware integration
are useful ideas. Upscaling a compressed football video does not create new measured
photons. Learned super-resolution can invent plausible texture; keep it out of
measurement unless a blinded held-out evaluation proves a benefit. A moving,
occluded ball under perspective distortion is not the same signal as a static
source accumulated over many exposures.

## Reproduce locally

From `backend`, using the configured vision environment:

```sh
python scripts/benchmark_vision.py /path/to/video.mp4 --start 300 --seconds 20 --fps 6 --profile small-ball --search adaptive --output /tmp/diagnostic.json
python scripts/benchmark_resolution.py /path/to/annotations.json --output /tmp/resolution.json
```

Models are the existing verified downloads. No uploaded private video was sent to
Roboflow or a new provider for these local experiments. Deployment must update the
Python worker as well as the Vercel frontend; otherwise old workers ignore new options.

References: [SAHI paper](https://arxiv.org/abs/2202.06934),
[Ultralytics inference controls](https://docs.ultralytics.com/modes/predict/).
These motivate techniques, not Pitchlens-specific performance claims.

## Check on the newly supplied match

For source 05:00–05:08 of `videoplayback.mp4`, 40 sampled frames: the experimental
lightweight-player/adaptive-ball run took 37.5s on this CPU and the updated
broadcast/exhaustive run took 83.8s. Both emitted 25 observed-ball frames. The
user's earlier GPU export has 23 observed-ball frames in the same interval.
Different precision, calibration and tracking state prevent interpreting this as
an accuracy gain. These timings compare two complete configurations, not just the
search algorithm; they do not predict deployed GPU latency. No verified passes
were recovered. Use the saved overlays to inspect errors before a full-match run.
