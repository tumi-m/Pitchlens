"""Start the authenticated loopback worker and configure the local Next.js proxy."""

import os
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
config = root / ".vision"
config.mkdir(exist_ok=True)
secret = config / "service-token"
if not secret.exists():
    secret.write_text(secrets.token_urlsafe(32))
    secret.chmod(0o600)
token = secret.read_text().strip()
env = root.parent / "frontend" / ".env.local"
lines = env.read_text().splitlines() if env.exists() else []
lines = [
    line for line in lines if not line.startswith(("VISION_SERVICE_URL=", "VISION_SERVICE_TOKEN="))
]
lines.extend(["VISION_SERVICE_URL=http://127.0.0.1:8100", f"VISION_SERVICE_TOKEN={token}"])
env.write_text("\n".join(lines) + "\n")
env.chmod(0o600)
os.environ["VISION_SERVICE_TOKEN"] = token
os.environ.setdefault("YOLO_CONFIG_DIR", str(root / ".ultralytics"))
Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(config / "matplotlib"))
print(
    "Local worker: http://127.0.0.1:8100. Restart Next.js to load its local proxy configuration.",
    flush=True,
)
import uvicorn  # noqa: E402

uvicorn.run("app.vision.server:app", host="127.0.0.1", port=8100, workers=1)
