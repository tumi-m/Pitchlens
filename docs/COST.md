# Cost per match (Modal GPU)

What one analysis costs, where the money goes, and why the defaults are what
they are. Numbers marked *measured* come from real runs; prices are cited;
everything marked *projected* is an estimate with its basis, to be replaced by
a measurement (see the last section).

## Measured run

The owner's 37:34 match (640x360, 25 fps, broadcast profile, 5 analysed
frames/second, 11,272 frames, pipeline 2.5) on Modal with CUDA, before round 6
(the export records the device as `cuda`, not the card; the worker's default
GPU is the L4), *measured* (`performance` in the owner's export `pitchlens-vision (8).json`):

| Stage | Seconds | Share | Runs on |
| --- | ---: | ---: | --- |
| Player model (YOLOv8x, 1280x736, FP16, batch 8) | 354.3 | 36% | GPU, plus host preparation |
| Ball model (YOLOv8x, 2 tiles of 640, FP16) | 305.1 | 31% | GPU, plus host preparation |
| Camera motion | 54.5 | 6% | CPU |
| Setup (model load, CUDA, 24 kit-colour frames) | 14.7 | 1% | both |
| Decode | 12.7 | 1% | CPU |
| Ball recovery | 10.3 | 1% | CPU |
| Everything else (tracking, pitch colour, ball candidates, metrics, hashing) | 237.6 | 24% | CPU |
| **Engine total** | **989.2** | | |

The 840.1 s quoted in [CORE-WORKFLOW.md](CORE-WORKFLOW.md) came from an
earlier export of the same match (`pitchlens-vision (6).json`); this page uses
the 989.2 s run.

The model-stage seconds are wall time around each model call. They include
host-side work, letterboxing and stacking the frames (about 45 ms per player
batch, so roughly 64 s over the 1,412 player calls), which does not get faster
on a faster card. The two stages reach about 19 TFLOPS (YOLOv8x is 258 GFLOPs
at 640x640: 593 GFLOPs per player frame, 516 per ball frame), about 16% of the
L4's dense FP16 tensor peak (121 TFLOPS). The rest, about 305 s of per-frame
CPU work and about 25 s of setup and ball recovery, ran while the GPU waited:
before round 6 the engine ran these stages one after the other.

Not in the engine total (billed, and recorded per run since round 9; see the
last section): container start
and imports, copying weights from the volume, downloading the video from the
worker, compressing the result. Estimated at 20 s for a 90 MB 360p file
(imports 3.3 s and the yolo11s load 1.4 s *measured* on a CPU here; the
worker's video endpoint served 150-330 MiB/s locally, *measured*, so Railway's
network sets the real download speed).

## Prices

Modal list prices per second, from modal.com/pricing as copied by third-party
price trackers in 2026. They were not re-verified for this page: modal.com was
unreachable from the build machine on 2026-10-14. Check the pricing page
before budgeting.

| GPU | $/s | $/h |
| --- | ---: | ---: |
| T4 | 0.000164 | 0.59 |
| L4 | 0.000222 | 0.80 |
| A10 (`VISION_MODAL_GPU=A10G`) | 0.000306 | 1.10 |
| L40S | 0.000542 | 1.95 |
| A100 40 GB | 0.000583 | 2.10 |
| A100 80 GB | 0.000694 | 2.50 |
| H100 | 0.001097 | 3.95 |

On top of the GPU: CPU at $0.0000131 per physical core-second and memory at
$0.00000222 per GiB-second (same source), each billed on whichever is higher,
the reservation (default 0.125 cores, 128 MiB) or actual use. The model below
assumes 1.5 cores and 4 GiB (not measured on Modal). Railway charges $0.05 per
GB of egress for the video the GPU downloads (railway.com/pricing, same
caveat).

## Cost per 40-minute match (projected)

Every figure in this section is *projected*. Each one is scaled from the
measured run (x1.065 for 40 minutes) and includes CPU, memory, the 20 s
overhead and egress. Wall time = GPU-side seconds (device work ÷ relative
speed, plus host preparation) + CPU seconds, or the longer of the two when
they overlap, + overhead + download; cost = wall time x (GPU + 4 GiB memory)
+ busy seconds x 1.5 cores x the CPU price + egress. Speed relative to L4
comes from MLPerf Inference ResNet-50 Offline,
one GPU, TensorRT (github.com/mlcommons inference_results): T4 6,036 (v0.7),
L4 13,158 (v3.0), A10 13,805 (v1.1), A100-PCIe-80GB 39,152 (v3.0), H100-PCIe
56,635 (v3.0). L40S uses NVIDIA's dense FP16 tensor ratio to L4 (3.0x). Only
the device part of the model stages is scaled; the host preparation and the
CPU stages stay the same on every card.

These ratios are for a compute-bound TensorRT benchmark. The engine runs eager
FP16 at about 16% of the L4's peak, where memory bandwidth may matter more:
A10 (600 GB/s) and L40S (864 GB/s) against L4 (300 GB/s) may beat L4 by more
than the table assumes. Treat "L4 is cheapest" as an estimate until a GPU A/B
on real footage exists.

360p (the owner's footage), 5 frames/second. "Pipeline off" is the round-6
pitch-colour saving alone, as on a worker with `VISION_PIPELINE=0`; "Round 6"
adds the overlap of inference with tracking, which is on by default with CUDA
([E21](market-ready/EXPERIMENTS.md)). The other round-6 CPU savings are not
modelled.

| GPU | Before round 6 | Pipeline off | Round 6 | Basis |
| --- | ---: | ---: | ---: | --- |
| T4 | $0.36 | $0.35 | $0.29 | projected |
| **L4** | **$0.27** | **$0.26** | **$0.19** | projected (from the measured L4 run) |
| A10 | $0.35 | $0.34 | $0.24 | projected |
| L40S | $0.38 | $0.35 | $0.20 | projected |
| A100 40 GB | $0.40 | $0.38 | $0.21 | projected |
| H100 | $0.67 | $0.61 | $0.38 | projected |

Engine time on the GPU alone: the L4 goes from about $0.234 (measured run,
scaled) to about $0.16 (projected) per 40-minute match, the owner's match
taking about 685-700 s instead of 989 s.

1080p original, 5 frames/second, 2 GB file (12 ball tiles instead of 2, the
same player tensor; the CPU image steps were *measured* here at 3.9x the 360p
time). Projected: L4 $0.96 before round 6, $0.88 with the pipeline off, $0.71
with round 6; projected: L40S $1.20, $1.02 and $0.62. At 6 frames/second
multiply by 1.2; Detailed (about 8.3-10 frames/second) takes about two-thirds
more GPU time than 6.

## Decisions

- **`VISION_MODAL_GPU=L4` stays the default** (projected, with the bandwidth
  caveat above). T4 is cheaper per hour but, at about 46% of L4's benchmark
  speed, dearer per match. A10 benchmarks about as fast as L4 at 38% more per
  hour. A faster card could pay off only once CPU work no longer sits between
  GPU calls (round 6) and the GPU share is large (1080p). Measure first (last
  section) before changing it.
- **One ephemeral app per job; scale to zero.** Keeping one L4 warm
  (`min_containers=1`) costs $19.18 a day, about 70 matches' worth. At a few
  matches a day, scale-to-zero wins, and the ephemeral app per job already
  gives it: no idle time is billed after a job.
- **Weights cached on the Modal Volume** (`pitchlens-models`). Verified: a
  second cold start makes no request to the worker for weights.

## Changes that keep every statistic (round 6)

| Change | Effect on the owner's match | Output |
| --- | --- | --- |
| Pitch colour from the sampled pixels only (convert every 17th surface pixel, not the whole frame) | 4.9 → 1.05 ms per 360p frame and 33.3 → 7.2 ms per 1080p frame, single-threaded, *measured*: about 43 s, 4-5% of the serial L4 run; almost nothing on L4 once inference is overlapped (the GPU side is the longer one) | Identical (array-equal; `tests/test_engine_speed.py`) |
| Model inference on a helper thread while the previous batch is tracked (on by default with CUDA) | Hides most of the about 305 s of per-frame CPU work behind the GPU (projected; see below) | Identical result.json |

Overlap evidence ([E21](market-ready/EXPERIMENTS.md)), *measured* on a 4-vCPU
host with the owner's GPU timings emulated: 900 frames of the Tryzub match
took 88.9 s serial and 58.7 s overlapped. A replay of 200 real frames that
holds the GIL during the model call, as the real call's host side does, took
20.2 s serial and 15.8 s overlapped. The real L4 gain is projected until one
Modal run confirms it.

## Considered, not adopted

- **TensorRT.** Ultralytics 8.4.152 exports TensorRT engines. The pinned
  stack is CUDA 13 (torch 2.14 cu130, onnxruntime-gpu 1.30, whose CUDA
  provider links libcublas.so.13 and libcudnn.so.9, the libraries torch
  ships). TensorRT 10.x needs `tensorrt-cu13`. TensorRT 11 drops the FP16
  builder flag, so FP16 under TensorRT 11 needs NVIDIA ModelOpt. Engines are
  tied to the GPU model and the TensorRT version: they would be built once per
  (model hash, GPU, TensorRT version, image size, batch) and cached in the
  `pitchlens-models` volume. The model stages run at about 16% of L4 peak, so
  there is headroom, but this needs a GPU A/B showing the speed-up and
  detections within noise on held-out footage first.
- **Splitting a match across GPUs.** Four chunks would cut the wait from about
  16 to about 5 minutes but cost about 11% more (projected): each chunk pays
  start-up, the weight copy, kit-colour setup and its own download. Player
  tracks, the ball track, camera chains and possession spells would also break
  at every chunk edge, with accuracy there unmeasured.
- **A deployed (always-registered) Modal app.** It would save the per-job app
  creation on the worker (not billed) and allow memory snapshots, but needs a
  separate deploy step, so the GPU code could drift from the worker's. Today's
  ephemeral app always runs the worker's own code.
- **Caching videos on Modal.** Footage would then live outside the worker's
  7-day retention and outside DELETE. Each run downloads the video from the
  worker instead.
- **HTTP Range reads for 20-second sections.** No saving on the owner's
  footage: a full run_video section read over HTTP sent 122.8 MiB in 33
  requests for the 85.5 MiB file, *measured*. Range reads could help only for
  large originals (unmeasured).
- **Gzip level 6 for the result.** 0.5 s instead of 1.6 s for a 14 MB result,
  *measured*. Too small to matter.

## Measuring real cost per job

Since round 9 every GPU run records what it cost (E27). Analyses, re-runs,
sections and ball-model training all count, including runs that failed, timed
out, were cancelled or handed over to the CPU; a run Modal refused before the
call counts $0.

- **What is recorded.** The worker times each run with a monotonic clock from
  the Modal call until the ephemeral app has stopped (`wallSeconds` to the
  last item, `appExitSeconds` to the exit, `firstItemSeconds`). The GPU
  function reports its own phases on its last item, never in the result:
  container uptime at entry, imports, weight copy, video download (seconds and
  bytes), engine, result compression, CPU seconds, peak memory, its total
  seconds and the card's name. The record also carries the Modal app id.
- **Where.** The job's `status.json` (`gpuRuns`, the last five; operator only,
  stripped from the owner's view), `gpu-spend.json` in the data folder (every
  run of the last 8 days, written when the run starts, refreshed every 30 s,
  closed with the record; a run open when the worker died is closed at its
  last heartbeat plus 300 s) and `/ops` → `gpuSpend` (budget, last 24 hours,
  per-day totals for 7 days, latest runs). The log gets one line per run:
  `event=gpu-run gpu=… seconds=… usd=… outcome=…`. Training also puts
  `gpuSeconds` and `gpuUsd` in the training state shown on `/ops`.
- **The estimate.** Billed seconds = from the call to the app's exit, less
  queueing (the wait for the first item beyond the container's own uptime and
  time to its first item). Dollars = seconds × the card's price (the table
  above) + CPU at $0.0000131 per physical core-second, counted as the GPU's
  vCPU-seconds ÷ 2 but at least 0.125 cores, + memory at $0.00000222 per
  GiB-second of peak use + $0.05 per GB of video downloaded (Railway egress).
  Without the GPU's report (a crash before its last item, training), it
  assumes 1.5 cores and 4 GiB and counts the whole wait. GPU strings follow
  Modal's rules (any case, an optional `:count`, a trailing `!`); a card the
  table does not know is priced as the dearest one it does (`priceKnown:
  false`). `VISION_GPU_PRICES` overrides the table in dollars per hour, for
  example `L4=0.80,H100=3.95`.
- **Budget.** `VISION_DAILY_GPU_BUDGET_USD` refuses new GPU starts once the
  last 24 hours' estimate reaches it ([DEPLOY-VISION.md](DEPLOY-VISION.md)).

These are estimates until checked against Modal's invoice. Each Modal app
is tagged `pitchlens-job` (the job id, or `ball-model` for training) and
`pitchlens-kind` (`analysis` or `training`). To reconcile, run `modal billing report --tag-names
pitchlens-job` (`--for "last month"` and similar ranges work), or from Python
`modal.Workspace.from_context().billing.report(start=..., resolution="h",
tag_names=["pitchlens-job"])` (Modal SDK 1.5.5), and match its rows to the
recorded runs by tag or by `modalAppId`. `billing.rates()` on the same
workspace object returns the rates Modal actually charges, to check the price
table above. The first real L4 full match measured this way replaces the
projections on this page.
