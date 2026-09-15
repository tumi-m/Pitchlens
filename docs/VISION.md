# Local computer vision

The default upload now runs automatic analysis. The former manual review room remains available as an explicit secondary mode, and existing reviews have an automatic-analysis button.

## Start

Python 3.12, Node 20+, and enough local disk for the source videos are required. The engine is a local companion service, not a Vercel serverless GPU job.

```sh
cd backend
python3.12 -m venv .vision-env
source .vision-env/bin/activate
pip install -r requirements-vision.txt
python scripts/setup_vision.py
python scripts/start_vision.py
```

The setup command downloads official YOLO11s player weights and the football-specific YOLOv9 ONNX ball weights, and verifies both pinned SHA256 checksums. The start command creates a local service token and updates only `VISION_SERVICE_URL` and `VISION_SERVICE_TOKEN` in `frontend/.env.local`, preserving other settings. Never commit that file. Restart Next.js after configuring the worker:

```sh
cd frontend
npm ci
npm run build
npm run start -- --hostname 127.0.0.1
```

Open `/upload`, choose an H.264 MP4, and select **Analyse video automatically**. The worker keeps running if you navigate away. Return through **Your matches → Computer vision analyses**. A stopped worker marks unfinished jobs interrupted; retry by submitting again. One upload/analysis is accepted at a time. Cancellation stops future inference calls, preserving its job record.

This session's installed test environment is `backend/.venv312`. Its model weights and source-video copies are excluded from Git. Use the setup above on another machine.

## What is measured

- YOLO11s player detection and a separate football-trained YOLOv9 ONNX ball detector on regularly sampled video frames. Default: approximately 3 frames/sec, 960-pixel player input and 1280-pixel letterboxed ball input. Inference runs locally; no video/frame is sent to Roboflow or an LLM.
- Green playing-surface filtering, background optical-flow camera-motion compensation, Hungarian short-term track association (new tracks require confidence ≥0.5; weaker detections can sustain existing tracks) and two automatically learned jersey-colour groups. Unknown colours remain unknown. The user maps kit groups to team names.
- Ball detection coverage and player detection coverage across sampled frames.
- Stable ball-to-player proximity time. The nearest foot-region candidate must be sufficiently close relative to player height and unambiguous. At least two consecutive observations are needed. The UI withholds shares below 50% coverage (a conservative display rule, not a validated accuracy threshold). Percentages use this observed time only; missing-ball and ambiguous periods remain explicitly unassigned.
- Unreviewed pass/turnover candidates from temporal ownership transitions. The ball must stay visible across the transfer, and it cannot span a detected camera cut or a gap over 1.2 seconds. A possible pass also requires an already-visible receiver and meaningful ball displacement, rejecting a stationary ID switch.
- Exportable timestamped boxes, confidence, kit groups, track fragments, ball locations, model checksum and derived observations. The report overlays the boxes on the original footage and links candidates back to it.

## Boundaries

This is genuine video inference, but not a validated commercial football event feed. The player detector is a general COCO model; the ball model is trained on amateur football. Neither has been validated on this indoor five-a-side recording. Ball confidence uses a 0.15 threshold with ambiguous competing detections rejected. Confidence scores are not calibrated probabilities. Missed/false ball detections, goalkeeper/referee confusion, sideline players, occlusions and ID switches affect estimates. Coverage is not precision or accuracy. Pregame/dead-ball time is not automatically segmented. The filming perspective and camera panning preclude converting image coordinates into physical distances with a simple scale.

Score, xG, physical speed/distance, calibrated pitch heatmaps and exhaustive pass completion are deliberately unmeasured. They require validated football detections, a temporal event model, pitch calibration maintained through camera movement and held-out labelled footage. A custom trusted local YOLO weight file can be configured with `VISION_MODEL_PATH`; its named classes must include players/person. A compatible one-class, fixed-input YOLO ONNX ball model can be configured with `VISION_BALL_MODEL_PATH`. Set `VISION_DEVICE` to a supported device when available; CPU is the default.

## Storage and access

Videos, status and results live in `backend/.vision/<job-id>/`. Jobs use random IDs, a loopback-only worker and a server-only token. Next.js proxies streaming upload and HTTP Range playback, allows only the local host and restricts proxy paths. Cross-site upload requests are rejected. Files are not sent to Firebase. No automatic retention/deletion is performed; stop the worker and remove unneeded job directories to reclaim disk. This single-user local design must not be exposed as a public multi-user service.

## Reproduce without the browser

```sh
cd backend
source .vision-env/bin/activate
python scripts/analyse_video.py '/path/to/match.mp4' --output /path/to/result.json --fps 3
```

`--seconds 150` performs a bounded diagnostic run and records the analysed duration separately from the video duration. It is not presented as a full-match analysis.

## References

The supplied [Football Analytics library](https://www.dropbox.com/scl/fo/h24qmp5214lb8fjwhvm9j/AJEHxadddbA4MECYYQZJjbs?rlkey=s5ulw1zfk2ee4yf8pfbs4n521&dl=0) includes metric primers, modelling, tools and visualisation references. Its Michael Caley xG reference describes shot location, angle, body part and assist/play context: [original article](https://cartilagefreecaptain.sbnation.com/2015/10/19/9295905/premier-league-projections-and-new-expected-goals). That distinction informed the separation of observed detections from unmeasured advanced metrics.

Implementation references: [Ultralytics YOLO11](https://docs.ultralytics.com/models/yolo11/), [tracking](https://docs.ultralytics.com/modes/track/), [Roboflow sports](https://github.com/roboflow/sports). Ultralytics code/weights use AGPL-3.0 or an enterprise licence; review those terms before commercial redistribution. The project does not redistribute the model weights.

Ball model source: [acatorcini/yolov9-soccer-ball](https://huggingface.co/acatorcini/yolov9-soccer-ball), revision `b30df5abc9f3eda4a9d326d953be12b3541a14b4`, SHA256 `9fd2031e5bced9dff47a48bae8c6809dd56493124ef8924ae28a3ce26a17a441`. Its model card declares AGPL-3.0. Setup downloads the ONNX data file, not repository Python or pickled third-party code. ONNX Runtime telemetry is disabled. CPU is explicitly selected: CoreML acceleration was tested in this sandbox and crashed; it is not enabled in the product.
