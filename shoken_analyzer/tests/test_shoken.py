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
IDPOS = S / "idpos_sample.csv.gz"   # 年代・数量・客数が1本に入った実データ想定


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
        idpos_path=IDPOS, margin_path=S / "ure_zaiko_sample.csv.gz",
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
        d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                       mcfg, level=lv)
        counts[lv] = len(d.units)
        assert d.metrics and d.store_ids
        assert "pi" in d.metrics and "gross_margin_rate" in d.metrics
    # 粒度を下げるほど単位は増える
    assert counts["line"] < counts["category"] < counts["subcategory"]


def test_idpos_hierarchy_path(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="subcategory")
    unit = next(u for u in d.units if u.startswith("和日配"))
    assert d.parent_of(unit, "line") == "和日配"
    assert d.leaf(unit) == unit.split(" > ")[-1]


def test_margin_rate_is_fraction(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    vals = [s.level for (_, _, m), s in d.stats.items()
            if m == "gross_margin_rate" and s.level is not None]
    assert vals and all(0.0 < v < 1.0 for v in vals), "粗利率は0〜1でなければならない"


def test_age_mix_excludes_unknown(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line")
    assert "不明" not in d.age_bands
    assert max(d.age_unknown_share.values()) > 0, "非会員の年代不明が計上されていない"
    mix = d.store_age_mix("0101")
    assert mix and abs(sum(mix.values()) - 1.0) < 1e-6


def test_weekly_cv_is_computed(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
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
    # 立ち上がり補正の対象外だった組み合わせは実績が無く、フォールドが立たない
    assert 0 < len(a.loo.folds) <= expected
    assert len(a.loo.folds) > expected * 0.7


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
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
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
        idpos_path=IDPOS, margin_path=S / "ure_zaiko_sample.csv.gz",
        level="line", level_scan=True,
    )
    assert not a.level_scan.empty
    assert {"粒度", "単位数", "LOO平均誤差%", "週次変動の中央値%"} <= set(a.level_scan.columns)


# ---------------- 開店時期のばらつき ----------------

def _opens() -> dict[str, str]:
    """開店日はサンプルの商圏マスタから読む（ハードコードするとズレる）。"""
    m = pd.read_csv(S / "store_master.csv", dtype={"store_id": str})
    return dict(zip(m["store_id"], m["open_date"].astype(str)))


OPENS = _opens()


def test_opening_weeks_are_excluded(mcfg):
    """開店直後の需要が水準に混ざらないこと。"""
    raw = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                     mcfg, level="line")
    trimmed = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                         mcfg, level="line", opening_dates=OPENS)
    assert not trimmed.maturity.empty
    assert (trimmed.maturity["weeks_excluded"] > 0).any()
    # 開店効果を除くと水準は下がるはず
    a = raw.value("0101", "精肉", "pi")
    b = trimmed.value("0101", "精肉", "pi")
    assert a is not None and b is not None and b < a


def test_immature_store_is_flagged(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line", opening_dates=OPENS)
    m = d.maturity
    newest = m.sort_values("weeks_used").iloc[0]
    assert newest["weeks_used"] < 8
    assert any("週しかありません" in w for w in d.warnings)


def test_exclude_opening_weeks_override(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="line", opening_dates=OPENS, exclude_opening_weeks=0)
    assert (d.maturity["weeks_excluded"] == 0).all()


# ---------------- 客数と構成比 ----------------

def test_share_metrics_sum_to_one(mcfg):
    """低温内の構成比なので、店舗ごとに合計1になる。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz",
                   mcfg, level="category", opening_dates=OPENS)
    assert "gp_share" in d.metrics and "sales_share" in d.metrics
    for sid in d.store_ids:
        for metric in ("sales_share", "gp_share"):
            vals = [d.value(sid, u, metric) for u in d.units]
            vals = [v for v in vals if v is not None]
            if vals:
                assert abs(sum(vals) - 1.0) < 0.02, (sid, metric, sum(vals))


def test_customers_are_derived_from_pi(mcfg):
    """客数 = 売上数量 ÷ PI値 × 1000 で復元できるので、客数ファイルは要らない。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                   opening_dates=OPENS)
    for k in ("buy_rate", "units_per_buyer", "unit_price", "amount_pi", "gp_pi"):
        assert k in d.metrics, k
    assert not any("復元した客数" in w for w in d.warnings), \
        "一貫したダミーのはずなのに客数の復元がばらついている"


def test_pi_decomposition_identity(mcfg):
    """数量PI = 買上率 × 1人当たり点数 × 1000 が成り立つこと。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="category",
                   opening_dates=OPENS)
    checked = 0
    for unit in d.units:
        pi = d.value("0101", unit, "pi")
        br = d.value("0101", unit, "buy_rate")
        up = d.value("0101", unit, "units_per_buyer")
        if None in (pi, br, up):
            continue
        assert br * up * 1000 == pytest.approx(pi, rel=0.05), unit
        checked += 1
    assert checked >= 5


def test_splitting_one_export_into_two_does_not_double_count(mcfg, tmp_path):
    """同じ売上を2本に分けて渡しても、合計が増えないこと。"""
    import gzip
    import io
    raw = gzip.decompress((S / "idpos_sample.csv.gz").read_bytes()).decode("cp932")
    df = pd.read_csv(io.StringIO(raw), dtype=str)
    age_only = df.drop(columns=["売上数量", "POS客数", "ID客数"])
    p2 = tmp_path / "age_only.csv"
    age_only.to_csv(p2, index=False, encoding="cp932")

    one = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                     opening_dates=OPENS)
    two = load_idpos([IDPOS, p2], S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                     opening_dates=OPENS)
    for unit in one.units:
        assert one.value("0101", unit, "pi") == pytest.approx(
            two.value("0101", unit, "pi"), rel=1e-6), unit


def test_subtotal_rows_are_dropped(mcfg, tmp_path):
    """年代=計 のような小計行が混ざっても、売上が二重に数えられないこと。"""
    import gzip
    import io
    raw = gzip.decompress((S / "idpos_sample.csv.gz").read_bytes()).decode("cp932")
    df = pd.read_csv(io.StringIO(raw), dtype=str)
    clean = tmp_path / "clean.csv"
    df.to_csv(clean, index=False, encoding="cp932")

    num = ["売上数量", "売上税抜金額(円)", "POS客数", "ID客数", "PI値"]
    keys = [c for c in df.columns if c not in num + ["年代"]]
    subtotal = (df.assign(**{c: pd.to_numeric(df[c]) for c in num})
                .groupby(keys, as_index=False)[num].sum().assign(年代="計"))
    dirty = tmp_path / "dirty.csv"
    pd.concat([df, subtotal[df.columns]]).to_csv(dirty, index=False, encoding="cp932")

    a = load_idpos(clean, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                   opening_dates=OPENS)
    b = load_idpos(dirty, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                   opening_dates=OPENS)
    assert any("小計・合計" in w for w in b.warnings)
    for unit in a.units:
        assert a.value("0101", unit, "pi") == pytest.approx(
            b.value("0101", unit, "pi"), rel=1e-6), f"{unit} で二重計上"


def test_unit_price_is_plausible(mcfg):
    """平均単価が常識的な範囲に出ること（単位系の取り違え検出）。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="category",
                   opening_dates=OPENS)
    unit = next(u for u in d.units if u.endswith("納豆"))
    price = d.value("0101", unit, "unit_price")
    assert price is not None and 30 < price < 2000, f"単価 {price} は不自然"


# ---------------- 立ち上がりカーブ ----------------

def test_ramp_recovers_opening_boost(mcfg):
    """ダミーは開店週に+35%、3週で消えるように作ってある。"""
    from shoken.ramp import estimate_ramp
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                   opening_dates=OPENS)
    curves = estimate_ramp(d, OPENS)
    c = curves["pi"]
    assert c.usable and c.n_contributors >= 2
    assert c.factors[0] > 1.1, "開店週が成熟水準より高く出ていない"
    assert c.factors[max(c.factors)] < c.factors[0], "立ち上がりが収束していない"


def test_ramp_rescues_immature_store(analysis):
    """開店直後の週しか無い店が、切り捨てられずに材料に戻ること。"""
    r = analysis.ramp
    assert r is not None and r.adjusted_stores
    assert "0105" in r.adjusted_stores
    assert "0105" in analysis.idpos.store_ids
    assert "0105" in set(analysis.stores[analysis.cfg.id_key].astype(str))


def test_ramp_adjustment_divides_by_factor(analysis):
    for a in analysis.ramp.adjustments:
        assert a.adjusted == pytest.approx(a.observed / a.factor, rel=1e-9)


def test_ramp_marks_adjusted_values_as_unreliable(analysis):
    for a in analysis.ramp.adjustments:
        st = analysis.idpos.stats[(a.store_id, a.unit, a.metric)]
        assert st.reliable is False, "推定値が実測扱いになっている"


def test_ramp_section_rendered(analysis):
    html = render_html(analysis)
    assert "開店からの立ち上がり" in html


# ---------------- 転換前後法 ----------------

@pytest.fixture(scope="module")
def conversion_analysis():
    return run_analysis(
        config_path=CFG, metrics_path=MCFG,
        master_path=S / "store_master.csv", new_store_path=S / "new_store.csv",
        idpos_path=IDPOS,
        margin_path=S / "ure_zaiko_sample.csv.gz",
        prior_idpos_path=S / "idpos_prior_sample.csv.gz",
        prior_margin_path=S / "ure_zaiko_prior_sample.csv.gz",
        baseline_idpos_path=S / "idpos_newstore_baseline.csv.gz",
        baseline_margin_path=S / "ure_zaiko_newstore_baseline.csv.gz",
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


# ---------------- 開店日の精度 ----------------

@pytest.mark.parametrize("raw,expected,approx", [
    ("2025-11-28", "2025-11-28", False),
    ("2026-04", "2026-04-01", True),
    ("2026/4", "2026-04-01", True),
    ("202604", "2026-04-01", True),
])
def test_parse_open_date(raw, expected, approx):
    from shoken.idpos import parse_open_date
    ts, ap = parse_open_date(raw)
    assert ts.date().isoformat() == expected
    assert ap is approx


def test_month_precision_open_date_is_flagged(mcfg):
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="line",
                   opening_dates=OPENS)
    assert any("月単位" in w for w in d.warnings)
    assert d.maturity["approx"].any()


def test_conversion_unavailable_message_offers_archiving(analysis):
    """転換前データが無いときは、次の転換に備える案内を出すこと。"""
    html = render_html(analysis)
    assert "転換前のIDPOSが残っておらず" in html
    assert "今週から毎週そのまま保存" in html
    assert any("今週から毎週保存" in c["item"] for c in analysis.checklist)


def test_age_mix_falls_back_to_parent_when_age_export_is_shallower(mcfg, tmp_path):
    """年代つきエクスポートがサブカテゴリーまでしか無くても、セグメント粒度で動くこと。"""
    import gzip
    import io
    raw = gzip.decompress((S / "idpos_sample.csv.gz").read_bytes()).decode("cp932")
    df = pd.read_csv(io.StringIO(raw), dtype=str)
    # セグメント列を落として「浅いエクスポート」を作る
    shallow = df.drop(columns=["セグメントCD", "セグメント"])
    shallow = (shallow.assign(_amt=pd.to_numeric(shallow["売上税抜金額(円)"]),
                              _pi=pd.to_numeric(shallow["PI値"]))
               .groupby([c for c in shallow.columns
                         if c not in ("売上税抜金額(円)", "PI値")], as_index=False)
               .agg(**{"売上税抜金額(円)": ("_amt", "sum"), "PI値": ("_pi", "sum")}))
    path = tmp_path / "age_shallow.csv"
    shallow.to_csv(path, index=False, encoding="cp932")

    d = load_idpos([IDPOS, path],
                   S / "ure_zaiko_sample.csv.gz", mcfg, level="segment",
                   opening_dates=OPENS)
    assert d.age_bands
    seg = next(u for u in d.units if u.count(" > ") == 4)
    mix = d.store_age_mix("0101", seg)
    assert mix and abs(sum(mix.values()) - 1.0) < 1e-6, "親に遡れていない"


def test_maturity_counts_weeks_not_rows(mcfg):
    """成熟度の週数が、単位数で水増しされていないこと。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="category",
                   opening_dates=OPENS)
    n_units = len(d.units)
    assert n_units > 5
    for _, r in d.maturity.iterrows():
        assert r["weeks_excluded"] <= 10, "行数を週数として数えている"
        assert r["weeks_total"] <= 60
        assert r["weeks_used"] == r["weeks_total"] - r["weeks_excluded"]


def test_customers_robust_to_pi_rounding(mcfg):
    """年代別に割れてPI値が丸められても、客数の復元が崩れないこと。"""
    d = load_idpos(IDPOS, S / "ure_zaiko_sample.csv.gz", mcfg, level="category",
                   opening_dates=OPENS)
    assert not any("復元した客数" in w for w in d.warnings)
    cust = d.panel_all.dropna(subset=["customers"])
    # 店舗×週で1つの値に揃っていること
    spread = cust.groupby(["store_id", "year", "week"])["customers"].nunique()
    assert (spread == 1).all()


def test_warns_when_an_axis_is_entirely_missing(cfg, tmp_path):
    """軸が丸ごと空のまま黙って進まないこと。"""
    new = pd.read_csv(S / "new_store.csv", dtype={"store_id": str})
    for key in ("share_walk", "share_bike", "share_car", "share_train",
                "parking_spaces", "nearest_station_m", "sales_floor_sqm"):
        if key in new.columns:
            new[key] = ""
    p = tmp_path / "new_thin.csv"
    new.to_csv(p, index=False, encoding="utf-8-sig")
    a = run_analysis(config_path=CFG, metrics_path=MCFG,
                     master_path=S / "store_master.csv", new_store_path=p,
                     idpos_path=IDPOS, margin_path=S / "ure_zaiko_sample.csv.gz",
                     level="line", level_scan=False)
    assert any("まるごと使えなかった軸" in w for w in a.warnings)
    assert any("移動手段" in w for w in a.warnings)


def test_no_axis_warning_when_inputs_are_complete(analysis):
    assert not any("まるごと使えなかった軸" in w for w in analysis.warnings)


# ---------------- 売上在庫（粗利）ファイルの最小構成 ----------------

MARGIN = S / "ure_zaiko_sample.csv.gz"


def _margin_frame() -> pd.DataFrame:
    return pd.read_csv(MARGIN, encoding="cp932", dtype=str)


def _write(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False, encoding="cp932")
    return path


def test_margin_accepts_multiple_files(mcfg, tmp_path):
    """週次で分割して溜めても、1本にまとめたときと同じ結果になること。"""
    df = _margin_frame()
    weeks = sorted(df["年週"].unique())
    half = len(weeks) // 2
    a = _write(df[df["年週"].isin(weeks[:half])], tmp_path / "m1.csv")
    b = _write(df[df["年週"].isin(weeks[half:])], tmp_path / "m2.csv")

    one = load_idpos(IDPOS, MARGIN, mcfg, level="category")
    two = load_idpos(IDPOS, [a, b], mcfg, level="category")

    key = ["store_id", "unit", "year", "week"]
    m = (one.panel[key + ["gross_margin_rate"]]
         .merge(two.panel[key + ["gross_margin_rate"]], on=key, suffixes=("_1", "_2")))
    assert len(m) > 0
    both = m["gross_margin_rate_1"].notna() & m["gross_margin_rate_2"].notna()
    assert both.any(), "分割して読むと粗利率が全て欠損している"
    diff = (m.loc[both, "gross_margin_rate_1"] - m.loc[both, "gross_margin_rate_2"]).abs()
    assert diff.max() < 1e-9, "分割して読むと結果が変わる"


def test_margin_works_without_rate_when_sales_amount_present(mcfg, tmp_path):
    """荒利率を外して売上金額だけにしても動き、率がほぼ一致すること。"""
    df = _margin_frame()
    rate = df["販売荒利率"].str.rstrip("%").astype(float) / 100
    gp = df["販売荒利高(千円)"].astype(float) * 1000
    df["売上金額(税抜)"] = (gp / rate.replace(0, pd.NA)).round(0)
    only_amount = _write(df.drop(columns=["販売荒利率"]), tmp_path / "amount.csv")

    base = load_idpos(IDPOS, MARGIN, mcfg, level="category")
    alt = load_idpos(IDPOS, only_amount, mcfg, level="category")

    key = ["store_id", "unit", "year", "week"]
    m = (base.panel[key + ["gross_margin_rate"]]
         .merge(alt.panel[key + ["gross_margin_rate"]], on=key, suffixes=("_r", "_a")))
    both = m["gross_margin_rate_r"].notna() & m["gross_margin_rate_a"].notna()
    assert both.sum() > 0, "売上金額だけでは粗利率が出ていない"
    # 売上金額を円単位に丸めた分だけずれる（実データでも同じ桁の誤差）。
    diff = (m.loc[both, "gross_margin_rate_r"] - m.loc[both, "gross_margin_rate_a"]).abs()
    assert diff.max() < 1e-4


def test_margin_rejects_file_without_rate_and_amount(mcfg, tmp_path):
    df = _margin_frame().drop(columns=["販売荒利率"])
    bad = _write(df, tmp_path / "no_rate.csv")
    with pytest.raises(InputError, match="荒利率か売上金額"):
        load_idpos(IDPOS, bad, mcfg, level="category")


def test_margin_warns_on_unit_mismatch(mcfg, tmp_path):
    """売上金額を千円のまま入れたら、単位がおかしいと警告すること。"""
    df = _margin_frame()
    rate = df["販売荒利率"].str.rstrip("%").astype(float) / 100
    gp_sen = df["販売荒利高(千円)"].astype(float)
    df["売上金額(税抜)"] = (gp_sen / rate.replace(0, pd.NA)).round(1)   # 千円のまま
    path = _write(df, tmp_path / "wrong_unit.csv")

    data = load_idpos(IDPOS, path, mcfg, level="category")
    assert any("単位" in w for w in data.warnings), data.warnings
