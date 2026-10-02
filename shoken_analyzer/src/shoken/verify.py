"""予測の保存と、開店後の答え合わせ。

開店直後の実績をそのまま年平均と比べてはいけない理由が2つある。
  1. 季節性 : 11月開店なら、既存店の11月と比べないと意味がない
  2. 開店効果: 最初の数週はご祝儀需要で必ず高く出る
両方を補正したうえで、差が「週次のブレの範囲か」「本当に外れているか」を分ける。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .idpos import IdposData, seasonal_index
from .metrics_config import MetricsConfig
from .predict import Prediction


# ---------------------------------------------------------------- 保存

def save_predictions(
    predictions: list[Prediction],
    *,
    store_name: str,
    store_id: str,
    path: str | Path,
    axis_weights: dict[str, float],
    extra: dict | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "store_id": store_id,
        "store_name": store_name,
        "axis_weights": axis_weights,
        "extra": extra or {},
        "predictions": [
            {
                "unit": p.unit, "metric": p.metric, "point": p.point,
                "low": p.low, "high": p.high,
                "peer_low": p.peer_low, "peer_high": p.peer_high,
                "all_median": p.all_median,
                "peers": [asdict(c) for c in p.peers],
                "weekly_cv": p.weekly_cv, "loo_mape": p.loo_mape,
                "loo_baseline_mape": p.loo_baseline_mape,
            }
            for p in predictions
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_predictions(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 答え合わせ

@dataclass
class VerifyRow:
    unit: str
    metric: str
    metric_label: str
    predicted: float | None
    pred_low: float | None
    pred_high: float | None
    actual_raw: float | None          # 観測期間の素の平均
    actual_adjusted: float | None     # 季節補正後
    season_index: float | None
    n_weeks: int
    n_weeks_used: int                 # 開店直後を除いた週数
    diff_pct: float | None
    noise_pct: float | None           # 週次ノイズから見た許容幅(±%)
    verdict: str
    comment: str


@dataclass
class VerifyResult:
    store_name: str
    predicted_at: str
    rows: list[VerifyRow]
    weeks_observed: int
    months_observed: list[int]
    excluded_first_weeks: int
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for r in self.rows:
            c[r.verdict] = c.get(r.verdict, 0) + 1
        return c


def verify_predictions(
    prediction_file: str | Path,
    actual_idpos: IdposData,
    mcfg: MetricsConfig,
    reference_idpos: IdposData,
    *,
    store_id: str | None = None,
) -> VerifyResult:
    """保存した予測と、新店の週次実績を突き合わせる。

    reference_idpos は既存店の週次（季節指数を借りるため）。
    """
    payload = load_predictions(prediction_file)
    sid = store_id or payload["store_id"]
    vcfg = mcfg.verification
    min_weeks = int(vcfg.get("min_weeks_for_judgement", 4))
    skip = int(vcfg.get("exclude_first_weeks", 2))
    sigma = float(vcfg.get("noise_sigma", 2.0))

    wk = actual_idpos.panel
    wk = wk[wk["store_id"].astype(str) == str(sid)]
    warnings: list[str] = []
    if wk.empty:
        return VerifyResult(payload["store_name"], payload["created_at"], [], 0, [], skip,
                            [f"実績データに店舗 {sid} の行がありません。"])

    weeks_all = sorted(wk["week_date"].dropna().unique())
    keep_weeks = weeks_all[skip:]
    if not keep_weeks:
        warnings.append(
            f"実績が{len(weeks_all)}週しかなく、開店直後の{skip}週を除くと残りません。"
            "開店効果が抜けないため、判定はすべて保留にします。"
        )
        keep_weeks = weeks_all
        skip_used = 0
    else:
        skip_used = skip
        warnings.append(
            f"開店直後の{skip}週はご祝儀需要が乗るため、判定から除外しました"
            f"（{len(weeks_all)}週中{len(keep_weeks)}週を使用）。"
        )
    used = wk[wk["week_date"].isin(keep_weeks)]
    months = sorted({int(d.month) for d in pd.to_datetime(keep_weeks)})

    rows: list[VerifyRow] = []
    for p in payload["predictions"]:
        cat, met = p["unit"], p["metric"]
        g = used[used["unit"] == cat]
        x = pd.to_numeric(g[met], errors="coerce").dropna() if met in g.columns else pd.Series(dtype=float)
        n = len(x)
        actual_raw = float(x.mean()) if n else None

        si = seasonal_index(reference_idpos.seasonality, cat, met, months)
        actual_adj = (actual_raw / si) if (actual_raw is not None and si) else actual_raw

        point, lo, hi = p["point"], p["low"], p["high"]
        diff_pct = None
        if actual_adj is not None and point not in (None, 0):
            diff_pct = (actual_adj - point) / abs(point) * 100

        cv = p.get("weekly_cv")
        noise_pct = (sigma * cv / np.sqrt(n) * 100) if (cv and n) else None

        verdict, comment = _judge(
            n, min_weeks, actual_adj, point, lo, hi, diff_pct, noise_pct
        )
        rows.append(VerifyRow(
            unit=cat, metric=met, metric_label=mcfg.metric_label(met),
            predicted=point, pred_low=lo, pred_high=hi,
            actual_raw=actual_raw, actual_adjusted=actual_adj, season_index=si,
            n_weeks=len(weeks_all), n_weeks_used=n,
            diff_pct=diff_pct, noise_pct=noise_pct,
            verdict=verdict, comment=comment,
        ))

    order = {"下振れ": 0, "上振れ": 1, "ブレの範囲": 2, "想定内": 3, "判定保留": 4, "データなし": 5}
    rows.sort(key=lambda r: (order.get(r.verdict, 9), -abs(r.diff_pct or 0)))

    return VerifyResult(
        store_name=payload["store_name"], predicted_at=payload["created_at"],
        rows=rows, weeks_observed=len(weeks_all), months_observed=months,
        excluded_first_weeks=skip_used, warnings=warnings,
    )


def _judge(n, min_weeks, actual, point, lo, hi, diff_pct, noise_pct):
    if actual is None or n == 0:
        return "データなし", "この週数ではこのカテゴリーの実績がありません。"
    if n < min_weeks:
        return "判定保留", (f"{n}週分しかありません（判定には{min_weeks}週必要）。"
                           "数字は参考にとどめてください。")
    if lo is not None and hi is not None and lo <= actual <= hi:
        return "想定内", f"予測区間（{lo:,.4g}〜{hi:,.4g}）の中に収まっています。"
    if noise_pct is not None and diff_pct is not None and abs(diff_pct) <= noise_pct:
        return "ブレの範囲", (f"区間からは{diff_pct:+.1f}%外れていますが、"
                            f"{n}週では週次のブレ（±{noise_pct:.1f}%）で説明できる範囲です。"
                            "週数を増やしてから判断してください。")
    side = "上振れ" if (diff_pct or 0) > 0 else "下振れ"
    reason = (f"予測を{abs(diff_pct):.1f}%{'上回って' if side == '上振れ' else '下回って'}おり、"
              f"週次のブレ（±{noise_pct:.1f}%）では説明できません。"
              if noise_pct is not None else
              f"予測を{abs(diff_pct):.1f}%{'上回って' if side == '上振れ' else '下回って'}います。")
    if side == "下振れ":
        reason += " 品揃え・売価・棚位置・競合のどれが効いているかを切り分けてください。"
    else:
        reason += " 棚を広げる余地がないかを確認してください。"
    return side, reason


# ---------------------------------------------------------------- 既存店の答え合わせ

def backtest_table(loo_folds, mcfg: MetricsConfig) -> pd.DataFrame:
    """LOOの全フォールドを表にする。既存店ごとの当たり外れが見える。"""
    return pd.DataFrame([
        {
            "対象店": f.held_out_name,
            "単位": f.unit,
            "指標": mcfg.metric_label(f.metric),
            "実績": f.actual,
            "類似店法の予測": f.predicted,
            "誤差%": f.err_pct,
            "全店平均の予測": f.baseline,
            "全店平均の誤差%": f.baseline_err_pct,
            "採用した類似店": "・".join(f.peers),
        }
        for f in loo_folds
    ])
