"""入力CSV/Excelの読み込みと正規化。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config, eval_formula


class InputError(ValueError):
    """入力データの不備。利用者にそのまま見せるメッセージを持つ。"""


def _read_any(path: str | Path, sheet: str | None = None,
              dtype: dict | None = None) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise InputError(f"ファイルが見つかりません: {path}")
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        return pd.read_excel(path, sheet_name=sheet or 0, dtype=dtype)
    # 店舗CDの先頭ゼロを落とさないよう dtype を指定できるようにしている
    for enc in ("utf-8-sig", "cp932"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=dtype)
        except UnicodeDecodeError:
            continue
    raise InputError(f"文字コードを判別できませんでした: {path}")


def load_store_master(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.DataFrame:
    """既存店マスタ（1行=1店舗）。列名を内部キーに揃え、派生変数を足す。"""
    df = _read_any(path, sheet, dtype={cfg.id_source: str})
    df = _normalize(df, cfg, source_label=str(path))
    if len(df) < 2:
        raise InputError(f"既存店が{len(df)}件しかありません。最低2件（推奨5件）必要です: {path}")
    return df


def load_new_store(path: str | Path, cfg: Config, *, sheet: str | None = None) -> pd.Series:
    """新店の商圏情報（1行）。"""
    df = _read_any(path, sheet, dtype={cfg.id_source: str})
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
