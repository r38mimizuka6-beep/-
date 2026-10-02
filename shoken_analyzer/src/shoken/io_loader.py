"""入力CSV/Excelの読み込みと正規化。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config, eval_formula


class InputError(ValueError):
    """入力データの不備。利用者にそのまま見せるメッセージを持つ。"""


def _read_any(path: str | Path, sheet: str | None = None) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise InputError(f"ファイルが見つかりません: {path}")
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    return pd.read_csv(path, encoding="utf-8-sig")


def load_store_master(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.DataFrame:
    """既存店マスタ（1行=1店舗）。列名を内部キーに揃え、派生変数を足す。"""
    df = _read_any(path, sheet)
    df = _normalize(df, cfg, source_label=str(path))
    if len(df) < 2:
        raise InputError(f"既存店が{len(df)}件しかありません。最低2件（推奨5件）必要です: {path}")
    return df


def load_new_store(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.Series:
    """新店の商圏情報（1行）。"""
    df = _read_any(path, sheet)
    df = _normalize(df, cfg, source_label=str(path))
    if len(df) != 1:
        raise InputError(f"新店ファイルは1行にしてください（現在{len(df)}行）: {path}")
    return df.iloc[0]


def _normalize(df: pd.DataFrame, cfg: Config, *, source_label: str) -> pd.DataFrame:
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    df = df.rename(columns=cfg.rename_map())

    if cfg.id_key not in df.columns:
        raise InputError(
            f"店舗IDの列 '{cfg.id_source}' が見つかりません: {source_label}\n"
            f"  読み込めた列: {list(df.columns)[:12]} ...\n"
            f"  config/columns.yaml の meta.id_column.source を実データの列名に合わせてください。"
        )
    if cfg.name_key not in df.columns:
        df[cfg.name_key] = df[cfg.id_key]

    df[cfg.id_key] = df[cfg.id_key].astype(str).str.strip()
    df[cfg.name_key] = df[cfg.name_key].astype(str).str.strip()

    for key, var in cfg.variables.items():
        if key not in df.columns:
            df[key] = pd.NA
        elif var.numeric:
            df[key] = pd.to_numeric(df[key], errors="coerce")

    return add_derived(df, cfg)


def add_derived(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """config の derived に書かれた式を行ごとに評価して列を足す。"""
    df = df.copy()
    for spec in cfg.derived:
        key, formula = spec["key"], spec["formula"]
        values = []
        for _, row in df.iterrows():
            row_vals = {k: v for k, v in row.items() if isinstance(v, (int, float)) and pd.notna(v)}
            values.append(eval_formula(formula, row_vals))
        df[key] = values
    return df


def load_sales_mix(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.DataFrame:
    """店舗 × カテゴリの売上構成比・粗利。

    必須列: store_id, category, sales_share
    任意列: category_major, gross_margin_rate, gross_profit, sales_amount
    """
    df = _read_any(path, sheet)
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    df = df.rename(columns={cfg.id_source: cfg.id_key})
    required = {cfg.id_key, "category", "sales_share"}
    missing = required - set(df.columns)
    if missing:
        raise InputError(f"売上構成比ファイルに必須列がありません: {sorted(missing)} ({path})")
    df[cfg.id_key] = df[cfg.id_key].astype(str).str.strip()
    for col in ("sales_share", "gross_margin_rate", "gross_profit", "sales_amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 構成比が%表記（合計100前後）なら 0-1 に直す
    totals = df.groupby(cfg.id_key)["sales_share"].sum()
    if len(totals) and totals.median() > 10:
        df["sales_share"] = df["sales_share"] / 100.0
        totals = df.groupby(cfg.id_key)["sales_share"].sum()
    off = totals[(totals < 0.95) | (totals > 1.05)]
    if len(off):
        bad = ", ".join(f"{k}={v:.3f}" for k, v in off.items())
        df.attrs["warning"] = f"売上構成比の合計が1.0から外れている店舗があります: {bad}"
    return df


def load_member_mix(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.DataFrame:
    """店舗 × 性別年代の会員構成比。

    必須列: store_id, gender, age_band, member_share
    """
    df = _read_any(path, sheet)
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    df = df.rename(columns={cfg.id_source: cfg.id_key})
    required = {cfg.id_key, "age_band", "member_share"}
    missing = required - set(df.columns)
    if missing:
        raise InputError(f"会員構成比ファイルに必須列がありません: {sorted(missing)} ({path})")
    if "gender" not in df.columns:
        df["gender"] = "計"
    df[cfg.id_key] = df[cfg.id_key].astype(str).str.strip()
    df["member_share"] = pd.to_numeric(df["member_share"], errors="coerce")
    totals = df.groupby(cfg.id_key)["member_share"].sum()
    if len(totals) and totals.median() > 10:
        df["member_share"] = df["member_share"] / 100.0
    return df
