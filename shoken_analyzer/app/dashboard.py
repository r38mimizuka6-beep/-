"""新店 低温カテゴリー予測ダッシュボード（ローカル実行）。

    streamlit run app/dashboard.py

データはこのPCから出ない。蓄積データは warehouse/ に貯まり、
判定したい新店の商圏ファイルだけを画面からアップロードする。
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from theme import ACCENT, BAD, GOOD, GRID, INK, MUTED, accuracy_band, verdict_color  # noqa: E402

from shoken import agentloop, llm, weather  # noqa: E402
from shoken.config import Config  # noqa: E402
from shoken.dashboard_html import render_dashboard  # noqa: E402
from shoken.extract_xlsx import extract_store  # noqa: E402
from shoken.idpos import load_idpos  # noqa: E402
from shoken.io_loader import InputError  # noqa: E402
from shoken.metrics_config import MetricsConfig  # noqa: E402
from shoken.pipeline import run_analysis  # noqa: E402
from shoken.report import render_html, write_excel  # noqa: E402
from shoken.search_profile import SEARCH_COLUMNS, store_name_from_filename  # noqa: E402
from shoken.verify import save_predictions, verify_predictions  # noqa: E402
from shoken.search_book import extract_search_row  # noqa: E402
from shoken.warehouse import Warehouse  # noqa: E402

CFG = ROOT / "config" / "columns.yaml"
EXTRACT_MAP = ROOT / "config" / "extract_shoken_report.yaml"
MCFG = ROOT / "config" / "metrics.yaml"
SEARCH_MAP = ROOT / "config" / "extract_search_book.yaml"
WAREHOUSE = ROOT / "warehouse"
OUTPUT = ROOT / "output"
LEVELS = {"ライン": "line", "部門": "department", "カテゴリー": "category",
          "サブカテゴリー": "subcategory", "セグメント": "segment"}

st.set_page_config(page_title="新店 低温予測", layout="wide", page_icon="📦")


# ---------------------------------------------------------------- 共通

@st.cache_resource
def get_warehouse() -> Warehouse:
    return Warehouse(WAREHOUSE)


def axis_cfg(chart: alt.Chart) -> alt.Chart:
    return chart.configure_axis(
        grid=True, gridColor=GRID, gridWidth=1, domainColor=GRID,
        tickColor=GRID, labelColor=INK, titleColor=MUTED, labelFontSize=11,
        titleFontSize=11,
    ).configure_view(stroke=None).configure_legend(
        labelColor=INK, titleColor=MUTED, labelFontSize=11)


def note(msg: str, kind: str = "info") -> None:
    {"info": st.info, "warn": st.warning, "err": st.error,
     "ok": st.success}[kind](msg)


@st.cache_data(show_spinner=False)
def _codes_from(paths: tuple[str, ...], col: str) -> list[str]:
    out: set[str] = set()
    for p in paths:
        try:
            df = pd.read_csv(p, encoding="cp932", dtype=str, usecols=[col])
        except Exception:                                    # noqa: BLE001
            try:
                df = pd.read_csv(p, encoding="utf-8-sig", dtype=str, usecols=[col])
            except Exception:                                # noqa: BLE001
                continue
        out |= {v.strip() for v in df[col].dropna() if v.strip()}
    return sorted(out)


def _idpos_store_codes(wh: Warehouse) -> list[str]:
    """蓄積ずみIDPOSに実際に入っている店舗CD。マスタ側を合わせる先。"""
    mcfg = MetricsConfig.load(MCFG)
    col = mcfg.idpos["pi_file"]["columns"]["store_code"]
    return _codes_from(tuple(str(p) for p in wh.idpos_files()), col)


def merge_search_rows(wh: Warehouse, rows: pd.DataFrame) -> int:
    """検索データに行を足す。同じ店舗の行があれば新しい方で置き換える。"""
    cols = ["store_id", "store_name"] + [k for k, _, _ in SEARCH_COLUMNS]
    rows = rows.reindex(columns=cols).fillna("").astype(str)
    if wh.search_path.exists():
        existing = pd.read_csv(wh.search_path, dtype=str).fillna("")
        existing = existing.reindex(columns=cols).fillna("").astype(str)
    else:
        existing = pd.DataFrame(columns=cols)
    out = pd.concat([existing, rows], ignore_index=True)
    # 後から入れた行を残す。store_id が空なら店舗名で突き合わせる。
    out["_key"] = out.apply(
        lambda r: r["store_id"].strip() or r["store_name"].strip(), axis=1)
    out = out[out["_key"] != ""].drop_duplicates(subset="_key", keep="last")
    out = out.drop(columns="_key")
    wh.put_text("search", "search_profile.csv",
                out.to_csv(index=False).encode("utf-8-sig"))
    return len(out)


def crash(what: str, e: Exception) -> None:
    """想定外の例外を、画面を壊さずに出す。

    InputError は原因が特定できているのでそのまま見せる。それ以外は
    何をしていて落ちたかを先に書き、技術的な内容は畳んでおく。
    貼り付けて相談できるよう、全文は残す。
    """
    import traceback
    if isinstance(e, InputError):
        note(str(e), "err")
        return
    note(f"{what}の途中でエラーになりました。入力ファイルの列名や中身が"
         "想定と違う可能性があります。下の詳細をコピーして共有してください。", "err")
    with st.expander("詳細（コピーして共有してください）"):
        st.code("".join(traceback.format_exception(type(e), e, e.__traceback__)))


# ---------------------------------------------------------------- 1. 蓄積

def tab_warehouse(wh: Warehouse) -> None:
    st.subheader("蓄積データ")
    st.caption("既存店の実績をここに貯めます。判定したい新店のファイルは次のタブで"
               "アップロードしてください（蓄積とは分けています）。")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**週次IDPOS**（年代・数量・客数が入った1本）")
        files = st.file_uploader("IDPOS", type=["csv", "gz"],
                                 accept_multiple_files=True, key="u_idpos",
                                 label_visibility="collapsed")
        if files and st.button("IDPOSを蓄積する", type="primary"):
            for f in files:
                r = wh.ingest("idpos", f.name, f.getvalue())
                note(f"{f.name}: {r.message}",
                     {"ok": "ok", "duplicate": "warn", "error": "err"}[r.status])
            st.cache_resource.clear()
    with c2:
        st.markdown("**週次 売上在庫（粗利）**")
        mfiles = st.file_uploader("粗利", type=["csv", "gz"],
                                  accept_multiple_files=True, key="u_margin",
                                  label_visibility="collapsed")
        if mfiles and st.button("粗利ファイルを蓄積する", type="primary"):
            for f in mfiles:
                r = wh.ingest("margin", f.name, f.getvalue())
                note(f"{f.name}: {r.message}",
                     {"ok": "ok", "duplicate": "warn", "error": "err"}[r.status])
            st.cache_resource.clear()

    st.divider()
    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**既存店の商圏マスタ**（store_id は店舗CDと一致させる）")
        m = st.file_uploader("マスタ", type=["csv", "xlsx"], key="u_master",
                             label_visibility="collapsed")
        if m and st.button("マスタを置き換える"):
            wh.put_text("master", m.name, m.getvalue())
            note("商圏マスタを更新しました（以前の版は .bak で残しています）。", "ok")
    with c4:
        st.markdown("**検索データ**（競合・周辺施設・アクセス。任意）")
        st.caption("1店舗=1ブックのExcel（複数可）か、全店まとめた記入済みCSV。")
        sfiles = st.file_uploader("検索データ", type=["csv", "xlsx", "xlsm"],
                                  accept_multiple_files=True, key="u_search",
                                  label_visibility="collapsed")
        if sfiles:
            books = [f for f in sfiles if f.name.lower().endswith((".xlsx", ".xlsm"))]
            csvs = [f for f in sfiles if f.name.lower().endswith(".csv")]
            if csvs and st.button("記入済みCSVで置き換える"):
                wh.put_text("search", "search_profile.csv", csvs[0].getvalue())
                note("検索データを置き換えました（以前の版は .bak で残しています）。", "ok")
            if books:
                _search_books_ui(wh, books)

    st.divider()
    st.markdown("### 商圏レポートExcelから商圏マスタを作る")
    st.caption("調査会社の商圏レポート（1店舗=1ブック、ファイル名が店舗名）を入れると、"
               "商圏の人に関する項目を自動で取り出します。"
               "競合・周辺施設・来店手段は入らないので、あとで⑤タブか手入力で足してください。")
    books = st.file_uploader("商圏レポート（複数可）", type=["xlsx", "xlsm"],
                             accept_multiple_files=True, key="u_books",
                             label_visibility="collapsed")
    if books and st.button("Excelから項目を取り出す"):
        tmpdir = OUTPUT / "_books"
        tmpdir.mkdir(parents=True, exist_ok=True)
        rows, errs, book_notes = [], [], []
        for f in books:
            dest = tmpdir / Path(f.name).name
            dest.write_bytes(f.getvalue())
            n: list[str] = []
            try:
                rows.append(extract_store(dest, EXTRACT_MAP, notes=n))
            except Exception as e:                            # noqa: BLE001
                errs.append(f"{f.name}: {e}")
                continue
            book_notes += [f"{f.name}: {x}" for x in n]
        for e in errs:
            note(e, "err")
        if rows:
            df = pd.DataFrame(rows)
            st.session_state["extracted_master"] = df
            st.session_state["extracted_notes"] = book_notes
            filled = [c for c in df.columns if df[c].notna().any()]
            note(f"{len(df)}店舗を読み取りました（値が入った列 {len(filled)} / "
                 f"{len(df.columns)}）。", "ok")

    ex = st.session_state.get("extracted_master")
    if ex is not None:
        bn = st.session_state.get("extracted_notes") or []
        if bn:
            note("**ブックの作りが店舗ごとに違う箇所がありました。**"
                 "該当する項目だけ空にして、残りは読み取っています。\n\n"
                 + "\n".join(f"- {x}" for x in bn), "warn")

        # どの店が薄いかが分かるように、店舗ごとの充足度を出す。
        value_cols = [c for c in ex.columns
                      if c not in ("store_id", "store_name", "trade_area_def")]
        fill = pd.DataFrame({
            "store_id": ex.get("store_id", pd.Series(dtype=str)).astype(str),
            "store_name": ex.get("store_name", pd.Series(dtype=str)).astype(str),
            "値が入った項目": ex[value_cols].notna().sum(axis=1).astype(int),
            "全項目": len(value_cols),
        })
        st.caption("店舗ごとの充足度（少ない店はブックの作りが違います）")
        st.dataframe(fill, use_container_width=True, hide_index=True)

        known = _idpos_store_codes(wh)
        if known:
            st.caption("蓄積ずみIDPOSに入っている店舗CDです。"
                       "**store_id をこの中のどれかに書き換えてください。**")
            st.code("  ".join(known))
        else:
            st.caption("先に週次IDPOSを取り込むと、使える店舗CDの一覧がここに出ます。")
        st.caption("**store_id を IDPOS の店舗CD と一致させてから**登録してください"
                   "（店舗名ではなくコード。先頭ゼロも含めて）。open_date も忘れずに。")
        edited = st.data_editor(ex.astype(str), use_container_width=True,
                                num_rows="dynamic", key="edit_master")

        ids = [str(v).strip() for v in edited.get("store_id", pd.Series(dtype=str))]
        matched = [i for i in ids if i in known] if known else []
        if known:
            if not matched:
                note("**いまの store_id は1つもIDPOSと一致しません。**"
                     "このまま登録すると、レポートの数字が全て空になります。"
                     f"上の一覧（{', '.join(known[:8])}…）から選んで書き換えてください。", "err")
            elif len(matched) < len(ids):
                note(f"{len(matched)}/{len(ids)}店だけIDPOSと一致しています。"
                     f"一致しない store_id: {[i for i in ids if i not in known]}", "warn")
            else:
                note(f"{len(matched)}店すべてIDPOSと一致しています。", "ok")

        c5, c6 = st.columns(2)
        c5.download_button("CSVとしてダウンロード",
                           edited.to_csv(index=False).encode("utf-8-sig"),
                           file_name="store_master.csv", use_container_width=True)
        if c6.button("既存店の商圏マスタとして登録する", use_container_width=True):
            wh.put_text("master", "store_master.csv",
                        edited.to_csv(index=False).encode("utf-8-sig"))
            note("商圏マスタを登録しました。", "ok")

    st.divider()
    st.markdown("### 入っているもの")
    cov = wh.coverage()
    if cov.frame.empty:
        note("まだ何も入っていません。上から取り込んでください。")
        return
    st.caption(f"{len(cov.stores)}店舗 × {len(cov.weeks)}週")
    long = (cov.frame.reset_index().melt(id_vars="store_id", var_name="year_week",
                                         value_name="有無"))
    long["状態"] = long["有無"].map({1: "あり", 0: "なし"})
    chart = alt.Chart(long).mark_rect(stroke="white", strokeWidth=1).encode(
        x=alt.X("year_week:O", title="年週", axis=alt.Axis(labelAngle=-90)),
        y=alt.Y("store_id:N", title="店舗CD"),
        color=alt.Color("状態:N",
                        scale=alt.Scale(domain=["あり", "なし"],
                                        range=[ACCENT, "#eef1f4"]),
                        legend=alt.Legend(title="データ")),
        tooltip=["store_id", "year_week", "状態"],
    ).properties(height=28 * max(len(cov.stores), 1) + 40)
    st.altair_chart(axis_cfg(chart), use_container_width=True)
    with st.expander("表で見る"):
        st.dataframe(cov.frame, use_container_width=True)
    for g in cov.gaps:
        note(g, "warn")


# ---------------------------------------------------------------- 2. 判定

def _search_books_ui(wh: Warehouse, books) -> None:
    """検索データExcel（1店舗=1ブック）を読み取り、確認してから登録する。"""
    rows, notes, radius = [], [], []
    # ファイル名の先頭にある店舗CDを読むので、名前を変えずに置く。
    tmpdir = OUTPUT / "_search_books"
    tmpdir.mkdir(parents=True, exist_ok=True)
    for f in books:
        tmp = tmpdir / Path(f.name).name
        tmp.write_bytes(f.getvalue())
        try:
            got = extract_search_row(tmp, SEARCH_MAP)
        except Exception as e:                               # noqa: BLE001
            crash(f"{f.name} の読み取り", e)
            continue
        row = got.row
        row.setdefault("store_name", store_name_from_filename(f.name))
        rows.append(row)
        notes += [f"{f.name}: {n}" for n in got.notes]
        radius += [f"{f.name}: {r}" for r in got.radius_notes]

    if not rows:
        return

    if radius:
        note("**件数を数えた範囲**（既存店と新店で揃っていないと比較が壊れます）\n\n"
             + "\n".join(f"- {r}" for r in radius), "warn")
    for n in notes:
        st.caption(f"・{n}")

    cols = ["store_id", "store_name"] + [k for k, _, _ in SEARCH_COLUMNS]
    frame = pd.DataFrame(rows).reindex(columns=cols).fillna("").astype(str)

    known = _idpos_store_codes(wh)
    if known:
        st.caption("蓄積ずみIDPOSの店舗CD: " + "  ".join(known))
    st.caption("**store_id が IDPOS の店舗CD と一致している必要があります。**"
               "ファイル名の先頭に店舗CDが付いていれば自動で入ります"
               "（例 `0785_花小金井.xlsx`）。"
               "**オープン日は open_date 列に `2024-03-15` の形で入れてください。**")
    edited = st.data_editor(frame, use_container_width=True, hide_index=True,
                            key="edit_search_books")

    ids = [str(v).strip() for v in edited.get("store_id", pd.Series(dtype=str))]
    if known:
        bad = [i for i in ids if i and i not in known]
        blank = sum(1 for i in ids if not i)
        if blank:
            note(f"{blank}件の store_id が空です。ファイル名の先頭に店舗CDを付けるか、"
                 "表に直接入力してください。", "warn")
        if bad:
            note(f"IDPOSに無い store_id: {bad}。"
                 "この行は既存店と結びつかず、使われません。", "warn")
        if ids and not bad and not blank:
            note(f"{len(ids)}件すべてIDPOSの店舗CDと一致しています。", "ok")

    if st.button("検索データとして登録する", type="primary"):
        n = merge_search_rows(wh, edited)
        note(f"登録しました。検索データは{n}店舗分になりました。", "ok")


def tab_predict(wh: Warehouse) -> None:
    st.subheader("新店を判定する")
    st.caption("判定したい新店の商圏情報（1行のCSV/Excel）をアップロードして、"
               "下の「レポートを出力」を押してください。")

    if not wh.master_path.exists():
        note("先に「蓄積データ」タブで既存店の商圏マスタを登録してください。", "warn")
        return
    if not wh.idpos_files():
        note("先に「蓄積データ」タブで週次IDPOSを取り込んでください。", "warn")
        return

    kind = st.radio("新店の商圏情報の入れ方", ["商圏レポートExcel（調査会社のブック）",
                                          "記入済みのCSV / Excel（1行）"],
                    horizontal=True, key="new_kind")
    up = st.file_uploader("新店の商圏情報", type=["csv", "xlsx", "xlsm"], key="u_new")
    c1, c2, c3 = st.columns(3)
    level_label = c1.selectbox("分析粒度", list(LEVELS), index=2)
    top_n = c2.number_input("類似店の数", 1, 4, 2)
    use_weather = c3.checkbox("気温を使う（外部接続）", value=False)

    with st.expander("軸の重み（既定は等重み。5店では変えても結果はあまり動きません）"):
        cfg = Config.load(CFG)
        weights = {}
        cols = st.columns(len(cfg.axes))
        for col, (ax, spec) in zip(cols, cfg.axes.items()):
            weights[ax] = col.slider(spec["label"].split("（")[0], 0.0, 3.0, 1.0, 0.25)

    if up is None:
        return
    newdir = OUTPUT / "_new"
    newdir.mkdir(parents=True, exist_ok=True)
    tmp = newdir / Path(up.name).name
    tmp.write_bytes(up.getvalue())

    if kind.startswith("商圏レポート"):
        try:
            row = extract_store(tmp, EXTRACT_MAP)
        except Exception as e:                                # noqa: BLE001
            note(f"商圏レポートとして読めませんでした: {e}\n"
                 "記入済みのCSVなら、上の選択を切り替えてください。", "err")
            return
        st.caption("Excelから取り出した内容です。**store_id を店舗CDに、"
                   "open_date をオープン予定日に**直してから出力してください。")
        row.setdefault("store_id", "NEW01")
        edited = st.data_editor(pd.DataFrame([row]).astype(str),
                                use_container_width=True, key="edit_new")
        tmp = newdir / "_new_from_book.csv"
        tmp.write_bytes(edited.to_csv(index=False).encode("utf-8-sig"))

    if not st.button("レポートを出力", type="primary", use_container_width=True):
        return

    with st.spinner("計算しています…"):
        try:
            a = run_analysis(
                config_path=CFG, metrics_path=MCFG,
                master_path=wh.master_path, new_store_path=tmp,
                idpos_path=wh.idpos_files(),
                margin_path=wh.margin_files() or None,
                search_path=wh.search_path if wh.search_path.exists() else None,
                level=LEVELS[level_label], weight_overrides=weights,
                top_n=int(top_n), level_scan=False,
            )
        except Exception as e:                               # noqa: BLE001
            crash("レポートの出力", e)
            return

    st.session_state["analysis"] = a
    _render_result(wh, a, use_weather)


def _render_result(wh: Warehouse, a, use_weather: bool) -> None:
    note(f"類似店: {'・'.join(p.store_name for p in a.peers)}　／　"
         f"立地: {a.urban_new.verdict}", "ok")

    st.markdown("### 予測（根拠と精度つき）")
    by_unit = a.predictions_by_unit()
    rows = []
    for unit, mets in by_unit.items():
        pi = mets.get("pi")
        if pi is None:
            continue
        colr, band = accuracy_band(pi.loo_mape)
        rows.append({
            "ライン": a.idpos.parent_of(unit, "line"),
            a.idpos.level_label(): a.idpos.leaf(unit),
            "数量PI": pi.point,
            "区間": f"{pi.low:,.1f}〜{pi.high:,.1f}" if pi.low is not None else "—",
            "粗利率": (mets["gross_margin_rate"].point
                     if "gross_margin_rate" in mets else None),
            "粗利PI": mets["gp_pi"].point if "gp_pi" in mets else None,
            "買上率": mets["buy_rate"].point if "buy_rate" in mets else None,
            "1人当点数": (mets["units_per_buyer"].point
                       if "units_per_buyer" in mets else None),
            "実測誤差%": pi.loo_mape,
            "精度の判定": band,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={
                     "数量PI": st.column_config.NumberColumn(format="%.2f"),
                     "粗利率": st.column_config.NumberColumn(format="%.3f"),
                     "粗利PI": st.column_config.NumberColumn(format="%.2f"),
                     "買上率": st.column_config.NumberColumn(format="%.4f"),
                     "1人当点数": st.column_config.NumberColumn(format="%.2f"),
                     "実測誤差%": st.column_config.NumberColumn(format="%.1f"),
                 })

    st.markdown("#### どれくらい当たるか（1店抜き検証の実測誤差）")
    st.caption("既存店を1店ずつ『新店だと思って』残りから予測し、実際に外れた幅です。"
               "色は判定の文字と同じ意味を表しています。")
    acc = (df.dropna(subset=["実測誤差%"])
           .sort_values("実測誤差%", ascending=False).head(15))
    if not acc.empty:
        acc = acc.assign(色=[accuracy_band(v)[0] for v in acc["実測誤差%"]])
        bars = alt.Chart(acc).mark_bar(cornerRadiusEnd=4, height=14).encode(
            y=alt.Y(f"{a.idpos.level_label()}:N", sort="-x", title=None),
            x=alt.X("実測誤差%:Q", title="1店抜き検証の平均誤差（%）"),
            color=alt.Color("色:N", scale=None, legend=None),
            tooltip=[a.idpos.level_label(), "実測誤差%", "精度の判定"],
        )
        labels = bars.mark_text(align="left", dx=4, color=INK, fontSize=11).encode(
            text=alt.Text("実測誤差%:Q", format=".0f"))
        st.altair_chart(axis_cfg(bars + labels), use_container_width=True)

    if a.ramp is not None and a.ramp.curves.get("pi") and a.ramp.curves["pi"].usable:
        rc = a.ramp.curves["pi"]
        st.markdown("#### 開店からの立ち上がり")
        st.caption(f"成熟期に届いた{rc.n_contributors}店"
                   f"（{'・'.join(rc.contributors)}）から測りました。"
                   "1.00が成熟水準です。")
        cur = pd.DataFrame({"開店からの週": sorted(rc.factors),
                            "成熟水準比": [rc.factors[w] for w in sorted(rc.factors)]})
        line = alt.Chart(cur).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(
            size=60, filled=True), color=ACCENT).encode(
            x=alt.X("開店からの週:Q", title="開店からの週"),
            y=alt.Y("成熟水準比:Q", scale=alt.Scale(zero=False),
                    title="成熟水準に対する倍率"),
            tooltip=["開店からの週", alt.Tooltip("成熟水準比:Q", format=".2f")])
        base = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(
            color=MUTED, strokeDash=[4, 4]).encode(y="y:Q")
        st.altair_chart(axis_cfg(line + base), use_container_width=True)

    st.markdown("### レポート")
    html = render_html(a)
    c1, c2 = st.columns(2)
    c1.download_button("HTMLレポートをダウンロード", html,
                       file_name=f"report_{a.new_id}.html", mime="text/html",
                       use_container_width=True)
    xlsx = OUTPUT / f"report_{a.new_id}.xlsx"
    write_excel(a, xlsx)
    c2.download_button("Excelレポートをダウンロード", xlsx.read_bytes(),
                       file_name=xlsx.name, use_container_width=True,
                       mime="application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet")
    dash = render_dashboard(a, coverage=wh.coverage(),
                            verify=st.session_state.get("verify"))
    st.download_button("このダッシュボードをHTMLで書き出す（配布用・1枚完結）",
                       dash, file_name=f"dashboard_{a.new_id}.html",
                       mime="text/html", use_container_width=True)
    st.caption("Python不要・オフラインで開けます。タブ・並べ替え・ツールチップは"
               "そのまま使えますが、アップロードして再計算することはできません。"
               "実績由来の数値が入るので社外に出さないでください。")
    with st.expander("レポートをこの画面で見る"):
        st.components.v1.html(html, height=800, scrolling=True)

    pred_path = wh.root / "predictions" / f"{a.new_id}.json"
    save_predictions(a.predictions, store_name=a.new_name, store_id=a.new_id,
                     path=pred_path, axis_weights=a.similarity.weights,
                     extra={"level": a.idpos.level})
    agentloop.record_prediction(wh, a.new_id, a.new_name, a.idpos.level,
                                len(a.idpos.units), a.loo.mape)
    note(f"予測を保存しました（{pred_path.name}）。"
         "開店後に「予実検証」タブで突き合わせます。", "ok")

    for w in a.warnings:
        note(w, "warn")

    if use_weather:
        _weather_block(wh, a)


def _weather_block(wh: Warehouse, a) -> None:
    st.markdown("### 気温（外部接続）")
    chk = weather.check_connection()
    if not chk.ok:
        note(f"{chk.message}　気温を使わずに続けます。"
             "社内ネットワークから外部に出られない場合は、"
             "気温CSV（store_id, year_week, temp_mean）を蓄積タブから入れてください。",
             "warn")
        return
    note(chk.message, "ok")
    place = st.text_input("新店の所在地（市区町村まで）", value="")
    if place and st.button("気温を取得"):
        ll = weather.geocode(place)
        if ll is None:
            note("緯度経度を特定できませんでした。市区町村名で試してください。", "warn")
            return
        import datetime as dt
        end = dt.date.today() + dt.timedelta(days=14)
        res = weather.fetch_daily(ll[0], ll[1], end - dt.timedelta(days=400), end)
        if not res.ok:
            note(res.message, "warn")
            return
        wk = weather.to_weekly(res.daily, a.new_id)
        note(f"{res.message}（{res.source}）", "ok")
        st.dataframe(wk.tail(12), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------- 3. 予実検証

def tab_verify(wh: Warehouse) -> None:
    st.subheader("予実検証（開店後）")
    preds = wh.prediction_files()
    if not preds:
        note("まだ保存された予測がありません。先に「新店を判定する」で出力してください。",
             "warn")
        return
    pick = st.selectbox("どの予測と突き合わせるか", [p.name for p in preds])
    pred_path = wh.root / "predictions" / pick

    st.markdown("**新店の週次IDPOS（開店後）**")
    f_idpos = st.file_uploader("新店IDPOS", type=["csv", "gz"], key="v_idpos",
                               accept_multiple_files=True,
                               label_visibility="collapsed")
    f_margin = st.file_uploader("新店の売上在庫（任意）", type=["csv", "gz"],
                                key="v_margin")
    if not f_idpos or not st.button("突き合わせる", type="primary"):
        return

    mcfg = MetricsConfig.load(MCFG)
    tmpdir = OUTPUT / "_verify"
    tmpdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in f_idpos:
        p = tmpdir / f.name
        p.write_bytes(f.getvalue())
        paths.append(p)
    mpath = None
    if f_margin:
        mpath = tmpdir / f_margin.name
        mpath.write_bytes(f_margin.getvalue())

    import json
    level = json.loads(pred_path.read_text(encoding="utf-8")).get(
        "extra", {}).get("level", "category")
    with st.spinner("突き合わせています…"):
        try:
            actual = load_idpos(paths, mpath, mcfg, level=level)
            ref = load_idpos(wh.idpos_files(),
                             wh.margin_files() or None,
                             mcfg, level=level)
            v = verify_predictions(pred_path, actual, mcfg, ref)
        except Exception as e:                               # noqa: BLE001
            crash("予実の突き合わせ", e)
            return

    cols = st.columns(len(v.summary) or 1)
    for col, (k, n) in zip(cols, v.summary.items()):
        col.metric(k, n)

    rows = pd.DataFrame([{
        "ライン": r.unit.split(" > ")[0], "単位": r.unit.split(" > ")[-1],
        "指標": r.metric_label, "予測": r.predicted,
        "実績(季節補正後)": r.actual_adjusted, "差%": r.diff_pct,
        "判定": r.verdict, "コメント": r.comment,
    } for r in v.rows])
    st.dataframe(rows, use_container_width=True, hide_index=True,
                 column_config={"差%": st.column_config.NumberColumn(format="%+.1f")})

    agentloop.record_outcome(wh, v)
    st.session_state["verify"] = v

    hit = rows[rows["判定"].isin(["下振れ", "上振れ"])]
    if not hit.empty:
        st.markdown("#### 週次のブレでは説明できなかったもの")
        hit = hit.assign(色=[verdict_color(x) for x in hit["判定"]])
        ch = alt.Chart(hit).mark_bar(cornerRadiusEnd=4, height=14).encode(
            y=alt.Y("単位:N", sort="-x", title=None),
            x=alt.X("差%:Q", title="予測との差（%）"),
            color=alt.Color("色:N", scale=None, legend=None),
            tooltip=["単位", "指標", "判定", "差%"])
        st.altair_chart(axis_cfg(ch), use_container_width=True)

    for w in v.warnings:
        note(w, "warn")


# ---------------------------------------------------------------- 4. 運用

def tab_loop(wh: Warehouse) -> None:
    st.subheader("運用の状態")
    st.caption("予測と実績を突き合わせるたびに、自動で直すものと、"
               "人が決めるものを分けて出します。")

    a = st.session_state.get("analysis")
    v = st.session_state.get("verify")
    ramp_summary = None
    if a is not None and a.ramp is not None and a.ramp.curves.get("pi"):
        rc = a.ramp.curves["pi"]
        ramp_summary = {"n_contributors": rc.n_contributors,
                        "factors": {str(k): round(x, 4)
                                    for k, x in rc.factors.items()}}
    res = agentloop.run_loop(
        wh, verify=v, loo_mape=(a.loo.mape if a else None),
        ramp_summary=ramp_summary, temp_sensitivity=None,
        weeks_since_open=(int(a.idpos.maturity["weeks_since_open"].max())
                          if a is not None and not a.idpos.maturity.empty
                          and a.idpos.maturity["weeks_since_open"].notna().any()
                          else None),
        store_name=(a.new_name if a else ""),
    )

    st.markdown("### 自動で更新したもの")
    if res.updates:
        st.dataframe(pd.DataFrame([{
            "項目": u.label, "前": u.before, "後": u.after, "理由": u.reason}
            for u in res.updates]), use_container_width=True, hide_index=True)
    else:
        st.caption("今回の更新はありません。")

    st.markdown("### 人が決めること")
    st.caption("5店舗では、これらを自動で決めると過学習します。")
    for p in res.proposals:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{p.label}**　`{p.decided}`")
            c1.write(p.detail)
            c1.caption(f"なぜ: {p.why}")
            c1.caption(f"リスク: {p.risk}")
            if c2.button("採用", key=f"ok_{p.key}"):
                agentloop.decide(wh, p.key, "accepted")
                st.rerun()
            if c2.button("見送り", key=f"ng_{p.key}"):
                agentloop.decide(wh, p.key, "rejected")
                st.rerun()

    st.markdown("### 精度の履歴")
    trend = agentloop.accuracy_trend(wh)
    if trend.empty:
        st.caption("まだ検証履歴がありません。")
    else:
        st.dataframe(trend, use_container_width=True, hide_index=True)
        t = trend.dropna(subset=["平均絶対誤差%"])
        if len(t) >= 2:
            ch = alt.Chart(t.reset_index()).mark_line(
                strokeWidth=2, point=alt.OverlayMarkDef(size=60, filled=True),
                color=ACCENT).encode(
                x=alt.X("index:O", title="検証した回"),
                y=alt.Y("平均絶対誤差%:Q", scale=alt.Scale(zero=False),
                        title="平均絶対誤差（%）"),
                tooltip=["日時", "店舗", "実績週数", "平均絶対誤差%"])
            st.altair_chart(axis_cfg(ch), use_container_width=True)

    st.markdown("### 所見の文章化（任意）")
    ok, msg = llm.available()
    if not ok:
        st.caption(msg)
    elif v is None:
        st.caption("先に予実検証を実行してください。")
    elif st.button("所見を書かせる"):
        payload = {"store": v.store_name, "weeks": v.weeks_observed,
                   "summary": v.summary,
                   "rows": [{"unit": r.unit, "metric": r.metric_label,
                             "verdict": r.verdict, "diff_pct": r.diff_pct}
                            for r in v.rows if r.verdict in ("下振れ", "上振れ")][:30]}
        with st.spinner("書いています…"):
            out = llm.review_verification(payload)
        if out.ok:
            st.write(out.text)
            st.caption("※ 文章化のみ。数値はすべて上の計算結果です。")
        else:
            note(out.error, "warn")


# ---------------------------------------------------------------- 5. 検索

def tab_search(wh: Warehouse) -> None:
    st.subheader("商圏の検索データ")
    st.caption("店舗名で検索して、競合・周辺施設・アクセスを埋めます。"
               "空欄のままでも分析は動きます（その項目が類似度から外れるだけ）。")

    st.markdown("### 検索データExcelを読み込む")
    st.caption("1店舗=1ブック。商圏サマリー・競合店一覧・周辺施設一覧の3シート構成を想定しています。")
    books = st.file_uploader("検索データExcel（複数可）", type=["xlsx", "xlsm"],
                             accept_multiple_files=True, key="u_search_book")
    if books:
        _search_books_ui(wh, books)

    st.divider()
    st.markdown("### 手で調べて1行ずつ入れる")
    name = st.text_input("店舗名（商圏レポートのファイル名でも可）")
    if not name:
        return
    store = store_name_from_filename(name)
    st.markdown(f"**抽出した店舗名: {store}**")
    queries = [f"{store} 住所", f"{store} 営業時間 駐車場", f"{store} 売場面積",
               f"{store} 最寄駅"]
    st.markdown("そのまま検索に貼れるクエリ:")
    st.code("\n".join(queries))
    st.caption("地図で店舗を中心に『スーパー』『コンビニ』『ドラッグストア』"
               "『小学校』『保育園』を半径を揃えて検索し、件数を数えてください。")

    pasted = st.text_area("検索結果を貼り付ける", height=200)
    ok, msg = llm.available()
    if not ok:
        st.caption(f"{msg} — 貼り付けから自動で項目を拾う機能は使えません。"
                   "下の表に手で入れてください。")
    elif pasted and st.button("貼り付けから項目を拾う"):
        with st.spinner("読み取っています…"):
            out = llm.extract_search_fields(store, pasted)
        if out.ok and out.data:
            st.session_state["extracted"] = out.data
            note("拾えた項目を下に入れました。必ず目で確認してください。", "ok")
        else:
            note(out.error or "拾えませんでした", "warn")

    base = st.session_state.get("extracted", {})
    cols = ["store_id", "store_name"] + [k for k, _, _ in SEARCH_COLUMNS]
    row = {c: str(base.get(c) or "") for c in cols}
    row["store_name"] = store
    edited = st.data_editor(pd.DataFrame([row]), use_container_width=True,
                            hide_index=True, key="search_editor")
    if st.button("この行を検索データに追記する"):
        n = merge_search_rows(wh, edited)
        note(f"追記しました。検索データは{n}店舗分になりました。", "ok")


# ---------------------------------------------------------------- main

def main() -> None:
    wh = get_warehouse()
    st.title("新店 低温カテゴリー予測")
    st.caption("データはこのPCから出ません。すべてローカルで計算しています。")

    t1, t2, t3, t4, t5 = st.tabs(
        ["① 蓄積データ", "② 新店を判定", "③ 予実検証", "④ 運用の状態", "⑤ 検索データ"])
    with t1:
        tab_warehouse(wh)
    with t2:
        tab_predict(wh)
    with t3:
        tab_verify(wh)
    with t4:
        tab_loop(wh)
    with t5:
        tab_search(wh)


main()
