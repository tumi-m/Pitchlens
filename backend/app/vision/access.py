"""Short-lived worker grants, scoped to one video's read operation."""

import hashlib
import hmac
import time


def video_grant(secret, job_id, expires=None):
    expiry = int(time.time()) + 4 * 3600 if expires is None else int(expires)
    message = f"video:{job_id}:{expiry}"
    signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"{expiry}.{signature}"


def valid_video_grant(secret, job_id, grant):
    try:
        expiry, _ = grant.split(".", 1)
        expiry = int(expiry)
        if not secret or not time.time() < expiry <= time.time() + 4 * 3600:
            return False
        return hmac.compare_digest(grant, video_grant(secret, job_id, expiry))
    except (ValueError, TypeError, AttributeError):
        return False
