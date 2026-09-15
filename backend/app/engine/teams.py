from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np


def jersey_feature(crop: np.ndarray) -> Optional[np.ndarray]:
    import cv2

    if crop is None or crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    h = max(8, crop.shape[0] // 2)
    hsv = cv2.cvtColor(crop[:h, :], cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 2] > 30) & (hsv[:, :, 2] < 245) & (hsv[:, :, 1] > 25)
    if mask.mean() < 0.05:
        return hsv.reshape(-1, 3).mean(axis=0)
    return hsv[mask].mean(axis=0)


def assign_teams(crops_by_track: dict[int, list[np.ndarray]], n_clusters: int = 2) -> dict[int, str]:
    features, ids = [], []
    for tid, crops in crops_by_track.items():
        vecs = [v for v in (jersey_feature(c) for c in crops[-8:]) if v is not None]
        if not vecs:
            continue
        features.append(np.mean(vecs, axis=0))
        ids.append(tid)
    if len(features) < n_clusters:
        return {tid: "unknown" for tid in ids}
    from sklearn.cluster import KMeans

    X = np.asarray(features, dtype=np.float32)
    X[:, 0] = X[:, 0] / 180.0
    X[:, 1:] = X[:, 1:] / 255.0
    labels = KMeans(n_clusters=n_clusters, n_init=10, random_state=0).fit_predict(X)
    return {int(tid): ("home" if int(lab) == 0 else "away") for tid, lab in zip(ids, labels)}


def majority_team(votes: list[str]) -> str:
    counted = Counter(v for v in votes if v in {"home", "away"})
    if not counted:
        return "unknown"
    team, n = counted.most_common(1)[0]
    if n / max(1, len(votes)) < 0.55:
        return "unknown"
    return team


def swap_if_needed(assignment: dict[int, str], mean_x_by_track: dict[int, float], home_defends_left: bool = True) -> dict[int, str]:
    homes = [mean_x_by_track[t] for t, s in assignment.items() if s == "home" and t in mean_x_by_track]
    aways = [mean_x_by_track[t] for t, s in assignment.items() if s == "away" and t in mean_x_by_track]
    if not homes or not aways:
        return assignment
    if home_defends_left and float(np.mean(homes)) > float(np.mean(aways)):
        return {t: ("away" if s == "home" else "home" if s == "away" else s) for t, s in assignment.items()}
    return assignment
