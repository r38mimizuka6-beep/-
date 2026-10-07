"""検索データExcel（1店舗=1ブック）から、検索プロファイルの1行を取り出す。

調査会社や担当者が作る検索ブックは、商圏レポートと違って文章で書かれている。
  商圏サマリー : 「駐車場 | 144台」のような縦持ち
  競合店一覧   : 1行1店。業態の列を数えれば件数になる
  周辺施設一覧 : 1行1施設。カテゴリーの列を数えれば件数になる

ここでは数えられるものだけを数え、読み取れなかった項目は空のまま返す。
推測で埋めると、5店舗しかない比較が静かに壊れるため。
どう解釈したかは notes に残し、画面で必ず人が確認できるようにする。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .io_loader import InputError
from .search_profile import store_id_from_filename

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_METERS = re.compile(r"(\d[\d,]*)\s*(?:m|メートル)\b", re.IGNORECASE)
_WALK_MIN = re.compile(r"徒歩[^\d]{0,6}(\d+)\s*(?:[〜~\-ー－]\s*(\d+))?\s*分")
_DATE = re.compile(r"(\d{4})\D{1,3}(\d{1,2})(?:\D{1,3}(\d{1,2}))?")


@dataclass
class SearchBook:
    row: dict[str, str]
    notes: list[str] = field(default_factory=list)
    radius_notes: list[str] = field(default_factory=list)


def _nfkc(v) -> str:
    # 空セルは NaN で来る。str() すると "nan" になって文面に紛れ込む。
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = unicodedata.normalize("NFKC", str(v)).strip()
    return "" if s.lower() in ("nan", "none", "nat") else s


def _first_number(text) -> float | None:
    m = _NUM.search(_nfkc(text))
    return float(m.group().replace(",", "")) if m else None


def _contains_any(haystack: str, needles: list[str]) -> bool:
    h = _nfkc(haystack).lower()
    return any(_nfkc(n).lower() in h for n in needles if str(n).strip())


def _walk_meters(text, per_minute: float) -> tuple[float | None, str | None]:
    """徒歩の記述から駅までの距離(m)を見積もる。"""
    s = _nfkc(text)
    m = _METERS.search(s)
    if m:
        return float(m.group(1).replace(",", "")), "記載の距離"
    w = _WALK_MIN.search(s)
    if w:
        lo = float(w.group(1))
        hi = float(w.group(2)) if w.group(2) else lo
        minutes = (lo + hi) / 2
        return round(minutes * per_minute), f"「{w.group(0)}」から1分={per_minute:g}mで換算"
    return None, None


def _to_date(text) -> str | None:
    m = _DATE.search(_nfkc(text))
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), m.group(3)
    if not 1 <= mo <= 12:
        return None
    return f"{y}-{mo:02d}-{int(d):02d}" if d else f"{y}-{mo:02d}-01"


def _find_sheet(book: dict[str, pd.DataFrame], names: list[str]) -> str | None:
    for want in names:
        for actual in book:
            if _nfkc(want).lower() in _nfkc(actual).lower():
                return actual
    return None


def _header_index(df: pd.DataFrame, names: list[str]) -> int | None:
    """1行目を見出しとみなし、別名のどれかに一致する列の位置を返す。"""
    for i, col in enumerate(df.columns):
        if _contains_any(col, names):
            return i
    return None


def extract_search_row(path: str | Path, config_path: str | Path) -> SearchBook:
    path = Path(path)
    if not path.exists():
        raise InputError(f"ファイルが見つかりません: {path}")
    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))

    try:
        book = pd.read_excel(path, sheet_name=None, dtype=str)
    except Exception as e:                                   # noqa: BLE001
        raise InputError(f"Excelとして読めませんでした: {path}（{e}）") from e

    row: dict[str, str] = {}
    notes: list[str] = []
    radius: list[str] = []

    # ファイル名の頭に店舗CDが付いていれば採用する（0785_花小金井.xlsx → 0785）。
    code = store_id_from_filename(path)
    if code:
        row["store_id"] = code
        notes.append(f"ファイル名から店舗CD {code} を読みました")

    scfg = cfg["summary"]
    sheet = _find_sheet(book, scfg["sheet_names"])
    if sheet is None:
        raise InputError(
            f"サマリーのシートが見つかりません: {path.name}\n"
            f"  シート: {list(book)}\n"
            f"  config/extract_search_book.yaml の summary.sheet_names に"
            "実際のシート名を足してください。"
        )

    df = book[sheet]
    ki = _header_index(df, scfg["key_header"]) or 0
    vi = _header_index(df, scfg["value_header"])
    ni = _header_index(df, scfg.get("note_header", []))
    if vi is None:
        vi = ki + 1
    if vi >= df.shape[1]:
        raise InputError(f"サマリーに値の列がありません: {path.name}")

    pairs: list[tuple[str, str, str]] = []
    for rec in df.itertuples(index=False):
        key = _nfkc(rec[ki])
        if not key:
            continue
        val = _nfkc(rec[vi]) if vi < len(rec) else ""
        note = _nfkc(rec[ni]) if ni is not None and ni < len(rec) else ""
        pairs.append((key, val, note))

    numeric = {"station_daily_users", "parking_spaces", "sales_floor_sqm",
               "nearest_comp_m", "nearest_comp_price"}
    # 構成比は「35%」でも「0.35」でも受ける。1を超えていたら百分率とみなす。
    shares = {"share_walk", "share_bike", "share_car", "share_train"}
    for field_key, aliases in scfg["fields"].items():
        for key, val, _ in pairs:
            if not _contains_any(key, aliases) or not val:
                continue
            if field_key in shares:
                n = _first_number(val)
                if n is None:
                    notes.append(f"{key}「{val}」から数値を取れませんでした")
                    continue
                frac = n / 100 if ("%" in _nfkc(val) or n > 1) else n
                row[field_key] = f"{frac:g}"
                if f"{frac:g}" != _nfkc(val):
                    notes.append(f"{key}「{val}」→ {frac:g}")
            elif field_key in numeric:
                n = _first_number(val)
                if n is None:
                    notes.append(f"{key}「{val}」から数値を取れませんでした")
                    continue
                row[field_key] = f"{n:g}"
                if _nfkc(val) != f"{n:g}":
                    notes.append(f"{key}「{val}」→ {n:g}")
            elif field_key == "open_date":
                d = _to_date(val)
                if d is None:
                    notes.append(f"{key}「{val}」から日付を取れませんでした")
                    continue
                row[field_key] = d
                notes.append(f"{key}「{val}」→ {d}")
            else:
                row[field_key] = val
            break

    per_min = float(scfg.get("walk_meters_per_minute", 80))
    for key, val, _ in pairs:
        if _contains_any(key, scfg.get("walk_field", [])) and val:
            meters, how = _walk_meters(val, per_min)
            if meters is not None:
                row["nearest_station_m"] = f"{meters:g}"
                notes.append(f"最寄駅までの距離 {meters:g}m（{how}。目安です）")
            break

    for key, val, note in pairs:
        if _contains_any(key, cfg.get("radius_note_keys", [])):
            radius.append(f"{key}: {val}" + (f"（{note}）" if note else ""))

    for spec in cfg.get("lists", []):
        sh = _find_sheet(book, spec["sheet_names"])
        if sh is None:
            notes.append(f"{spec['name']}のシートが見つかりませんでした")
            continue
        ldf = book[sh]
        ci = _header_index(ldf, spec["category_header"])
        if ci is None:
            notes.append(f"{spec['name']}に業態／カテゴリーの列が見つかりませんでした")
            continue
        cats = [_nfkc(v) for v in ldf.iloc[:, ci].tolist() if _nfkc(v)]
        counted = 0
        for out_key, words in spec["counts"].items():
            n = sum(1 for c in cats if _contains_any(c, words))
            if n:
                row[out_key] = str(n)
                counted += n
        if cats and counted < len(cats):
            unmatched = sorted({c for c in cats
                                if not any(_contains_any(c, w)
                                           for w in spec["counts"].values())})
            notes.append(f"{spec['name']}で分類できなかった区分（数えていません）: "
                         + "、".join(unmatched))

    return SearchBook(row=row, notes=notes, radius_notes=radius)
