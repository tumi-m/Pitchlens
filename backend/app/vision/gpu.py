"""Send a job's heavy inference to a Modal GPU; the worker keeps everything else."""

import gzip
import os
from pathlib import Path


class GPUUnavailable(Exception):
    """Modal could not run the job (auth, quota, image build): use the CPU instead."""


def modal_enabled():
    return (
        os.getenv("VISION_USE_MODAL", "1") != "0"
        and bool(os.getenv("MODAL_TOKEN_ID"))
        and bool(os.getenv("MODAL_TOKEN_SECRET"))
    )


def public_base():
    explicit = os.getenv("VISION_PUBLIC_URL", "").rstrip("/")
    if explicit:
        return explicit
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{domain}" if domain else ""


def run_on_modal(job_id, output, token, progress, cancelled, profile, sample_fps):
    """Stream progress from the GPU function and write its result to `output`."""
    base = public_base()
    if not base:
        raise GPUUnavailable("No public URL for the GPU to fetch the video from.")
    try:
        from app.vision.modal_app import analyse, app
    except Exception as exc:
        raise GPUUnavailable(f"Modal client unavailable: {exc}") from exc
    try:
        with app.run():
            stream = analyse.remote_gen(
                f"{base}/jobs/{job_id}/video", token, profile, int(sample_fps)
            )
            for item in stream:
                if cancelled():
                    stream.close()
                    raise InterruptedError("Analysis cancelled")
                if "error" in item:
                    if item.get("user"):
                        raise ValueError(item["error"])
                    raise RuntimeError(item["error"])
                if "result_gz" in item:
                    output = Path(output)
                    temp = output.with_suffix(".tmp")
                    temp.write_bytes(gzip.decompress(item["result_gz"]))
                    temp.replace(output)
                    return
                stage = item.pop("stage", None)
                if stage:
                    item["stage"] = f"GPU · {stage}"
                progress(**item)
    except (InterruptedError, ValueError):
        raise
    except Exception as exc:
        raise GPUUnavailable(str(exc)[:300]) from exc
    raise GPUUnavailable("The GPU run ended without a result.")
