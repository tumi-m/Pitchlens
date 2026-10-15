"""Team labels from kit colour under uneven light (E22).

Numbers are LAB medians measured on the owner's 37-minute match: the light kit
reads L 180 in the middle of the picture and ~105 at the darker edges with its
chroma (a, b) nearly unchanged; the red kit falls from L 111 to ~52.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from app.vision.engine import assign_team, kit_distance, train_colours

RED_LIT, RED_SHADE = [111.0, 168.0, 147.0], [52.0, 155.0, 143.0]
LIGHT_LIT, LIGHT_SHADE = [180.0, 123.0, 140.0], [105.0, 127.0, 134.0]


def owner_like_features(seed=0):
    rng = np.random.default_rng(seed)

    def noise(base, n):
        return (np.array(base) + rng.normal(0, [14, 7, 5], (n, 3))).tolist()

    # About a third of each kit stands in the darker part of the picture.
    return noise(RED_LIT, 40) + noise(RED_SHADE, 20) + noise(LIGHT_LIT, 40) + noise(LIGHT_SHADE, 20)


def team_of(colour, centres, use_hue=False):
    return assign_team(np.array(colour), centres, use_hue=use_hue)


def test_shaded_light_kit_is_not_given_to_the_dark_kit():
    centres, _ = train_colours(owner_like_features(), n_clusters=2)
    red, light = team_of(RED_LIT, centres), team_of(LIGHT_LIT, centres)
    assert {red, light} == {0, 1}
    # A plain LAB distance put these with the red kit (similar lightness).
    assert team_of(LIGHT_SHADE, centres) == light
    assert team_of([100.0, 126.0, 132.0], centres) == light
    assert team_of(RED_SHADE, centres) == red


def test_kit_centres_are_not_pulled_by_the_shaded_light_kit():
    centres, _ = train_colours(owner_like_features(1), n_clusters=2)
    red = centres[int(np.argmax(centres[:, 1]))]
    # The red centre keeps the red chroma (a plain fit dragged it towards neutral).
    assert red[1] > 160


def test_kits_that_differ_only_in_lightness_still_separate():
    rng = np.random.default_rng(2)
    white, black = [225.0, 128.0, 130.0], [45.0, 128.0, 126.0]
    features = (np.array(white) + rng.normal(0, [14, 4, 4], (40, 3))).tolist()
    features += (np.array(black) + rng.normal(0, [14, 4, 4], (40, 3))).tolist()
    centres, _ = train_colours(features, n_clusters=2)
    w, b = team_of(white, centres), team_of(black, centres)
    assert {w, b} == {0, 1}
    assert team_of([190.0, 128.0, 129.0], centres) == w
    assert team_of([70.0, 128.0, 127.0], centres) == b


@pytest.mark.parametrize(
    "kit_a, kit_b",
    [
        ([230.0, 128.0, 128.0], [170.0, 128.0, 128.0]),  # white and light grey
        ([120.0, 95.0, 150.0], [185.0, 100.0, 145.0]),  # green and mint
    ],
)
def test_kits_that_differ_mainly_in_lightness_are_still_labelled(kit_a, kit_b):
    # The separation gate stays in plain LAB: weighted, these pairs fall under
    # it and the whole match would lose its team assignments.
    rng = np.random.default_rng(3)
    features = (np.array(kit_a) + rng.normal(0, [8, 3, 3], (40, 3))).tolist()
    features += (np.array(kit_b) + rng.normal(0, [8, 3, 3], (40, 3))).tolist()
    centres, _ = train_colours(features, n_clusters=2)
    a, b = team_of(kit_a, centres), team_of(kit_b, centres)
    assert {a, b} == {0, 1}


def test_tryzub_k4_fit_picks_the_mint_kit_not_sideline_spectators():
    # Calibration colours from the Tryzub clip (general model, four clusters).
    # A plain LAB fit split the mint kit by lightness, so a cluster of
    # dark-clothed spectators came second and became Kit B.
    path = Path(__file__).parent / "fixtures" / "tryzub_kit_features.json"
    features = json.loads(path.read_text())["features"]
    centres, _ = train_colours(features, n_clusters=4)
    # The two largest clusters tie, so the kit order is not fixed.
    red = [c for c in centres if c[1] > 150]
    mint = [c for c in centres if c[1] < 115 and c[0] > 120]
    assert len(red) == 1 and len(mint) == 1
    assert all(c[0] >= 80 for c in centres)


def test_colour_between_the_kits_or_far_from_both_stays_unknown():
    centres = np.array([[105.0, 167.0, 146.0], [160.0, 125.0, 139.0]])
    # Midway in chroma: no claim rather than a guess.
    assert team_of([130.0, 146.0, 142.0], centres) == -1
    # A yellow official is far from both kits.
    assert team_of([200.0, 120.0, 200.0], centres) == -1


def test_kit_distance_weights_lightness_less_than_chroma():
    assert kit_distance([100, 128, 128], [180, 128, 128]) == pytest.approx(28.0)
    assert kit_distance([100, 128, 128], [100, 156, 128]) == pytest.approx(28.0)


def test_track_vote_does_not_give_a_player_kit_to_the_referee():
    from app.vision.engine import vote_teams

    # Tryzub clip: red against mint, a yellow referee. A track that switched
    # from a mint player to the referee carried the mint kit onto the referee.
    centres = np.array([[104.0, 163.0, 153.0], [148.0, 104.0, 145.0]])
    mint, referee, between = [150.0, 105.0, 146.0], [175.0, 110.0, 185.0], [128.0, 134.0, 149.0]
    assert team_of(referee, centres, use_hue=True) == -1
    assert team_of(between, centres, use_hue=True) == -1

    def obs(colour):
        team = team_of(colour, centres, use_hue=True)
        return {"id": 4, "role": "person", "box": [0, 0, 10, 30], "confidence": 0.9,
                "kitFeature": colour, "team": team}

    frames = [{"players": [obs(c)]} for c in (mint, mint, mint, between, referee, referee)]
    vote_teams(frames, centres, use_hue=True)
    assert [f["players"][0]["team"] for f in frames] == [1, 1, 1, 1, -1, -1]
    # Without the centres the vote fills every observation, as before.
    frames = [{"players": [obs(c)]} for c in (mint, mint, mint, referee)]
    vote_teams(frames)
    assert [f["players"][0]["team"] for f in frames] == [1, 1, 1, 1]
