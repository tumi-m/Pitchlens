"""Fine-tune the ball detector on a Modal GPU (its own app, so it never collides
with an analysis run that the worker starts on the pitchlens-vision app)."""

import os
from pathlib import Path

import modal

from app.vision.modal_app import image

TRAIN_GPU = os.getenv("VISION_MODAL_TRAIN_GPU", "L4")
app = modal.App("pitchlens-train", image=image)


@app.function(gpu=TRAIN_GPU, timeout=2 * 3600, max_containers=1)
def train_ball(dataset_zip: bytes, base_weights: bytes, epochs: int = 60) -> dict:
    """Fine-tune on a labelled dataset; score old and new weights on its held-out test frames.

    Returns {"weights": bytes, "baseline": metrics, "candidate": metrics}. The
    worker decides whether to keep the new weights (balltrain.better).
    """
    import io
    import zipfile

    from app.vision import balltrain
    from app.vision.ball import TiledBallDetector

    root = Path("/tmp/train")
    dataset = root / "dataset"
    dataset.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(dataset_zip)) as archive:
        archive.extractall(dataset)
    # The dataset's data.yaml points at the worker's paths; rewrite for this machine.
    yaml = (dataset / "data.yaml").read_text().splitlines()
    (dataset / "data.yaml").write_text("\n".join([f"path: {dataset}"] + [l for l in yaml if not l.startswith("path:")]) + "\n")
    base = root / "base.pt"
    base.write_bytes(base_weights)
    balltrain.seed_everything(0)
    best = balltrain.train(dataset, base, root / "runs", epochs=epochs, device=0)
    baseline = balltrain.score_detector(TiledBallDetector(base, "cuda"), dataset)
    candidate = balltrain.score_detector(TiledBallDetector(best, "cuda"), dataset)
    return {"weights": best.read_bytes(), "baseline": baseline, "candidate": candidate}
