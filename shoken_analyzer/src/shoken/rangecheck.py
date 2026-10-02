"""新店の各指標が既存店の最小〜最大に収まるかの判定。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class RangeItem:
    key: str
    label: str
    group: str
    axis: str | None
    value: float | None
    vmin: float | None
    vmax: float | None
    status: str          # 範囲内 / 範囲外(上) / 範囲外(下) / 新店データなし / 既存店データなし
    overshoot: float     # 範囲幅に対して何倍はみ出しているか（範囲内は0）

    @property
    def out_of_range(self) -> bool:
        return self.status.startswith("範囲外")


def check_ranges(stores: pd.DataFrame, new_store: pd.Series, cfg: Config) -> list[RangeItem]:
    items: list[RangeItem] = []
    keys = list(cfg.variables.keys()) + [d["key"] for d in cfg.derived]

    for key in keys:
        if key in cfg.variables and not cfg.variables[key].numeric:
            continue
        if key not in stores.columns:
            continue
        col = pd.to_numeric(stores[key], errors="coerce").dropna()
        value = new_store.get(key)
        value = float(value) if value is not None and pd.notna(value) else None
        group = cfg.variables[key].group if key in cfg.variables else "derived"
        label, axis = cfg.label(key), cfg.axis_of(key)

        if col.empty:
            items.append(RangeItem(key, label, group, axis, value, None, None, "既存店データなし", 0.0))
            continue
        vmin, vmax = float(col.min()), float(col.max())
        if value is None:
            items.append(RangeItem(key, label, group, axis, None, vmin, vmax, "新店データなし", 0.0))
            continue

        span = vmax - vmin
        if value > vmax:
            over = (value - vmax) / span if span > 0 else np.inf
            status = "範囲外(上)"
        elif value < vmin:
            over = (vmin - value) / span if span > 0 else np.inf
            status = "範囲外(下)"
        else:
            over, status = 0.0, "範囲内"
        items.append(RangeItem(key, label, group, axis, value, vmin, vmax, status, float(over)))

    items.sort(key=lambda i: (-i.overshoot, i.key))
    return items


def confidence_note(items: list[RangeItem], axis_keys: list[str]) -> dict:
    """範囲外の件数から、レポートに出す信頼度の目安を作る。"""
    graded = [i for i in items if i.status in ("範囲内", "範囲外(上)", "範囲外(下)")]
    out = [i for i in graded if i.out_of_range]
    out_axis = [i for i in out if i.key in axis_keys]

    if not graded:
        level, msg = "判定不能", "比較できる指標がありません。"
    elif out_axis:
        level = "低"
        msg = (f"類似度の3軸に使う指標が{len(out_axis)}件、既存5店の範囲外です。"
               "新店は既存店の経験が届かない位置にあり、類似店法の前提が弱くなります。")
    elif len(out) / len(graded) > 0.25:
        level = "中"
        msg = (f"参考指標が{len(out)}件（{len(out)/len(graded):.0%}）範囲外です。"
               "軸そのものは範囲内なので、類似店の選定は使えますが、個別カテゴリの外挿は慎重に。")
    elif out:
        level = "中〜高"
        msg = f"範囲外は{len(out)}件のみ。類似店法の前提は概ね満たしています。"
    else:
        level = "高"
        msg = "全指標が既存5店の範囲内です。類似店法の前提を満たしています。"

    return {"level": level, "message": msg, "n_checked": len(graded),
            "n_out": len(out), "n_out_axis": len(out_axis), "out_items": out}
