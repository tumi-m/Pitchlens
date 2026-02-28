"""Basic pipeline unit tests (no GPU required)."""
import pytest
import math
from app.services.pipeline import _compute_xg, _compute_gaussian_heatmap, _cluster_teams_by_jersey


def test_xg_penalty_spot():
    """Shot from penalty spot (~6m) should yield high xG."""
    xg = _compute_xg(x=6.0, y=12.5, goal_x=0.0)  # 6m from home goal
    assert 0.5 < xg < 0.95, f"Penalty xG should be high, got {xg}"


def test_xg_long_shot():
    """Long shot from halfway (21m) should yield low xG."""
    xg = _compute_xg(x=21.0, y=12.5, goal_x=0.0)
    assert xg < 0.15, f"Long shot xG should be low, got {xg}"


def test_xg_bounds():
    """xG should always be between 0 and 1."""
    for x in range(0, 42, 3):
        for y in range(0, 25, 3):
            xg = _compute_xg(float(x), float(y), 42.0)
            assert 0 <= xg <= 1, f"xG out of bounds at ({x},{y}): {xg}"


def test_heatmap_empty():
    """Too few positions should return empty heatmap."""
    result = _compute_gaussian_heatmap([(1.0, 1.0), (2.0, 2.0)])
    assert result == []


def test_heatmap_valid():
    """Sufficient positions should produce heatmap points."""
    positions = [(float(i % 10), float(i % 8)) for i in range(20)]
    result = _compute_gaussian_heatmap(positions)
    assert len(result) > 0


def test_heatmap_intensity_range():
    """All heatmap intensities should be in [0, 1]."""
    positions = [(float(i % 10), float(i % 8)) for i in range(30)]
    result = _compute_gaussian_heatmap(positions)
    for pt in result:
        assert 0 <= pt.intensity <= 1, f"Intensity out of range: {pt.intensity}"
