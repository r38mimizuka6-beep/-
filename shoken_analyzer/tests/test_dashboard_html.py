"""HTMLダッシュボード（自己完結・オフライン）の出力テスト。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shoken.dashboard_html import (  # noqa: E402
    accuracy_band, bar_chart, coverage_grid, diverging_bar, line_chart,
    render_dashboard, write_dashboard,
)
from shoken.pipeline import run_analysis  # noqa: E402
from shoken.warehouse import Warehouse  # noqa: E402

S = ROOT / "data" / "sample"
CFG = ROOT / "config" / "columns.yaml"
MCFG = ROOT / "config" / "metrics.yaml"


@pytest.fixture(scope="module")
def analysis():
    return run_analysis(
        config_path=CFG, metrics_path=MCFG,
        master_path=S / "store_master.csv", new_store_path=S / "new_store.csv",
        idpos_path=S / "idpos_sample.csv.gz",
        margin_path=S / "ure_zaiko_sample.csv.gz",
        level="category", level_scan=False,
    )


def test_html_is_self_contained(analysis):
    """外部CDNやネットワーク参照を含まないこと（社内・オフラインで開くため）。"""
    html = render_dashboard(analysis)
    for bad in ("http://", "https://", "<script src", "<link rel=\"stylesheet\""):
        assert bad not in html, f"外部参照が入っている: {bad}"
    assert "<svg" in html, "グラフがSVGで埋め込まれていない"


def test_all_tabs_present(analysis):
    html = render_dashboard(analysis)
    for label in ("概要", "予測", "精度", "蓄積状況", "予実検証", "運用の状態"):
        assert f">{label}</button>" in html, label
    assert html.count('<section data-tab=') == 6


def test_first_tab_visible_rest_hidden(analysis):
    html = render_dashboard(analysis)
    assert '<section data-tab="overview">' in html
    assert html.count("hidden>") == 5


def test_numbers_match_the_analysis(analysis):
    """表示されている予測値が、計算結果と一致すること。"""
    html = render_dashboard(analysis)
    pi = next(p for p in analysis.predictions if p.metric == "pi" and p.point)
    assert f"{pi.point:,.2f}" in html


def test_status_is_labelled_not_only_coloured(analysis):
    """色だけで意味を運んでいないこと。"""
    html = render_dashboard(analysis)
    for label in ("当たる", "幅をもって見る", "当たらない"):
        assert label in html, label


def test_warnings_are_carried_over(analysis):
    import html as htmllib
    out = render_dashboard(analysis)
    assert analysis.warnings, "サンプルでは必ず警告が出るはず"
    for w in analysis.warnings:
        assert htmllib.escape(w) in out, w[:30]


def test_written_file_opens_as_html(analysis, tmp_path):
    p = write_dashboard(analysis, tmp_path / "d.html")
    text = p.read_text(encoding="utf-8")
    assert text.startswith("<!doctype html>")
    assert p.stat().st_size > 20000


def test_dashboard_with_coverage(analysis, tmp_path):
    wh = Warehouse(tmp_path / "wh")
    wh.ingest("idpos", "i.csv.gz", (S / "idpos_sample.csv.gz").read_bytes())
    html = render_dashboard(analysis, coverage=wh.coverage())
    assert "店舗 ×" in html and "data-tip" in html


# ---------------- グラフ部品 ----------------

def test_bar_chart_marks_carry_tooltips():
    rows = [{"label": "納豆", "value": 36.2, "color": "#b91c1c", "tip": "納豆 36.2%"}]
    svg = bar_chart(rows)
    assert svg.count('class="mark"') == 1
    assert 'data-tip="納豆 36.2%"' in svg
    assert "36.2%" in svg, "値が直接ラベルで出ていない"


def test_bar_chart_handles_empty():
    assert "データがありません" in bar_chart([])


def test_diverging_bar_puts_negatives_left():
    rows = [{"label": "a", "value": -10.0, "color": "#b91c1c", "tip": "a"},
            {"label": "b", "value": 10.0, "color": "#1d4ed8", "tip": "b"}]
    svg = diverging_bar(rows, width=720)
    xs = [float(m) for m in re.findall(r'<rect class="mark" x="([\d.]+)"', svg)]
    assert xs[0] < xs[1], "負の値が0の左に置かれていない"
    assert "-10.0%" in svg and "+10.0%" in svg


def test_line_chart_needs_two_points():
    assert "点が足りません" in line_chart([(1, 1.0)])
    svg = line_chart([(0, 1.3), (1, 1.1), (2, 1.0)], baseline=1.0)
    assert svg.count('class="mark"') == 3
    assert "stroke-dasharray" in svg, "基準線が引かれていない"


def test_line_chart_is_monotonic_in_x():
    svg = line_chart([(0, 1.0), (5, 2.0)], width=400)
    pts = re.findall(r"[ML]([\d.]+),([\d.]+)", svg)
    assert float(pts[0][0]) < float(pts[1][0])


def test_coverage_grid_cells_match_frame():
    frame = pd.DataFrame([[1, 0], [1, 1]], index=["0101", "0102"],
                         columns=["202601", "202602"])
    svg = coverage_grid(frame, {"0101": "A店", "0102": "B店"})
    assert svg.count('class="mark"') == 4
    assert "A店（0101）" in svg
    assert svg.count("なし") >= 1


def test_coverage_grid_empty():
    assert "蓄積データがありません" in coverage_grid(pd.DataFrame(), {})


@pytest.mark.parametrize("mape,label", [
    (None, "未測定"), (3.0, "当たる"), (12.0, "幅をもって見る"), (30.0, "当たらない"),
])
def test_accuracy_band(mape, label):
    assert accuracy_band(mape)[1] == label
