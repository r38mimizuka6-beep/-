"""標準化、3軸スコア、都市部／郊外の判定。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class Standardizer:
    """既存店5店の平均・標準偏差で固定する。新店も同じ尺度に載せる。"""

    mean: pd.Series
    std: pd.Series
    degenerate: list[str]  # 既存店でばらつきがなく、距離に効かせられない変数

    @classmethod
    def fit(cls, df: pd.DataFrame, keys: list[str]) -> "Standardizer":
        sub = df[keys].apply(pd.to_numeric, errors="coerce")
        mean = sub.mean()
        # 店舗数が少ないので不偏(ddof=1)を使う。全店同値なら0になる。
        std = sub.std(ddof=1)
        degenerate = [k for k in keys if not np.isfinite(std.get(k, np.nan)) or std.get(k, 0) == 0]
        return cls(mean=mean, std=std, degenerate=degenerate)

    def transform_row(self, row: pd.Series, keys: list[str]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for k in keys:
            v = row.get(k)
            s = self.std.get(k, np.nan)
            if v is None or pd.isna(v) or not np.isfinite(s) or s == 0:
                out[k] = None
            else:
                out[k] = float((v - self.mean[k]) / s)
        return out

    def transform(self, df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            [self.transform_row(r, keys) for _, r in df.iterrows()], index=df.index
        )


def axis_profile(z: dict[str, float | None], cfg: Config) -> dict[str, float | None]:
    """軸ごとの平均zスコア。その店が各軸のどちら側にいるかを1数字で表す。"""
    prof: dict[str, float | None] = {}
    for axis, spec in cfg.axes.items():
        vals = [z[k] for k in spec.get("variables", []) if z.get(k) is not None]
        prof[axis] = float(np.mean(vals)) if vals else None
    return prof


def _rule_score(value: float, rural_at: float, urban_at: float) -> float:
    """rural_at を0、urban_at を1 に線形写像し[0,1]にクリップ。"""
    if urban_at == rural_at:
        return 0.5
    return float(np.clip((value - rural_at) / (urban_at - rural_at), 0.0, 1.0))


@dataclass
class UrbanRuralResult:
    score: float | None          # 1に近いほど都市部
    verdict: str                 # 都市部寄り / 中間 / 郊外寄り / 判定不能
    details: list[dict]          # ルールごとの内訳
    used_rules: int
    total_rules: int

    @property
    def is_rural(self) -> bool:
        return self.verdict == "郊外寄り"


def judge_urban_rural(row: pd.Series, cfg: Config) -> UrbanRuralResult:
    spec = cfg.urban_rural
    rules = spec["rules"]
    th = spec["thresholds"]
    details, num, den = [], 0.0, 0.0
    for rule in rules:
        key = rule["variable"]
        value = row.get(key)
        if value is None or pd.isna(value):
            details.append({"variable": key, "label": cfg.label(key), "value": None,
                            "score": None, "weight": rule.get("weight", 1.0)})
            continue
        s = _rule_score(float(value), float(rule["rural_at"]), float(rule["urban_at"]))
        w = float(rule.get("weight", 1.0))
        num += s * w
        den += w
        details.append({"variable": key, "label": cfg.label(key), "value": float(value),
                        "score": s, "weight": w,
                        "rural_at": rule["rural_at"], "urban_at": rule["urban_at"]})

    used = sum(1 for d in details if d["score"] is not None)
    if den == 0:
        return UrbanRuralResult(None, "判定不能", details, used, len(rules))

    score = num / den
    if score >= float(th["urban_min"]):
        verdict = "都市部寄り"
    elif score <= float(th["rural_max"]):
        verdict = "郊外寄り"
    else:
        verdict = "中間"
    return UrbanRuralResult(score, verdict, details, used, len(rules))
