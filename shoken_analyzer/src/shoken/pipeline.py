"""入力の読み込みから分析結果の組み立てまで。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from . import axes as axes_mod
from . import direction as direction_mod
from . import membergap as member_mod
from . import rangecheck as range_mod
from . import shelf as shelf_mod
from .config import Config
from .io_loader import load_member_mix, load_new_store, load_sales_mix, load_store_master
from .similarity import SimilarityResult, compute_similarity


@dataclass
class Analysis:
    cfg: Config
    stores: pd.DataFrame
    new_store: pd.Series
    similarity: SimilarityResult
    urban_new: axes_mod.UrbanRuralResult
    urban_stores: dict[str, axes_mod.UrbanRuralResult]
    ranges: list[range_mod.RangeItem]
    confidence: dict
    directions: direction_mod.DirectionResult
    rural_gaps: list[dict]
    member_gaps: list[member_mod.MemberGapResult]
    shelf: list[shelf_mod.ShelfProposal]
    checklist: list[dict]
    peers: list[Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def new_name(self) -> str:
        return str(self.new_store[self.cfg.name_key])


def run_analysis(
    *,
    config_path: str | Path,
    master_path: str | Path,
    new_store_path: str | Path,
    sales_path: str | Path,
    member_path: str | Path | None = None,
    weight_overrides: dict[str, float] | None = None,
    top_n: int | None = None,
) -> Analysis:
    cfg = Config.load(config_path)
    warnings: list[str] = []

    stores = load_store_master(master_path, cfg)
    new_store = load_new_store(new_store_path, cfg)
    sales_mix = load_sales_mix(sales_path, cfg)
    if "warning" in sales_mix.attrs:
        warnings.append(sales_mix.attrs["warning"])
    member_mix = load_member_mix(member_path, cfg) if member_path else None

    if len(stores) < 5:
        warnings.append(
            f"既存店が{len(stores)}店です。本ツールは5店前後を前提にしています。"
            "店舗数が少ないほど、距離の標準化も方向性分析も不安定になります。"
        )

    sim = compute_similarity(stores, new_store, cfg, weight_overrides=weight_overrides)
    if sim.skipped:
        labels = "、".join(cfg.label(k) for k in sim.skipped)
        warnings.append(
            f"既存店の間でばらつきが無く、類似度計算から外した変数があります: {labels}"
        )

    urban_new = axes_mod.judge_urban_rural(new_store, cfg)
    urban_stores = {
        str(r[cfg.id_key]): axes_mod.judge_urban_rural(r, cfg) for _, r in stores.iterrows()
    }
    rural_ids = [sid for sid, u in urban_stores.items() if u.is_rural]

    if urban_new.is_rural:
        if len(rural_ids) <= 1:
            warnings.append(
                f"新店は郊外寄りと判定されましたが、既存店の郊外は{len(rural_ids)}店しかありません。"
                "類似店法の根拠はかなり弱く、本レポートの数値は出発点としてのみ扱ってください。"
            )
    elif urban_new.verdict == "中間":
        warnings.append(
            "新店は都市部とも郊外とも言い切れない位置です。"
            "都市部寄り・郊外寄りの両方の類似店で棚割レンジを確認してください。"
        )

    ranges = range_mod.check_ranges(stores, new_store, cfg)
    confidence = range_mod.confidence_note(ranges, cfg.all_model_keys())

    directions = direction_mod.analyze_directions(stores, sales_mix, cfg)
    rural_gaps = direction_mod.urban_vs_rural_gaps(stores, sales_mix, cfg, rural_ids)
    member_gaps = member_mod.analyze_member_gap(stores, member_mix, cfg) if member_mix is not None else []

    n = top_n or int(cfg.report.get("top_n_similar", 2))
    peers = sim.ranking[:n]
    peer_ids = [p.store_id for p in peers]
    peer_names = {p.store_id: p.store_name for p in peers}
    shelf = shelf_mod.build_shelf_proposal(sales_mix, cfg, peer_ids, peer_names)

    gap_note = None
    if member_gaps:
        worst = max((g for g in member_gaps if g.tvd is not None), key=lambda g: g.tvd, default=None)
        if worst is not None:
            gap_note = (
                f"既存店では会員構成と商圏年齢構成のズレが最大{worst.tvd:.0%}（{worst.store_name}）。"
                "新店でも同程度のズレを見込み、実測後に商圏統計の使い方を見直す。"
            )
    checklist = shelf_mod.idpos_checklist(shelf, gap_note)

    return Analysis(
        cfg=cfg, stores=stores, new_store=new_store, similarity=sim,
        urban_new=urban_new, urban_stores=urban_stores, ranges=ranges,
        confidence=confidence, directions=directions, rural_gaps=rural_gaps,
        member_gaps=member_gaps, shelf=shelf, checklist=checklist,
        peers=peers, warnings=warnings,
    )
