"""顧客層（年代別）の予測。性別は使わない（不明層が多く信用できないため）。

2つの方法で出して、両方を並べる。食い違う場合はそれ自体が不確実性の表示になる。
  方法A 類似店ベース : 類似店の会員年代構成をそのまま加重平均する
  方法B 商圏補正ベース: 商圏の年齢構成に、既存5店で共通の「来店バイアス」をかける

来店バイアス = 会員構成比 ÷ 商圏年齢構成比。5店で平均を取る。
これが店によって大きくばらつくなら、方法Bは使えない（レポートに出す）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .idpos import IdposData
from .similarity import compute_similarity


@dataclass
class AgeMixRow:
    band: str
    peer_based: float | None
    area_based: float | None
    blended: float | None
    area_share: float | None       # 商圏の年齢構成（10代以上で再正規化）
    bias_mean: float | None        # 既存5店の平均来店バイアス
    bias_cv: float | None          # そのばらつき


@dataclass
class AgeMixPrediction:
    rows: list[AgeMixRow]
    peer_names: list[str]
    loo_mae_peer_pt: float | None      # LOOでの平均絶対誤差（ptポイント）
    loo_mae_area_pt: float | None
    loo_mae_blend_pt: float | None
    bias_usable: bool
    notes: list[str] = field(default_factory=list)

    @property
    def best_method(self) -> str:
        cands = {"類似店ベース": self.loo_mae_peer_pt,
                 "商圏補正ベース": self.loo_mae_area_pt,
                 "両者の平均": self.loo_mae_blend_pt}
        cands = {k: v for k, v in cands.items() if v is not None}
        return min(cands, key=cands.get) if cands else "判定不能"


def _member_vector(idpos: IdposData, store_id: str, bands: list[str],
                   unit: str | None = None) -> dict[str, float] | None:
    """IDPOSの年代別売上から、その店の年代構成を出す。

    実データには性別が無く、年代だけ。顧客種類の絞り込みは idpos 側で済ませてある。
    """
    mix = idpos.store_age_mix(store_id, unit)
    if not mix:
        return None
    sub = {b: float(mix.get(b, 0.0)) for b in bands}
    total = sum(sub.values())
    return {b: v / total for b, v in sub.items()} if total > 0 else None


def _area_vector(row: pd.Series, cfg: Config, bands: list[str]) -> dict[str, float] | None:
    buckets = cfg.member_buckets
    raw = {}
    for b in bands:
        keys = buckets[b]["area"]
        vals = [row.get(k) for k in keys]
        vals = [float(v) for v in vals if v is not None and pd.notna(v)]
        if not vals:
            return None
        raw[b] = sum(vals)
    total = sum(raw.values())
    if total <= 0:
        return None
    return {b: v / total for b, v in raw.items()}   # 会員にいない年代を除いて再正規化


def _bias_table(stores: pd.DataFrame, idpos: IdposData, cfg: Config,
                bands: list[str], exclude: str | None = None,
                unit: str | None = None) -> dict[str, list[float]]:
    bias: dict[str, list[float]] = {b: [] for b in bands}
    for _, r in stores.iterrows():
        sid = str(r[cfg.id_key])
        if exclude and sid == exclude:
            continue
        mv = _member_vector(idpos, sid, bands, unit)
        av = _area_vector(r, cfg, bands)
        if not mv or not av:
            continue
        for b in bands:
            if av[b] > 0:
                bias[b].append(mv[b] / av[b])
    return bias


def _area_based(row: pd.Series, cfg: Config, bands: list[str],
                bias: dict[str, list[float]]) -> dict[str, float] | None:
    av = _area_vector(row, cfg, bands)
    if not av:
        return None
    est = {}
    for b in bands:
        vals = bias.get(b) or []
        if not vals:
            return None
        est[b] = av[b] * float(np.mean(vals))
    total = sum(est.values())
    return {b: v / total for b, v in est.items()} if total > 0 else None


def _peer_based(pool: pd.DataFrame, target: pd.Series, cfg: Config,
                idpos: IdposData, bands: list[str], *, top_n: int,
                power: float, weight_overrides,
                unit: str | None = None) -> tuple[dict[str, float] | None, list[str]]:
    sim = compute_similarity(pool, target, cfg, weight_overrides=weight_overrides)
    chosen, vecs = [], []
    for s in sim.ranking:
        v = _member_vector(idpos, s.store_id, bands, unit)
        if v is not None:
            chosen.append(s)
            vecs.append(v)
        if len(chosen) >= top_n:
            break
    if not chosen:
        return None, []
    raw = [max(s.similarity, 1e-9) ** power for s in chosen]
    tot = sum(raw)
    ws = [r / tot for r in raw]
    est = {b: float(sum(w * v[b] for w, v in zip(ws, vecs))) for b in bands}
    t = sum(est.values())
    return ({b: v / t for b, v in est.items()} if t > 0 else None,
            [s.store_name for s in chosen])


def _mae_pt(a: dict[str, float], b: dict[str, float]) -> float:
    return float(np.mean([abs(a[k] - b[k]) for k in a])) * 100


def predict_age_mix(
    stores: pd.DataFrame,
    new_store: pd.Series,
    idpos: IdposData,
    cfg: Config,
    *,
    unit: str | None = None,
    top_n: int = 2,
    power: float = 2.0,
    weight_overrides: dict[str, float] | None = None,
) -> AgeMixPrediction | None:
    """unit を指定すると、そのカテゴリーの購買層だけで推定する。"""
    if not cfg.member_buckets or not idpos.age_bands:
        return None
    bands = [b for b in cfg.member_buckets if b in idpos.age_bands]
    if not bands:
        return None
    notes: list[str] = []

    unk = [v for v in idpos.age_unknown_share.values() if v is not None]
    if unk and max(unk) > 0.15:
        notes.append(
            f"年代が取れていない売上が最大{max(unk):.0%}あります（非会員など）。"
            "ここで出す年代構成は、年代が分かっている分だけの構成比です。"
        )

    bias = _bias_table(stores, idpos, cfg, bands, unit=unit)
    bias_cv = {}
    for b in bands:
        v = bias.get(b) or []
        bias_cv[b] = (float(np.std(v, ddof=1) / np.mean(v))
                      if len(v) > 1 and np.mean(v) != 0 else None)
    worst_cv = max((c for c in bias_cv.values() if c is not None), default=None)
    bias_usable = worst_cv is not None and worst_cv < 0.35
    if not bias_usable:
        notes.append(
            f"来店バイアス（会員構成比÷商圏年齢構成比）が店舗間でばらついています"
            f"（最大変動係数 {worst_cv:.0%}）。商圏補正ベースの推定は参考値にとどめてください。"
            if worst_cv is not None else
            "来店バイアスを計算できる店舗が足りません。"
        )

    peer_est, peer_names = _peer_based(stores, new_store, cfg, idpos, bands,
                                       top_n=top_n, power=power,
                                       weight_overrides=weight_overrides, unit=unit)
    area_est = _area_based(new_store, cfg, bands, bias)
    area_self = _area_vector(new_store, cfg, bands)

    blended = None
    if peer_est and area_est:
        blended = {b: (peer_est[b] + area_est[b]) / 2 for b in bands}
        gap = _mae_pt(peer_est, area_est)
        if gap > 3.0:
            notes.append(
                f"2つの方法の食い違いが平均{gap:.1f}pt あります。"
                "年代構成は読み切れていないと考えてください。"
            )

    # LOO: 1店抜きで同じ推定をして、実績の会員構成と比べる
    errs = {"peer": [], "area": [], "blend": []}
    for i in range(len(stores)):
        target = stores.iloc[i]
        sid = str(target[cfg.id_key])
        actual = _member_vector(idpos, sid, bands, unit)
        if actual is None:
            continue
        pool = stores.drop(stores.index[i])
        if len(pool) < 2:
            continue
        pe, _ = _peer_based(pool, target, cfg, idpos, bands, top_n=top_n,
                            power=power, weight_overrides=weight_overrides, unit=unit)
        ae = _area_based(target, cfg, bands,
                         _bias_table(stores, idpos, cfg, bands, exclude=sid, unit=unit))
        if pe:
            errs["peer"].append(_mae_pt(pe, actual))
        if ae:
            errs["area"].append(_mae_pt(ae, actual))
        if pe and ae:
            errs["blend"].append(_mae_pt({b: (pe[b] + ae[b]) / 2 for b in bands}, actual))

    rows = [
        AgeMixRow(
            band=b,
            peer_based=peer_est.get(b) if peer_est else None,
            area_based=area_est.get(b) if area_est else None,
            blended=blended.get(b) if blended else None,
            area_share=area_self.get(b) if area_self else None,
            bias_mean=float(np.mean(bias[b])) if bias.get(b) else None,
            bias_cv=bias_cv.get(b),
        )
        for b in bands
    ]
    return AgeMixPrediction(
        rows=rows, peer_names=peer_names,
        loo_mae_peer_pt=float(np.mean(errs["peer"])) if errs["peer"] else None,
        loo_mae_area_pt=float(np.mean(errs["area"])) if errs["area"] else None,
        loo_mae_blend_pt=float(np.mean(errs["blend"])) if errs["blend"] else None,
        bias_usable=bias_usable, notes=notes,
    )
