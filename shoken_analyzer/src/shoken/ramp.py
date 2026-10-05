"""開店からの立ち上がりカーブ。

転換時期が店ごとに違うため、開店から日が浅い店は「まだ水準に達していない」。
開店直後の週を単に捨てると、新しい店は丸ごと使えなくなる（5店中2店が消える）。
捨てる代わりに、立ち上がりの形を成熟した店から測り、未成熟な店の観測値を
成熟水準に割り戻す。

  成熟水準の推定 = 観測した水準 ÷ （観測した週に対応する立ち上がり係数の平均）

立ち上がり係数を測れるのは、成熟期まで到達した店だけ。
その店数が少ないうちはカーブ自体が不確かなので、必ず店数を併記する。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .idpos import IdposData


@dataclass
class RampCurve:
    metric: str
    factors: dict[int, float]            # 開店からの週 -> 成熟水準に対する倍率
    spread: dict[int, float]             # 同じ週での店舗間のばらつき(変動係数)
    n_contributors: int
    contributors: list[str]
    mature_from: int
    mature_to: int
    usable: bool

    def factor_for(self, week: int) -> float:
        if not self.factors:
            return 1.0
        if week >= self.mature_from:
            return 1.0
        known = sorted(self.factors)
        nearest = min(known, key=lambda w: abs(w - week))
        return self.factors[nearest]

    def mean_factor(self, weeks: list[int]) -> float:
        ws = [w for w in weeks if w is not None]
        return float(np.mean([self.factor_for(w) for w in ws])) if ws else 1.0


@dataclass
class RampAdjustment:
    store_id: str
    store_name: str
    unit: str
    metric: str
    observed: float
    factor: float
    adjusted: float
    weeks_used: list[int]

    @property
    def lift_pct(self) -> float:
        return (self.adjusted / self.observed - 1) * 100 if self.observed else 0.0


@dataclass
class RampResult:
    curves: dict[str, RampCurve]
    adjustments: list[RampAdjustment]
    adjusted_stores: list[str]
    warnings: list[str] = field(default_factory=list)

    def adjusted_level(self, store: str, unit: str, metric: str) -> float | None:
        for a in self.adjustments:
            if (a.store_id, a.unit, a.metric) == (store, unit, metric):
                return a.adjusted
        return None


def _weeks_since_open(panel: pd.DataFrame, opening_dates: dict[str, str]) -> pd.DataFrame:
    df = panel.copy()
    from .idpos import parse_open_date
    od = {k: parse_open_date(v)[0] for k, v in (opening_dates or {}).items()}
    df["_open"] = df["store_id"].map(od)
    df["weeks_since_open"] = (
        (pd.to_datetime(df["week_date"]) - df["_open"]).dt.days // 7
    )
    return df[df["weeks_since_open"].notna() & (df["weeks_since_open"] >= 0)]


def estimate_ramp(
    idpos: IdposData,
    opening_dates: dict[str, str],
    *,
    metrics: list[str] | None = None,
    mature_from: int = 13,
    mature_to: int = 26,
) -> dict[str, RampCurve]:
    """成熟期まで到達した店から、開店何週目に何%まで来るかを測る。"""
    # 全指標を対象にする。粗利率や単価は実際には立ち上がらないので、
    # 係数がほぼ1.0として出てくる（それ自体が検証になる）。
    metrics = metrics or list(idpos.metrics)
    df = _weeks_since_open(idpos.panel_all, opening_dates)
    curves: dict[str, RampCurve] = {}
    if df.empty:
        return curves

    for metric in metrics:
        if metric not in df.columns:
            continue
        # 成熟期のデータがある店だけが、カーブの形を決められる
        mature = df[(df["weeks_since_open"] >= mature_from)
                    & (df["weeks_since_open"] <= mature_to)]
        base = (mature.groupby(["store_id", "unit"])[metric]
                .mean().rename("_base").reset_index())
        contributors = sorted(set(base["store_id"]))
        if not contributors:
            curves[metric] = RampCurve(metric, {}, {}, 0, [], mature_from, mature_to, False)
            continue

        d = df.merge(base, on=["store_id", "unit"], how="inner")
        d = d[(d["_base"] > 0) & (d["weeks_since_open"] < mature_from)]
        d["_ratio"] = d[metric] / d["_base"]

        factors, spread = {}, {}
        for w, g in d.groupby("weeks_since_open"):
            r = g["_ratio"].dropna()
            if len(r) < 3:
                continue
            factors[int(w)] = float(r.median())
            m = r.median()
            spread[int(w)] = float(r.std(ddof=1) / m) if m else float("nan")

        names = [idpos.store_names.get(s, s) for s in contributors]
        curves[metric] = RampCurve(
            metric=metric, factors=factors, spread=spread,
            n_contributors=len(contributors), contributors=names,
            mature_from=mature_from, mature_to=mature_to,
            usable=bool(factors) and len(contributors) >= 1,
        )
    return curves


def apply_ramp(
    idpos: IdposData,
    opening_dates: dict[str, str],
    curves: dict[str, RampCurve],
    *,
    min_weeks: int = 4,
) -> RampResult:
    """未成熟な店の観測水準を、成熟水準に割り戻す。"""
    warnings: list[str] = []
    df = _weeks_since_open(idpos.panel_all, opening_dates)
    if df.empty or not curves:
        return RampResult({}, [], [], ["立ち上がりカーブを推定できませんでした。"])

    usable = {m: c for m, c in curves.items() if c.usable}
    if not usable:
        return RampResult(curves, [], [],
                          ["成熟期まで到達した店が無く、立ち上がり補正はできません。"])

    thin = {c.n_contributors for c in usable.values()}
    if max(thin) < 2:
        warnings.append(
            f"立ち上がりカーブを測れた店が{max(thin)}店しかありません。"
            "カーブの形がその1店の個性に引きずられます。補正後の値は幅をもって見てください。"
        )

    # 成熟期に届いていない店だけを補正する
    per_store = df.groupby("store_id")["weeks_since_open"].max()
    targets = [s for s, w in per_store.items()
               if w < list(usable.values())[0].mature_from]
    adjustments: list[RampAdjustment] = []
    for sid in targets:
        g = df[df["store_id"] == sid]
        for metric, curve in usable.items():
            if metric not in g.columns:
                continue
            for unit, gu in g.groupby("unit"):
                vals = pd.to_numeric(gu[metric], errors="coerce").dropna()
                if len(vals) < min_weeks:
                    continue
                weeks = [int(w) for w in gu.loc[vals.index, "weeks_since_open"]]
                f = curve.mean_factor(weeks)
                if not np.isfinite(f) or f <= 0:
                    continue
                observed = float(vals.mean())
                adjustments.append(RampAdjustment(
                    store_id=str(sid),
                    store_name=idpos.store_names.get(str(sid), str(sid)),
                    unit=str(unit), metric=metric, observed=observed,
                    factor=f, adjusted=observed / f, weeks_used=weeks,
                ))
    if adjustments:
        names = sorted({a.store_name for a in adjustments})
        warnings.append(
            f"立ち上がり補正を適用した店舗: {'、'.join(names)}。"
            "開店直後の週しか無い店を切り捨てずに使えるようにしていますが、"
            "補正後の値は実測ではなく推定です。"
        )
    return RampResult(curves, adjustments,
                      sorted({a.store_id for a in adjustments}), warnings)


def apply_to_idpos(idpos: IdposData, result: RampResult) -> int:
    """補正後の水準を IdposData に書き戻す。

    これをやらないと、開店直後の週しか無い店は水準が測れず、
    類似店の候補からも方向性分析からも丸ごと消えてしまう。
    """
    from .idpos import CellStat

    n = 0
    for a in result.adjustments:
        key = (a.store_id, a.unit, a.metric)
        old = idpos.stats.get(key)
        idpos.stats[key] = CellStat(
            store_id=a.store_id, unit=a.unit, metric=a.metric,
            level=a.adjusted,
            median=a.adjusted,
            sd=old.sd if old else None,
            cv=old.cv if old else None,
            se=old.se if old else None,
            n_weeks=len(a.weeks_used),
            vmin=old.vmin if old else None,
            vmax=old.vmax if old else None,
            reliable=False,            # 推定値なので「信頼できる」とは言わない
        )
        n += 1
        if a.store_id not in idpos.store_ids:
            idpos.store_ids.append(a.store_id)
    idpos.store_ids = sorted(set(idpos.store_ids))
    idpos.ramp_adjusted = sorted({a.store_id for a in result.adjustments})
    return n


def trajectory(point_mature: float, curve: RampCurve, n_weeks: int = 16) -> list[dict]:
    """成熟水準の予測から、開店後の週ごとの到達見込みを出す。"""
    out = []
    for w in range(1, n_weeks + 1):
        f = curve.factor_for(w)
        out.append({"week": w, "factor": f, "value": point_mature * f,
                    "spread": curve.spread.get(w)})
    return out
