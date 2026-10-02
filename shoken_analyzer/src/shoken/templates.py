"""入力CSVテンプレートの生成。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config

GROUP_ORDER = ["people", "access", "competition", "facility", "ownstore"]
GROUP_LABEL = {
    "people": "A 商圏の人",
    "access": "B アクセス・立地",
    "competition": "C 競合",
    "facility": "D 周辺施設",
    "ownstore": "E 自店の魅力",
}


def master_columns(cfg: Config) -> list[str]:
    cols = [cfg.id_source, cfg.name_source]
    for g in GROUP_ORDER:
        cols += [v.source for v in cfg.variables.values() if v.group == g]
    return cols


def write_templates(cfg: Config, out_dir: str | Path, *, with_header_note: bool = True) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    cols = master_columns(cfg)

    # 1) 既存店マスタ（5行の空行つき）
    master = pd.DataFrame([{c: "" for c in cols} for _ in range(5)])
    master[cfg.id_source] = [f"S0{i}" for i in range(1, 6)]
    master[cfg.name_source] = [f"既存店{i}" for i in range(1, 6)]
    p = out_dir / "01_store_master.csv"
    master.to_csv(p, index=False, encoding="utf-8-sig")
    written.append(p)

    # 2) 新店（1行）
    new = pd.DataFrame([{c: "" for c in cols}])
    new[cfg.id_source] = ["NEW01"]
    new[cfg.name_source] = ["新店"]
    p = out_dir / "02_new_store.csv"
    new.to_csv(p, index=False, encoding="utf-8-sig")
    written.append(p)

    # 3) 売上構成比・粗利（縦持ち）
    sales = pd.DataFrame(columns=[
        cfg.id_source, "category_major", "category", "sales_share",
        "gross_margin_rate", "sales_amount", "gross_profit",
    ])
    p = out_dir / "03_sales_mix.csv"
    sales.to_csv(p, index=False, encoding="utf-8-sig")
    written.append(p)

    # 4) 会員構成比（縦持ち）
    member = pd.DataFrame(columns=[cfg.id_source, "gender", "age_band", "member_share"])
    p = out_dir / "04_member_mix.csv"
    member.to_csv(p, index=False, encoding="utf-8-sig")
    written.append(p)

    # 5) 項目の説明書き
    rows = []
    for g in GROUP_ORDER:
        for v in cfg.variables.values():
            if v.group != g:
                continue
            rows.append({
                "要素": GROUP_LABEL[g], "列名": v.source, "項目名": v.label,
                "型": v.kind, "軸": cfg.axis_of(v.key) or "",
                "必須": "○" if cfg.axis_of(v.key) else "",
                "取得元の例": _source_hint(v.key),
            })
    for d in cfg.derived:
        rows.append({"要素": "（自動計算）", "列名": "-", "項目名": d["label"],
                     "型": "derived", "軸": cfg.axis_of(d["key"]) or "",
                     "必須": "", "取得元の例": f"式: {d['formula']}"})
    p = out_dir / "00_項目定義.csv"
    pd.DataFrame(rows).to_csv(p, index=False, encoding="utf-8-sig")
    written.append(p)

    if with_header_note:
        p = out_dir / "README_入力テンプレート.txt"
        p.write_text(_readme(cfg), encoding="utf-8")
        written.append(p)
    return written


def _source_hint(key: str) -> str:
    if key.startswith(("age_", "pop_", "hh_", "housing_", "income_", "daytime", "nighttime",
                       "worker_", "student_", "day_night")):
        return "商圏レポート（国勢調査ベース）"
    if key.startswith("comp_") or key.startswith("nearest_comp"):
        return "地図検索・現地調査"
    if key.startswith("fac_"):
        return "地図検索"
    if key.startswith("share_") or key in ("parking_spaces", "nearest_station_m",
                                           "station_daily_users", "frontage_traffic"):
        return "現地調査・既存店は実測/店頭観察"
    return "自社マスタ"


def _readme(cfg: Config) -> str:
    return f"""入力テンプレートの使い方
================================================================

商圏定義: {cfg.trade_area_definition}
  ※ 全店で必ず同じ定義に揃えてください。1店だけ半径3km、他は自転車10分
     のように混ざると、距離計算の結果が意味を持ちません。

----------------------------------------------------------------
01_store_master.csv  … 既存店マスタ（1行=1店舗、5行）
02_new_store.csv     … 新店（1行のみ）
03_sales_mix.csv     … 店舗×カテゴリの売上構成比・粗利（縦持ち）
04_member_mix.csv    … 店舗×性別年代の会員構成比（縦持ち）
00_項目定義.csv      … 各列の意味・取得元・どの軸で使うか
----------------------------------------------------------------

■ 01 / 02 の埋め方
  ・「軸」列に値が入っている項目は類似度計算に直接効きます。最優先で埋めてください。
  ・空欄のままでも動きます。その項目は類似度・範囲チェックから除外され、
    レポートに「未入力」として出ます。
  ・比率(share)は 0〜1 でも 0〜100 でも構いませんが、全店で統一してください。
  ・商圏レポートのExcelがあるなら、次のコマンドで A（商圏の人）の項目を自動で埋められます:
        python run_report.py extract --xlsx 店舗A.xlsx 店舗B.xlsx --out master_auto.csv
    残りの B〜E（アクセス・競合・周辺施設・自店）は手で追記してください。

■ 03_sales_mix.csv
    store_id, category_major, category, sales_share, gross_margin_rate,
    sales_amount, gross_profit
  ・category は全店で同じ名前にしてください（表記ゆれがあると別カテゴリ扱いになります）。
  ・sales_share は店舗内で合計1.0（または100）になるようにしてください。
  ・gross_margin_rate は粗利率（0〜1 または 0〜100）。空欄可。

■ 04_member_mix.csv
    store_id, gender, age_band, member_share
  ・gender は 男/女、age_band は 10代/20代/.../70代以上。
  ・age_band の区分を変える場合は config/columns.yaml の
    member_compare_buckets を合わせて直してください。
  ・member_share は店舗内で合計1.0（または100）。

■ 実データの列名が違う場合
  config/columns.yaml の各項目の source: を、実データの列名に書き換えるだけです。
  コードは触る必要がありません。使わない項目は enabled: false にしてください。
"""
