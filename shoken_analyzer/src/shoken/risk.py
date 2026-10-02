"""苦戦が予想されるカテゴリーの検出。

「予測値が低い」だけでは苦戦とは言えない。次の5つを分けて出す。
  1. 水準が低い      : 予測が既存5店の中央値を大きく下回る
  2. 読めない        : 予測区間が広い／LOO誤差が大きい／週次のブレが大きい
  3. 商圏が範囲外    : そのカテゴリーに効く商圏指標が既存5店の外にある
  4. 競合が重い      : 近接競合の条件が既存店より厳しい
  5. 粗利貢献が小さい: 粗利PIが既存店の下位
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .idpos import IdposData
from .metrics_config import MetricsConfig
from .predict import Prediction
from .rangecheck import RangeItem


@dataclass
class RiskFlag:
    kind: str           # 水準 / 不確実 / 商圏 / 競合 / 粗利貢献
    severity: int       # 1=注意 2=警戒 3=要対策
    message: str


@dataclass
class CategoryRisk:
    unit: str
    line: str
    score: int                  # 商売上のリスク（水準・商圏・競合・粗利貢献）
    uncertainty: int            # 読めなさ（予測が当たらない度合い）
    flags: list[RiskFlag] = field(default_factory=list)
    action: str = ""

    @property
    def level(self) -> str:
        """苦戦の度合い。「読めない」ことは苦戦ではないので score だけで決める。"""
        if self.score >= 4:
            return "要対策"
        if self.score >= 2:
            return "警戒"
        if self.score >= 1:
            return "注意"
        return "—"

    @property
    def confidence(self) -> str:
        """この判定自体をどれだけ信じてよいか。"""
        if self.uncertainty >= 4:
            return "低"
        if self.uncertainty >= 2:
            return "中"
        return "高"

    @property
    def business_flags(self) -> list[RiskFlag]:
        return [f for f in self.flags if f.kind != "不確実"]

    @property
    def uncertainty_flags(self) -> list[RiskFlag]:
        return [f for f in self.flags if f.kind == "不確実"]


# ラインごとに、特に効くと考えられる商圏指標（範囲外チェックの対象を絞る）
# サブカテゴリーまで下げたときは、その親ラインのルールを使う。
CATEGORY_SENSITIVE_VARS: dict[str, list[str]] = {
    "和日配":     ["age_share_65plus", "age_dec_60s", "age_dec_70plus", "hh_share_senior"],
    "洋日配":     ["hh_share_with_child", "age_dec_30s", "age_dec_40s", "hh_share_single"],
    "フローズン": ["hh_share_single", "hh_avg_size", "share_walk_bike", "day_night_ratio"],
    "精肉":       ["hh_share_with_child", "hh_avg_size", "hh_income_avg", "share_car"],
    "パン":       ["day_night_ratio", "worker_pop_per_pop", "hh_share_single", "age_dec_20s"],
}


def assess_risk(
    predictions: list[Prediction],
    ranges: list[RangeItem],
    stores: pd.DataFrame,
    new_store: pd.Series,
    cfg: Config,
    mcfg: MetricsConfig,
    idpos: IdposData,
) -> list[CategoryRisk]:
    rules = mcfg.risk
    by_cat: dict[str, dict[str, Prediction]] = {}
    for p in predictions:
        by_cat.setdefault(p.unit, {})[p.metric] = p
    range_by_key = {r.key: r for r in ranges}

    # 粗利PIの順位づけ（既存店との比較）
    gp_rank: dict[str, int] = {}
    for cat, mets in by_cat.items():
        p = mets.get("gp_pi")
        if p is None or p.point is None or p.all_low is None:
            continue
        vals = sorted(
            [v for v in _all_store_values(cat, "gp_pi", stores, cfg, mets) if v is not None]
        )
        gp_rank[cat] = sum(1 for v in vals if v < p.point) + 1

    out: list[CategoryRisk] = []
    for cat, mets in by_cat.items():
        flags: list[RiskFlag] = []
        score = 0        # 商売上のリスク
        unc = 0          # 読めなさ

        # 1. 水準が低い
        for mk in ("pi", "gp_pi", "gross_margin_rate"):
            p = mets.get(mk)
            if p is None or p.point is None or not p.all_median:
                continue
            diff = (p.point - p.all_median) / abs(p.all_median) * 100
            if diff <= float(rules["low_vs_median_pct"]):
                sev = 2 if diff <= float(rules["low_vs_median_pct"]) * 2 else 1
                flags.append(RiskFlag(
                    "水準", sev,
                    f"{mcfg.metric_label(mk)}の予測が既存5店の中央値を{abs(diff):.0f}%下回る"
                    f"（予測 {p.point:,.4g} / 中央値 {p.all_median:,.4g}）",
                ))
                score += sev

        # 2. 読めない
        for mk, p in mets.items():
            w = p.interval_width_pct
            if w is not None and w > float(rules["wide_interval_pct"]):
                flags.append(RiskFlag(
                    "不確実", 1,
                    f"{mcfg.metric_label(mk)}の予測区間が点推定比{w:.0f}%と広い",
                ))
                unc += 1
            if p.loo_mape is not None and p.loo_mape > float(rules["loo_mape_poor"]):
                sev = 2 if p.loo_mape > float(rules["loo_mape_poor"]) * 1.5 else 1
                flags.append(RiskFlag(
                    "不確実", sev,
                    f"{mcfg.metric_label(mk)}はLOO誤差{p.loo_mape:.0f}%。"
                    "既存店でも当てられていないので、この予測は当たらない前提で",
                ))
                unc += sev
            if p.beats_baseline is False:
                flags.append(RiskFlag(
                    "不確実", 1,
                    f"{mcfg.metric_label(mk)}は類似店法が全店平均に負けている"
                    f"（{p.loo_mape:.0f}% vs {p.loo_baseline_mape:.0f}%）。"
                    "類似店を選ぶ意味がない指標",
                ))
                unc += 1
            if p.weekly_cv is not None and p.weekly_cv > float(rules["weekly_cv_high"]):
                flags.append(RiskFlag(
                    "不確実", 1,
                    f"{mcfg.metric_label(mk)}は類似店でも週次変動±{p.weekly_cv:.0%}。"
                    "週単位の判断には向かない",
                ))
                unc += 1

        # 3. 商圏が範囲外
        line = idpos.parent_of(cat, "line") or cat.split(" > ")[0]
        for key in CATEGORY_SENSITIVE_VARS.get(line, []):
            item = range_by_key.get(key)
            if item is not None and item.out_of_range:
                flags.append(RiskFlag(
                    "商圏", 2,
                    f"このカテゴリーに効く「{item.label}」が既存5店の範囲外"
                    f"（新店 {item.value:,.4g} / 既存 {item.vmin:,.4g}〜{item.vmax:,.4g}）。"
                    "類似店の実績をそのまま当てられない",
                ))
                score += 2

        # 4. 競合
        for key, label, worse_if_small in (
            ("nearest_comp_m", "最寄競合までの距離", True),
            ("comp_sm_1km", "1km内の競合SM数", False),
        ):
            item = range_by_key.get(key)
            if item is None or item.value is None or item.vmin is None:
                continue
            if worse_if_small and item.value < item.vmin:
                flags.append(RiskFlag(
                    "競合", 1,
                    f"{label}が既存5店のどれより近い（{item.value:,.0f}m < {item.vmin:,.0f}m）",
                ))
                score += 1
            if not worse_if_small and item.vmax is not None and item.value > item.vmax:
                flags.append(RiskFlag(
                    "競合", 1,
                    f"{label}が既存5店のどれより多い（{item.value:,.0f} > {item.vmax:,.0f}）",
                ))
                score += 1

        # 5. 粗利貢献
        r = gp_rank.get(cat)
        if r is not None and r <= int(rules["gp_pi_low_rank"]):
            flags.append(RiskFlag(
                "粗利貢献", 1,
                f"粗利PIの予測が既存5店中{r}番目に低い水準。"
                "数量を取っても粗利額が積み上がりにくい",
            ))
            score += 1

        out.append(CategoryRisk(unit=cat, line=line, score=score, uncertainty=unc,
                                flags=flags, action=_action_for(cat, flags)))

    out.sort(key=lambda r: (-r.score, -r.uncertainty))
    return out


def _all_store_values(cat: str, metric: str, stores: pd.DataFrame,
                      cfg: Config, mets: dict[str, Prediction]) -> list[float | None]:
    p = mets.get(metric)
    if p is None:
        return []
    return [p.all_low, p.all_high, p.all_median]


def _action_for(cat: str, flags: list[RiskFlag]) -> str:
    kinds = {f.kind for f in flags if f.kind != "不確実"}
    if not kinds:
        return "特段の手当ては不要。類似店のレンジで初期値を置き、週次で追う。"
    acts = []
    if "商圏" in kinds:
        acts.append("この商圏指標が効く前提自体を疑う。開店4週の実績を最優先で確認")
    if "競合" in kinds:
        acts.append("競合店の同カテゴリー売価・品揃えを事前に実査し、価格政策を決めてから棚を決める")
    if "水準" in kinds:
        acts.append("棚幅を類似店レンジの下限から入り、実績を見てから広げる")
    if "粗利貢献" in kinds:
        acts.append("数量より値入・PB構成で粗利を取りにいく")
    return "／".join(acts)
