"""入力の読み込みから分析結果の組み立てまで。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import axes as axes_mod
from . import checklist as checklist_mod
from . import customers as customers_mod
from . import direction as direction_mod
from . import rangecheck as range_mod
from . import ramp as ramp_mod
from . import risk as risk_mod
from .conversion import ConversionResult, compare_methods, run_conversion
from .config import Config
from .idpos import LEVELS, IdposData, load_idpos
from .io_loader import InputError, load_new_store, load_store_master
from .metrics_config import MetricsConfig
from .predict import LooResult, Prediction, predict_new_store, run_loo
from .search_profile import load_search_profile, merge_search_profile
from .similarity import SimilarityResult, compute_similarity


@dataclass
class Analysis:
    cfg: Config
    mcfg: MetricsConfig
    stores: pd.DataFrame
    new_store: pd.Series
    idpos: IdposData
    similarity: SimilarityResult
    urban_new: axes_mod.UrbanRuralResult
    urban_stores: dict[str, axes_mod.UrbanRuralResult]
    ranges: list[range_mod.RangeItem]
    confidence: dict
    loo: LooResult
    predictions: list[Prediction]
    age_mix: customers_mod.AgeMixPrediction | None
    age_mix_by_unit: dict[str, customers_mod.AgeMixPrediction]
    risks: list[risk_mod.CategoryRisk]
    directions: dict[str, direction_mod.DirectionResult]
    rural_gaps: list[dict]
    checklist: list[dict]
    peers: list[Any]
    level_scan: pd.DataFrame
    ramp: ramp_mod.RampResult | None = None
    conversion: ConversionResult | None = None
    method_compare: pd.DataFrame = field(default_factory=pd.DataFrame)
    customers_known: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def new_name(self) -> str:
        return str(self.new_store[self.cfg.name_key])

    @property
    def new_id(self) -> str:
        return str(self.new_store[self.cfg.id_key])

    def predictions_by_unit(self) -> dict[str, dict[str, Prediction]]:
        out: dict[str, dict[str, Prediction]] = {}
        for p in self.predictions:
            out.setdefault(p.unit, {})[p.metric] = p
        return out


def run_analysis(
    *,
    config_path: str | Path,
    metrics_path: str | Path,
    master_path: str | Path,
    new_store_path: str | Path,
    idpos_path: str | Path,
    margin_path: str | Path | None = None,
    customers_path: str | Path | None = None,
    prior_idpos_path: str | Path | None = None,
    prior_margin_path: str | Path | None = None,
    prior_customers_path: str | Path | None = None,
    baseline_idpos_path: str | Path | None = None,
    baseline_margin_path: str | Path | None = None,
    baseline_customers_path: str | Path | None = None,
    search_path: str | Path | None = None,
    level: str | None = None,
    weight_overrides: dict[str, float] | None = None,
    top_n: int | None = None,
    level_scan: bool = True,
) -> Analysis:
    cfg = Config.load(config_path)
    mcfg = MetricsConfig.load(metrics_path)
    warnings: list[str] = []

    stores = load_store_master(master_path, cfg)
    new_store = load_new_store(new_store_path, cfg)

    if search_path:
        search = load_search_profile(search_path, cfg)
        stores, notes = merge_search_profile(stores, search, cfg)
        one = pd.DataFrame([new_store])
        one, notes2 = merge_search_profile(one, search, cfg)
        new_store = one.iloc[0]
        warnings += notes + [n for n in notes2 if "該当行がない" not in n]
        from .io_loader import add_derived
        stores = add_derived(stores, cfg)
        new_store = add_derived(pd.DataFrame([new_store]), cfg).iloc[0]

    # 開店・改装オープン日を商圏マスタから拾い、開店直後の週を水準から外す
    opens = _opening_dates(stores, cfg)
    opens.update(_opening_dates(pd.DataFrame([new_store]), cfg))

    idpos = load_idpos(idpos_path, margin_path, mcfg, level=level,
                       opening_dates=opens, customers_path=customers_path)
    warnings += idpos.warnings
    if not opens:
        warnings.append(
            "open_date（開店・改装オープン日）が1店も入っていません。"
            "転換時期が店ごとに違う場合、開店直後の需要が水準に混ざり、"
            "店舗間の比較が歪みます。商圏マスタか検索データのどちらかに、"
            "`2024-03-15` の形で必ず入れてください（検索データ側で構いません）。"
        )

    # ---- 立ち上がりカーブ ----
    # 開店から日が浅い店を切り捨てず、成熟水準に割り戻して使えるようにする。
    ramp = None
    if opens:
        curves = ramp_mod.estimate_ramp(idpos, opens)
        ramp = ramp_mod.apply_ramp(idpos, opens, curves)
        n = ramp_mod.apply_to_idpos(idpos, ramp)
        warnings += ramp.warnings
        if n:
            warnings.append(
                f"{n}件の店舗×単位に立ち上がり補正を当て、"
                f"{len(ramp.adjusted_stores)}店を予測の材料に戻しました。"
            )

    # 店舗CDの突き合わせ
    master_ids = set(stores[cfg.id_key].astype(str))
    idpos_ids = set(idpos.store_ids)
    missing = sorted(master_ids - idpos_ids)
    extra = sorted(idpos_ids - master_ids)
    if missing:
        excluded_all = set()
        if not idpos.maturity.empty:
            m = idpos.maturity
            excluded_all = set(m.loc[m["weeks_used"] <= 0, "store_id"].astype(str))
        immature = [s_ for s_ in missing if s_ in excluded_all]
        unknown = [s_ for s_ in missing if s_ not in excluded_all]
        if immature:
            warnings.append(
                f"開店から日が浅く、使える週が残らなかった店舗: {immature}。"
                "開店効果を除くと水準が測れないため、予測の材料から外しました。"
                "週がたまれば自動的に戻ります。"
            )
        if unknown:
            warnings.append(
                f"商圏マスタにあってIDPOSに無い店舗CD: {unknown}。"
                "店舗CDの桁（先頭ゼロ）が合っているか確認してください。"
            )
    if extra:
        warnings.append(f"IDPOSにあって商圏マスタに無い店舗CD: {extra}（無視します）。")
    stores = stores[stores[cfg.id_key].astype(str).isin(idpos_ids)].reset_index(drop=True)
    if stores.empty:
        # ここで止めないと、全セルが「—」のレポートが出て原因が埋もれる。
        raise InputError(
            "商圏マスタとIDPOSで、店舗CDが一致する店が1つもありません。\n"
            f"  商圏マスタの store_id : {sorted(master_ids)[:8]}\n"
            f"  IDPOSの店舗CD        : {sorted(idpos_ids)[:8]}\n"
            "\n"
            "  予測はすべて既存店の実績から作るため、この2つが突き合わないと\n"
            "  数字は1つも出せません（全項目が『—』になります）。\n"
            "\n"
            "  商圏マスタの store_id を、IDPOSの店舗CDと同じ表記に直してください。\n"
            "  店舗名ではなくコード（先頭ゼロを含む桁数そのまま。例 0785）です。\n"
            "  ①蓄積データ タブの『既存店の商圏マスタ』から修正できます。"
        )
    if len(stores) < 3:
        warnings.append(
            f"両方に揃っている既存店が{len(stores)}店しかありません。LOO検証の意味が薄くなります。"
        )

    sim = compute_similarity(stores, new_store, cfg, weight_overrides=weight_overrides)
    if sim.skipped:
        warnings.append(
            "既存店の間でばらつきが無く、類似度計算から外した変数: "
            + "、".join(cfg.label(k) for k in sim.skipped)
        )

    # 軸が丸ごと使えないと、類似店は残りの軸だけで選ばれる。黙って進めない。
    # 軸が使えない理由は2つあり、打ち手が違う。取り違えると直せない。
    #   (a) 既存店の間でその変数にばらつきが無い → 既存店マスタを埋める
    #   (b) 新店の入力が空              → 新店の商圏・検索データを埋める
    degenerate = set(sim.skipped)
    empty_axes, thin_axes = [], []
    for axis, spec in cfg.axes.items():
        keys = spec.get("variables", [])
        used = [k for k in keys if sim.z_new.get(k) is not None]
        label = spec["label"].split("（")[0]
        if not used:
            by_var = [k for k in keys if k in degenerate]
            if len(by_var) == len(keys):
                why = "既存店の間でばらつきが無いため"
            elif by_var:
                why = "既存店にばらつきが無い変数と、新店が空の変数が混在しているため"
            else:
                why = "新店の入力が空のため"
            empty_axes.append(f"{label}（{why}）")
        elif len(used) < len(keys) / 2:
            thin_axes.append(f"{label}（{len(used)}/{len(keys)}）")
    if empty_axes:
        warnings.append(
            f"まるごと使えなかった軸があります: {'、'.join(empty_axes)}。"
            "類似店は残りの軸だけで選ばれています。"
            "「ばらつきが無い」は既存店マスタの値が全店同じか空という意味なので、"
            "①蓄積データ の商圏マスタを埋めてください。"
            "「新店の入力が空」なら、新店の商圏データと⑤検索データを埋めてください。"
        )
    if thin_axes:
        warnings.append(
            f"入力が半分未満しか埋まっていない軸: {'、'.join(thin_axes)}。"
            "その軸の距離は不安定です。"
        )

    urban_new = axes_mod.judge_urban_rural(new_store, cfg)
    urban_stores = {
        str(r[cfg.id_key]): axes_mod.judge_urban_rural(r, cfg) for _, r in stores.iterrows()
    }
    rural_ids = [sid for sid, u in urban_stores.items() if u.is_rural]
    if urban_new.is_rural and len(rural_ids) <= 1:
        warnings.append(
            f"新店は郊外寄りですが、既存店の郊外は{len(rural_ids)}店しかありません。"
            "類似店法の根拠はかなり弱く、予測値は出発点としてのみ扱ってください。"
        )
    elif urban_new.verdict == "中間":
        warnings.append("新店は都市部とも郊外とも言い切れない位置です。両方の類似店を見てください。")

    ranges = range_mod.check_ranges(stores, new_store, cfg)
    confidence = range_mod.confidence_note(ranges, cfg.all_model_keys())

    loo = run_loo(stores, cfg, idpos, mcfg, weight_overrides=weight_overrides)
    predictions = predict_new_store(stores, new_store, cfg, idpos, mcfg, loo,
                                    weight_overrides=weight_overrides)

    n_peers = top_n or int(mcfg.prediction.get("top_n_peers", 2))
    peers = sim.ranking[:n_peers]

    age_mix = customers_mod.predict_age_mix(
        stores, new_store, idpos, cfg, top_n=n_peers,
        power=float(mcfg.prediction.get("similarity_power", 2.0)),
        weight_overrides=weight_overrides,
    )
    age_by_unit: dict[str, customers_mod.AgeMixPrediction] = {}
    for unit in idpos.units:
        a = customers_mod.predict_age_mix(
            stores, new_store, idpos, cfg, unit=unit, top_n=n_peers,
            power=float(mcfg.prediction.get("similarity_power", 2.0)),
            weight_overrides=weight_overrides,
        )
        if a is not None:
            age_by_unit[unit] = a

    risks = risk_mod.assess_risk(predictions, ranges, stores, new_store, cfg, mcfg, idpos)

    directions: dict[str, direction_mod.DirectionResult] = {}
    for mk in ("pi", "gp_pi", "gross_margin_rate"):
        if mk not in idpos.metrics:
            continue
        levels = idpos.frame(mk)
        directions[mk] = direction_mod.analyze_directions(
            stores, levels, cfg, metric_label=mcfg.metric_label(mk)
        )
    rural_gaps = (direction_mod.urban_vs_rural_gaps(stores, idpos.frame("pi"), cfg, rural_ids)
                  if "pi" in idpos.metrics else [])

    # ---- 転換前後法 ----
    conversion = None
    compare = pd.DataFrame()
    if prior_idpos_path:
        prior = load_idpos(prior_idpos_path, prior_margin_path, mcfg, level=idpos.level,
                           customers_path=prior_customers_path,
                           exclude_opening_weeks=0)
        baseline = None
        if baseline_idpos_path:
            baseline = load_idpos(baseline_idpos_path, baseline_margin_path, mcfg,
                                  level=idpos.level,
                                  customers_path=baseline_customers_path,
                                  exclude_opening_weeks=0)
        conversion = run_conversion(prior, idpos, baseline,
                                    str(new_store[cfg.id_key]), mcfg)
        warnings += conversion.warnings
        if conversion.predictions:
            compare = compare_methods(predictions, conversion, mcfg, loo)

    scan = (_level_scan(cfg, mcfg, stores, idpos_path, margin_path, weight_overrides,
                        opens)
            if level_scan else pd.DataFrame())

    chk = checklist_mod.build_checklist(
        predictions, risks, loo, age_mix,
        min_weeks=int(mcfg.verification.get("min_weeks_for_judgement", 4)),
        exclude_first_weeks=int(mcfg.verification.get("exclude_first_weeks", 2)),
    )

    return Analysis(
        cfg=cfg, mcfg=mcfg, stores=stores, new_store=new_store, idpos=idpos,
        similarity=sim, urban_new=urban_new, urban_stores=urban_stores,
        ranges=ranges, confidence=confidence, loo=loo, predictions=predictions,
        age_mix=age_mix, age_mix_by_unit=age_by_unit, risks=risks,
        directions=directions, rural_gaps=rural_gaps, checklist=chk,
        peers=peers, level_scan=scan, ramp=ramp, conversion=conversion,
        method_compare=compare,
        customers_known=("gp_pi" in idpos.metrics),
        warnings=warnings,
    )


def _opening_dates(df: pd.DataFrame, cfg: Config) -> dict[str, str]:
    if "open_date" not in df.columns:
        return {}
    out = {}
    for _, r in df.iterrows():
        v = r.get("open_date")
        if v is not None and pd.notna(v) and str(v).strip():
            out[str(r[cfg.id_key])] = str(v).strip()
    return out


def _level_scan(cfg, mcfg, stores, idpos_path, margin_path, weight_overrides,
                opens=None) -> pd.DataFrame:
    """粒度を下げると予測精度がどこで崩れるかを測る。

    サブカテゴリーまで下げれば提案は具体的になるが、1セルあたりの数字は
    小さく不安定になり、5店舗では当たらなくなる。その境目を数字で出す。
    """
    rows = []
    for lv in ("line", "department", "category", "subcategory"):
        try:
            d = load_idpos(idpos_path, margin_path, mcfg, level=lv,
                           opening_dates=opens)
        except Exception:
            continue
        if not d.units:
            continue
        lo = run_loo(stores, cfg, d, mcfg, weight_overrides=weight_overrides)
        if not lo.mape:
            continue
        for met in d.metrics:
            vals = [v for (u, m), v in lo.mape.items() if m == met]
            base = [v for (u, m), v in lo.baseline_mape.items() if m == met]
            if not vals:
                continue
            rows.append({
                "粒度": {"line": "ライン", "department": "部門",
                         "category": "カテゴリー", "subcategory": "サブカテゴリー"}[lv],
                "単位数": len(d.units),
                "指標": mcfg.metric_label(met),
                "metric": met,
                "LOO平均誤差%": float(np.mean(vals)),
                "全店平均の誤差%": float(np.mean(base)) if base else None,
                "週次変動の中央値%": float(np.nanmedian([
                    st.cv * 100 for (s, u, m), st in d.stats.items()
                    if m == met and st.cv is not None
                ])) if any(m == met for (_, _, m) in d.stats) else None,
            })
    return pd.DataFrame(rows)
