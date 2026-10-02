"""新店と既存店の距離・類似度ランキング。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .axes import Standardizer, axis_profile
from .config import Config


@dataclass
class StoreSimilarity:
    store_id: str
    store_name: str
    distance: float
    similarity: float                       # 0-100。近いほど大きい
    axis_distance: dict[str, float | None]  # 軸ごとの距離寄与
    axis_profile: dict[str, float | None]   # その店の軸スコア
    coverage: dict[str, str]                # 軸ごとの「使えた変数/全変数」
    rank: int = 0

    def top_gaps(self, cfg: Config, z_new, z_store, n: int = 4) -> list[dict]:
        """差が大きかった変数。選定理由の説明に使う。"""
        gaps = []
        for k in cfg.all_model_keys():
            a, b = z_new.get(k), z_store.get(k)
            if a is None or b is None:
                continue
            gaps.append({"variable": k, "label": cfg.label(k),
                         "axis": cfg.axis_of(k), "z_gap": abs(a - b),
                         "direction": "新店が高い" if a > b else "新店が低い"})
        gaps.sort(key=lambda d: -d["z_gap"])
        return gaps[:n]


@dataclass
class SimilarityResult:
    ranking: list[StoreSimilarity]
    z_new: dict[str, float | None]
    z_stores: dict[str, dict[str, float | None]]
    new_axis_profile: dict[str, float | None]
    weights: dict[str, float]
    standardizer: Standardizer
    skipped: list[str]    # 既存店でばらつきがなく距離に使えなかった変数


def compute_similarity(
    stores: pd.DataFrame,
    new_store: pd.Series,
    cfg: Config,
    *,
    weight_overrides: dict[str, float] | None = None,
) -> SimilarityResult:
    keys = cfg.all_model_keys()
    weights = cfg.axis_weights(weight_overrides)
    std = Standardizer.fit(stores, keys)

    z_new = std.transform_row(new_store, keys)
    z_stores = {
        str(row[cfg.id_key]): std.transform_row(row, keys) for _, row in stores.iterrows()
    }

    ranking: list[StoreSimilarity] = []
    for _, row in stores.iterrows():
        sid = str(row[cfg.id_key])
        zs = z_stores[sid]
        axis_d: dict[str, float | None] = {}
        coverage: dict[str, str] = {}
        total, wsum = 0.0, 0.0

        for axis, spec in cfg.axes.items():
            axis_keys = spec.get("variables", [])
            diffs = [
                (z_new[k] - zs[k]) ** 2
                for k in axis_keys
                if z_new.get(k) is not None and zs.get(k) is not None
            ]
            coverage[axis] = f"{len(diffs)}/{len(axis_keys)}"
            if not diffs:
                axis_d[axis] = None
                continue
            # 変数の数が軸ごとに違うので平均を取ってから重みをかける
            d = float(np.sqrt(np.mean(diffs)))
            axis_d[axis] = d
            w = weights[axis]
            total += w * (d ** 2)
            wsum += w

        distance = float(np.sqrt(total / wsum)) if wsum > 0 else float("inf")
        similarity = 100.0 / (1.0 + distance) if np.isfinite(distance) else 0.0
        ranking.append(
            StoreSimilarity(
                store_id=sid,
                store_name=str(row[cfg.name_key]),
                distance=distance,
                similarity=similarity,
                axis_distance=axis_d,
                axis_profile=axis_profile(zs, cfg),
                coverage=coverage,
            )
        )

    ranking.sort(key=lambda s: s.distance)
    for i, s in enumerate(ranking, 1):
        s.rank = i

    return SimilarityResult(
        ranking=ranking,
        z_new=z_new,
        z_stores=z_stores,
        new_axis_profile=axis_profile(z_new, cfg),
        weights=weights,
        standardizer=std,
        skipped=std.degenerate,
    )
