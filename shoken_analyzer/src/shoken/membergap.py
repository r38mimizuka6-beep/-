"""会員の性別年代構成比と、商圏統計の年齢構成のズレ。

ズレが大きい店ほど「商圏統計どおりの人が来ているわけではない」ので、
新店で商圏統計を根拠に使うときの割引率の目安になる。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Config


@dataclass
class MemberGapRow:
    bucket: str
    member_share: float | None
    area_share: float | None
    gap_pt: float | None       # 会員 - 商圏（ptポイント）

    @property
    def lean(self) -> str:
        if self.gap_pt is None:
            return "-"
        if self.gap_pt > 0.03:
            return "会員が多い（商圏より来店が厚い）"
        if self.gap_pt < -0.03:
            return "会員が少ない（商圏にいるが来ていない）"
        return "ほぼ一致"


@dataclass
class MemberGapResult:
    store_id: str
    store_name: str
    rows: list[MemberGapRow]
    tvd: float | None          # 総変動距離 = Σ|差| / 2。0=完全一致、1=全く別
    gender_mix: dict[str, float]

    @property
    def reliability(self) -> str:
        if self.tvd is None:
            return "判定不能"
        if self.tvd < 0.08:
            return "高（商圏年齢構成をほぼそのまま使える）"
        if self.tvd < 0.15:
            return "中（方向性の根拠には使えるが水準はずれる）"
        return "低（商圏年齢構成をそのまま来店客に当てない）"


def _area_shares(row: pd.Series, area_keys: list[str]) -> float | None:
    vals = [row.get(k) for k in area_keys]
    vals = [float(v) for v in vals if v is not None and pd.notna(v)]
    return sum(vals) if vals else None


def analyze_member_gap(
    stores: pd.DataFrame, member_mix: pd.DataFrame, cfg: Config
) -> list[MemberGapResult]:
    buckets = cfg.member_buckets
    if not buckets:
        return []
    id_key = cfg.id_key
    results: list[MemberGapResult] = []

    for _, srow in stores.iterrows():
        sid = str(srow[id_key])
        m = member_mix[member_mix[id_key] == sid]
        if m.empty:
            continue
        by_band = m.groupby("age_band")["member_share"].sum()
        total = by_band.sum()
        if total <= 0:
            continue
        by_band = by_band / total

        # 商圏側は5区分のうち15歳以上だけで正規化する（会員に子どもがいないため）
        area_raw = {b: _area_shares(srow, spec["area"]) for b, spec in buckets.items()}
        area_sum = sum(v for v in area_raw.values() if v is not None)

        rows, abs_sum, usable = [], 0.0, True
        for bucket, spec in buckets.items():
            ms = float(sum(by_band.get(b, 0.0) for b in spec["member"]))
            a = area_raw[bucket]
            asr = (a / area_sum) if (a is not None and area_sum > 0) else None
            gap = (ms - asr) if asr is not None else None
            if gap is None:
                usable = False
            else:
                abs_sum += abs(gap)
            rows.append(MemberGapRow(bucket, ms, asr, gap))

        gender = m.groupby("gender")["member_share"].sum()
        gtotal = gender.sum()
        gmix = {str(k): float(v / gtotal) for k, v in gender.items()} if gtotal > 0 else {}

        results.append(MemberGapResult(
            store_id=sid, store_name=str(srow[cfg.name_key]), rows=rows,
            tvd=(abs_sum / 2 if usable else None), gender_mix=gmix,
        ))
    return results
