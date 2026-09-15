"""Local computer-vision engine for Pitchlens.

This package turns a match video into detections, tracks, team labels,
optional pitch coordinates, and *candidate* events. Nothing here is a
validated official stat sheet. Every derived number carries provenance.
"""

__all__ = ["LocalCVPipeline"]


def __getattr__(name: str):
    if name == "LocalCVPipeline":
        from app.engine.pipeline import LocalCVPipeline

        return LocalCVPipeline
    raise AttributeError(name)
