"""週次IDPOSの読み込みと集計。

実データは2ファイル構成。
  IDPOS____.csv     : 年週 × 店舗 × 階層 × 顧客種類 × 年代 → 売上税抜金額・PI値
  URE_ZAIKO____.csv : 年週 × 店舗 × 階層 → 販売荒利高(千円)・販売荒利率

階層は ディビジョン > ライン > 部門 > カテゴリー > サブカテゴリー。
level を変えるとどの粒度でも集計できる（和日配 → 納豆 → … のドリルダウン）。

週次データで増えるのは観測の精度であって、商圏→PIを学習するための
独立サンプル数ではない。52週あっても店舗は5のまま。
週次から取り出すのは次の3つ。
  1. 水準   : 外れ週を落とした平均（年1本の数字より安定する）
  2. ブレ   : 週次の変動係数。予測精度の上限はここで決まる
  3. 季節性 : 月別指数。開店月の実績を年平均と直接比べないために使う
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .io_loader import InputError
from .metrics_config import MetricsConfig

LEVELS = ["division", "line", "department", "category", "subcategory"]
LEVEL_LABEL = {
    "division": "ディビジョン", "line": "ライン", "department": "部門",
    "category": "カテゴリー", "subcategory": "サブカテゴリー",
}
SEP = " > "


# ---------------------------------------------------------------- 読み込み

def _read_csv(path: str | Path, encoding: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise InputError(f"ファイルが見つかりません: {path}")
    last: Exception | None = None
    for enc in (encoding, "cp932", "utf-8-sig", "utf-8", "euc_jp"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str)
        except UnicodeDecodeError as e:
            last = e
    raise InputError(f"文字コードを判別できませんでした: {path}（{last}）")


def _rename(df: pd.DataFrame, mapping: dict[str, str], path, what: str) -> pd.DataFrame:
    df = df.rename(columns={c: str(c).strip() for c in df.columns})
    rev, missing = {}, []
    for key, col in mapping.items():
        if col in df.columns:
            rev[col] = key
        else:
            missing.append(f"{key} ← '{col}'")
    if not rev:
        raise InputError(
            f"{what}の列が1つも一致しませんでした: {path}\n"
            f"  読み込めた列: {list(df.columns)}\n"
            f"  config/metrics.yaml の idpos.*_file.columns を実データの列名に合わせてください。"
        )
    return df.rename(columns=rev), missing


_WEEK_RE = re.compile(r"(\d{4})\D*?(\d{1,2})\D*$")


def parse_year_week(value) -> tuple[int | None, int | None]:
    """'202640' と '2026年40週' の両方を (年, 週) にする。"""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None, None
    s = str(value).strip()
    if re.fullmatch(r"\d{6}", s):
        return int(s[:4]), int(s[4:])
    m = _WEEK_RE.search(s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def week_to_date(year: int | None, week: int | None) -> pd.Timestamp | None:
    """年週をISO週の月曜日にする。季節性（月）の判定に使うだけ。"""
    if not year or not week:
        return None
    try:
        return pd.Timestamp(dt.date.fromisocalendar(int(year), min(int(week), 53), 1))
    except ValueError:
        try:
            return pd.Timestamp(dt.date.fromisocalendar(int(year), 52, 1))
        except ValueError:
            return None


def _num(s: pd.Series) -> pd.Series:
    """'14.30%' '1,234' '△5' などを数値にする。%は0-1に直す。"""
    t = s.astype(str).str.strip()
    pct = t.str.endswith("%")
    t = (t.str.replace(",", "", regex=False)
          .str.replace("%", "", regex=False)
          .str.replace("△", "-", regex=False)
          .str.replace("▲", "-", regex=False))
    v = pd.to_numeric(t, errors="coerce")
    return v.where(~pct, v / 100.0)


# ---------------------------------------------------------------- データ構造

@dataclass
class CellStat:
    """1店舗 × 1分析単位 × 1指標 の集計結果。"""

    store_id: str
    unit: str
    metric: str
    level: float | None        # 外れ週を落とした平均
    median: float | None
    sd: float | None
    cv: float | None           # 変動係数 = sd / level。予測精度の上限
    se: float | None
    n_weeks: int
    vmin: float | None
    vmax: float | None
    reliable: bool


@dataclass
class IdposData:
    panel: pd.DataFrame                   # store × unit × week の週次パネル
    age: pd.DataFrame                     # store × unit × age_band の構成比
    stats: dict[tuple[str, str, str], CellStat]
    seasonality: pd.DataFrame
    units: list[str]                      # 分析単位（例 "和日配 > 納豆"）
    unit_parents: dict[str, dict[str, str]]  # unit -> {level: 名称}
    metrics: list[str]
    store_ids: list[str]
    store_names: dict[str, str]
    level: str
    age_bands: list[str]
    age_unknown_share: dict[str, float]   # store -> 年代不明の売上シェア
    warnings: list[str] = field(default_factory=list)

    def level_label(self) -> str:
        return LEVEL_LABEL.get(self.level, self.level)

    def leaf(self, unit: str) -> str:
        return unit.split(SEP)[-1]

    def parent_of(self, unit: str, level: str = "line") -> str:
        return self.unit_parents.get(unit, {}).get(level, "")

    def value(self, store: str, unit: str, metric: str) -> float | None:
        st = self.stats.get((store, unit, metric))
        return st.level if st else None

    def frame(self, metric: str) -> pd.DataFrame:
        rows: dict[str, dict[str, float | None]] = {}
        for (sid, unit, m), st in self.stats.items():
            if m == metric:
                rows.setdefault(sid, {})[unit] = st.level
        return pd.DataFrame(rows).T.reindex(columns=self.units)

    def store_age_mix(self, store: str, unit: str | None = None) -> dict[str, float] | None:
        a = self.age[self.age["store_id"] == store]
        if unit is not None:
            a = a[a["unit"] == unit]
        if a.empty:
            return None
        by = a.groupby("age_band")["sales_amount"].sum()
        total = by.sum()
        if total <= 0:
            return None
        return {b: float(by.get(b, 0.0) / total) for b in self.age_bands}


# ---------------------------------------------------------------- 本体

def load_idpos(
    pi_path: str | Path,
    margin_path: str | Path | None,
    mcfg: MetricsConfig,
    *,
    level: str | None = None,
) -> IdposData:
    spec = mcfg.idpos
    enc = spec.get("encoding", "cp932")
    level = level or spec.get("level", "line")
    if level not in LEVELS:
        raise InputError(f"level は {LEVELS} のいずれかにしてください（指定: {level}）")
    warnings: list[str] = []

    # ---- PIファイル ----
    pcols = spec["pi_file"]["columns"]
    pdf = _read_csv(pi_path, enc)
    pdf, miss = _rename(pdf, pcols, pi_path, "IDPOSファイル")
    if miss:
        warnings.append(f"IDPOSファイルに無い列（無視します）: {'、'.join(miss)}")
    for need in ("store_code", "year_week", "line"):
        if need not in pdf.columns:
            raise InputError(f"IDPOSファイルに必須の列 '{pcols.get(need)}' がありません。")

    pdf["store_id"] = pdf["store_code"].astype(str).str.strip()
    if "store_name" not in pdf.columns:
        pdf["store_name"] = pdf["store_id"]
    pdf["store_name"] = pdf["store_name"].astype(str).str.strip()
    yw = pdf["year_week"].map(parse_year_week)
    pdf["year"] = [a for a, _ in yw]
    pdf["week"] = [b for _, b in yw]
    pdf["week_date"] = [week_to_date(a, b) for a, b in yw]
    bad = int(pdf["week_date"].isna().sum())
    if bad:
        warnings.append(f"年週を解釈できない行が{bad}件あり、季節性の計算から外しました。")
    pdf["sales_amount"] = _num(pdf["sales_amount"]) if "sales_amount" in pdf else np.nan
    pdf["pi"] = _num(pdf["pi"]) if "pi" in pdf else np.nan

    for lv in LEVELS:
        if lv not in pdf.columns:
            pdf[lv] = ""
        pdf[lv] = pdf[lv].astype(str).str.strip()

    div_filter = spec.get("division_filter") or []
    if div_filter and "division" in pdf.columns:
        before = len(pdf)
        pdf = pdf[pdf["division"].isin(div_filter)]
        if pdf.empty:
            raise InputError(
                f"ディビジョン {div_filter} の行がIDPOSにありません。\n"
                f"  実データのディビジョン: "
                f"{sorted(set(_read_csv(pi_path, enc).get(pcols['division'], pd.Series(dtype=str)).dropna()))[:10]}"
            )
        warnings.append(f"ディビジョン {div_filter} に絞り込み: {before}行 → {len(pdf)}行")

    # ---- 分析単位 ----
    path_levels = LEVELS[1:LEVELS.index(level) + 1]   # line から level まで
    pdf["unit"] = pdf[path_levels].agg(SEP.join, axis=1).str.strip(SEP)
    unit_parents = {
        u: dict(zip(path_levels, u.split(SEP)))
        for u in sorted(set(pdf["unit"])) if u
    }

    # ---- 年代の扱い ----
    unknown = set(spec.get("age_unknown_labels", []))
    ct = spec.get("customer_type_for_age")

    # 年代が取れない売上の割合は、顧客種類で絞る前に見る（非会員はたいてい年代不明）
    unknown_share: dict[str, float] = {}
    if "age_band" in pdf.columns:
        tmp = pdf.copy()
        tmp["age_band"] = tmp["age_band"].astype(str).str.strip()
        for sid, g in tmp.groupby("store_id"):
            tot = g["sales_amount"].sum()
            unk = g.loc[g["age_band"].isin(unknown), "sales_amount"].sum()
            unknown_share[str(sid)] = float(unk / tot) if tot else 0.0

    age_src = pdf.copy()
    if ct and "customer_type" in age_src.columns:
        hit = age_src["customer_type"].astype(str).str.strip() == str(ct)
        if hit.any():
            age_src = age_src[hit]
        else:
            warnings.append(
                f"顧客種類 '{ct}' の行が無いため、年代構成は全件から算出しました。"
            )
    if "age_band" in age_src.columns:
        age_src["age_band"] = age_src["age_band"].astype(str).str.strip()
        age_src = age_src[~age_src["age_band"].isin(unknown)]
    else:
        age_src = age_src.iloc[0:0]

    age = (age_src.groupby(["store_id", "unit", "age_band"], as_index=False)["sales_amount"]
           .sum()) if not age_src.empty else pd.DataFrame(
        columns=["store_id", "unit", "age_band", "sales_amount"])
    age_bands = _sort_age_bands(sorted(set(age["age_band"]))) if not age.empty else []

    # ---- 週次パネル（年代・顧客種類を畳む） ----
    keys = ["store_id", "store_name", "unit", "year", "week", "week_date"]
    agg = pdf.groupby(keys, as_index=False, dropna=False).agg(
        pi=("pi", "sum"), sales_amount=("sales_amount", "sum")
    )

    # ---- 粗利ファイル ----
    if margin_path:
        mspec = spec["margin_file"]
        mdf = _read_csv(margin_path, enc)
        mdf, mmiss = _rename(mdf, mspec["columns"], margin_path, "粗利ファイル")
        if mmiss:
            warnings.append(f"粗利ファイルに無い列（無視します）: {'、'.join(mmiss)}")
        mdf["store_id"] = mdf["store_code"].astype(str).str.strip()
        myw = mdf["year_week"].map(parse_year_week)
        mdf["year"] = [a for a, _ in myw]
        mdf["week"] = [b for _, b in myw]
        for lv in LEVELS:
            if lv not in mdf.columns:
                mdf[lv] = ""
            mdf[lv] = mdf[lv].astype(str).str.strip()
        if div_filter:
            mdf = mdf[mdf["division"].isin(div_filter)]
        mdf["unit"] = mdf[path_levels].agg(SEP.join, axis=1).str.strip(SEP)
        scale = float(mspec.get("gross_profit_scale", 1))
        mdf["gross_profit"] = _num(mdf["gross_profit"]) * scale
        mdf["gross_margin_rate"] = _num(mdf["gross_margin_rate"])
        # 率から売上を逆算して、上位階層の率を加重平均で正しく出す
        with np.errstate(divide="ignore", invalid="ignore"):
            mdf["sales_est"] = mdf["gross_profit"] / mdf["gross_margin_rate"].replace(0, np.nan)
        magg = mdf.groupby(["store_id", "unit", "year", "week"], as_index=False).agg(
            gross_profit=("gross_profit", "sum"), sales_est=("sales_est", "sum")
        )
        magg["gross_margin_rate"] = np.where(
            magg["sales_est"] > 0, magg["gross_profit"] / magg["sales_est"], np.nan
        )
        agg = agg.merge(magg[["store_id", "unit", "year", "week",
                              "gross_profit", "gross_margin_rate"]],
                        on=["store_id", "unit", "year", "week"], how="left")
        matched = agg["gross_margin_rate"].notna().mean() if len(agg) else 0.0
        if matched < 0.5:
            warnings.append(
                f"粗利ファイルと突き合わせられた週が{matched:.0%}しかありません。"
                "年週の表記か店舗CD・階層名がずれていないか確認してください。"
            )
    else:
        agg["gross_profit"] = np.nan
        agg["gross_margin_rate"] = np.nan
        warnings.append("粗利ファイルが指定されていないため、粗利率は出力しません。")

    # ---- 派生指標 ----
    pi_kind = spec.get("pi_kind", "amount")
    metric_keys = ["pi"]
    if agg["gross_margin_rate"].notna().any():
        metric_keys.append("gross_margin_rate")
    agg["sales_per_week"] = agg["sales_amount"]
    if agg["sales_per_week"].notna().any():
        metric_keys.append("sales_per_week")
    if pi_kind == "amount" and "gross_margin_rate" in metric_keys:
        agg["gp_pi"] = agg["pi"] * agg["gross_margin_rate"]
        metric_keys.append("gp_pi")
    else:
        warnings.append(
            "PI値が数量ベース（pi_kind: quantity）の設定のため、粗利PIは計算していません。"
            "PI値が金額ベースなら config/metrics.yaml の idpos.pi_kind を amount にしてください。"
        )

    # ---- 店舗 × 単位 × 指標 の集計 ----
    min_weeks = int(spec.get("min_weeks", 8))
    trim = float(spec.get("trim_ratio", 0.1))
    stats: dict[tuple[str, str, str], CellStat] = {}
    for (sid, unit), g in agg.groupby(["store_id", "unit"]):
        for mk in metric_keys:
            x = pd.to_numeric(g[mk], errors="coerce").dropna()
            if x.empty:
                continue
            lvl = _trimmed_mean(x, trim)
            sd = float(x.std(ddof=1)) if len(x) > 1 else None
            stats[(str(sid), str(unit), mk)] = CellStat(
                store_id=str(sid), unit=str(unit), metric=mk, level=lvl,
                median=float(x.median()), sd=sd,
                cv=(sd / lvl) if (sd is not None and lvl not in (None, 0)) else None,
                se=(sd / np.sqrt(len(x))) if sd is not None else None,
                n_weeks=len(x), vmin=float(x.min()), vmax=float(x.max()),
                reliable=len(x) >= min_weeks,
            )

    thin = sorted({f"{k[0]}/{k[1]}" for k, v in stats.items() if not v.reliable})
    if thin:
        warnings.append(
            f"週数が{min_weeks}週未満の店舗×単位が{len(thin)}件あります"
            f"（{'、'.join(thin[:5])}{' ほか' if len(thin) > 5 else ''}）。水準の信頼度が落ちます。"
        )

    units = sorted(set(agg["unit"]))
    store_names = (pdf.drop_duplicates("store_id")
                   .set_index("store_id")["store_name"].to_dict())

    return IdposData(
        panel=agg, age=age, stats=stats,
        seasonality=_seasonality(agg, metric_keys),
        units=units, unit_parents=unit_parents, metrics=metric_keys,
        store_ids=sorted(set(agg["store_id"])), store_names=store_names,
        level=level, age_bands=age_bands, age_unknown_share=unknown_share,
        warnings=warnings,
    )


# ---------------------------------------------------------------- 補助

_AGE_ORDER = ["10代未満", "10代", "20代", "30代", "40代", "50代", "60代",
              "70代", "70代以上", "80代以上"]


def _sort_age_bands(bands: list[str]) -> list[str]:
    def key(b: str) -> tuple[int, str]:
        return (_AGE_ORDER.index(b) if b in _AGE_ORDER else 99, b)
    return sorted(bands, key=key)


def _trimmed_mean(x: pd.Series, ratio: float) -> float | None:
    x = x.dropna()
    if x.empty:
        return None
    if len(x) < 5 or ratio <= 0:
        return float(x.mean())
    lo, hi = x.quantile(ratio), x.quantile(1 - ratio)
    kept = x[(x >= lo) & (x <= hi)]
    return float(kept.mean()) if len(kept) else float(x.mean())


def _seasonality(panel: pd.DataFrame, metric_keys: list[str]) -> pd.DataFrame:
    d = panel[panel["week_date"].notna()].copy()
    if d.empty:
        return pd.DataFrame()
    d["month"] = pd.to_datetime(d["week_date"]).dt.month
    rows = []
    for unit, g in d.groupby("unit"):
        for mk in metric_keys:
            overall = pd.to_numeric(g[mk], errors="coerce").mean()
            if not np.isfinite(overall) or overall == 0:
                continue
            for month, gm in g.groupby("month"):
                v = pd.to_numeric(gm[mk], errors="coerce").mean()
                if np.isfinite(v):
                    rows.append({"unit": unit, "metric": mk, "month": int(month),
                                 "index": float(v / overall), "n_weeks": len(gm)})
    return pd.DataFrame(rows)


def seasonal_index(seasonality: pd.DataFrame, unit: str, metric: str,
                   months: list[int]) -> float | None:
    if seasonality.empty or not months:
        return None
    sub = seasonality[(seasonality["unit"] == unit)
                      & (seasonality["metric"] == metric)
                      & (seasonality["month"].isin(months))]
    if sub.empty:
        return None
    return float(np.average(sub["index"], weights=sub["n_weeks"]))
