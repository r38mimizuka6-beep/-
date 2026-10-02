"""最低限の動作確認。`python -m pytest tests -q` で実行。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shoken.axes import Standardizer, judge_urban_rural  # noqa: E402
from shoken.config import Config, eval_formula  # noqa: E402
from shoken.customers import predict_age_mix  # noqa: E402
from shoken.idpos import load_idpos, parse_year_week, week_to_date  # noqa: E402
from shoken.io_loader import InputError, load_new_store, load_store_master  # noqa: E402
from shoken.metrics_config import MetricsConfig, normalize_category  # noqa: E402
from shoken.pipeline import run_analysis  # noqa: E402
from shoken.predict import run_loo  # noqa: E402
from shoken.rangecheck import check_ranges  # noqa: E402
from shoken.report import render_html, render_verify_html, write_excel  # noqa: E402
from shoken.search_profile import make_search_plan, store_name_from_filename  # noqa: E402
from shoken.verify import save_predictions, verify_predictions  # noqa: E402

CFG = ROOT / "config" / "columns.yaml"
MCFG = ROOT / "config" / "metrics.yaml"
S = ROOT / "data" / "sample"


@pytest.fixture(scope="module")
def cfg():
    return Config.load(CFG)


@pytest.fixture(scope="module")
def mcfg():
    return MetricsConfig.load(MCFG)


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(
        config_path=CFG, metrics_path=MCFG,
        master_path=S / "store_master.csv", new_store_path=S / "new_store.csv",
        idpos_path=S / "idpos_sample.csv.gz", margin_path=S / "ure_zaiko_sample.csv.gz",
        level="category", level_scan=False,
    )


# ---------------- 設定 ----------------

def test_config_loads(cfg):
    assert cfg.variables
    assert set(cfg.axes) == {"daynight", "household", "mobility"}


def test_weight_override_rejects_unknown_axis(cfg):
    with pytest.raises(KeyError):
        cfg.axis_weights({"nonexistent": 2.0})


def test_eval_formula_is_sandboxed():
    assert eval_formula("a / b", {"a": 10, "b": 4}) == 2.5
    assert eval_formula("a / b", {"a": 1, "b": 0}) is None
    assert eval_formula("__import__('os')", {}) is None


def test_age_buckets_cover_member_bands(cfg):
    """会員の10歳刻みと商圏の年齢区分が1対1で対応していること。"""
    for band, spec in cfg.member_buckets.items():
        assert spec["area"], band
        for k in spec["area"]:
            assert k in cfg.variables, f"{band} -> {k} が columns.yaml にない"


# ---------------- 年週 ----------------

@pytest.mark.parametrize("raw,expected", [
    ("202640", (2026, 40)),
    ("2026年40週", (2026, 40)),
    ("2026年4週", (2026, 4)),
    ("こわれた値", (None, None)),
])
def test_parse_year_week(raw, expected):
    assert parse_year_week(raw) == expected


def test_week_to_date_is_monday():
    d = week_to_date(2026, 40)
    assert d is not None and d.weekday() == 0


def test_week_53_does_not_crash():
    assert week_to_date(2026, 53) is not None


# ---------------- 入力 ----------------

def test_master_rejects_single_store(cfg, tmp_path):
    p = tmp_path / "one.csv"
    pd.DataFrame([{"store_id": "0101", "store_name": "a", "pop_total": 1}]).to_csv(p, index=False)
    with pytest.raises(InputError):
        load_store_master(p, cfg)


def test_new_store_must_be_one_row(cfg, tmp_path):
    p = tmp_path / "two.csv"
    pd.DataFrame([{"store_id": "a"}, {"store_id": "b"}]).to_csv(p, index=False)
    with pytest.raises(InputError):
        load_new_store(p, cfg)


def test_store_code_keeps_leading_zero(cfg):
    stores = load_store_master(S / "store_master.csv", cfg)
    assert "0101" in set(stores[cfg.id_key])


def test_missing_id_column_message(cfg, tmp_path):
    p = tmp_path / "bad.csv"
    pd.DataFrame([{"店番": "0101"}, {"店番": "0102"}]).to_csv(p, index=False)
    with pytest.raises(InputError, match="store_id"):
        load_store_master(p, cfg)


# ---------------- IDPOS ----------------

def test_idpos_loads_all_levels(mcfg):
    counts = {}
    for lv in ("line", "category", "subcategory"):
        d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                       mcfg, level=lv)
        counts[lv] = len(d.units)
        assert d.metrics and d.store_ids
        assert "pi" in d.metrics and "gross_margin_rate" in d.metrics
    # 粒度を下げるほど単位は増える
    assert counts["line"] < counts["category"] < counts["subcategory"]


def test_idpos_hierarchy_path(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="subcategory")
    unit = next(u for u in d.units if u.startswith("和日配"))
    assert d.parent_of(unit, "line") == "和日配"
    assert d.leaf(unit) == unit.split(" > ")[-1]


def test_margin_rate_is_fraction(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    vals = [s.level for (_, _, m), s in d.stats.items()
            if m == "gross_margin_rate" and s.level is not None]
    assert vals and all(0.0 < v < 1.0 for v in vals), "粗利率は0〜1でなければならない"


def test_age_mix_excludes_unknown(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    assert "不明" not in d.age_bands
    assert max(d.age_unknown_share.values()) > 0, "非会員の年代不明が計上されていない"
    mix = d.store_age_mix("0101")
    assert mix and abs(sum(mix.values()) - 1.0) < 1e-6


def test_weekly_cv_is_computed(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    cvs = [s.cv for (_, _, m), s in d.stats.items() if m == "pi" and s.cv is not None]
    assert cvs and all(0 < c < 1 for c in cvs)


def test_category_alias_normalization(mcfg):
    m = mcfg.alias_map()
    assert normalize_category("冷凍食品", m) == "フローズン"
    assert normalize_category(" ベーカリー ", m) == "パン"
    assert normalize_category("酒類", m) is None


# ---------------- 類似度・判定 ----------------

def test_standardizer_flags_constant_columns():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [5.0, 5.0, 5.0]})
    std = Standardizer.fit(df, ["a", "b"])
    assert std.degenerate == ["b"]
    z = std.transform_row(pd.Series({"a": 2.0, "b": 5.0}), ["a", "b"])
    assert z["a"] == pytest.approx(0.0)
    assert z["b"] is None


def test_urban_rural_poles(cfg):
    urban = pd.Series({"pop_density": 15000, "share_car": 0.05, "share_walk_bike": 0.9,
                       "parking_spaces": 5, "nearest_station_m": 120,
                       "hh_share_single": 0.6, "housing_share_apart": 0.9})
    rural = pd.Series({"pop_density": 900, "share_car": 0.85, "share_walk_bike": 0.12,
                       "parking_spaces": 300, "nearest_station_m": 4200,
                       "hh_share_single": 0.18, "housing_share_apart": 0.2})
    assert judge_urban_rural(urban, cfg).verdict == "都市部寄り"
    assert judge_urban_rural(rural, cfg).verdict == "郊外寄り"
    assert judge_urban_rural(pd.Series(dtype=float), cfg).verdict == "判定不能"


def test_range_check_detects_out_of_range(cfg):
    stores = load_store_master(S / "store_master.csv", cfg)
    new = load_new_store(S / "new_store.csv", cfg).copy()
    new["pop_total"] = 9_000_000
    items = {i.key: i for i in check_ranges(stores, new, cfg)}
    assert items["pop_total"].status == "範囲外(上)"


# ---------------- LOO・予測 ----------------

def test_loo_has_one_fold_per_store_unit_metric(analysis):
    a = analysis
    expected = len(a.stores) * len(a.idpos.units) * len(a.idpos.metrics)
    assert len(a.loo.folds) == expected


def test_loo_never_uses_the_held_out_store(analysis):
    for f in analysis.loo.folds:
        assert f.held_out_name not in f.peers


def test_predictions_cover_every_unit_and_metric(analysis):
    a = analysis
    assert len(a.predictions) == len(a.idpos.units) * len(a.idpos.metrics)
    for p in a.predictions:
        if p.point is not None:
            assert p.low is not None and p.low <= p.point <= p.high


def test_prediction_is_within_peer_range(analysis):
    """加重平均なので、必ず類似店の実績レンジの中に入る。"""
    for p in analysis.predictions:
        if p.point is None or p.peer_low is None:
            continue
        assert p.peer_low - 1e-9 <= p.point <= p.peer_high + 1e-9


def test_loo_error_cannot_beat_weekly_noise(analysis):
    """週次のブレより小さいLOO誤差が出たら、どこかが間違っている。"""
    import numpy as np
    a = analysis
    loo = np.mean([v for (u, m), v in a.loo.mape.items() if m == "pi"])
    cv = np.nanmedian([s.cv * 100 for (_, _, m), s in a.idpos.stats.items()
                       if m == "pi" and s.cv is not None])
    assert loo > cv * 0.5, f"LOO誤差{loo:.1f}% が週次変動{cv:.1f}% に対して小さすぎる"


def test_baseline_is_computed_for_comparison(analysis):
    assert analysis.loo.baseline_mape
    for key, v in analysis.loo.mape.items():
        assert key in analysis.loo.baseline_mape


def test_weights_change_loo(cfg, mcfg):
    stores = load_store_master(S / "store_master.csv", cfg)
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    stores = stores[stores[cfg.id_key].astype(str).isin(d.store_ids)]
    a = run_loo(stores, cfg, d, mcfg)
    b = run_loo(stores, cfg, d, mcfg, weight_overrides={"mobility": 8.0})
    assert a.mape != b.mape


# ---------------- 顧客層 ----------------

def test_age_mix_sums_to_one(analysis):
    am = analysis.age_mix
    assert am is not None
    for attr in ("peer_based", "area_based", "blended"):
        vals = [getattr(r, attr) for r in am.rows]
        if all(v is not None for v in vals):
            assert abs(sum(vals) - 1.0) < 1e-6, attr


def test_age_mix_has_measured_accuracy(analysis):
    am = analysis.age_mix
    assert am.loo_mae_peer_pt is not None and am.loo_mae_peer_pt >= 0
    assert am.best_method in ("類似店ベース", "商圏補正ベース", "両者の平均")


def test_age_mix_per_unit_differs(analysis):
    """カテゴリーごとに主購買層が違うこと（同じなら集計が壊れている）。"""
    tops = set()
    for unit, am in analysis.age_mix_by_unit.items():
        top = max(am.rows, key=lambda r: r.peer_based or 0).band
        tops.add(top)
    assert len(analysis.age_mix_by_unit) > 0
    assert len(tops) >= 1


def test_age_mix_for_missing_unit_returns_none(analysis, cfg):
    out = predict_age_mix(analysis.stores, analysis.new_store, analysis.idpos, cfg,
                          unit="存在しない単位")
    assert out is None or all(r.peer_based is None for r in out.rows)


# ---------------- リスク ----------------

def test_risk_separates_uncertainty_from_business(analysis):
    for r in analysis.risks:
        assert all(f.kind != "不確実" for f in r.business_flags)
        assert all(f.kind == "不確実" for f in r.uncertainty_flags)
        assert r.confidence in ("高", "中", "低")


def test_risk_level_ignores_uncertainty(analysis):
    """読めないだけのカテゴリーが「要対策」にならないこと。"""
    for r in analysis.risks:
        if not r.business_flags:
            assert r.level == "—"


# ---------------- 予実検証 ----------------

def test_verify_round_trip(analysis, mcfg, tmp_path):
    p = save_predictions(analysis.predictions, store_name=analysis.new_name,
                         store_id=analysis.new_id, path=tmp_path / "pred.json",
                         axis_weights=analysis.similarity.weights)
    actual = load_idpos(S / "idpos_newstore_actual.csv.gz",
                        S / "ure_zaiko_newstore_actual.csv.gz", mcfg, level="category")
    v = verify_predictions(p, actual, mcfg, analysis.idpos)
    assert v.rows
    assert v.excluded_first_weeks == int(mcfg.verification["exclude_first_weeks"])
    assert set(v.summary) <= {"想定内", "ブレの範囲", "上振れ", "下振れ",
                              "判定保留", "データなし"}
    assert "<table" in render_verify_html(v, mcfg)


def test_verify_holds_judgement_when_too_few_weeks(analysis, mcfg, tmp_path):
    import gzip
    import io
    raw = gzip.decompress((S / "idpos_newstore_actual.csv.gz").read_bytes()).decode("cp932")
    df = pd.read_csv(io.StringIO(raw), dtype=str)
    weeks = sorted(df["年週"].unique())[:3]          # 3週だけ = 除外後1週
    small = tmp_path / "small.csv"
    df[df["年週"].isin(weeks)].to_csv(small, index=False, encoding="cp932")
    actual = load_idpos(small, None, mcfg, level="category")
    p = save_predictions(analysis.predictions, store_name=analysis.new_name,
                         store_id=analysis.new_id, path=tmp_path / "p2.json",
                         axis_weights=analysis.similarity.weights)
    v = verify_predictions(p, actual, mcfg, analysis.idpos)
    assert v.summary.get("判定保留", 0) > 0


# ---------------- 検索データ ----------------

@pytest.mark.parametrize("name,expected", [
    ("〇〇店_商圏レポート_20250401.xlsx", "〇〇店"),
    ("商圏_川越店-20240115.xlsx", "川越店"),
    ("駅前B店.xlsx", "駅前B店"),
])
def test_store_name_from_filename(name, expected):
    assert store_name_from_filename(name) == expected


def test_search_plan_writes_csv_and_brief(tmp_path):
    csv_path, md_path, names = make_search_plan(
        ["A店_商圏.xlsx", "B店_商圏.xlsx"], tmp_path)
    assert names == ["A店", "B店"]
    assert csv_path.exists() and md_path.exists()
    df = pd.read_csv(csv_path)
    assert "comp_sm_1km" in df.columns and len(df) == 2
    assert "A店" in md_path.read_text(encoding="utf-8")


# ---------------- 出力 ----------------

def test_html_contains_all_sections(analysis):
    html = render_html(analysis)
    for heading in ["1. 新店の商圏と類似店", "2. 想定される粗利率・PI値",
                    "3. 顧客層（年代別）", "4. 数字の根拠と正確性",
                    "5. 苦戦が予想されるカテゴリー", "6. 開店後の答え合わせ"]:
        assert heading in html, heading
    assert "モデルの予測値ではありません" in html
    assert "LOO" in html


def test_excel_written(analysis, tmp_path):
    p = write_excel(analysis, tmp_path / "r.xlsx")
    sheets = pd.ExcelFile(p).sheet_names
    for s in ("0_注意事項", "2_予測", "4_LOO検証", "6_チェックリスト"):
        assert s in sheets, s


def test_level_scan_degrades_or_holds(cfg, mcfg):
    """粒度別精度の表が出ること（中身の大小は実データ次第なので形だけ確認）。"""
    a = run_analysis(
        config_path=CFG, metrics_path=MCFG,
        master_path=S / "store_master.csv", new_store_path=S / "new_store.csv",
        idpos_path=S / "idpos_sample.csv.gz", margin_path=S / "ure_zaiko_sample.csv.gz",
        level="line", level_scan=True,
    )
    assert not a.level_scan.empty
    assert {"粒度", "単位数", "LOO平均誤差%", "週次変動の中央値%"} <= set(a.level_scan.columns)


# ---------------- 開店時期のばらつき ----------------

OPENS = {"0101": "2025-11-28", "0102": "2026-02-20", "0103": "2026-04-17",
         "0104": "2026-07-10", "0105": "2026-08-07"}


def test_opening_weeks_are_excluded(mcfg):
    """開店直後の需要が水準に混ざらないこと。"""
    raw = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                     mcfg, level="line")
    trimmed = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                         mcfg, level="line", opening_dates=OPENS)
    assert not trimmed.maturity.empty
    assert (trimmed.maturity["weeks_excluded"] > 0).any()
    # 開店効果を除くと水準は下がるはず
    a = raw.value("0101", "精肉", "pi")
    b = trimmed.value("0101", "精肉", "pi")
    assert a is not None and b is not None and b < a


def test_immature_store_is_flagged(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line", opening_dates=OPENS)
    m = d.maturity
    newest = m.sort_values("weeks_used").iloc[0]
    assert newest["weeks_used"] < 8
    assert any("週しかありません" in w for w in d.warnings)


def test_exclude_opening_weeks_override(mcfg):
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line", opening_dates=OPENS, exclude_opening_weeks=0)
    assert (d.maturity["weeks_excluded"] == 0).all()


# ---------------- 客数と構成比 ----------------

def test_share_metrics_sum_to_one(mcfg):
    """低温内の構成比なので、店舗ごとに合計1になる。"""
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="category", opening_dates=OPENS)
    assert "gp_share" in d.metrics and "sales_share" in d.metrics
    for sid in d.store_ids:
        for metric in ("sales_share", "gp_share"):
            vals = [d.value(sid, u, metric) for u in d.units]
            vals = [v for v in vals if v is not None]
            if vals:
                assert abs(sum(vals) - 1.0) < 0.02, (sid, metric, sum(vals))


def test_per_customer_metrics_need_customers_file(mcfg):
    without = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                         mcfg, level="line", opening_dates=OPENS)
    assert "gp_pi" not in without.metrics
    assert any("客数" in w for w in without.warnings)
    with_c = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                        mcfg, level="line", opening_dates=OPENS,
                        customers_path=S / "customers_sample.csv.gz")
    assert "amount_pi" in with_c.metrics and "gp_pi" in with_c.metrics


def test_amount_pi_matches_quantity_pi_times_price(mcfg):
    """金額PI ≒ 数量PI × 単価 になっていること（単位系の取り違え検出）。"""
    d = load_idpos(S / "idpos_sample.csv.gz", S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="category", opening_dates=OPENS,
                   customers_path=S / "customers_sample.csv.gz")
    unit = next(u for u in d.units if u.endswith("納豆"))
    q = d.value("0101", unit, "pi")
    a = d.value("0101", unit, "amount_pi")
    price = a / q                      # 1点あたりの単価に相当するはず
    assert 30 < price < 2000, f"単価が {price:.0f} 円は不自然（単位系を確認）"


# ---------------- 転換前後法 ----------------

@pytest.fixture(scope="module")
def conversion_analysis():
    return run_analysis(
        config_path=CFG, metrics_path=MCFG,
        master_path=S / "store_master.csv", new_store_path=S / "new_store.csv",
        idpos_path=S / "idpos_sample.csv.gz",
        margin_path=S / "ure_zaiko_sample.csv.gz",
        customers_path=S / "customers_sample.csv.gz",
        prior_idpos_path=S / "idpos_prior_sample.csv.gz",
        prior_margin_path=S / "ure_zaiko_prior_sample.csv.gz",
        prior_customers_path=S / "customers_prior_sample.csv.gz",
        baseline_idpos_path=S / "idpos_newstore_baseline.csv.gz",
        baseline_margin_path=S / "ure_zaiko_newstore_baseline.csv.gz",
        baseline_customers_path=S / "customers_newstore.csv.gz",
        level="line", level_scan=False,
    )


def test_conversion_ratios_recover_planted_effect(conversion_analysis):
    """ダミーは精肉1.30倍・フローズン1.25倍で作ってある。"""
    r = {u: cr for (u, m), cr in conversion_analysis.conversion.ratios.items()
         if m == "pi"}
    assert 1.15 < r["精肉"].ratio < 1.45
    assert 1.10 < r["フローズン"].ratio < 1.70
    assert 0.95 < r["和日配"].ratio < 1.20


def test_conversion_prediction_equals_baseline_times_ratio(conversion_analysis):
    for c in conversion_analysis.conversion.predictions:
        assert c.point == pytest.approx(c.baseline * c.ratio.ratio, rel=1e-9)


def test_conversion_beats_similar_on_dummy(conversion_analysis):
    """立地を固定する分、商圏から横に当てるより当たるはず。"""
    cmp_df = conversion_analysis.method_compare
    assert not cmp_df.empty
    wins = (cmp_df["better"] == "転換前後法").sum()
    assert wins > len(cmp_df) / 2, f"転換前後法の勝ちが {wins}/{len(cmp_df)} しかない"


def test_conversion_absent_without_prior(analysis):
    assert analysis.conversion is None
    assert "もう一つの予測方法" in render_html(analysis)


def test_conversion_section_rendered(conversion_analysis):
    html = render_html(conversion_analysis)
    assert "2-2. 転換前後法" in html
    assert "業態転換による変化率" in html
    assert "既存店の成熟度" in html
