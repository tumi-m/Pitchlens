"""Short-lived grants for the GPU, so a Modal run never holds the service token.

Each GPU run gets one grant to read its own job's video and one per weight
file it may copy from the worker. A grant is an HMAC whose key is derived from
the service token and the grant's purpose, over the job id, the expiry and (for
weights) the file name. Whoever reads a run's arguments (Modal's control plane,
its logs, any package in the GPU image) can therefore fetch that video and
those weights until the grant expires, and nothing else: no other job, no other
route. The worker also honours a grant only while its job runs on the GPU.
"""

import hashlib
import hmac
import re
import time

# A GPU run's grants last up to four hours, so that Modal re-running a
# preempted generator input can fetch again; the worker revokes them as soon as
# the run is no longer on the GPU (server.gpu_grant checks engines[job] == "gpu").
GRANT_SECONDS = 2 * 3600
MAX_GRANT_SECONDS = 4 * 3600
JOB = re.compile(r"[a-f0-9]{32}")
SIGNATURE = re.compile(r"[a-f0-9]{64}")
EXPIRY = re.compile(r"[0-9]{1,12}")


def _key(secret, purpose):
    # One key per purpose: a video grant can never pass as a weights grant.
    return hmac.new(secret.encode(), f"pitchlens-grant:v1:{purpose}".encode(), hashlib.sha256).digest()


def _signature(secret, purpose, job_id, expiry, scope=""):
    message = f"{job_id}:{expiry}:{scope}".encode()
    return hmac.new(_key(secret, purpose), message, hashlib.sha256).hexdigest()


def _expiry(expires):
    return int(time.time()) + GRANT_SECONDS if expires is None else int(expires)


def _fresh(expiry):
    now = time.time()
    return now < expiry <= now + MAX_GRANT_SECONDS


def video_grant(secret, job_id, expires=None):
    """Read GET /jobs/{job_id}/video until `expires` (header x-pitchlens-video-grant)."""
    expiry = _expiry(expires)
    return f"{expiry}.{_signature(secret, 'video', job_id, expiry)}"


def model_grant(secret, job_id, name, expires=None):
    """Read GET /models/{name} for job_id's run until `expires` (header x-pitchlens-model-grant)."""
    expiry = _expiry(expires)
    return f"{job_id}.{expiry}.{_signature(secret, 'model', job_id, expiry, name)}"


def valid_video_grant(secret, job_id, grant):
    try:
        expiry, signature = grant.split(".")
        expiry = int(expiry) if EXPIRY.fullmatch(expiry) else 0
    except (ValueError, TypeError, AttributeError):
        return False
    if not secret or not JOB.fullmatch(str(job_id)) or not SIGNATURE.fullmatch(signature) or not _fresh(expiry):
        return False
    return hmac.compare_digest(signature, _signature(secret, "video", job_id, expiry))


def model_grant_job(secret, name, grant):
    """The job a weights grant was issued to, when it is valid for `name`; otherwise None."""
    try:
        job_id, expiry, signature = grant.split(".")
        expiry = int(expiry) if EXPIRY.fullmatch(expiry) else 0
    except (ValueError, TypeError, AttributeError):
        return None
    if not secret or not JOB.fullmatch(job_id) or not SIGNATURE.fullmatch(signature) or not _fresh(expiry):
        return None
    if not hmac.compare_digest(signature, _signature(secret, "model", job_id, expiry, str(name))):
        return None
    return job_id
