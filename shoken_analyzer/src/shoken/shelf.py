"""類似店のカテゴリ別構成比・粗利から、新店の初期棚割の「幅」を作る。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class ShelfProposal:
    category: str
    category_major: str | None
    share_low: float          # 類似店A〜Bの下限
    share_high: float         # 上限
    share_mid: float
    peer_shares: dict[str, float]
    all_store_mean: float
    delta_vs_all_pt: float    # 類似店中央値 - 既存5店平均（pt）
    direction: str            # 増やす / 減らす / 据え置き
    margin_low: float | None
    margin_high: float | None
    margin_mid: float | None
    note: str


def build_shelf_proposal(
    sales_mix: pd.DataFrame,
    cfg: Config,
    peer_ids: list[str],
    peer_names: dict[str, str],
    *,
    threshold_pt: float = 0.5,
) -> list[ShelfProposal]:
    id_key = cfg.id_key
    share = sales_mix.pivot_table(index=id_key, columns="category",
                                  values="sales_share", aggfunc="sum")
    has_margin = "gross_margin_rate" in sales_mix.columns
    margin = (
        sales_mix.pivot_table(index=id_key, columns="category",
                              values="gross_margin_rate", aggfunc="mean")
        if has_margin else None
    )
    major = {}
    if "category_major" in sales_mix.columns:
        major = (sales_mix.drop_duplicates("category")
                 .set_index("category")["category_major"].to_dict())

    peers = [p for p in peer_ids if p in share.index]
    if not peers:
        return []

    out: list[ShelfProposal] = []
    for cat in share.columns:
        vals = share.loc[peers, cat].dropna()
        if vals.empty:
            continue
        lo, hi = float(vals.min()), float(vals.max())
        mid = float(np.median(vals))
        allm = float(share[cat].dropna().mean())
        delta_pt = (mid - allm) * 100

        if delta_pt >= threshold_pt:
            direction = "増やす"
        elif delta_pt <= -threshold_pt:
            direction = "減らす"
        else:
            direction = "据え置き"

        mlo = mhi = mmid = None
        if margin is not None and cat in margin.columns:
            mv = margin.loc[peers, cat].dropna()
            if not mv.empty:
                mlo, mhi, mmid = float(mv.min()), float(mv.max()), float(np.median(mv))

        spread_pt = (hi - lo) * 100
        if len(peers) < 2:
            note = "類似店が1店のみ。幅ではなく単一店の値なので不確実性が大きい。"
        elif spread_pt > 2.0:
            note = f"類似店2店の開きが{spread_pt:.1f}pt。どちらに寄せるかは開店後のIDPOSで判断。"
        else:
            note = "類似店2店が近い値。初期値として採用しやすい。"

        out.append(ShelfProposal(
            category=str(cat), category_major=major.get(cat),
            share_low=lo, share_high=hi, share_mid=mid,
            peer_shares={peer_names.get(p, p): float(share.at[p, cat])
                         for p in peers if pd.notna(share.at[p, cat])},
            all_store_mean=allm, delta_vs_all_pt=delta_pt, direction=direction,
            margin_low=mlo, margin_high=mhi, margin_mid=mmid, note=note,
        ))

    out.sort(key=lambda p: -p.share_mid)
    return out


def idpos_checklist(proposals: list[ShelfProposal], gap_note: str | None) -> list[dict]:
    """開店後にIDPOSで検証・修正すべき項目。"""
    items: list[dict] = [
        {"priority": "最優先", "item": "会員の性別年代構成比を実測し、本レポートの商圏年齢構成と突き合わせる",
         "timing": "開店1か月後", "action": "ズレが10pt超の年代があれば、その年代が主購買層のカテゴリの棚を見直す"},
        {"priority": "最優先", "item": "カテゴリ別売上構成比の実績を、本レポートの提案レンジと比較",
         "timing": "開店1か月後 / 3か月後", "action": "レンジ外のカテゴリを抽出し、原因（品揃え・価格・棚位置）を切り分ける"},
        {"priority": "高", "item": "カテゴリ別粗利率の実績を類似店レンジと比較",
         "timing": "開店2か月後", "action": "構成比が想定どおりでも粗利率が低いカテゴリは、値入・PB構成を調整"},
        {"priority": "高", "item": "来店手段（徒歩・自転車・車）の実態を店頭観察または会員住所で確認",
         "timing": "開店1か月後", "action": "想定と違えば移動手段軸の入力を更新し、本ツールを再実行して類似店を選び直す"},
    ]
    wide = [p for p in proposals if p.share_high - p.share_low > 0.02]
    if wide:
        names = "、".join(p.category for p in wide[:5])
        items.append({
            "priority": "高",
            "item": f"類似店間の開きが大きいカテゴリの実績確認: {names}",
            "timing": "開店1か月後",
            "action": "提案レンジのどちら寄りかを確定し、次期棚割に反映",
        })
    if gap_note:
        items.append({
            "priority": "中",
            "item": "商圏統計の信頼度の再評価",
            "timing": "開店3か月後",
            "action": gap_note,
        })
    items += [
        {"priority": "中", "item": "新規会員の獲得エリア（郵便番号）を取得できる仕組みを作る",
         "timing": "開店前〜1か月", "action": "商圏設定（自転車10分等）が妥当だったかを事後検証できるようにする"},
        {"priority": "中", "item": "競合の出店・改装の有無を確認",
         "timing": "四半期ごと", "action": "競合変数が変わったらマスタを更新し、類似店判定をやり直す"},
        {"priority": "低", "item": "本ツールの既存店マスタに新店を6店目として追加",
         "timing": "開店6か月後", "action": "次の新店の類似店候補が増え、郊外の根拠不足も徐々に解消する"},
    ]
    return items
