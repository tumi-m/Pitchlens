"""Choose available local hardware without overriding an operator's device."""

import os


def inference_device(torch_module=None):
    requested = os.getenv("VISION_DEVICE", "auto").strip() or "auto"
    if requested != "auto":
        return requested
    if torch_module is None:
        import torch as torch_module
    cuda = getattr(torch_module, "cuda", None)
    if cuda is not None and cuda.is_available():
        return "cuda"
    mps = getattr(getattr(torch_module, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"
