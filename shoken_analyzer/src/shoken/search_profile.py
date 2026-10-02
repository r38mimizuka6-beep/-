"""店舗名からの検索で埋める項目（競合・周辺施設・アクセス・自店条件）の扱い。

商圏レポートのExcelはファイル名が店舗名になっているので、ファイル名から
店舗名を取り出し、「何を調べればよいか」を店舗ごとに書き出す。
調べた結果は1枚のCSVに書き戻せば、既存店マスタに自動で結合される。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .config import Config
from .io_loader import InputError, _read_any

# 検索で埋める列。columns.yaml の key に対応している。
SEARCH_COLUMNS: list[tuple[str, str, str]] = [
    # (key, 見出し, 調べ方のヒント)
    ("address",            "住所",                 "店舗名で検索し、公式サイトか地図サービスの住所"),
    ("nearest_station_m",  "最寄駅までの距離(m)",   "地図で徒歩ルートの距離。複数路線なら最も近い駅"),
    ("station_daily_users", "最寄駅1日乗降客数",    "鉄道会社の駅別乗降人員データ（直近年度）"),
    ("parking_spaces",     "駐車場台数",           "公式サイトの店舗情報。記載が無ければ航空写真で概算"),
    ("sales_floor_sqm",    "売場面積(m2)",         "自社マスタ優先。無ければ大規模小売店舗立地法の届出"),
    ("store_format",       "業態",                 "都市型SM / 郊外型SM / 駅前小型SM など自社の区分"),
    ("share_walk",         "来店手段 徒歩比率",     "既存店は店頭観察か自社調査。新店は商圏と駐車場から想定"),
    ("share_bike",         "来店手段 自転車比率",   "同上。駐輪場の規模も手がかりになる"),
    ("share_car",          "来店手段 車比率",       "同上。駐車場台数と立地から想定"),
    ("share_train",        "来店手段 鉄道比率",     "同上。駅直結・駅前なら高くなる"),
    ("comp_sm_1km",        "競合SM数(1km内)",       "地図で「スーパー」を検索し半径1km内を数える"),
    ("comp_sm_3km",        "競合SM数(3km内)",       "同上、半径3km"),
    ("comp_cvs_1km",       "競合CVS数(1km内)",      "地図で「コンビニ」を検索"),
    ("comp_drug_1km",      "競合ドラッグ数(1km内)", "地図で「ドラッグストア」を検索。低温の競合になる"),
    ("comp_discount_1km",  "競合ディスカウント数(1km内)", "業務スーパー・ディスカウント業態"),
    ("nearest_comp_m",     "最寄競合までの距離(m)", "最も近いSMまでの道なり距離"),
    ("nearest_comp_name",  "最寄競合の店名",        "社名・店名。価格帯の判断材料にする"),
    ("nearest_comp_price", "最寄競合の価格帯(1低-5高)", "1=ディスカウント 3=標準 5=高品質"),
    ("own_store_overlap",  "自社他店との商圏重なり率", "自社店舗網と商圏の重なり。自社マスタから"),
    ("fac_school",         "小中学校数",           "地図で半径1km内を数える"),
    ("fac_nursery",        "保育園・幼稚園数",      "同上。洋日配・精肉に効く可能性"),
    ("fac_university",     "大学・専門学校数",      "同上。パン・フローズンに効く可能性"),
    ("fac_factory",        "工場数",               "同上"),
    ("fac_office_workers", "オフィス就業者数",      "商圏レポートの従業者数で代用可"),
    ("fac_apartment_units", "集合住宅戸数",         "地図・自治体統計。概算で可"),
    ("pop_density",        "人口密度(人/km2)",      "商圏人口 ÷ 商圏面積。商圏レポートに面積があればそこから"),
]

META_COLUMNS = ["store_id", "store_name", "searched_at", "source_url", "confidence", "notes"]


def store_name_from_filename(path: str | Path) -> str:
    """ファイル名から店舗名を取り出す。

    「〇〇店_商圏レポート_20250401.xlsx」「〇〇店 (自転車10分).xlsx」などを想定し、
    末尾の日付・定型語・括弧書きを落とす。
    """
    stem = Path(path).stem
    stem = re.sub(r"[（(][^）)]*[）)]\s*$", "", stem)          # 末尾の括弧書き
    stem = re.sub(r"[_\-\s]*\d{6,8}\s*$", "", stem)            # 末尾の日付
    for word in ("商圏レポート", "商圏分析", "商圏データ", "商圏", "レポート", "様"):
        stem = stem.replace(word, "")
    stem = re.sub(r"[_\-\s]+", " ", stem).strip(" _-")
    return stem or Path(path).stem


def make_search_plan(
    xlsx_paths: list[str | Path], out_dir: str | Path
) -> tuple[Path, Path, list[str]]:
    """検索用のCSVひな形と、店舗ごとの調査メモを書き出す。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [store_name_from_filename(p) for p in xlsx_paths]

    cols = META_COLUMNS + [k for k, _, _ in SEARCH_COLUMNS]
    df = pd.DataFrame([{c: "" for c in cols} for _ in names])
    df["store_name"] = names
    df["store_id"] = [f"S{i:02d}" for i in range(1, len(names) + 1)]
    csv_path = out_dir / "search_profile.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        "# 商圏の検索データ 調査メモ",
        "",
        "商圏レポートのファイル名から店舗名を取り出しました。",
        "下の項目を店舗ごとに調べ、`search_profile.csv` の同じ列に書き込んでください。",
        "空欄のままでも動きます（その項目は類似度・範囲チェックから外れます）。",
        "",
        "## 調べる項目",
        "",
        "| 列名 | 項目 | 調べ方 |",
        "|---|---|---|",
    ]
    lines += [f"| `{k}` | {label} | {hint} |" for k, label, hint in SEARCH_COLUMNS]
    lines += [
        "",
        "## 店舗ごとの検索クエリ",
        "",
        "そのまま検索に貼れる形にしてあります。地図サービスの件数は、",
        "**半径を必ず揃えて**数えてください（店によって半径が違うと比較が壊れます）。",
        "",
    ]
    for sid, name in zip(df["store_id"], names):
        lines += [
            f"### {sid} {name}",
            "",
            f"- `{name} 住所`",
            f"- `{name} 営業時間 駐車場`",
            f"- `{name} 売場面積`",
            f"- `{name} 最寄駅`",
            f"- 地図で `{name}` を中心に `スーパー` / `コンビニ` / `ドラッグストア`"
            " をそれぞれ半径1km・3kmで検索し件数を数える",
            f"- 地図で `{name}` を中心に `小学校` / `保育園` / `大学` / `工場` を半径1kmで検索",
            "",
        ]
    md_path = out_dir / "search_brief.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, md_path, names


def load_search_profile(path: str | Path, cfg: Config) -> pd.DataFrame:
    """検索結果CSVを読み込み、内部キーに揃える。"""
    df = _read_any(path)
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    if "store_id" not in df.columns and "store_name" not in df.columns:
        raise InputError(
            f"検索データに store_id も store_name もありません: {path}"
        )
    for c in df.columns:
        if c in cfg.variables and cfg.variables[c].numeric:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "store_id" in df.columns:
        df["store_id"] = df["store_id"].astype(str).str.strip()
    return df


def merge_search_profile(
    master: pd.DataFrame, search: pd.DataFrame, cfg: Config
) -> tuple[pd.DataFrame, list[str]]:
    """既存店マスタに検索結果を上書き結合する（マスタ側が空の列だけ埋める）。"""
    notes: list[str] = []
    key = "store_id" if "store_id" in search.columns and search["store_id"].notna().any() \
        else cfg.name_key
    if key not in master.columns:
        raise InputError(f"結合キー '{key}' が既存店マスタにありません。")

    idx = search.set_index(search[key].astype(str))
    merged = master.copy()
    filled: dict[str, int] = {}
    for i, row in merged.iterrows():
        k = str(row[key])
        if k not in idx.index:
            notes.append(f"検索データに該当行がない店舗: {k}")
            continue
        src = idx.loc[k]
        if isinstance(src, pd.DataFrame):
            src = src.iloc[0]
        for col in search.columns:
            if col in (key, "store_id", "store_name", "searched_at",
                       "source_url", "confidence", "notes"):
                continue
            if col not in merged.columns:
                merged[col] = pd.NA
            v = src.get(col)
            cur = merged.at[i, col]
            if (pd.isna(cur) or cur == "") and v is not None and not pd.isna(v) and v != "":
                merged.at[i, col] = v
                filled[col] = filled.get(col, 0) + 1
    if filled:
        notes.append("検索データで埋めた列: "
                     + "、".join(f"{cfg.label(k)}({v}店)" for k, v in sorted(filled.items())))
    return merged, notes
