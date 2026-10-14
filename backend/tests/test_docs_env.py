"""The operator docs name every setting the vision worker reads, and say what the code does."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VISION = REPO / "backend" / "app" / "vision"
DOCS = REPO / "docs"

# Deliberately internal: never set by an operator, so not documented.
INTERNAL = {
    "VISION_GPU_WORKDIR",  # where modal_app.analyse writes; tests run it locally
}


def _docs_text():
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(DOCS.rglob("*.md")))


def test_every_vision_variable_in_the_code_is_documented():
    names = set()
    for source in sorted(VISION.glob("*.py")):
        names |= set(re.findall(r"VISION_[A-Z0-9_]+", source.read_text(encoding="utf-8")))
    assert "VISION_SERVICE_TOKEN" in names  # the scan found the worker's settings
    docs = _docs_text()
    missing = sorted(n for n in names - INTERNAL if not re.search(rf"\b{n}\b", docs))
    assert not missing, f"Document these in docs/ (or list them in INTERNAL with a reason): {missing}"
    stale = sorted(INTERNAL - names)
    assert not stale, f"No longer in the code; remove from INTERNAL: {stale}"


def test_the_deploy_guide_matches_the_code_and_the_product_rules():
    guide = (DOCS / "DEPLOY-VISION.md").read_text(encoding="utf-8")
    # The access code is optional (unset = an open site for a small test group).
    assert "is required before anyone can start an analysis" not in guide
    # A hosted full match does not fall back to the CPU unless the operator allows it.
    assert "falls back to the CPU and says so" not in guide
    for needed in ("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET", "VISION_ALLOW_CPU_FALLBACK", "drainingSeconds",
                   "fullMatch", "VISION_MAX_TRAINING_PER_DAY", "/ops", "Volume low"):
        assert needed in guide, needed
