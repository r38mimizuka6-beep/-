"""ダッシュボードを1枚のHTMLに書き出す。

Streamlitを起動できない人にも配れるよう、自己完結のHTMLにする。
  ・外部CDNを読まない（社内ネットワークやオフラインでも開ける）
  ・グラフはインラインSVG。JavaScriptはタブ切替・並べ替え・ツールチップだけ
  ・計算はしない。アップロードして再計算する用途はダッシュボード本体（Streamlit）で

中身は実績由来の数値なので、このHTML自体が社外秘になる点に注意。
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

GOOD, WARN, BAD, ACCENT = "#15803d", "#ca8a04", "#b91c1c", "#1d4ed8"
INK, MUTED, GRID, SURFACE = "#1b1f23", "#6b7280", "#e3e6ea", "#ffffff"


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _fmt(v, digits=2):
    if v is None or (isinstance(v, float) and (pd.isna(v) or not np.isfinite(v))):
        return "—"
    return f"{v:,.{digits}f}"


def accuracy_band(mape: float | None) -> tuple[str, str]:
    if mape is None:
        return MUTED, "未測定"
    if mape < 8:
        return GOOD, "当たる"
    if mape < 15:
        return WARN, "幅をもって見る"
    return BAD, "当たらない"


# ---------------------------------------------------------------- SVG 部品

def bar_chart(rows: list[dict], *, width=720, row_h=26, x_label="", unit="%") -> str:
    """横棒。rows: [{label, value, color, tip}]。値は直接ラベルで出す。"""
    if not rows:
        return '<p class="muted">データがありません。</p>'
    pad_l, pad_r, pad_t, pad_b = 170, 60, 8, 28
    h = pad_t + pad_b + row_h * len(rows)
    vmax = max(abs(r["value"]) for r in rows) or 1
    scale = (width - pad_l - pad_r) / vmax
    ticks = [0, vmax / 2, vmax]

    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" height="{h}" '
             f'role="img" class="chart">']
    for t in ticks:
        x = pad_l + t * scale
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" '
                     f'y2="{h - pad_b}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - pad_b + 16}" fill="{MUTED}" '
                     f'font-size="11" text-anchor="middle">{t:,.0f}</text>')
    for i, r in enumerate(rows):
        y = pad_t + i * row_h + 5
        w = max(abs(r["value"]) * scale, 2)
        parts.append(
            f'<rect class="mark" x="{pad_l}" y="{y}" width="{w:.1f}" height="14" '
            f'rx="4" ry="4" fill="{r["color"]}" data-tip="{esc(r["tip"])}"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 11}" fill="{INK}" '
                     f'font-size="12" text-anchor="end">{esc(r["label"])}</text>')
        parts.append(f'<text x="{pad_l + w + 6:.1f}" y="{y + 11}" fill="{INK}" '
                     f'font-size="11">{r["value"]:,.1f}{unit}</text>')
    if x_label:
        parts.append(f'<text x="{(pad_l + width - pad_r) / 2:.0f}" y="{h - 4}" '
                     f'fill="{MUTED}" font-size="11" text-anchor="middle">'
                     f'{esc(x_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def diverging_bar(rows: list[dict], *, width=720, row_h=26, x_label="") -> str:
    """0を挟む横棒。上振れと下振れを色で分け、中央は灰色の基準線。"""
    if not rows:
        return '<p class="muted">データがありません。</p>'
    pad_l, pad_r, pad_t, pad_b = 170, 60, 8, 28
    h = pad_t + pad_b + row_h * len(rows)
    vmax = max(abs(r["value"]) for r in rows) or 1
    half = (width - pad_l - pad_r) / 2
    zero = pad_l + half
    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" height="{h}" class="chart">']
    parts.append(f'<line x1="{zero}" y1="{pad_t}" x2="{zero}" y2="{h - pad_b}" '
                 f'stroke="{MUTED}" stroke-width="1"/>')
    for i, r in enumerate(rows):
        y = pad_t + i * row_h + 5
        w = max(abs(r["value"]) / vmax * half, 2)
        x = zero if r["value"] >= 0 else zero - w
        parts.append(
            f'<rect class="mark" x="{x:.1f}" y="{y}" width="{w:.1f}" height="14" '
            f'rx="4" ry="4" fill="{r["color"]}" data-tip="{esc(r["tip"])}"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 11}" fill="{INK}" font-size="12" '
                     f'text-anchor="end">{esc(r["label"])}</text>')
        tx = x + w + 6 if r["value"] >= 0 else x - 6
        anc = "start" if r["value"] >= 0 else "end"
        parts.append(f'<text x="{tx:.1f}" y="{y + 11}" fill="{INK}" font-size="11" '
                     f'text-anchor="{anc}">{r["value"]:+.1f}%</text>')
    parts.append(f'<text x="{zero}" y="{h - 4}" fill="{MUTED}" font-size="11" '
                 f'text-anchor="middle">{esc(x_label)}</text></svg>')
    return "".join(parts)


def line_chart(points: list[tuple[float, float]], *, width=720, height=240,
               x_label="", y_label="", baseline: float | None = None,
               tips: list[str] | None = None) -> str:
    if len(points) < 2:
        return '<p class="muted">点が足りません。</p>'
    pad_l, pad_r, pad_t, pad_b = 56, 20, 16, 34
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    if baseline is not None:
        lo, hi = min(lo, baseline), max(hi, baseline)
    span = (hi - lo) or 1
    lo, hi = lo - span * 0.12, hi + span * 0.12
    sx = lambda v: pad_l + (v - min(xs)) / ((max(xs) - min(xs)) or 1) * (width - pad_l - pad_r)   # noqa: E731
    sy = lambda v: pad_t + (hi - v) / (hi - lo) * (height - pad_t - pad_b)                        # noqa: E731

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" '
             f'class="chart">']
    for frac in (0, 0.5, 1):
        v = lo + (hi - lo) * frac
        y = sy(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" fill="{MUTED}" '
                     f'font-size="11" text-anchor="end">{v:.2f}</text>')
    if baseline is not None:
        y = sy(baseline)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" stroke="{MUTED}" stroke-width="1" '
                     f'stroke-dasharray="4 4"/>')
    d = " ".join(("M" if i == 0 else "L") + f"{sx(x):.1f},{sy(y):.1f}"
                 for i, (x, y) in enumerate(points))
    parts.append(f'<path d="{d}" fill="none" stroke="{ACCENT}" stroke-width="2" '
                 f'stroke-linejoin="round"/>')
    for i, (x, y) in enumerate(points):
        tip = (tips[i] if tips and i < len(tips) else f"{x:g} / {y:.2f}")
        parts.append(f'<circle class="mark" cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="5" '
                     f'fill="{ACCENT}" stroke="{SURFACE}" stroke-width="2" '
                     f'data-tip="{esc(tip)}"/>')
    for x in xs:
        parts.append(f'<text x="{sx(x):.1f}" y="{height - 14}" fill="{MUTED}" '
                     f'font-size="11" text-anchor="middle">{x:g}</text>')
    parts.append(f'<text x="{(width) / 2:.0f}" y="{height - 1}" fill="{MUTED}" '
                 f'font-size="11" text-anchor="middle">{esc(x_label)}</text>')
    parts.append("</svg>")
    head = (f'<p class="axis-label">{esc(y_label)}</p>' if y_label else "")
    return head + "".join(parts)


def coverage_grid(frame: pd.DataFrame, names: dict[str, str]) -> str:
    if frame is None or frame.empty:
        return '<p class="muted">蓄積データがありません。</p>'
    weeks = [str(c) for c in frame.columns]
    stores = [str(i) for i in frame.index]
    cw = max(6, min(14, int(900 / max(len(weeks), 1))))
    ch, pad_l, pad_t = 22, 150, 18
    w = pad_l + cw * len(weeks) + 10
    h = pad_t + ch * len(stores) + 26
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" class="chart">']
    for r, sid in enumerate(stores):
        y = pad_t + r * ch
        label = f"{names.get(sid, sid)}（{sid}）"
        parts.append(f'<text x="{pad_l - 8}" y="{y + 15}" fill="{INK}" font-size="12" '
                     f'text-anchor="end">{esc(label)}</text>')
        for c, wk in enumerate(weeks):
            on = bool(frame.iloc[r, c])
            parts.append(
                f'<rect class="mark" x="{pad_l + c * cw}" y="{y + 4}" '
                f'width="{cw - 2}" height="{ch - 8}" rx="2" '
                f'fill="{ACCENT if on else "#eef1f4"}" '
                f'data-tip="{esc(label)} / {esc(wk)} / {"あり" if on else "なし"}"/>')
    for c, wk in enumerate(weeks):
        if c % max(1, len(weeks) // 12) == 0:
            x = pad_l + c * cw + cw / 2
            parts.append(f'<text x="{x:.0f}" y="{h - 8}" fill="{MUTED}" font-size="10" '
                         f'text-anchor="middle" transform="rotate(-60 {x:.0f} '
                         f'{h - 8})">{esc(wk)}</text>')
    parts.append("</svg>")
    legend = (f'<p class="legend"><span class="sw" style="background:{ACCENT}"></span>あり'
              f'<span class="sw" style="background:#eef1f4"></span>なし</p>')
    return legend + "".join(parts)


def table(df: pd.DataFrame, *, numeric_fmt: dict | None = None,
          tag_col: str | None = None, tag_colors: dict | None = None) -> str:
    if df is None or df.empty:
        return '<p class="muted">データがありません。</p>'
    numeric_fmt = numeric_fmt or {}
    head = "".join(f'<th data-col="{i}">{esc(c)}</th>'
                   for i, c in enumerate(df.columns))
    body = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if c == tag_col and tag_colors:
                col = tag_colors.get(str(v), MUTED)
                cells.append(f'<td><span class="tag" style="color:{col};'
                             f'border-color:{col}33">{esc(v)}</span></td>')
            elif c in numeric_fmt:
                cells.append(f'<td class="num" data-v="{"" if pd.isna(v) else v}">'
                             f'{_fmt(v, numeric_fmt[c])}</td>')
            else:
                cells.append(f'<td>{esc(v)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f'<table class="sortable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


# ---------------------------------------------------------------- 各タブ

def _tab_overview(a, warnings_html: str) -> str:
    mape = [v for (u, m), v in a.loo.mape.items() if m == "pi"]
    mean = float(np.mean(mape)) if mape else None
    col, band = accuracy_band(mean)
    risky = [r for r in a.risks if r.level in ("要対策", "警戒")]
    return f"""
<div class="cards">
  <div class="card"><div class="k">類似店</div>
    <div class="v">{esc("・".join(p.store_name for p in a.peers))}</div></div>
  <div class="card"><div class="k">立地</div><div class="v">{esc(a.urban_new.verdict)}</div>
    <div class="sub">都市度 {_fmt(a.urban_new.score)}</div></div>
  <div class="card"><div class="k">数量PIの実測精度</div>
    <div class="v" style="color:{col}">±{_fmt(mean, 1)}%</div>
    <div class="sub">{esc(band)}（1店抜き検証）</div></div>
  <div class="card"><div class="k">苦戦予想</div>
    <div class="v">{len(risky)} 件</div>
    <div class="sub">{esc("、".join(a.idpos.leaf(r.unit) for r in risky[:4])) or "なし"}</div></div>
</div>
<div class="note">この数値は<strong>既存店の実績の加重平均</strong>であって、モデルの予測ではありません。
「実測精度」は、同じ手順で既存店を予測したときに実際に外れた幅です。</div>
{warnings_html}
"""


def _tab_predictions(a) -> str:
    by_unit = a.predictions_by_unit()
    rows = []
    for unit, mets in by_unit.items():
        pi = mets.get("pi")
        if pi is None:
            continue
        _, band = accuracy_band(pi.loo_mape)
        rows.append({
            "ライン": a.idpos.parent_of(unit, "line"),
            a.idpos.level_label(): a.idpos.leaf(unit),
            "数量PI": pi.point,
            "下限": pi.low, "上限": pi.high,
            "粗利率": mets["gross_margin_rate"].point if "gross_margin_rate" in mets else None,
            "粗利PI": mets["gp_pi"].point if "gp_pi" in mets else None,
            "買上率": mets["buy_rate"].point if "buy_rate" in mets else None,
            "1人当点数": mets["units_per_buyer"].point if "units_per_buyer" in mets else None,
            "実測誤差%": pi.loo_mape,
            "精度": band,
        })
    df = pd.DataFrame(rows).sort_values("数量PI", ascending=False)
    t = table(df, numeric_fmt={"数量PI": 2, "下限": 2, "上限": 2, "粗利率": 3,
                               "粗利PI": 2, "買上率": 4, "1人当点数": 2,
                               "実測誤差%": 1},
              tag_col="精度",
              tag_colors={"当たる": GOOD, "幅をもって見る": WARN,
                          "当たらない": BAD, "未測定": MUTED})
    return f"""
<p class="sub">列見出しをクリックすると並べ替えられます。「下限〜上限」は
1店抜き検証で実際に出た誤差の80パーセンタイルから作った幅です。</p>
{t}
"""


def _tab_accuracy(a) -> str:
    rows = []
    for (unit, metric), v in a.loo.mape.items():
        if metric != "pi":
            continue
        col, band = accuracy_band(v)
        base = a.loo.baseline_mape.get((unit, metric))
        rows.append({"label": a.idpos.leaf(unit), "value": v, "color": col,
                     "tip": f"{a.idpos.leaf(unit)}｜誤差 ±{v:.1f}%（{band}）"
                            + (f"／全店平均なら {base:.1f}%" if base else "")})
    rows.sort(key=lambda r: -r["value"])
    bars = bar_chart(rows[:18], x_label="1店抜き検証の平均誤差（%）")

    ramp_html = '<p class="muted">立ち上がりカーブは推定できませんでした。</p>'
    if a.ramp is not None and a.ramp.curves.get("pi") and a.ramp.curves["pi"].usable:
        rc = a.ramp.curves["pi"]
        pts = [(float(w), rc.factors[w]) for w in sorted(rc.factors)]
        tips = [f"開店{w}週目｜成熟水準の {rc.factors[w]:.2f} 倍" for w in sorted(rc.factors)]
        ramp_html = (
            f'<p class="sub">成熟期に届いた{rc.n_contributors}店'
            f'（{esc("・".join(rc.contributors))}）から測定。1.00が成熟水準。</p>'
            + line_chart(pts, x_label="開店からの週", y_label="成熟水準比",
                         baseline=1.0, tips=tips))

    mt = a.idpos.maturity
    mat = '<p class="muted">—</p>'
    if mt is not None and not mt.empty:
        names = a.idpos.store_names
        d = pd.DataFrame({
            "店舗": [names.get(str(s), s) for s in mt["store_id"]],
            "オープン": mt["open_date"],
            "概算": mt.get("approx", pd.Series([False] * len(mt))).map(
                {True: "月のみ", False: ""}),
            "経過週": mt["weeks_since_open"],
            "除外週": mt["weeks_excluded"],
            "水準に使った週": mt["weeks_used"],
        })
        mat = table(d, numeric_fmt={"経過週": 0, "除外週": 0, "水準に使った週": 0})

    return f"""
<h3>どれくらい当たるか</h3>
<p class="sub">既存店を1店ずつ「新店だと思って」残りから予測し、実際に外れた幅です。
色は右の判定と同じ意味です（色だけで判断しないでください）。</p>
{bars}
<p class="legend"><span class="sw" style="background:{GOOD}"></span>当たる（±8%未満）
<span class="sw" style="background:{WARN}"></span>幅をもって見る
<span class="sw" style="background:{BAD}"></span>当たらない（±15%超）</p>

<h3>開店からの立ち上がり</h3>
{ramp_html}

<h3>既存店の成熟度</h3>
{mat}
"""


def _tab_coverage(a, coverage) -> str:
    if coverage is None:
        return ('<p class="muted">蓄積データの情報が渡されていません。</p>')
    grid = coverage_grid(coverage.frame, a.idpos.store_names)
    gaps = "".join(f'<div class="note warn">{esc(g)}</div>' for g in coverage.gaps)
    return f"""
<p class="sub">{len(coverage.stores)}店舗 × {len(coverage.weeks)}週が入っています。</p>
{grid}
{gaps or '<div class="note">週の抜けは見つかりませんでした。</div>'}
"""


def _tab_verify(a, verify) -> str:
    if verify is None:
        return ('<div class="note">まだ予実検証をしていません。'
                '開店後の週次IDPOSを入れると、ここに結果が入ります。</div>')
    cards = "".join(
        f'<div class="card"><div class="k">{esc(k)}</div><div class="v">{n}</div></div>'
        for k, n in verify.summary.items())
    bars = []
    for r in verify.rows:
        if r.verdict not in ("下振れ", "上振れ") or r.diff_pct is None:
            continue
        bars.append({"label": r.unit.split(" > ")[-1], "value": r.diff_pct,
                     "color": BAD if r.verdict == "下振れ" else ACCENT,
                     "tip": f"{r.unit}｜{r.metric_label}｜{r.verdict} {r.diff_pct:+.1f}%"})
    bars.sort(key=lambda b: b["value"])
    df = pd.DataFrame([{
        "ライン": r.unit.split(" > ")[0], "単位": r.unit.split(" > ")[-1],
        "指標": r.metric_label, "予測": r.predicted,
        "実績(季節補正後)": r.actual_adjusted, "差%": r.diff_pct,
        "判定": r.verdict} for r in verify.rows])
    return f"""
<p class="sub">予測作成 {esc(verify.predicted_at)}／実績 {verify.weeks_observed}週
（うち開店直後{verify.excluded_first_weeks}週は判定から除外）</p>
<div class="cards">{cards}</div>
<h3>週次のブレでは説明できなかったもの</h3>
<p class="legend"><span class="sw" style="background:{ACCENT}"></span>上振れ
<span class="sw" style="background:{BAD}"></span>下振れ</p>
{diverging_bar(bars[:20], x_label="予測との差（%）")}
<h3>全件</h3>
{table(df, numeric_fmt={"予測": 2, "実績(季節補正後)": 2, "差%": 1},
       tag_col="判定",
       tag_colors={"想定内": GOOD, "上振れ": ACCENT, "下振れ": BAD,
                   "ブレの範囲": MUTED, "判定保留": MUTED, "データなし": MUTED})}
"""


def _tab_loop(loop, trend) -> str:
    if loop is None:
        return '<div class="note">運用の状態はまだ記録されていません。</div>'
    up = pd.DataFrame([{"項目": u.label, "前": u.before, "後": u.after,
                        "理由": u.reason} for u in loop.updates])
    props = "".join(
        f'<div class="prop"><div class="prop-h"><strong>{esc(p.label)}</strong>'
        f'<span class="tag">{esc(p.decided)}</span></div>'
        f'<p>{esc(p.detail)}</p>'
        f'<p class="sub">なぜ: {esc(p.why)}</p>'
        f'<p class="sub">リスク: {esc(p.risk)}</p></div>'
        for p in loop.proposals)
    tr = ""
    if trend is not None and not trend.empty:
        tr = f"<h3>精度の履歴</h3>{table(trend, numeric_fmt={'平均絶対誤差%': 1})}"
    return f"""
<h3>自動で更新したもの</h3>
<p class="sub">決定論的に決まるもの（立ち上がりカーブ、予測区間の幅、気温感応度）だけを自動で直します。</p>
{table(up) if not up.empty else '<p class="muted">今回の更新はありません。</p>'}
<h3>人が決めること</h3>
<p class="sub">5店舗ではここを自動で決めると過学習します。</p>
{props}
{tr}
"""


# ---------------------------------------------------------------- 本体

CSS = """
*{box-sizing:border-box}
body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
color:#1b1f23;background:#fff;margin:0;padding:0 24px 64px;line-height:1.7;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto}
header{border-bottom:3px solid #1b1f23;padding:28px 0 16px;margin-bottom:8px}
h1{font-size:23px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}
h3{font-size:15px;margin:26px 0 8px;color:#374151}
p,li,td,th{font-size:13px}.sub,.muted{color:#6b7280;font-size:12.5px}
nav{display:flex;gap:4px;flex-wrap:wrap;border-bottom:1px solid #e3e6ea;
margin-bottom:18px;position:sticky;top:0;background:#fff;z-index:5;padding-top:8px}
nav button{font:inherit;font-size:13px;background:none;border:none;cursor:pointer;
padding:9px 14px;color:#6b7280;border-bottom:2px solid transparent}
nav button[aria-selected=true]{color:#1d4ed8;border-bottom-color:#1d4ed8;font-weight:600}
section[hidden]{display:none}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.card{flex:1 1 180px;border:1px solid #e3e6ea;border-radius:6px;padding:12px 14px}
.card .k{font-size:11.5px;color:#6b7280}.card .v{font-size:18px;font-weight:600}
.card .sub{font-size:11.5px}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:10px 0}
th,td{border:1px solid #e3e6ea;padding:6px 9px;text-align:left}
th{background:#f6f8fa;font-weight:600;white-space:nowrap}
table.sortable th{cursor:pointer;user-select:none}
table.sortable th:hover{background:#eef2f7}
td.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:nth-child(even){background:#fcfcfd}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;
border:1px solid #e3e6ea;white-space:nowrap}
.note{background:#f6f8fa;border-left:4px solid #1d4ed8;padding:10px 14px;margin:12px 0;
font-size:12.5px;border-radius:0 4px 4px 0}
.note.warn{background:#fffbeb;border-left-color:#ca8a04}
.note.bad{background:#fef2f2;border-left-color:#b91c1c}
.chart{display:block;margin:8px 0;overflow:visible}
.mark{cursor:pointer}.mark:hover{opacity:.82}
.legend{font-size:12px;color:#6b7280;margin:6px 0}
.axis-label{font-size:11.5px;color:#6b7280;margin:10px 0 -4px}
.sw{display:inline-block;width:11px;height:11px;border-radius:2px;margin:0 5px 0 14px;
vertical-align:-1px}.legend .sw:first-child{margin-left:0}
.prop{border:1px solid #e3e6ea;border-radius:6px;padding:10px 14px;margin:8px 0}
.prop-h{display:flex;justify-content:space-between;align-items:center}
#tip{position:fixed;pointer-events:none;background:#1b1f23;color:#fff;font-size:12px;
padding:5px 9px;border-radius:4px;opacity:0;transition:opacity .1s;z-index:50;
max-width:340px}
footer{margin-top:40px;padding-top:14px;border-top:1px solid #e3e6ea;color:#6b7280;
font-size:12px}
@media print{nav{display:none}section[hidden]{display:block}body{padding:0}}
"""

JS = """
document.querySelectorAll('nav button').forEach(function(b){
  b.addEventListener('click', function(){
    document.querySelectorAll('nav button').forEach(function(x){
      x.setAttribute('aria-selected', x === b ? 'true' : 'false'); });
    document.querySelectorAll('section[data-tab]').forEach(function(s){
      s.hidden = s.dataset.tab !== b.dataset.tab; });
  });
});
var tip = document.getElementById('tip');
document.addEventListener('mouseover', function(e){
  var t = e.target.closest('[data-tip]');
  if(!t){ tip.style.opacity = 0; return; }
  tip.textContent = t.getAttribute('data-tip');
  tip.style.opacity = 1;
});
document.addEventListener('mousemove', function(e){
  if(tip.style.opacity === '0') return;
  var x = e.clientX + 14, y = e.clientY + 14;
  if(x + tip.offsetWidth > window.innerWidth) x = e.clientX - tip.offsetWidth - 10;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
});
document.querySelectorAll('table.sortable').forEach(function(tb){
  tb.querySelectorAll('th').forEach(function(th, i){
    var asc = true;
    th.addEventListener('click', function(){
      var rows = Array.from(tb.tBodies[0].rows);
      rows.sort(function(a, b){
        var ca = a.cells[i], cb = b.cells[i];
        var va = ca.dataset.v !== undefined ? parseFloat(ca.dataset.v) : NaN;
        var vb = cb.dataset.v !== undefined ? parseFloat(cb.dataset.v) : NaN;
        if(!isNaN(va) && !isNaN(vb)) return asc ? va - vb : vb - va;
        return asc ? ca.textContent.localeCompare(cb.textContent, 'ja')
                   : cb.textContent.localeCompare(ca.textContent, 'ja');
      });
      rows.forEach(function(r){ tb.tBodies[0].appendChild(r); });
      asc = !asc;
    });
  });
});
"""


def render_dashboard(a, *, coverage=None, verify=None, loop=None,
                     trend=None) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    warnings_html = "".join(f'<div class="note warn">{esc(w)}</div>'
                            for w in a.warnings)
    tabs = [
        ("overview", "概要", _tab_overview(a, warnings_html)),
        ("pred", "予測", _tab_predictions(a)),
        ("acc", "精度", _tab_accuracy(a)),
        ("cov", "蓄積状況", _tab_coverage(a, coverage)),
        ("ver", "予実検証", _tab_verify(a, verify)),
        ("loop", "運用の状態", _tab_loop(loop, trend)),
    ]
    nav = "".join(
        f'<button data-tab="{k}" aria-selected="{"true" if i == 0 else "false"}">'
        f'{esc(label)}</button>' for i, (k, label, _) in enumerate(tabs))
    secs = "".join(
        f'<section data-tab="{k}"{"" if i == 0 else " hidden"}>'
        f'<h2>{esc(label)}</h2>{body}</section>'
        for i, (k, label, body) in enumerate(tabs))
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>新店 低温予測ダッシュボード｜{esc(a.new_name)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><h1>新店 低温予測ダッシュボード — {esc(a.new_name)}</h1>
<p class="sub">既存店 {len(a.stores)}店／粒度 {esc(a.idpos.level_label())}
（{len(a.idpos.units)}単位）／商圏定義 {esc(a.cfg.trade_area_definition)}／作成 {now}</p>
</header>
<nav>{nav}</nav>
{secs}
<footer>shoken_analyzer のダッシュボード書き出し。外部への通信はありません（オフラインで開けます）。<br>
<strong>このファイルには実績由来の数値が埋め込まれています。</strong>社外に出さないでください。<br>
アップロードして再計算する操作は、Streamlit版のダッシュボードで行ってください。</footer>
</div><div id="tip"></div><script>{JS}</script></body></html>"""


def write_dashboard(a, path, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(a, **kw), encoding="utf-8")
    return path
