"""開店後にIDPOSで検証・修正すべき項目のチェックリスト。"""

from __future__ import annotations

from .customers import AgeMixPrediction
from .predict import LooResult, Prediction
from .risk import CategoryRisk


def build_checklist(
    predictions: list[Prediction],
    risks: list[CategoryRisk],
    loo: LooResult,
    age: AgeMixPrediction | None,
    *,
    min_weeks: int,
    exclude_first_weeks: int,
) -> list[dict]:
    items: list[dict] = [
        {"priority": "最優先",
         "item": f"開店{exclude_first_weeks + min_weeks}週目に verify コマンドで予実を突き合わせる",
         "timing": f"開店{exclude_first_weeks + min_weeks}週後",
         "action": "最初の2週はご祝儀需要で高く出るので判定から除外される。"
                   "下振れ判定が出たカテゴリーから手を打つ"},
        {"priority": "最優先",
         "item": "週次IDPOSを毎週このツールに追加し、判定を更新する",
         "timing": "毎週",
         "action": "週数が増えるほど「ブレの範囲」が狭まり、判定が確定していく"},
    ]

    high = [r for r in risks if r.level in ("要対策", "警戒")]
    if high:
        items.append({
            "priority": "最優先",
            "item": "苦戦予想カテゴリーの実績を最優先で確認: "
                    + "、".join(r.unit for r in high),
            "timing": f"開店{exclude_first_weeks + min_weeks}週後",
            "action": "各カテゴリーの対策欄に沿って切り分ける",
        })

    poor = sorted(
        {(k[0], k[1]): v for k, v in loo.mape.items() if v > 12.0}.items(),
        key=lambda kv: -kv[1],
    )[:4]
    if poor:
        items.append({
            "priority": "高",
            "item": "LOO誤差が大きい組み合わせは予測を信用しない: "
                    + "、".join(f"{c}×{m}({v:.0f}%)" for (c, m), v in poor),
            "timing": "開店前に認識しておく",
            "action": "初期棚はレンジ下限から入り、実績で決める",
        })

    losers = [p for p in predictions if p.beats_baseline is False]
    if losers:
        cats = sorted({p.unit for p in losers})
        items.append({
            "priority": "高",
            "item": f"類似店法が全店平均に負けている指標がある（{'、'.join(cats)}）",
            "timing": "開店前",
            "action": "これらは類似店ではなく既存5店の平均を初期値にしたほうがマシ。"
                      "商圏変数では説明がついていない",
        })

    if age is not None:
        items.append({
            "priority": "高",
            "item": f"会員の年代構成を実測し、予測（{age.best_method}）と比較する",
            "timing": "開店1〜2か月後",
            "action": f"予測誤差の実績は平均{_fmt(age.loo_mae_peer_pt)}pt。"
                      "これを超えてずれた年代があれば、その層が主購買層のカテゴリーを見直す",
        })

    items += [
        {"priority": "最優先",
         "item": "まだ転換していない店の週次IDPOSを、今週から毎週保存しはじめる",
         "timing": "今週から毎週",
         "action": "システムは転換前に遡れない。これから貯めた分だけが、次の転換店で"
                   "「転換前後法」（類似店法よりはるかに正確）を使うための材料になる。"
                   "数量・客数つき／年代つきの2エクスポートと売上在庫を、加工せずそのまま保存"},
        {"priority": "高",
         "item": "粗利率が予測レンジを下回ったカテゴリーの値入・ロス率を確認",
         "timing": "開店1か月後",
         "action": "低温はロス率が粗利率を直撃する。PIが想定どおりでも粗利率が落ちていないか"},
        {"priority": "中",
         "item": "来店手段（徒歩・自転車・車）の実態を店頭で確認",
         "timing": "開店1か月後",
         "action": "想定と違えば search_profile.csv を更新し、類似店を選び直す"},
        {"priority": "中",
         "item": "競合の出店・改装・価格変更を確認",
         "timing": "四半期ごと",
         "action": "変化があれば検索データを更新して再実行"},
        {"priority": "中",
         "item": "新店を6店目として既存店マスタに追加する",
         "timing": "開店6か月後（26週以上たまってから）",
         "action": "LOOの検証点が5→6に増える。郊外店が1店しかない問題も、"
                   "郊外に出すたびに少しずつ解消する"},
        {"priority": "低",
         "item": "weights コマンドで軸の重みの感度を見る",
         "timing": "店舗数が6を超えてから",
         "action": "5店では誤差の地形が平坦で、重みを変えても意味がないことが多い"},
    ]
    return items


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.1f}"
