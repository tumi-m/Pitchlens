"""Install the optional, checksum-pinned weights from Roboflow's soccer example."""

import hashlib
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

MODELS = {
    "roboflow-football-ball.pt": (
        "1isw4wx-MK9h9LMr36VvIWlJD6ppUvw7V",
        "678fbad05134f19c5094cb8d273812ec9c6691228180d46832551ecf99ed2912",
    ),
    "roboflow-football-player.pt": (
        "17PXFNlx-jI7VjVo_vQnB1sONjRyvoB-q",
        "75b09c377fbf9d0791d23f6cfb689f5aed6eaa43a6818bd1fb884cf7507fffaf",
    ),
}


class DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden" and "name" in values:
            self.fields[values["name"]] = values.get("value", "")


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install():
    root = Path(__file__).resolve().parents[1] / "models"
    root.mkdir(exist_ok=True)
    for name, (file_id, expected) in MODELS.items():
        path = root / name
        if not path.exists():
            temp = path.with_suffix(".download")
            try:
                url = "https://drive.google.com/uc?" + urllib.parse.urlencode(
                    {"export": "download", "id": file_id}
                )
                urllib.request.urlretrieve(url, temp)
                if temp.stat().st_size < 100_000:
                    form = DownloadForm()
                    form.feed(temp.read_text())
                    if form.fields.get("id") != file_id or "uuid" not in form.fields:
                        raise RuntimeError("Model download did not return the expected file")
                    # Only follow Google's known download endpoint, never an arbitrary form URL.
                    url = "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
                        {key: form.fields[key] for key in ("id", "export", "confirm", "uuid")}
                    )
                    urllib.request.urlretrieve(url, temp)
                if checksum(temp) != expected:
                    raise RuntimeError(f"Checksum mismatch for {name}; model was not installed")
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
        if checksum(path) != expected:
            raise RuntimeError(f"Installed model checksum mismatch: {name}")
        print(f"Verified {name}: {expected}", flush=True)


if __name__ == "__main__":
    install()
