"""低温カテゴリーの粗利率・PI値の予測と、LOO（1店抜き）による精度検証。

予測は「類似店の実績の加重平均」であって、モデルの出力ではない。
その代わり、同じ手順を既存5店に1店ずつ当てはめて（LOO）、実際にどれだけ
外れるかを測る。レポートに出す「正確性」はこのLOO誤差そのもの。

LOOは全店平均（ベースライン）とも比較する。類似店法がベースラインに
勝てていない指標は、わざわざ類似店を選ぶ意味がないので、そう明記する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .idpos import IdposData
from .metrics_config import MetricsConfig
from .similarity import compute_similarity


# ---------------------------------------------------------------- 予測

@dataclass
class PeerContribution:
    store_id: str
    store_name: str
    similarity: float
    weight: float
    value: float


@dataclass
class Prediction:
    unit: str            # 分析単位（例 '和日配 > 納豆'）
    metric: str
    point: float | None
    low: float | None             # 予測区間の下限（LOO誤差ベース）
    high: float | None
    peer_low: float | None        # 類似店の実績レンジ
    peer_high: float | None
    all_low: float | None         # 既存全店のレンジ
    all_high: float | None
    all_median: float | None
    peers: list[PeerContribution] = field(default_factory=list)
    weekly_cv: float | None = None       # 類似店の週次変動係数（加重平均）
    loo_mape: float | None = None        # この指標のLOO平均絶対誤差率(%)
    loo_baseline_mape: float | None = None
    n_weeks_min: int = 0                 # 類似店の最小週数
    basis: str = ""                      # 根拠の一文

    @property
    def interval_width_pct(self) -> float | None:
        if self.point in (None, 0) or self.low is None or self.high is None:
            return None
        return (self.high - self.low) / abs(self.point) * 100

    @property
    def beats_baseline(self) -> bool | None:
        if self.loo_mape is None or self.loo_baseline_mape is None:
            return None
        return self.loo_mape < self.loo_baseline_mape


def _weights(sims: list[float], power: float) -> list[float]:
    raw = [max(s, 1e-9) ** power for s in sims]
    total = sum(raw)
    return [r / total for r in raw] if total > 0 else [1 / len(raw)] * len(raw)


def _predict_one(
    pool: pd.DataFrame,
    target: pd.Series,
    cfg: Config,
    idpos: IdposData,
    category: str,
    metric: str,
    *,
    top_n: int,
    power: float,
    weight_overrides: dict[str, float] | None,
) -> tuple[float | None, list[PeerContribution], dict]:
    """pool の中から類似店を選び、target の値を加重平均で推定する。"""
    sim = compute_similarity(pool, target, cfg, weight_overrides=weight_overrides)
    ranked = [
        s for s in sim.ranking
        if idpos.value(s.store_id, category, metric) is not None
    ]
    if not ranked:
        return None, [], {}
    chosen = ranked[:top_n]
    ws = _weights([c.similarity for c in chosen], power)
    peers = [
        PeerContribution(
            store_id=c.store_id, store_name=c.store_name,
            similarity=c.similarity, weight=w,
            value=float(idpos.value(c.store_id, category, metric)),
        )
        for c, w in zip(chosen, ws)
    ]
    point = float(sum(p.weight * p.value for p in peers))
    meta = {
        "cv": _weighted_cv(idpos, peers, category, metric),
        "n_weeks_min": min(
            (idpos.stats[(p.store_id, category, metric)].n_weeks for p in peers),
            default=0,
        ),
    }
    return point, peers, meta


def _weighted_cv(idpos: IdposData, peers: list[PeerContribution],
                 category: str, metric: str) -> float | None:
    vals = []
    for p in peers:
        st = idpos.stats.get((p.store_id, category, metric))
        if st and st.cv is not None:
            vals.append((st.cv, p.weight))
    if not vals:
        return None
    return float(sum(c * w for c, w in vals) / sum(w for _, w in vals))


# ---------------------------------------------------------------- LOO検証

@dataclass
class LooFold:
    held_out: str
    held_out_name: str
    unit: str
    metric: str
    actual: float
    predicted: float | None
    baseline: float | None
    peers: list[str] = field(default_factory=list)

    @property
    def err_pct(self) -> float | None:
        if self.predicted is None or self.actual == 0:
            return None
        return (self.predicted - self.actual) / abs(self.actual) * 100

    @property
    def baseline_err_pct(self) -> float | None:
        if self.baseline is None or self.actual == 0:
            return None
        return (self.baseline - self.actual) / abs(self.actual) * 100


@dataclass
class LooResult:
    folds: list[LooFold]
    mape: dict[tuple[str, str], float]            # (category, metric) -> MAPE%
    baseline_mape: dict[tuple[str, str], float]
    p80_abs_err: dict[tuple[str, str], float]     # 予測区間に使う分位点
    n_folds: dict[tuple[str, str], int]

    def summary_rows(self, mcfg: MetricsConfig) -> list[dict]:
        rows = []
        for key in sorted(self.mape, key=lambda k: (k[1], k[0])):
            cat, met = key
            rows.append({
                "unit": cat, "metric": met,
                "metric_label": mcfg.metric_label(met),
                "mape": self.mape[key],
                "baseline_mape": self.baseline_mape.get(key),
                "n_folds": self.n_folds.get(key, 0),
                "beats_baseline": (self.baseline_mape.get(key) is not None
                                   and self.mape[key] < self.baseline_mape[key]),
            })
        return rows


def run_loo(
    stores: pd.DataFrame,
    cfg: Config,
    idpos: IdposData,
    mcfg: MetricsConfig,
    *,
    weight_overrides: dict[str, float] | None = None,
) -> LooResult:
    """既存店を1店ずつ「新店だと思って」残りから予測し、実績と比べる。"""
    pred_cfg = mcfg.prediction
    top_n = int(pred_cfg.get("top_n_peers", 2))
    power = float(pred_cfg.get("similarity_power", 2.0))
    q = float(pred_cfg.get("interval_quantile", 0.8))
    id_key = cfg.id_key

    folds: list[LooFold] = []
    for i in range(len(stores)):
        target = stores.iloc[i]
        pool = stores.drop(stores.index[i])
        if len(pool) < 2:
            continue
        sid = str(target[id_key])
        for cat in idpos.units:
            for met in idpos.metrics:
                actual = idpos.value(sid, cat, met)
                if actual is None:
                    continue
                point, peers, _ = _predict_one(
                    pool, target, cfg, idpos, cat, met,
                    top_n=top_n, power=power, weight_overrides=weight_overrides,
                )
                pool_vals = [
                    idpos.value(str(r[id_key]), cat, met) for _, r in pool.iterrows()
                ]
                pool_vals = [v for v in pool_vals if v is not None]
                baseline = float(np.mean(pool_vals)) if pool_vals else None
                folds.append(LooFold(
                    held_out=sid, held_out_name=str(target[cfg.name_key]),
                    unit=cat, metric=met, actual=float(actual),
                    predicted=point, baseline=baseline,
                    peers=[p.store_name for p in peers],
                ))

    mape: dict[tuple[str, str], float] = {}
    base: dict[tuple[str, str], float] = {}
    p80: dict[tuple[str, str], float] = {}
    nf: dict[tuple[str, str], int] = {}
    by_key: dict[tuple[str, str], list[LooFold]] = {}
    for f in folds:
        by_key.setdefault((f.unit, f.metric), []).append(f)
    for key, fs in by_key.items():
        errs = [abs(f.err_pct) for f in fs if f.err_pct is not None]
        berrs = [abs(f.baseline_err_pct) for f in fs if f.baseline_err_pct is not None]
        if errs:
            mape[key] = float(np.mean(errs))
            p80[key] = float(np.quantile(errs, q))
            nf[key] = len(errs)
        if berrs:
            base[key] = float(np.mean(berrs))
    return LooResult(folds, mape, base, p80, nf)


# ---------------------------------------------------------------- 本番予測

def predict_new_store(
    stores: pd.DataFrame,
    new_store: pd.Series,
    cfg: Config,
    idpos: IdposData,
    mcfg: MetricsConfig,
    loo: LooResult,
    *,
    weight_overrides: dict[str, float] | None = None,
) -> list[Prediction]:
    pred_cfg = mcfg.prediction
    top_n = int(pred_cfg.get("top_n_peers", 2))
    power = float(pred_cfg.get("similarity_power", 2.0))
    id_key = cfg.id_key

    out: list[Prediction] = []
    for cat in idpos.units:
        for met in idpos.metrics:
            point, peers, meta = _predict_one(
                stores, new_store, cfg, idpos, cat, met,
                top_n=top_n, power=power, weight_overrides=weight_overrides,
            )
            all_vals = [
                idpos.value(str(r[id_key]), cat, met) for _, r in stores.iterrows()
            ]
            all_vals = [v for v in all_vals if v is not None]
            peer_vals = [p.value for p in peers]

            err_q = loo.p80_abs_err.get((cat, met))
            low = high = None
            if point is not None and err_q is not None:
                low, high = point * (1 - err_q / 100), point * (1 + err_q / 100)
            elif point is not None and peer_vals:
                low, high = min(peer_vals), max(peer_vals)

            out.append(Prediction(
                unit=cat, metric=met, point=point, low=low, high=high,
                peer_low=min(peer_vals) if peer_vals else None,
                peer_high=max(peer_vals) if peer_vals else None,
                all_low=min(all_vals) if all_vals else None,
                all_high=max(all_vals) if all_vals else None,
                all_median=float(np.median(all_vals)) if all_vals else None,
                peers=peers,
                weekly_cv=meta.get("cv"),
                loo_mape=loo.mape.get((cat, met)),
                loo_baseline_mape=loo.baseline_mape.get((cat, met)),
                n_weeks_min=meta.get("n_weeks_min", 0),
                basis=_basis_text(peers, loo, cat, met, meta),
            ))
    return out


def _basis_text(peers, loo: LooResult, cat: str, met: str, meta: dict) -> str:
    if not peers:
        return "類似店にこのカテゴリーの実績がなく、推定できませんでした。"
    parts = [
        "類似店 "
        + "・".join(f"{p.store_name}（重み{p.weight:.0%}、実績{p.value:,.4g}）" for p in peers)
        + " の加重平均"
    ]
    n = meta.get("n_weeks_min", 0)
    if n:
        parts.append(f"各店{n}週以上の週次実績から算出")
    m = loo.mape.get((cat, met))
    b = loo.baseline_mape.get((cat, met))
    if m is not None:
        s = f"同じ手順を既存店に当てた誤差（LOO）は平均{m:.1f}%"
        if b is not None:
            s += f"（全店平均で代用した場合は{b:.1f}%）"
        parts.append(s)
    cv = meta.get("cv")
    if cv is not None:
        parts.append(f"類似店の週次変動は±{cv:.0%}")
    return "／".join(parts)


# ---------------------------------------------------------------- 重みの感度

def weight_sensitivity(
    stores: pd.DataFrame,
    cfg: Config,
    idpos: IdposData,
    mcfg: MetricsConfig,
    *,
    grid: list[float] | None = None,
) -> pd.DataFrame:
    """軸の重みを振って、LOO誤差がどう動くかを見る。

    5店舗のLOO（実質5点）で重みを最適化すると、それ自体が過学習になる。
    ここでやるのは最適化ではなく「誤差の地形がどれだけ平坦か」を見せること。
    平坦なら、どの重みを選んでも結果は変わらない＝重み調整に意味はない。
    """
    grid = grid or [0.5, 1.0, 2.0]
    axes = list(cfg.axes)
    rows = []
    for a in grid:
        for b in grid:
            for c in grid:
                w = dict(zip(axes, (a, b, c)))
                loo = run_loo(stores, cfg, idpos, mcfg, weight_overrides=w)
                if not loo.mape:
                    continue
                rows.append({
                    **{f"w_{k}": v for k, v in w.items()},
                    "mean_mape": float(np.mean(list(loo.mape.values()))),
                    "n_cells": len(loo.mape),
                })
    df = pd.DataFrame(rows).sort_values("mean_mape").reset_index(drop=True)
    return df
