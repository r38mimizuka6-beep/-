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
from shoken.io_loader import InputError, load_new_store, load_store_master  # noqa: E402
from shoken.pipeline import run_analysis  # noqa: E402
from shoken.rangecheck import check_ranges  # noqa: E402
from shoken.report import render_html, write_excel  # noqa: E402

CONFIG = ROOT / "config" / "columns.yaml"
SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="module")
def cfg() -> Config:
    return Config.load(CONFIG)


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(
        config_path=CONFIG,
        master_path=SAMPLE / "store_master.csv",
        new_store_path=SAMPLE / "new_store.csv",
        sales_path=SAMPLE / "sales_mix.csv",
        member_path=SAMPLE / "member_mix.csv",
    )


def test_config_loads(cfg):
    assert cfg.variables
    assert set(cfg.axes) == {"daynight", "household", "mobility"}
    assert cfg.axis_weights() == {"daynight": 1.0, "household": 1.0, "mobility": 1.0}


def test_weight_override_rejects_unknown_axis(cfg):
    with pytest.raises(KeyError):
        cfg.axis_weights({"nonexistent": 2.0})


def test_eval_formula_is_sandboxed():
    assert eval_formula("a / b", {"a": 10, "b": 4}) == 2.5
    assert eval_formula("a / b", {"a": 1, "b": 0}) is None   # ゼロ除算は None
    assert eval_formula("__import__('os')", {}) is None      # 外部呼び出しは通らない


def test_master_rejects_single_store(cfg, tmp_path):
    p = tmp_path / "one.csv"
    pd.DataFrame([{"store_id": "S01", "store_name": "a", "pop_total": 1}]).to_csv(p, index=False)
    with pytest.raises(InputError):
        load_store_master(p, cfg)


def test_new_store_must_be_one_row(cfg, tmp_path):
    p = tmp_path / "two.csv"
    pd.DataFrame([{"store_id": "N1", "store_name": "a"},
                  {"store_id": "N2", "store_name": "b"}]).to_csv(p, index=False)
    with pytest.raises(InputError):
        load_new_store(p, cfg)


def test_missing_id_column_message(cfg, tmp_path):
    p = tmp_path / "bad.csv"
    pd.DataFrame([{"店番": "S01"}, {"店番": "S02"}]).to_csv(p, index=False)
    with pytest.raises(InputError, match="store_id"):
        load_store_master(p, cfg)


def test_standardizer_flags_constant_columns():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [5.0, 5.0, 5.0]})
    std = Standardizer.fit(df, ["a", "b"])
    assert std.degenerate == ["b"]
    z = std.transform_row(pd.Series({"a": 2.0, "b": 5.0}), ["a", "b"])
    assert z["a"] == pytest.approx(0.0)
    assert z["b"] is None   # 定数列は距離に使わない


def test_urban_rural_poles(cfg):
    urban = pd.Series({"pop_density": 15000, "share_car": 0.05, "share_walk_bike": 0.9,
                       "parking_spaces": 5, "nearest_station_m": 120,
                       "hh_share_single": 0.6, "housing_share_apart": 0.9})
    rural = pd.Series({"pop_density": 900, "share_car": 0.85, "share_walk_bike": 0.12,
                       "parking_spaces": 300, "nearest_station_m": 4200,
                       "hh_share_single": 0.18, "housing_share_apart": 0.2})
    assert judge_urban_rural(urban, cfg).verdict == "都市部寄り"
    assert judge_urban_rural(rural, cfg).verdict == "郊外寄り"


def test_urban_rural_handles_all_missing(cfg):
    assert judge_urban_rural(pd.Series(dtype=float), cfg).verdict == "判定不能"


def test_range_check_detects_out_of_range(cfg):
    stores = load_store_master(SAMPLE / "store_master.csv", cfg)
    new = load_new_store(SAMPLE / "new_store.csv", cfg).copy()
    new["pop_total"] = 9_000_000      # 既存店の最大を大きく超える
    items = {i.key: i for i in check_ranges(stores, new, cfg)}
    assert items["pop_total"].status == "範囲外(上)"
    assert items["pop_total"].overshoot > 0


def test_pipeline_end_to_end(analysis):
    a = analysis
    assert len(a.similarity.ranking) == 5
    assert [s.rank for s in a.similarity.ranking] == [1, 2, 3, 4, 5]
    # 距離は昇順
    d = [s.distance for s in a.similarity.ranking]
    assert d == sorted(d)
    assert len(a.peers) == 2
    assert a.urban_new.verdict in ("都市部寄り", "中間", "郊外寄り")
    assert a.shelf and a.checklist


def test_rural_store_is_detected(analysis):
    rural = [sid for sid, u in analysis.urban_stores.items() if u.is_rural]
    assert rural == ["S05"]


def test_direction_recovers_known_drivers(analysis):
    """ダミーデータは既知のドライバで作ってある。方向が合っているか。"""
    found = {(f.var_label, f.category): f.direction for f in analysis.directions.findings}
    assert found.get(("年齢構成比 65歳以上", "和日配")) == "正"
    assert found.get(("6歳未満同居世帯比率", "精肉")) == "正"
    # 共線性でまとめた分、代表変数が変わることがあるので件数だけ確認
    assert analysis.directions.n_tests > 0
    assert len(analysis.directions.findings) < analysis.directions.n_tests


def test_direction_reports_chance_expectation(analysis):
    dr = analysis.directions
    assert dr.expected_by_chance > 0
    assert "偶然" in dr.note or "ランダム" in dr.note


def test_shelf_range_contains_peer_values(analysis):
    for p in analysis.shelf:
        for v in p.peer_shares.values():
            assert p.share_low - 1e-9 <= v <= p.share_high + 1e-9


def test_member_gap_tvd_in_bounds(analysis):
    assert analysis.member_gaps
    for g in analysis.member_gaps:
        assert g.tvd is None or 0.0 <= g.tvd <= 1.0


def test_html_contains_all_sections(analysis):
    html = render_html(analysis)
    for heading in ["1. 新店の商圏サマリ", "2. 類似店ランキング", "3. 範囲外の指標",
                    "4. 商圏差とカテゴリ構成比差", "5. 初期棚割の提案",
                    "6. 開店後にIDPOSで検証"]:
        assert heading in html
    assert "仮説" in html          # 断定していないこと
    assert "統計的に検定された予測値ではありません" in html


def test_extract_map_is_valid_yaml():
    import yaml
    m = yaml.safe_load((ROOT / "config" / "extract_shoken_report.yaml").read_text(encoding="utf-8"))
    for section in ("cells", "anchors", "header_lookups", "age_table", "category_demand"):
        assert section in m, section
    for key, spec in m["cells"].items():
        assert {"sheet", "cell"} <= set(spec), key


def test_excel_written(analysis, tmp_path):
    p = write_excel(analysis, tmp_path / "r.xlsx")
    assert p.exists() and p.stat().st_size > 5000
    sheets = pd.ExcelFile(p).sheet_names
    assert "5_初期棚割提案" in sheets and "0_注意事項" in sheets


def test_weights_change_ranking(cfg):
    base = run_analysis(config_path=CONFIG, master_path=SAMPLE / "store_master.csv",
                        new_store_path=SAMPLE / "new_store.csv",
                        sales_path=SAMPLE / "sales_mix.csv")
    tilted = run_analysis(config_path=CONFIG, master_path=SAMPLE / "store_master.csv",
                          new_store_path=SAMPLE / "new_store.csv",
                          sales_path=SAMPLE / "sales_mix.csv",
                          weight_overrides={"mobility": 8.0})
    assert base.similarity.weights != tilted.similarity.weights
    # 重みを変えれば距離は変わる（順位は変わらないこともある）
    assert base.similarity.ranking[0].distance != tilted.similarity.ranking[0].distance
