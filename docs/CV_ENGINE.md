# Local computer-vision engine

This path turns Pitchlens into video analytics instead of six Roboflow stills plus a template paragraph.

## What it computes

| Output | How | Trust |
| --- | --- | --- |
| Player boxes + IDs | YOLO + ByteTrack (or IoU fallback) | High if the camera is stable |
| Ball box | Same detector, lower confidence | Medium — COCO misses many balls |
| Team label | Jersey HSV clustering, majority vote | Medium — confirm colours |
| Pitch x,y | Homography from ≥4 landmarks | High only when residual is low |
| Possession % | Time nearest player is inside radius while ball is visible | Medium; unknown time excluded |
| Pass / shot / goal | Temporal heuristics with cooldown | Candidates. Confirm on video. |
| Heatmap | Occupancy grid in pitch space | Only if calibrated |

## Run it

```sh
cd backend
poetry install
poetry run python -m app.engine.cli /path/to/match.mp4 --out outputs/match.json --stride 3
poetry run uvicorn app.main:app --port 8080
```

Football weights: `export YOLO_WEIGHTS=/path/to/football.pt`

Then enable **Run local computer vision** on upload, or import `outputs/match.json` in the review room.

See `docs/100X.md` for the product roadmap.
