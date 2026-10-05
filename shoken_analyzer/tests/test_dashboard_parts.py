"""ダッシュボードが使う部品のテスト（蓄積・気温・運用ループ）。"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from shoken import weather  # noqa: E402
from shoken.agentloop import accuracy_trend, decide, run_loop  # noqa: E402
from shoken.warehouse import Warehouse, _missing_weeks  # noqa: E402

S = ROOT / "data" / "sample"


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    return Warehouse(tmp_path / "warehouse")


# ---------------- 蓄積 ----------------

def test_ingest_and_coverage(wh):
    r = wh.ingest("idpos", "idpos.csv.gz", (S / "idpos_sample.csv.gz").read_bytes())
    assert r.status == "ok" and r.rows > 0
    r2 = wh.ingest("margin", "ure.csv.gz", (S / "ure_zaiko_sample.csv.gz").read_bytes())
    assert r2.status == "ok"
    cov = wh.coverage()
    assert len(cov.stores) == 5
    assert all(len(w) == 6 and w.isdigit() for w in cov.weeks), "年週が正規化されていない"


def test_same_file_twice_is_rejected(wh):
    data = (S / "idpos_sample.csv.gz").read_bytes()
    assert wh.ingest("idpos", "a.csv.gz", data).status == "ok"
    second = wh.ingest("idpos", "別名.csv.gz", data)
    assert second.status == "duplicate", "同じ中身を二重に取り込んでいる"
    assert len(wh.idpos_files()) == 1


def test_week_formats_merge_in_coverage(wh):
    """202640 と 2026年40週 が同じ週として数えられること。"""
    wh.ingest("idpos", "i.csv.gz", (S / "idpos_sample.csv.gz").read_bytes())
    only_idpos = set(wh.coverage().weeks)
    wh.ingest("margin", "m.csv.gz", (S / "ure_zaiko_sample.csv.gz").read_bytes())
    both = set(wh.coverage().weeks)
    assert both == only_idpos, "表記違いで週が増えている"


def test_unreadable_file_is_not_kept(wh):
    r = wh.ingest("idpos", "broken.csv", b"\x00\x01\x02not a csv")
    assert r.status in ("ok", "error")
    if r.status == "error":
        assert not wh.idpos_files()


def test_master_backup_on_replace(wh):
    wh.put_text("master", "m.csv", b"store_id\n0101\n")
    wh.put_text("master", "m.csv", b"store_id\n0102\n")
    assert wh.master_path.read_bytes() == b"store_id\n0102\n"
    assert list(wh.master_path.parent.glob("*.bak*")), "上書き前の版が残っていない"


def test_missing_weeks_detects_gap():
    assert _missing_weeks(["202601", "202602", "202604"]) == ["2026年3週"]
    assert _missing_weeks(["202601"]) == []


def test_state_round_trip(wh):
    wh.append_history({"event": "verify", "summary": {"想定内": 3}})
    state = wh.load_state()
    assert state["history"][0]["event"] == "verify"
    assert "at" in state["history"][0]


# ---------------- 気温 ----------------

def test_weekly_aggregation_uses_iso_weeks():
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2026-09-28", "2026-09-29", "2026-10-05"]),
        "temp_mean": [20.0, 22.0, 15.0],
        "temp_max": [25.0, 26.0, 19.0], "temp_min": [16.0, 18.0, 12.0],
    })
    wk = weather.to_weekly(daily, "0101")
    assert len(wk) == 2
    first = wk.iloc[0]
    assert first["temp_mean"] == pytest.approx(21.0)
    assert first["n_days"] == 2
    assert first["store_id"] == "0101"


def test_weekly_on_empty_input():
    assert weather.to_weekly(pd.DataFrame(), "0101").empty


def test_connection_failure_is_reported_not_raised():
    """外部に出られない環境でも例外で落ちないこと。"""
    res = weather.check_connection()
    assert isinstance(res.ok, bool)
    if not res.ok:
        assert res.message


def test_weather_csv_requires_columns(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("store_id,year_week\n0101,202640\n", encoding="utf-8")
    with pytest.raises(ValueError, match="temp_mean"):
        weather.load_weather_csv(p)


def test_sensitivity_recovers_planted_slope():
    """気温1℃で+3%動くように作ったデータから、その傾きが出ること。"""
    rows, temps = [], []
    for i in range(40):
        t = 5 + i * 0.7
        temps.append({"store_id": "0101", "year_week": f"2026{i + 1:02d}",
                      "temp_mean": t})
        rows.append({"store_id": "0101", "unit": "アイス", "year": 2026,
                     "week": i + 1, "pi": 10 * (1 + 0.03 * (t - 20))})
    sens = weather.estimate_sensitivity(pd.DataFrame(rows), pd.DataFrame(temps),
                                        ["pi"])
    assert sens and sens[0].unit == "アイス"
    assert sens[0].slope_pct_per_deg == pytest.approx(3.0, abs=0.3)
    assert sens[0].meaningful


def test_temperature_adjustment_divides_out_the_effect():
    sens = weather.TempSensitivity("アイス", "pi", 3.0, 0.9, 40, 1)
    adj, msg = weather.adjust_for_temperature(112.0, 24.0, 20.0, sens)
    assert adj == pytest.approx(112.0 / 1.12, rel=1e-6)
    assert "気温補正" in msg
    same, msg2 = weather.adjust_for_temperature(100.0, 20.0, 20.0, sens)
    assert same == pytest.approx(100.0)


def test_no_adjustment_when_sensitivity_is_weak():
    weak = weather.TempSensitivity("納豆", "pi", 0.1, 0.01, 40, 1)
    v, msg = weather.adjust_for_temperature(100.0, 30.0, 20.0, weak)
    assert v == 100.0 and "補正なし" in msg
    v2, _ = weather.adjust_for_temperature(100.0, 30.0, 20.0, None)
    assert v2 == 100.0


# ---------------- 運用ループ ----------------

def test_loop_updates_automatically_and_proposes_separately(wh):
    res = run_loop(wh, verify=None, loo_mape={("和日配", "pi"): 12.0},
                   ramp_summary={"n_contributors": 2, "factors": {"0": 1.3, "1": 1.1}},
                   temp_sensitivity=None, weeks_since_open=30, store_name="新店X")
    keys = {u.key for u in res.updates}
    assert {"ramp", "interval_mape"} <= keys
    pkeys = {p.key for p in res.proposals}
    assert "promote_store" in pkeys, "26週を超えた店の昇格が提案されていない"
    assert "axis_weights" in pkeys, "軸の重みが自動更新されてしまっている"


def test_axis_weights_are_never_auto_updated(wh):
    res = run_loop(wh, verify=None, loo_mape={("x", "pi"): 10.0},
                   ramp_summary=None, temp_sensitivity=None,
                   weeks_since_open=None)
    assert all(u.key != "axis_weights" for u in res.updates)


def test_decision_is_remembered(wh):
    run_loop(wh, verify=None, loo_mape=None, ramp_summary=None,
             temp_sensitivity=None, weeks_since_open=None)
    decide(wh, "axis_weights", "rejected")
    again = run_loop(wh, verify=None, loo_mape=None, ramp_summary=None,
                     temp_sensitivity=None, weeks_since_open=None)
    p = next(p for p in again.proposals if p.key == "axis_weights")
    assert p.decided == "rejected"


def test_accuracy_trend_from_history(wh):
    wh.append_history({"event": "verify", "store_name": "新店X",
                       "weeks_observed": 6, "summary": {"想定内": 8, "下振れ": 2},
                       "rows": [{"diff_pct": 5.0}, {"diff_pct": -15.0}]})
    t = accuracy_trend(wh)
    assert len(t) == 1
    assert t.iloc[0]["平均絶対誤差%"] == pytest.approx(10.0)
    assert t.iloc[0]["下振れ"] == 2
