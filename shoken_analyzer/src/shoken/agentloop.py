"""予測 → 実績 → 見直し のループ。

自動で更新してよいものと、人が決めるべきものを分ける。
5店舗しかないので、何でも自動で学習させると過学習する。

  自動更新（決定論的・検証できる）
    ・立ち上がりカーブ        週が増えれば形が安定する
    ・予測区間の幅            LOO誤差の実測から決まる
    ・季節／気温の感応度      観測が増えるほど確かになる
  提案のみ（人が承認）
    ・軸の重み                5店では誤差の地形が平坦で、選ぶこと自体が過学習
    ・新店を既存店に昇格      業態・立地が代表的かは人が判断する
    ・苦戦判定の閾値          商売の判断が入る
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from .verify import VerifyResult
from .warehouse import Warehouse


@dataclass
class AutoUpdate:
    key: str
    label: str
    before: str
    after: str
    reason: str


@dataclass
class Proposal:
    key: str
    label: str
    detail: str
    why: str
    risk: str
    decided: str = "pending"      # pending / accepted / rejected


@dataclass
class LoopResult:
    updates: list[AutoUpdate] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    accuracy: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def record_prediction(wh: Warehouse, store_id: str, store_name: str,
                      level: str, n_units: int, loo_mape: dict) -> None:
    wh.append_history({
        "event": "predict", "store_id": store_id, "store_name": store_name,
        "level": level, "n_units": n_units,
        "loo_mape": {f"{u}|{m}": round(v, 3) for (u, m), v in loo_mape.items()},
    })


def record_outcome(wh: Warehouse, result: VerifyResult) -> dict:
    """予実検証の結果を履歴に積む。"""
    summary = result.summary
    entry = {
        "event": "verify", "store_name": result.store_name,
        "weeks_observed": result.weeks_observed,
        "excluded_first_weeks": result.excluded_first_weeks,
        "summary": summary,
        "rows": [
            {"unit": r.unit, "metric": r.metric, "verdict": r.verdict,
             "predicted": r.predicted, "actual": r.actual_adjusted,
             "diff_pct": r.diff_pct}
            for r in result.rows
        ],
    }
    wh.append_history(entry)
    return entry


def accuracy_trend(wh: Warehouse) -> pd.DataFrame:
    """検証を重ねるほど精度がどう動いたかを並べる。"""
    rows = []
    for h in wh.load_state().get("history", []):
        if h.get("event") != "verify":
            continue
        diffs = [abs(r["diff_pct"]) for r in h.get("rows", [])
                 if r.get("diff_pct") is not None]
        s = h.get("summary", {})
        total = sum(s.values()) or 1
        rows.append({
            "日時": h.get("at", "")[:16],
            "店舗": h.get("store_name", ""),
            "実績週数": h.get("weeks_observed"),
            "平均絶対誤差%": float(np.mean(diffs)) if diffs else None,
            "想定内の割合": s.get("想定内", 0) / total,
            "下振れ": s.get("下振れ", 0),
            "上振れ": s.get("上振れ", 0),
            "判定保留": s.get("判定保留", 0),
        })
    return pd.DataFrame(rows)


def run_loop(
    wh: Warehouse,
    *,
    verify: VerifyResult | None,
    loo_mape: dict | None,
    ramp_summary: dict | None,
    temp_sensitivity: list | None,
    weeks_since_open: int | None,
    store_name: str = "",
) -> LoopResult:
    """今の状態から、自動更新したものと、人に判断を仰ぐことを整理する。"""
    state = wh.load_state()
    params = state.setdefault("params", {})
    out = LoopResult()

    # ---- 自動更新 ----
    if ramp_summary:
        before = params.get("ramp", {})
        if before != ramp_summary:
            params["ramp"] = ramp_summary
            out.updates.append(AutoUpdate(
                "ramp", "立ち上がりカーブ",
                _fmt_ramp(before), _fmt_ramp(ramp_summary),
                f"成熟期に届いた店が{ramp_summary.get('n_contributors', 0)}店になり、"
                "カーブを測り直しました。",
            ))

    if loo_mape:
        mean = float(np.mean(list(loo_mape.values())))
        before = params.get("interval_mape")
        params["interval_mape"] = round(mean, 3)
        if before is None or abs(before - mean) > 0.5:
            out.updates.append(AutoUpdate(
                "interval_mape", "予測区間の幅（LOO誤差）",
                "—" if before is None else f"±{before:.1f}%", f"±{mean:.1f}%",
                "週が増えて各店の水準が安定し、1店抜き検証の誤差が変わりました。",
            ))

    if temp_sensitivity:
        strong = [s for s in temp_sensitivity if getattr(s, "meaningful", False)]
        params["temp_sensitivity"] = [
            {"unit": s.unit, "metric": s.metric,
             "slope_pct_per_deg": round(s.slope_pct_per_deg, 3),
             "r2": round(s.r2, 3), "n_weeks": s.n_weeks}
            for s in strong
        ]
        if strong:
            top = strong[0]
            out.updates.append(AutoUpdate(
                "temp_sensitivity", "気温感応度",
                "—", f"{len(strong)}件（最大 {top.unit} {top.slope_pct_per_deg:+.1f}%/℃）",
                "気温で説明できる分を切り分け、実績の判定に使います。",
            ))

    # ---- 提案（人が決める） ----
    if verify is not None:
        down = [r for r in verify.rows if r.verdict == "下振れ"]
        up = [r for r in verify.rows if r.verdict == "上振れ"]
        if down:
            names = "、".join(sorted({r.unit.split(" > ")[-1] for r in down})[:6])
            out.proposals.append(Proposal(
                "investigate_down", "下振れカテゴリーの要因を切り分ける",
                f"該当: {names}",
                "週次のブレでは説明できない下振れです。"
                "品揃え・売価・棚位置・競合のどれが効いているかを現場で確認してください。",
                "ここで原因を特定しないまま棚を縮めると、取れたはずの数量を落とします。",
            ))
        if up:
            names = "、".join(sorted({r.unit.split(" > ")[-1] for r in up})[:6])
            out.proposals.append(Proposal(
                "expand_up", "上振れカテゴリーの棚を広げる余地を見る",
                f"該当: {names}", "想定を超えています。棚幅・在庫を確認してください。",
                "一時的な要因（気温・チラシ・競合の休業）の可能性もあります。",
            ))

    if weeks_since_open is not None and weeks_since_open >= 26:
        out.proposals.append(Proposal(
            "promote_store", f"{store_name} を既存店マスタに追加する",
            f"開店から{weeks_since_open}週。26週を超えました。",
            "検証点が1店増え、立ち上がりカーブと変化率の精度が上がります。",
            "業態や立地が他店と大きく違う場合、かえって予測を乱すことがあります。"
            "代表的な店かどうかは人が判断してください。",
        ))

    if loo_mape:
        worst = sorted(loo_mape.items(), key=lambda kv: -kv[1])[:3]
        if worst and worst[0][1] > 20:
            out.proposals.append(Proposal(
                "drop_metric", "当たらない組み合わせを予測対象から外す",
                "、".join(f"{u.split(' > ')[-1]}×{m}（±{v:.0f}%）" for (u, m), v in worst),
                "既存店ですら当てられていません。数字を出すこと自体が誤解を生みます。",
                "外すと、その棚の判断材料が無くなります。"
                "「出さない」か「幅だけ出す」かを決めてください。",
            ))

    out.proposals.append(Proposal(
        "axis_weights", "軸の重みは自動で変えません",
        "変えたい場合は weights コマンドで誤差の地形を見てから。",
        "5店舗のLOOは実質5点しかなく、ここで最良の重みを選ぶこと自体が過学習です。",
        "現場の感覚と合う重みを人が選んでください。",
    ))

    # 既存の決定を引き継ぐ
    decided = {p["key"]: p.get("decided", "pending")
               for p in state.get("proposals", [])}
    for p in out.proposals:
        p.decided = decided.get(p.key, "pending")

    state["params"] = params
    state["proposals"] = [asdict(p) for p in out.proposals]
    state["last_run"] = dt.datetime.now().isoformat(timespec="seconds")
    wh.save_state(state)

    out.accuracy = {"loo_mape_mean": params.get("interval_mape")}
    return out


def decide(wh: Warehouse, key: str, decision: str) -> None:
    state = wh.load_state()
    for p in state.get("proposals", []):
        if p["key"] == key:
            p["decided"] = decision
    wh.save_state(state)
    wh.append_history({"event": "decision", "key": key, "decision": decision})


def _fmt_ramp(r: dict | None) -> str:
    if not r or not r.get("factors"):
        return "—"
    f = r["factors"]
    keys = sorted(int(k) for k in f)
    return "／".join(f"{k}週 {float(f[str(k)]):.2f}倍" for k in keys[:4])
