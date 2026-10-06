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

LEVELS = ["division", "line", "department", "category", "subcategory", "segment"]
LEVEL_LABEL = {
    "division": "ディビジョン", "line": "ライン", "department": "部門",
    "category": "カテゴリー", "subcategory": "サブカテゴリー", "segment": "セグメント",
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
    panel: pd.DataFrame                   # store × unit × week（開店直後を除外済み）
    panel_all: pd.DataFrame               # 同上。除外前（立ち上がりカーブの推定に使う）
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
    maturity: pd.DataFrame = field(default_factory=pd.DataFrame)  # 店舗ごとの週数と経過
    ramp_adjusted: list[str] = field(default_factory=list)  # 立ち上がり補正を当てた店
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
        """年代構成。年代つきエクスポートが浅い階層までしか無い場合は親に遡る。

        実データは、年代つきのエクスポートが サブカテゴリーまで、
        数量つきのエクスポートが セグメントまで、のように深さが違うことがある。
        その場合、深い単位の年代構成は一番近い親のもので代用する。
        """
        a = self.age[self.age["store_id"] == store]
        if a.empty:
            return None
        if unit is not None:
            parts = unit.split(SEP)
            for depth in range(len(parts), 0, -1):
                prefix = SEP.join(parts[:depth])
                hit = a[a["unit"] == prefix]
                if not hit.empty:
                    a = hit
                    break
            else:
                return None
        if a.empty:
            return None
        by = a.groupby("age_band")["sales_amount"].sum()
        total = by.sum()
        if total <= 0:
            return None
        return {b: float(by.get(b, 0.0) / total) for b in self.age_bands}


# ---------------------------------------------------------------- 本体

def load_idpos(
    pi_path: str | Path | list,
    margin_path: str | Path | None,
    mcfg: MetricsConfig,
    *,
    level: str | None = None,
    opening_dates: dict[str, str] | None = None,
    customers_path: str | Path | None = None,
    exclude_opening_weeks: int | None = None,
) -> IdposData:
    """IDPOSを読み込み、店舗×分析単位×指標に集計する。

    pi_path には複数ファイルを渡せる。実データのエクスポートは種類が複数あり、
      ・年代の内訳つき（数量・客数なし）
      ・数量・客数つき（年代なし）
    のように列が違う。同じ売上を二重に数えないよう、合計は「数量つき」の
    ファイル1本から取り、年代構成は年代つきのファイルから取る。

    opening_dates を渡すと、各店の開店直後の週を水準の計算から外す。
    転換時期が店ごとに違うと、開店効果が残っている店の水準が高く出て、
    店舗間の比較が歪む。ここで揃えておかないと類似店法そのものが狂う。
    """
    spec = mcfg.idpos
    enc = spec.get("encoding", "cp932")
    level = level or spec.get("level", "line")
    if level not in LEVELS:
        raise InputError(f"level は {LEVELS} のいずれかにしてください（指定: {level}）")
    warnings: list[str] = []

    # ---- PIファイル（複数可） ----
    pcols = spec["pi_file"]["columns"]
    paths = [pi_path] if isinstance(pi_path, (str, Path)) else list(pi_path)
    views: list[tuple[Path, pd.DataFrame]] = []
    for path in paths:
        df = _read_csv(path, enc)
        df, miss = _rename(df, pcols, path, "IDPOSファイル")
        for need in ("store_code", "year_week", "line"):
            if need not in df.columns:
                raise InputError(
                    f"IDPOSファイルに必須の列 '{pcols.get(need)}' がありません: {path}")
        df["_source"] = Path(path).name
        views.append((Path(path), df))

    def _prep(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["store_id"] = df["store_code"].astype(str).str.strip()
        if "store_name" not in df.columns:
            df["store_name"] = df["store_id"]
        df["store_name"] = df["store_name"].astype(str).str.strip()
        yw = df["year_week"].map(parse_year_week)
        df["year"] = [a for a, _ in yw]
        df["week"] = [b for _, b in yw]
        df["week_date"] = [week_to_date(a, b) for a, b in yw]
        for col in ("sales_amount", "pi", "quantity", "pos_customers", "id_customers"):
            df[col] = _num(df[col]) if col in df.columns else np.nan
        for lv in LEVELS:
            if lv not in df.columns:
                df[lv] = ""
            df[lv] = df[lv].astype(str).str.strip()
        if div_filter and "division" in df.columns:
            df = df[df["division"].isin(div_filter)]
        return df

    div_filter = spec.get("division_filter") or []
    views = [(path, _prep(df)) for path, df in views]
    views = [(path, _drop_total_rows(df, spec, path, warnings)) for path, df in views]
    views = [(path, df) for path, df in views if not df.empty]
    if not views:
        raise InputError(
            f"ディビジョン {div_filter} の行がIDPOSにありません。"
            "config/metrics.yaml の idpos.division_filter を確認してください。"
        )

    # 合計を取るファイルは1本だけ選ぶ（二重計上を避ける）。数量つきを優先。
    def _rank(item) -> tuple:
        _, df = item
        return (df["quantity"].notna().any(), df["pos_customers"].notna().any(), len(df))
    totals_path, pdf = max(views, key=_rank)
    if len(views) > 1:
        warnings.append(
            f"IDPOSを{len(views)}本読み込みました。合計は {totals_path.name} から取り、"
            "他のファイルは内訳（年代など）の算出にだけ使います（二重計上を避けるため）。"
        )
    bad = int(pdf["week_date"].isna().sum())
    if bad:
        warnings.append(f"年週を解釈できない行が{bad}件あり、季節性の計算から外しました。")
    if div_filter:
        warnings.append(f"ディビジョン {div_filter} に絞り込みました。")

    # ---- 分析単位 ----
    path_levels = LEVELS[1:LEVELS.index(level) + 1]   # line から level まで
    for _, df in views:
        df["unit"] = df[path_levels].agg(SEP.join, axis=1).str.strip(SEP)
    pdf = pdf.copy()
    unit_parents = {
        u: dict(zip(path_levels, u.split(SEP)))
        for u in sorted(set(pdf["unit"])) if u
    }

    # ---- 年代の扱い ----
    unknown = set(spec.get("age_unknown_labels", []))
    ct = spec.get("customer_type_for_age")

    age_views = [df for _, df in views
                 if "age_band" in df.columns and df["age_band"].notna().any()
                 and df["age_band"].astype(str).str.strip().ne("").any()]
    age_src = (age_views[0].copy() if age_views else pdf.iloc[0:0].copy())
    if not age_views:
        warnings.append(
            "年代の内訳を持つIDPOSが渡されていないため、顧客層（年代別）は出せません。"
            "年代つきのエクスポートも --idpos に並べて渡してください。"
        )

    # 年代が取れない売上の割合は、顧客種類で絞る前に見る（非会員はたいてい年代不明）
    unknown_share: dict[str, float] = {}
    if "age_band" in age_src.columns and not age_src.empty:
        tmp = age_src.copy()
        tmp["age_band"] = tmp["age_band"].astype(str).str.strip()
        for sid, g in tmp.groupby("store_id"):
            tot = g["sales_amount"].sum()
            unk = g.loc[g["age_band"].isin(unknown), "sales_amount"].sum()
            unknown_share[str(sid)] = float(unk / tot) if tot else 0.0

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
        pi=("pi", "sum"), sales_amount=("sales_amount", "sum"),
        quantity=("quantity", "sum"), pos_customers=("pos_customers", "sum"),
    )

    # ---- 店全体の客数を復元 ----
    # 数量PI = 売上数量 ÷ 店全体の客数 × 1000 なので、客数 = 数量 ÷ PI × 1000。
    # 行ごとに同じ値になるはずなので、ばらつきはデータの異常を示す。
    store_customers = pd.DataFrame()
    if spec.get("derive_customers_from_pi", True) and pdf["quantity"].notna().any():
        src = pdf[(pdf["pi"] > 0) & pdf["quantity"].notna()]
        # 行ごとに割るとPI値の丸め（小数3桁）が効いてしまうので、
        # 店舗×週で合計してから割る。
        tot = src.groupby(["store_id", "year", "week"], as_index=False).agg(
            _q=("quantity", "sum"), _pi=("pi", "sum")
        )
        tot = tot[tot["_pi"] > 0]
        tot["customers"] = tot["_q"] / tot["_pi"] * 1000
        store_customers = tot[["store_id", "year", "week", "customers"]]

        # 検算: 丸めの影響が小さい大きな行だけで同じ計算をして、合計と突き合わせる
        big = src[src["quantity"] >= max(50, float(src["quantity"].quantile(0.9)))]
        if len(big) > 20:
            chk = big.assign(_c=big["quantity"] / big["pi"] * 1000).groupby(
                ["store_id", "year", "week"])["_c"].median().reset_index()
            cmp_ = chk.merge(store_customers, on=["store_id", "year", "week"])
            if len(cmp_):
                rel = ((cmp_["_c"] - cmp_["customers"]).abs()
                       / cmp_["customers"]).median()
                if float(rel) > 0.02:
                    warnings.append(
                        f"売上数量÷PI値から復元した客数が、明細単位と週合計で"
                        f"中央{float(rel):.1%}食い違います。PI値の分母が明細ごとに"
                        "違う可能性があります。金額PI・粗利PIの解釈に注意してください。"
                    )

    # ---- 粗利ファイル ----
    if margin_path:
        mspec = spec["margin_file"]
        # 週次で溜めるので複数ファイルを受ける。年週×店舗×階層が重なる行は
        # 後から入れたファイルを採用する（再エクスポートでの差し替えを想定）。
        mpaths = ([margin_path] if isinstance(margin_path, (str, Path))
                  else list(margin_path))
        mparts: list[pd.DataFrame] = []
        for mp in mpaths:
            part = _read_csv(mp, enc)
            part, mmiss = _rename(part, mspec["columns"], mp, "粗利ファイル")
            if mmiss:
                warnings.append(
                    f"粗利ファイル {Path(mp).name} に無い列（無視します）: {'、'.join(mmiss)}")
            part["_source"] = Path(mp).name
            mparts.append(part)
        mdf = pd.concat(mparts, ignore_index=True)
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

        if "gross_profit" not in mdf.columns:
            raise InputError(
                f"粗利ファイルに '{mspec['columns']['gross_profit']}' がありません。"
                "エクスポートの抽出項目に荒利高を含めてください。"
            )
        scale = float(mspec.get("gross_profit_scale", 1))
        mdf["gross_profit"] = _num(mdf["gross_profit"]) * scale

        # 上位階層の粗利率は単純平均ではなく売上で加重しないと正しくない。
        # そのための売上は、売上金額の列があればそれを使い、無ければ
        # 荒利高 ÷ 荒利率 で逆算する。率は丸めて出力されることが多いので、
        # 売上金額が取れるならそちらの方が誤差が小さい。
        has_rate = "gross_margin_rate" in mdf.columns
        has_amount = "sales_amount" in mdf.columns
        if not has_rate and not has_amount:
            raise InputError(
                "粗利ファイルに "
                f"'{mspec['columns'].get('gross_margin_rate')}' と "
                f"'{mspec['columns'].get('sales_amount')}' のどちらもありません。"
                "エクスポートの抽出項目に、荒利率か売上金額のどちらかを含めてください。"
            )
        if has_amount:
            amt_scale = float(mspec.get("sales_amount_scale", 1))
            mdf["sales_est"] = _num(mdf["sales_amount"]) * amt_scale
            if has_rate:
                # 単位取り違え（千円と円）を取り違えたまま進めないよう突き合わせる。
                chk = _num(mdf["gross_margin_rate"])
                with np.errstate(divide="ignore", invalid="ignore"):
                    implied = mdf["gross_profit"] / mdf["sales_est"].replace(0, np.nan)
                both = chk.notna() & implied.notna() & (chk != 0)
                if both.any():
                    ratio = float((implied[both] / chk[both]).median())
                    if not (0.5 < ratio < 2.0):
                        warnings.append(
                            f"荒利高と売上金額から計算した粗利率が、ファイルの荒利率の"
                            f"{ratio:.3g}倍になっています。単位（円／千円）の設定"
                            " idpos.margin_file.gross_profit_scale / sales_amount_scale"
                            " を確認してください。"
                        )
        else:
            mdf["gross_margin_rate"] = _num(mdf["gross_margin_rate"])
            with np.errstate(divide="ignore", invalid="ignore"):
                mdf["sales_est"] = (mdf["gross_profit"]
                                    / mdf["gross_margin_rate"].replace(0, np.nan))

        # 同じ行が複数ファイルに入っていたら、最後のものを残す。
        # キーはファイルの明細粒度（全階層）であって集計単位 unit ではない。
        # unit で重複排除すると、同じカテゴリーに属する別セグメントの行まで
        # 捨ててしまい、粗利高が過少になる。
        if len(mpaths) > 1:
            before = len(mdf)
            mdf = mdf.drop_duplicates(
                subset=["store_id", "year", "week"] + list(LEVELS), keep="last")
            if before != len(mdf):
                warnings.append(
                    f"粗利ファイルで重複していた{before - len(mdf):,}行を、"
                    "後から取り込んだファイルの値で上書きしました。"
                )
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
    metric_keys = ["pi"]
    if agg["gross_margin_rate"].notna().any():
        metric_keys.append("gross_margin_rate")

    # 客数（店全体）をパネルに付ける。別ファイルが渡されていればそちらを優先。
    if customers_path:
        cust = _load_customers(customers_path, spec, enc)
        agg = agg.merge(cust, on=["store_id", "year", "week"], how="left")
    elif not store_customers.empty:
        agg = agg.merge(store_customers, on=["store_id", "year", "week"], how="left")
    else:
        agg["customers"] = np.nan

    with np.errstate(divide="ignore", invalid="ignore"):
        # 数量PI = 買上率 × 1人当たり点数 × 1000（恒等式）
        agg["buy_rate"] = np.where(agg["customers"] > 0,
                                   agg["pos_customers"] / agg["customers"], np.nan)
        agg["units_per_buyer"] = np.where(agg["pos_customers"] > 0,
                                          agg["quantity"] / agg["pos_customers"], np.nan)
        agg["unit_price"] = np.where(agg["quantity"] > 0,
                                     agg["sales_amount"] / agg["quantity"], np.nan)
        agg["amount_pi"] = np.where(agg["customers"] > 0,
                                    agg["sales_amount"] / agg["customers"] * 1000, np.nan)
        agg["gp_pi"] = np.where(agg["customers"] > 0,
                                agg["gross_profit"] / agg["customers"] * 1000, np.nan)
    for k in ("buy_rate", "units_per_buyer", "unit_price", "amount_pi", "gp_pi"):
        if agg[k].notna().any():
            metric_keys.append(k)

    if "buy_rate" not in metric_keys:
        warnings.append(
            "売上数量・POS客数が無いため、PIの分解（買上率 × 1人当たり点数）と"
            "粗利PIは出せません。数量・客数つきのエクスポートを --idpos に渡してください。"
        )

    # 低温全体に占める構成比。客数も店舗規模も効かないので店舗間でそのまま比べられる。
    totals_df = agg.groupby(["store_id", "year", "week"], as_index=False).agg(
        _tot_sales=("sales_amount", "sum"), _tot_gp=("gross_profit", "sum")
    )
    agg = agg.merge(totals_df, on=["store_id", "year", "week"], how="left")
    for key, num, den in (("sales_share", "sales_amount", "_tot_sales"),
                          ("gp_share", "gross_profit", "_tot_gp")):
        if num in agg.columns and agg[num].notna().any():
            agg[key] = np.where(agg[den] > 0, agg[num] / agg[den], np.nan)
            if agg[key].notna().any():
                metric_keys.append(key)
    agg = agg.drop(columns=[c for c in ("_tot_sales", "_tot_gp") if c in agg.columns])

    # ---- 開店・改装直後の週を落とす ----
    # 派生指標まで計算し終えてから落とす。除外前のパネルは立ち上がりカーブの推定に使う。
    panel_all = agg.copy()
    skip_w = int(spec.get("exclude_opening_weeks", 0)
                 if exclude_opening_weeks is None else exclude_opening_weeks)
    maturity_rows = []
    if opening_dates:
        keep = pd.Series(True, index=agg.index)
        for sid, g in agg.groupby("store_id"):
            od = opening_dates.get(str(sid))
            od_ts, approx = parse_open_date(od)
            # 行数ではなく「週」の数で数える（1週に単位の数だけ行があるため）
            total = int(g["week_date"].nunique())
            if pd.isna(od_ts):
                maturity_rows.append({"store_id": sid, "open_date": None,
                                      "approx": False,
                                      "weeks_total": total, "weeks_excluded": 0,
                                      "weeks_used": total, "weeks_since_open": None})
                continue
            cutoff = od_ts + pd.Timedelta(weeks=skip_w)
            drop = g["week_date"].notna() & (g["week_date"] < cutoff)
            keep.loc[g.index[drop]] = False
            since = g["week_date"].max()
            n_dropped = int(g.loc[drop, "week_date"].nunique())
            maturity_rows.append({
                "store_id": sid, "open_date": od_ts.date().isoformat(),
                "approx": approx,
                "weeks_total": total, "weeks_excluded": n_dropped,
                "weeks_used": total - n_dropped,
                "weeks_since_open": (int((since - od_ts).days // 7)
                                     if pd.notna(since) else None),
            })
        agg = agg[keep]
        if skip_w:
            warnings.append(
                f"各店の開店・改装オープンから{skip_w}週間を水準の計算から外しました"
                "（開店効果で水準が高く出るため）。"
            )
    maturity = pd.DataFrame(maturity_rows)
    if not maturity.empty and "approx" in maturity.columns:
        ap = maturity[maturity["approx"]]
        if len(ap):
            warnings.append(
                f"{len(ap)}店は開店日が月単位（その月の1日として扱いました）。"
                "実際の開店日が月末寄りだと、除外する週と立ち上がり補正の週がずれ、"
                "その店の水準は最大で数%ずれます。日が分かり次第 open_date を直してください。"
            )
    if not maturity.empty:
        thin = maturity[maturity["weeks_used"] < int(spec.get("min_weeks", 8))]
        for _, r in thin.iterrows():
            warnings.append(
                f"店舗{r['store_id']}は開店効果を除いた週が{int(r['weeks_used'])}週しかありません"
                f"（オープン{r['open_date']}）。この店の水準は不安定で、"
                "類似店に選ばれた場合の予測も不安定になります。"
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
        panel=agg, panel_all=panel_all, age=age, stats=stats,
        seasonality=_seasonality(agg, metric_keys),
        units=units, unit_parents=unit_parents, metrics=metric_keys,
        store_ids=sorted(set(agg["store_id"])), store_names=store_names,
        level=level, age_bands=age_bands, age_unknown_share=unknown_share,
        maturity=maturity, warnings=warnings,
    )


def _drop_total_rows(df: pd.DataFrame, spec: dict, path, warnings: list[str]) -> pd.DataFrame:
    """小計・合計行を落とす。

    1本のファイルに年代の内訳と数量が同居する形式では、
    「年代=計」のような小計行が混ざっていると売上が2倍になる。
    """
    labels = {str(x).strip() for x in (spec.get("total_row_labels") or [])}
    if not labels or df.empty:
        return df
    mask = pd.Series(False, index=df.index)
    hit_cols: list[str] = []
    for col in ("customer_type", "age_band", "segment", "subcategory",
                "category", "department", "line"):
        if col not in df.columns:
            continue
        m = df[col].astype(str).str.strip().isin(labels)
        if m.any():
            mask |= m
            hit_cols.append(col)
    n = int(mask.sum())
    if n:
        warnings.append(
            f"{Path(path).name} から小計・合計とみられる行を{n:,}件除外しました"
            f"（{'、'.join(hit_cols)} が {'、'.join(sorted(labels & set(df[hit_cols].astype(str).stack().str.strip())))}）。"
            "明細と二重に数えないためです。意図した明細が消えていないか確認してください。"
        )
    return df[~mask]


def parse_open_date(value) -> tuple[pd.Timestamp, bool]:
    """開店日を読む。日が分からない場合は 'YYYY-MM' で渡してよい。

    その月の1日として扱い、概算であることを呼び出し側に返す。
    概算だと立ち上がり補正の週インデックスが最大±2週ずれるので、
    レポートにその旨を出す。
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return pd.NaT, False
    s = str(value).strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{1,2}", s):
        return pd.to_datetime(s + "-01", errors="coerce"), True
    if re.fullmatch(r"\d{6}", s):
        return pd.to_datetime(f"{s[:4]}-{s[4:]}-01", errors="coerce"), True
    return pd.to_datetime(s, errors="coerce"), False


def _load_customers(path: str | Path, spec: dict, enc: str) -> pd.DataFrame:
    cols = spec.get("customers_file", {}).get("columns", {})
    df = _read_csv(path, enc)
    df, _ = _rename(df, cols, path, "客数ファイル")
    df["store_id"] = df["store_code"].astype(str).str.strip()
    yw = df["year_week"].map(parse_year_week)
    df["year"] = [a for a, _ in yw]
    df["week"] = [b for _, b in yw]
    df["customers"] = _num(df["customers"])
    return (df.groupby(["store_id", "year", "week"], as_index=False)["customers"].sum())


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
