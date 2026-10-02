"""業態転換前後の比較による予測（転換前後法）。

今回の「新店」は更地の新店ではなく、既存店の業態転換リニューアルである。
その場合、同じ立地の転換前実績が手元にあるので、商圏から横に当てる類似店法より
はるかに強い予測ができる。

  予測 = その店の転換前実績 × （既存店で測った 転換後÷転換前 の比率）

立地・商圏・競合が同じ店の前後を比べるので、商圏の違いを推定する必要がない。
推定するのは「業態転換そのものの効果」だけで、これは既存の転換済み店から
直接測れる。測った比率が店によってばらつく分が、そのまま不確実性になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .idpos import IdposData
from .metrics_config import MetricsConfig


@dataclass
class ConversionRatio:
    unit: str
    metric: str
    ratio: float                       # 既存店の中央値
    by_store: dict[str, float] = field(default_factory=dict)
    n_stores: int = 0
    lo: float | None = None            # 店舗間の最小
    hi: float | None = None            # 最大
    loo_mape: float | None = None      # 1店抜きで他店の中央値を当てたときの誤差%

    @property
    def spread_pct(self) -> float | None:
        if self.lo in (None, 0) or self.hi is None:
            return None
        return (self.hi - self.lo) / abs(self.ratio) * 100 if self.ratio else None


@dataclass
class ConversionPrediction:
    unit: str
    metric: str
    baseline: float                    # 新店の転換前実績
    point: float
    low: float | None
    high: float | None
    ratio: ConversionRatio
    basis: str


@dataclass
class ConversionResult:
    ratios: dict[tuple[str, str], ConversionRatio]
    predictions: list[ConversionPrediction]
    paired_stores: list[str]
    warnings: list[str] = field(default_factory=list)

    def by_key(self) -> dict[tuple[str, str], ConversionPrediction]:
        return {(p.unit, p.metric): p for p in self.predictions}


def _levels(d: IdposData, metric: str) -> dict[tuple[str, str], float]:
    return {(sid, unit): st.level
            for (sid, unit, m), st in d.stats.items()
            if m == metric and st.level is not None}


def compute_ratios(
    prior: IdposData,
    post: IdposData,
    mcfg: MetricsConfig,
    *,
    store_names: dict[str, str] | None = None,
    min_stores: int = 2,
) -> tuple[dict[tuple[str, str], ConversionRatio], list[str], list[str]]:
    """転換前データと転換後データから、単位×指標ごとの変化率を測る。"""
    warnings: list[str] = []
    names = store_names or post.store_names
    paired = sorted(set(prior.store_ids) & set(post.store_ids))
    if len(paired) < min_stores:
        warnings.append(
            f"転換前後の両方が揃っている店舗が{len(paired)}店しかありません"
            f"（必要{min_stores}店）。転換前後法は使えません。"
        )
        return {}, paired, warnings

    metrics = [m for m in post.metrics if m in prior.metrics]
    skipped = [m for m in post.metrics if m not in prior.metrics]
    if skipped:
        warnings.append(
            "転換前データに無いため比較できない指標: "
            + "、".join(mcfg.metric_label(m) for m in skipped)
        )

    out: dict[tuple[str, str], ConversionRatio] = {}
    for metric in metrics:
        pre, aft = _levels(prior, metric), _levels(post, metric)
        for unit in post.units:
            vals: dict[str, float] = {}
            for sid in paired:
                a, b = pre.get((sid, unit)), aft.get((sid, unit))
                if a is None or b is None or a == 0:
                    continue
                r = b / a
                if not np.isfinite(r) or r <= 0:
                    continue
                vals[sid] = float(r)
            if len(vals) < min_stores:
                continue

            arr = np.array(list(vals.values()))
            med = float(np.median(arr))

            # 1店抜き: 他店の中央値でその店の比率を当てられるか
            errs = []
            if len(arr) >= 3:
                for sid, actual in vals.items():
                    others = [v for k, v in vals.items() if k != sid]
                    pred = float(np.median(others))
                    if actual:
                        errs.append(abs(pred - actual) / abs(actual) * 100)

            out[(unit, metric)] = ConversionRatio(
                unit=unit, metric=metric, ratio=med,
                by_store={names.get(s, s): v for s, v in vals.items()},
                n_stores=len(vals), lo=float(arr.min()), hi=float(arr.max()),
                loo_mape=float(np.mean(errs)) if errs else None,
            )
    return out, paired, warnings


def predict_from_baseline(
    baseline: IdposData,
    store_id: str,
    ratios: dict[tuple[str, str], ConversionRatio],
    mcfg: MetricsConfig,
) -> list[ConversionPrediction]:
    """新店の転換前実績に、測った変化率をかける。"""
    out: list[ConversionPrediction] = []
    for (unit, metric), cr in sorted(ratios.items()):
        st = baseline.stats.get((str(store_id), unit, metric))
        if st is None or st.level is None:
            continue
        point = st.level * cr.ratio
        lo = st.level * cr.lo if cr.lo is not None else None
        hi = st.level * cr.hi if cr.hi is not None else None
        # LOOの誤差が店舗間のばらつきより広いならそちらを採る
        if cr.loo_mape is not None:
            l2, h2 = point * (1 - cr.loo_mape / 100), point * (1 + cr.loo_mape / 100)
            lo = min(lo, l2) if lo is not None else l2
            hi = max(hi, h2) if hi is not None else h2
        by = "・".join(f"{n} {v:.2f}倍" for n, v in sorted(cr.by_store.items()))
        basis = (
            f"この店の転換前実績 {st.level:,.4g}（{st.n_weeks}週）× "
            f"既存{cr.n_stores}店の転換前後の変化率 中央値{cr.ratio:.2f}倍"
            f"（{by}）"
        )
        if cr.loo_mape is not None:
            basis += f"／変化率の1店抜き誤差 ±{cr.loo_mape:.1f}%"
        out.append(ConversionPrediction(
            unit=unit, metric=metric, baseline=float(st.level), point=float(point),
            low=lo, high=hi, ratio=cr, basis=basis,
        ))
    return out


def run_conversion(
    prior: IdposData,
    post: IdposData,
    baseline: IdposData | None,
    new_store_id: str,
    mcfg: MetricsConfig,
) -> ConversionResult:
    ratios, paired, warns = compute_ratios(prior, post, mcfg)
    preds: list[ConversionPrediction] = []
    if baseline is None:
        warns.append(
            "新店の転換前データが指定されていないため、転換前後法による予測はできません。"
            "同じ立地の転換前IDPOSがあれば --baseline-idpos で渡してください。"
            "商圏から横に当てる類似店法より精度が上がります。"
        )
    elif ratios:
        preds = predict_from_baseline(baseline, new_store_id, ratios, mcfg)
        if not preds:
            warns.append(
                f"転換前データに店舗CD {new_store_id} の該当単位がありません。"
                "店舗CDが転換前後で変わっていないか確認してください。"
            )
    return ConversionResult(ratios=ratios, predictions=preds,
                            paired_stores=paired, warnings=warns)


def compare_methods(
    similar: list, conversion: ConversionResult, mcfg: MetricsConfig, loo
) -> pd.DataFrame:
    """類似店法と転換前後法を、実測誤差で横並びにする。"""
    conv = conversion.by_key()
    rows = []
    for p in similar:
        c = conv.get((p.unit, p.metric))
        sim_err = p.loo_mape
        conv_err = c.ratio.loo_mape if c else None
        if sim_err is None and conv_err is None:
            continue
        if conv_err is None:
            better = "類似店法"
        elif sim_err is None:
            better = "転換前後法"
        else:
            better = "転換前後法" if conv_err < sim_err else "類似店法"
        rows.append({
            "unit": p.unit, "metric": p.metric,
            "metric_label": mcfg.metric_label(p.metric),
            "similar_point": p.point, "similar_err": sim_err,
            "conversion_point": c.point if c else None,
            "conversion_err": conv_err,
            "baseline": c.baseline if c else None,
            "ratio": c.ratio.ratio if c else None,
            "better": better,
            "gap_pct": (((c.point - p.point) / abs(p.point) * 100)
                        if (c and p.point) else None),
        })
    return pd.DataFrame(rows)
