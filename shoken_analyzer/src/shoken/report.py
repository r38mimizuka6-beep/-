"""HTML / Excel レポートの出力。"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------- 小道具

def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def fnum(x, digits: int = 1, suffix: str = "") -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if isinstance(x, (int, float)):
        if abs(x) >= 10000:
            return f"{x:,.0f}{suffix}"
        return f"{x:,.{digits}f}{suffix}"
    return str(x)


def fpct(x, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x * 100:.{digits}f}%"


def fpt(x, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:+.{digits}f}pt"


def _axis_bar(z: float | None) -> str:
    """-2..+2 のzスコアを横棒で表す。中央が既存5店の平均。"""
    if z is None:
        return '<span class="muted">—</span>'
    pos = max(0.0, min(1.0, (z + 2.5) / 5.0)) * 100
    cls = "hi" if z > 0 else "lo"
    return (
        '<span class="bar"><span class="bar-mid"></span>'
        f'<span class="bar-dot {cls}" style="left:{pos:.1f}%"></span></span>'
        f'<span class="bar-val">{z:+.2f}</span>'
    )


CSS = """
:root{--fg:#1b1f23;--muted:#6b7280;--line:#e3e6ea;--bg:#fff;--soft:#f6f8fa;
--ok:#15803d;--warn:#b45309;--bad:#b91c1c;--accent:#1d4ed8;}
*{box-sizing:border-box}
body{font-family:"Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
color:var(--fg);background:var(--bg);margin:0;padding:0 24px 80px;line-height:1.7;
-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto}
header{border-bottom:3px solid var(--fg);padding:32px 0 18px;margin-bottom:28px}
h1{font-size:25px;margin:0 0 6px;letter-spacing:.01em}
h2{font-size:19px;margin:44px 0 12px;padding-bottom:7px;border-bottom:2px solid var(--line)}
h3{font-size:15px;margin:24px 0 8px;color:#374151}
p,li{font-size:13.5px}
.sub{color:var(--muted);font-size:13px;margin:0}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:10px 0 6px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:right;vertical-align:middle}
th{background:var(--soft);font-weight:600;text-align:center;white-space:nowrap}
td.l,th.l{text-align:left}
tbody tr:nth-child(even){background:#fcfcfd}
.muted{color:var(--muted)}
.note{background:var(--soft);border-left:4px solid var(--accent);padding:11px 15px;
margin:14px 0;font-size:13px;border-radius:0 4px 4px 0}
.warn{background:#fff8ed;border-left:4px solid var(--warn)}
.bad{background:#fef2f2;border-left:4px solid var(--bad)}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11.5px;
border:1px solid var(--line);background:#fff;white-space:nowrap}
.tag.ok{color:var(--ok);border-color:#bbf7d0;background:#f0fdf4}
.tag.warn{color:var(--warn);border-color:#fed7aa;background:#fffbeb}
.tag.bad{color:var(--bad);border-color:#fecaca;background:#fef2f2}
.tag.pick{color:var(--accent);border-color:#bfdbfe;background:#eff6ff;font-weight:600}
.bar{position:relative;display:inline-block;width:118px;height:9px;background:#eef1f4;
border-radius:5px;vertical-align:middle;margin-right:7px}
.bar-mid{position:absolute;left:50%;top:-2px;width:1px;height:13px;background:#c3c8ce}
.bar-dot{position:absolute;top:-2px;width:9px;height:13px;border-radius:3px;margin-left:-4px}
.bar-dot.hi{background:var(--accent)}.bar-dot.lo{background:#64748b}
.bar-val{font-variant-numeric:tabular-nums;font-size:11.5px;color:var(--muted)}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.card{flex:1 1 200px;border:1px solid var(--line);border-radius:6px;padding:12px 14px}
.card .k{font-size:11.5px;color:var(--muted);margin-bottom:3px}
.card .v{font-size:19px;font-weight:600}
.card.pick{border-color:var(--accent);background:#f8fbff}
footer{margin-top:56px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:12px}
ol.chk{padding-left:0;list-style:none}
details{margin:8px 0}summary{cursor:pointer;font-size:13px;color:var(--accent)}
@media print{body{padding:0}h2{page-break-after:avoid}table{page-break-inside:avoid}}
"""

DISCLAIMER = """
本レポートの数値は<strong>既存店の実績の加重平均</strong>であって、モデルの予測値ではありません。
店舗数（5店）が商圏変数の数より少ないため、回帰やGBMで「商圏→PI・粗利率」を
学習することはできません。週次IDPOSが何十週あっても、この点は変わりません
（週を増やしても店舗数は増えないため）。<br>
そのかわり、<strong>同じ手順を既存店に1店ずつ当てはめて（LOO検証）、実際にどれだけ外れるか</strong>を
測っています。本レポートの「正確性」はすべてこの実測誤差です。
さらに、週次のブレ（変動係数）から<strong>そもそも何%まで当てられるかの上限</strong>も出しています。
"""


# ---------------------------------------------------------------- 各セクション

def _sec_summary(a) -> str:
    cfg, u = a.cfg, a.urban_new
    rows = []
    for axis, spec in cfg.axes.items():
        z = a.similarity.new_axis_profile.get(axis)
        w = a.similarity.weights[axis]
        if z is None:
            reading = "この軸の入力が揃っていません"
        elif z > 0.75:
            reading = "既存店の中では明確に高い側"
        elif z > 0.25:
            reading = "やや高い側"
        elif z > -0.25:
            reading = "既存店の平均付近"
        elif z > -0.75:
            reading = "やや低い側"
        else:
            reading = "既存店の中では明確に低い側"
        rows.append(
            f'<tr><td class="l">{esc(spec["label"])}</td><td class="l">{_axis_bar(z)}</td>'
            f'<td class="l">{esc(reading)}</td><td>{w:.2f}</td></tr>'
        )

    detail_rows = []
    for d in u.details:
        score = "—" if d["score"] is None else f'{d["score"]:.2f}'
        detail_rows.append(
            f'<tr><td class="l">{esc(d["label"])}</td><td>{fnum(d["value"], 2)}</td>'
            f'<td>{fnum(d.get("rural_at"), 0)}</td><td>{fnum(d.get("urban_at"), 0)}</td>'
            f'<td>{score}</td><td>{d["weight"]:.1f}</td></tr>'
        )

    ucls = {"都市部寄り": "ok", "中間": "warn", "郊外寄り": "bad"}.get(u.verdict, "warn")
    rural_n = sum(1 for x in a.urban_stores.values() if x.is_rural)
    urban_n = sum(1 for x in a.urban_stores.values() if x.verdict == "都市部寄り")

    picked = {p.store_id for p in a.peers}
    prank = []
    for s in a.similarity.ranking:
        tag = '<span class="tag pick">採用</span>' if s.store_id in picked else ""
        ud = a.urban_stores.get(s.store_id)
        cells = "".join(
            f'<td>{fnum(s.axis_distance.get(ax), 2)}'
            f'<br><span class="muted" style="font-size:11px">{esc(s.coverage.get(ax, ""))}</span></td>'
            for ax in cfg.axes
        )
        prank.append(
            f'<tr><td>{s.rank}</td><td class="l">{esc(s.store_name)} {tag}</td>'
            f'<td class="l"><span class="tag">{esc(ud.verdict if ud else "—")}</span></td>'
            f'<td><strong>{s.similarity:.1f}</strong></td><td>{s.distance:.3f}</td>{cells}</tr>'
        )
    ahead = "".join(f'<th>{esc(cfg.axes[ax]["label"].split("（")[0])}</th>' for ax in cfg.axes)

    reasons = []
    for p in a.peers:
        gaps = p.top_gaps(cfg, a.similarity.z_new, a.similarity.z_stores[p.store_id])
        close = sorted([(ax, d) for ax, d in p.axis_distance.items() if d is not None],
                       key=lambda t: t[1])
        near = "、".join(f"{cfg.axes[ax]['label'].split('（')[0]}（距離{d:.2f}）" for ax, d in close[:2])
        gl = "".join(f'<li>{esc(g["label"])}：{esc(g["direction"])}（差 {g["z_gap"]:.2f}σ）</li>'
                     for g in gaps)
        reasons.append(
            f'<p><strong>第{p.rank}位 {esc(p.store_name)}（類似度 {p.similarity:.1f}）</strong>'
            f'<br>近い理由: {esc(near)}。残っている差:</p><ul>{gl}</ul>'
        )

    wtxt = "、".join(f"{cfg.axes[k]['label'].split('（')[0]}={v:.2f}"
                    for k, v in a.similarity.weights.items())

    return f"""
<h2>1. 新店の商圏と類似店</h2>
<p class="sub">商圏定義: {esc(cfg.trade_area_definition)} ／ 分析粒度: {esc(a.idpos.level_label())}
（{len(a.idpos.units)}単位） ／ 対象: 低温ディビジョン</p>
<div class="cards">
  <div class="card"><div class="k">都市部／郊外の判定</div>
    <div class="v"><span class="tag {ucls}">{esc(u.verdict)}</span></div>
    <div class="muted" style="font-size:11.5px">都市度スコア {fnum(u.score, 2)}（1.00=都市部／0.00=郊外）</div></div>
  <div class="card"><div class="k">既存店の内訳</div>
    <div class="v">都市部 {urban_n} ／ 郊外 {rural_n}</div>
    <div class="muted" style="font-size:11.5px">全{len(a.stores)}店</div></div>
  <div class="card pick"><div class="k">採用した類似店</div>
    <div class="v" style="font-size:15px">{esc("・".join(p.store_name for p in a.peers))}</div>
    <div class="muted" style="font-size:11.5px">軸の重み: {esc(wtxt)}</div></div>
</div>

<h3>3軸での位置づけ</h3>
<p class="sub">値は既存店を基準にした標準化スコア（0=平均、±1=標準偏差1つ分）。</p>
<table><thead><tr><th class="l">軸</th><th class="l">新店の位置（低 ← 平均 → 高）</th>
<th class="l">読み方</th><th>重み</th></tr></thead><tbody>{"".join(rows)}</tbody></table>

<h3>類似店ランキング</h3>
<table><thead><tr><th>順位</th><th class="l">既存店</th><th class="l">立地タイプ</th>
<th>類似度</th><th>距離</th>{ahead}</tr></thead><tbody>{"".join(prank)}</tbody></table>
{"".join(reasons)}

<details><summary>都市部／郊外 判定の内訳</summary>
<table><thead><tr><th class="l">指標</th><th>新店の値</th><th>郊外の目安</th>
<th>都市部の目安</th><th>スコア</th><th>重み</th></tr></thead>
<tbody>{"".join(detail_rows)}</tbody></table></details>
"""


def _pred_cell(p, mcfg) -> str:
    if p is None or p.point is None:
        return '<td class="muted">—</td>'
    m = mcfg.metric(p.metric)
    dg = getattr(m, "digits", 2)
    rng = (f'<br><span class="muted" style="font-size:11px">'
           f'{p.low:,.{dg}f}〜{p.high:,.{dg}f}</span>') if p.low is not None else ""
    return f'<td><strong>{p.point:,.{dg}f}</strong>{rng}</td>'


def _sec_predictions(a) -> str:
    mcfg, idpos = a.mcfg, a.idpos
    by_unit = a.predictions_by_unit()
    mets = [m for m in ("pi", "buy_rate", "units_per_buyer",
                        "gross_margin_rate", "gp_pi", "gp_share")
            if m in idpos.metrics]
    risk_by_unit = {r.unit: r for r in a.risks}

    head = "".join(f'<th>{esc(mcfg.metric_label(m))}<br>'
                   f'<span class="muted" style="font-size:11px">予測／区間</span></th>'
                   for m in mets)

    body, current_line = [], None
    for unit in sorted(by_unit, key=lambda u: (idpos.parent_of(u, "line") or u, u)):
        line = idpos.parent_of(unit, "line") or unit
        if line != current_line:
            current_line = line
            body.append(f'<tr><td class="l" colspan="{len(mets) + 4}" '
                        f'style="background:#eef2f7;font-weight:600">{esc(line)}</td></tr>')
        mets_d = by_unit[unit]
        cells = "".join(_pred_cell(mets_d.get(m), mcfg) for m in mets)
        pi = mets_d.get("pi")
        peers = ("・".join(f"{c.store_name}{c.weight:.0%}" for c in pi.peers)
                 if pi and pi.peers else "—")
        acc = "—"
        if pi and pi.loo_mape is not None:
            cls = "ok" if pi.loo_mape < 8 else ("warn" if pi.loo_mape < 15 else "bad")
            acc = f'<span class="tag {cls}">±{pi.loo_mape:.0f}%</span>'
        r = risk_by_unit.get(unit)
        rlv = (f'<span class="tag {"bad" if r.level == "要対策" else "warn" if r.level == "警戒" else ""}">'
               f'{esc(r.level)}</span>') if r and r.level != "—" else ""
        leaf = idpos.leaf(unit)
        body.append(
            f'<tr><td class="l" style="padding-left:18px">{esc(leaf)}</td>{cells}'
            f'<td>{acc}</td><td class="l muted" style="font-size:11px">{esc(peers)}</td>'
            f'<td>{rlv}</td></tr>'
        )

    basis = "".join(
        f'<tr><td class="l">{esc(idpos.leaf(p.unit))}</td>'
        f'<td class="l">{esc(mcfg.metric_label(p.metric))}</td>'
        f'<td class="l" style="font-size:11.5px">{esc(p.basis)}</td></tr>'
        for p in a.predictions if p.metric == "pi"
    )

    return f"""
<h2>2. 想定される粗利率・PI値</h2>
<p class="sub">{esc(mcfg.pi_definition)}<br>
予測値 = 類似店の実績の加重平均。下段の区間は<strong>LOO検証で実際に出た誤差</strong>の
80パーセンタイルから作っています（モデルの信頼区間ではありません）。</p>
<table><thead><tr><th class="l">{esc(idpos.level_label())}</th>{head}
<th>実測精度<br><span class="muted" style="font-size:11px">LOO誤差</span></th>
<th class="l">根拠にした類似店</th><th>苦戦</th></tr></thead>
<tbody>{"".join(body)}</tbody></table>
<div class="note">「実測精度 ±X%」は、同じ手順で既存店を予測したときの平均誤差です。
±15%を超えるものは、予測値そのものより<strong>レンジの下限</strong>で初期棚を組み、
開店後の実績で決めてください。</div>

<details><summary>1件ずつの根拠を見る</summary>
<table><thead><tr><th class="l">{esc(idpos.level_label())}</th><th class="l">指標</th>
<th class="l">根拠</th></tr></thead><tbody>{basis}</tbody></table></details>
"""


def _sec_methods(a) -> str:
    """転換前後法と類似店法の比較。業態転換リニューアルのときだけ出る。"""
    conv = a.conversion
    if conv is None:
        return """
<h2>2-2. もう一つの予測方法と、次の転換への備え</h2>
<div class="note warn">同じ立地の<strong>転換前実績</strong>があれば、
<code>予測 = この店の転換前実績 × 既存店で測った転換後÷転換前の比率</code> という方法が使えます。
立地・商圏・競合が同じ店の前後を比べるので、商圏の違いを推定する必要がなく、
類似店法よりはるかに正確です。<br>
<strong>今回は転換前のIDPOSが残っておらず、使えません。</strong>そのため本レポートは
類似店法だけで出しています。</div>
<div class="note bad"><strong>次に転換する店のために、今すぐできること</strong><br>
まだ転換していない店の週次IDPOSを、<strong>今週から毎週そのまま保存してください。</strong>
システムが遡れなくても、これから先の分は貯められます。
転換の半年前から貯まっていれば、その店の予測はこの方法で出せます。<br>
保存するのは本ツールに渡すのと同じ2種類のエクスポート（数量・客数つき／年代つき）と
売上在庫ファイルです。加工は要りません。1店あたり週数千行なので保管コストはほぼかかりません。</div>"""

    if not conv.predictions:
        warns = "".join(f"<li>{esc(w)}</li>" for w in conv.warnings)
        return f"""
<h2>2-2. 転換前後法（算出できませんでした）</h2>
<div class="note bad"><ul style="margin:0;padding-left:18px">{warns}</ul></div>"""

    cmp_df = a.method_compare
    mcfg, idpos = a.mcfg, a.idpos
    conv_by = conv.by_key()

    rows = []
    for _, r in cmp_df.sort_values(["metric", "unit"]).iterrows():
        c = conv_by.get((r["unit"], r["metric"]))
        m = mcfg.metric(r["metric"])
        dg = getattr(m, "digits", 2)
        better = r["better"]
        bcls = "ok" if better == "転換前後法" else "warn"
        se = "—" if pd.isna(r["similar_err"]) else f'±{r["similar_err"]:.1f}%'
        ce = "—" if pd.isna(r["conversion_err"]) else f'±{r["conversion_err"]:.1f}%'
        gap = "—" if pd.isna(r["gap_pct"]) else f'{r["gap_pct"]:+.1f}%'
        rows.append(
            f'<tr><td class="l">{esc(idpos.parent_of(r["unit"], "line") or "")}</td>'
            f'<td class="l"><strong>{esc(idpos.leaf(r["unit"]))}</strong></td>'
            f'<td class="l">{esc(r["metric_label"])}</td>'
            f'<td>{r["baseline"]:,.{dg}f}</td>'
            f'<td>{r["ratio"]:.2f}倍</td>'
            f'<td><strong>{r["conversion_point"]:,.{dg}f}</strong>'
            f'<br><span class="muted" style="font-size:11px">{ce}</span></td>'
            f'<td>{r["similar_point"]:,.{dg}f}'
            f'<br><span class="muted" style="font-size:11px">{se}</span></td>'
            f'<td>{gap}</td>'
            f'<td><span class="tag {bcls}">{esc(better)}</span></td></tr>'
        )

    ratio_rows = []
    seen = set()
    for (unit, metric), cr in sorted(conv.ratios.items()):
        if metric != "pi":
            continue
        key = idpos.parent_of(unit, "line") or unit
        if key in seen and idpos.level != "line":
            pass
        seen.add(key)
        by = "、".join(f"{n} {v:.2f}" for n, v in sorted(cr.by_store.items()))
        sp = "—" if cr.spread_pct is None else f'{cr.spread_pct:.0f}%'
        lm = "—" if cr.loo_mape is None else f'±{cr.loo_mape:.1f}%'
        ratio_rows.append(
            f'<tr><td class="l">{esc(idpos.leaf(unit))}</td>'
            f'<td><strong>{cr.ratio:.2f}倍</strong></td>'
            f'<td>{cr.lo:.2f}〜{cr.hi:.2f}</td><td>{sp}</td>'
            f'<td>{cr.n_stores}</td><td>{lm}</td>'
            f'<td class="l muted" style="font-size:11px">{esc(by)}</td></tr>'
        )

    wins = int((cmp_df["better"] == "転換前後法").sum())
    total = len(cmp_df)
    verdict = ("転換前後法を主に使ってください" if wins > total / 2
               else "この指標群では類似店法のほうが安定しています")
    warns = "".join(f'<div class="note warn">{esc(w)}</div>' for w in conv.warnings)

    return f"""
<h2>2-2. 転換前後法（同じ立地の転換前実績から）</h2>
<p class="sub">この新店は既存店の業態転換リニューアルなので、同じ立地の転換前実績が使えます。<br>
<strong>予測 = この店の転換前実績 × （既存{len(conv.paired_stores)}店で測った 転換後÷転換前 の比率）</strong><br>
立地・商圏・競合が同じ店の前後を比べるので、商圏の違いを推定する必要がありません。
推定するのは業態転換そのものの効果だけで、これは転換済みの既存店から直接測れます。</p>
<div class="note"><strong>実測誤差での勝敗: 転換前後法 {wins} / {total} 組み合わせ。{esc(verdict)}</strong><br>
類似店法の誤差はLOO（1店抜き）、転換前後法の誤差は「他店の変化率中央値でその店の変化率を当てたときの誤差」です。
どちらも既存店で実測した値です。</div>
{warns}

<h3>業態転換による変化率（既存{len(conv.paired_stores)}店の実測）</h3>
<p class="sub">数量PIについて。店舗間でばらつく分が、そのまま予測の不確実性になります。</p>
<table><thead><tr><th class="l">{esc(idpos.level_label())}</th><th>変化率<br>中央値</th>
<th>店舗間レンジ</th><th>ばらつき</th><th>店舗数</th><th>1店抜き誤差</th>
<th class="l">店舗別</th></tr></thead><tbody>{"".join(ratio_rows)}</tbody></table>

<h3>2つの方法の比較</h3>
<table><thead><tr><th class="l">ライン</th><th class="l">{esc(idpos.level_label())}</th>
<th class="l">指標</th><th>この店の<br>転換前実績</th><th>変化率</th>
<th>転換前後法<br>の予測</th><th>類似店法<br>の予測</th><th>差</th>
<th>採用</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<div class="note">2つの方法が大きく食い違う組み合わせは、<strong>その食い違い自体が警告</strong>です。
商圏から見た期待値と、その立地の実績が合っていないということなので、
どちらを採るかを決める前に理由（競合、売場、客層）を確かめてください。</div>
"""


def _sec_customers(a) -> str:
    am = a.age_mix
    if am is None:
        return ("<h2>3. 顧客層（年代別）</h2>"
                "<p class=\"muted\">IDPOSに年代の行が無いため、この分析はスキップしました。</p>")

    rows = "".join(
        f'<tr><td class="l">{esc(r.band)}</td>'
        f'<td><strong>{fpct(r.blended if r.blended is not None else r.peer_based)}</strong></td>'
        f'<td>{fpct(r.peer_based)}</td><td>{fpct(r.area_based)}</td>'
        f'<td>{fpct(r.area_share)}</td>'
        f'<td>{"—" if r.bias_mean is None else f"{r.bias_mean:.2f}倍"}</td>'
        f'<td class="muted">{"—" if r.bias_cv is None else f"±{r.bias_cv:.0%}"}</td></tr>'
        for r in am.rows
    )

    unit_rows = []
    for unit, u in sorted(a.age_mix_by_unit.items()):
        best = max(u.rows, key=lambda r: (r.blended or r.peer_based or 0))
        second = sorted(u.rows, key=lambda r: -(r.blended or r.peer_based or 0))[1:2]
        s = second[0] if second else None
        unit_rows.append(
            f'<tr><td class="l">{esc(a.idpos.parent_of(unit, "line") or "")}</td>'
            f'<td class="l">{esc(a.idpos.leaf(unit))}</td>'
            f'<td class="l">{esc(best.band)} '
            f'({fpct(best.blended if best.blended is not None else best.peer_based)})</td>'
            f'<td class="l">{esc(s.band) if s else "—"} '
            f'({fpct(s.blended if s and s.blended is not None else (s.peer_based if s else None))})</td>'
            f'<td class="muted" style="font-size:11px">'
            f'{esc("・".join(u.peer_names))}</td></tr>'
        )

    unk = a.idpos.age_unknown_share
    unk_txt = ""
    if unk:
        worst = max(unk.values())
        unk_txt = (f'<p class="sub">年代が取れていない売上の割合（既存店の最大）: '
                   f'{worst:.1%}。この分は構成比の計算から外しています。</p>')

    notes = "".join(f'<div class="note warn">{esc(n)}</div>' for n in am.notes)

    return f"""
<h2>3. 顧客層（年代別）</h2>
<p class="sub">IDPOSの年代別売上から算出。性別は実データに無いため使っていません。<br>
2つの方法で推定しています。どちらが当たるかはLOO検証で測りました。</p>
<div class="cards">
  <div class="card"><div class="k">類似店ベースの実測誤差</div>
    <div class="v">{"—" if am.loo_mae_peer_pt is None else f"±{am.loo_mae_peer_pt:.1f}pt"}</div>
    <div class="muted" style="font-size:11.5px">類似店の年代構成をそのまま使う</div></div>
  <div class="card"><div class="k">商圏補正ベースの実測誤差</div>
    <div class="v">{"—" if am.loo_mae_area_pt is None else f"±{am.loo_mae_area_pt:.1f}pt"}</div>
    <div class="muted" style="font-size:11.5px">商圏年齢構成 × 来店バイアス</div></div>
  <div class="card pick"><div class="k">採用すべき方法</div>
    <div class="v" style="font-size:16px">{esc(am.best_method)}</div>
    <div class="muted" style="font-size:11.5px">誤差が小さいほうを採用</div></div>
</div>
{notes}
{unk_txt}
<table><thead><tr><th class="l">年代</th><th>予測（両者の平均）</th>
<th>類似店ベース</th><th>商圏補正ベース</th><th>商圏の年齢構成</th>
<th>来店バイアス</th><th>店舗間のばらつき</th></tr></thead><tbody>{rows}</tbody></table>
<p class="sub">来店バイアス = 会員の年代構成比 ÷ 商圏の年齢構成比。既存店の平均。
1.0を超える年代は「商圏にいる以上に来ている」。ばらつきが大きい年代は、
この補正自体が当てになりません。</p>

<h3>カテゴリーごとの主購買層</h3>
<table><thead><tr><th class="l">ライン</th><th class="l">{esc(a.idpos.level_label())}</th>
<th class="l">最も多い年代</th><th class="l">2番目</th>
<th class="l">根拠にした類似店</th></tr></thead><tbody>{"".join(unit_rows)}</tbody></table>
<div class="note">棚割を動かすときは、この主購買層と商圏の年齢構成を突き合わせてください。
商圏に多い年代が主購買層のカテゴリーは伸ばす余地があり、
商圏に少ない年代が主購買層のカテゴリーは類似店ほど取れない可能性があります。</div>
"""


def _sec_accuracy(a) -> str:
    mcfg = a.mcfg
    rows = a.loo.summary_rows(mcfg)
    by_metric: dict[str, list[dict]] = {}
    for r in rows:
        by_metric.setdefault(r["metric"], []).append(r)

    mrows = []
    for met, rs in by_metric.items():
        vals = [r["mape"] for r in rs]
        bvals = [r["baseline_mape"] for r in rs if r["baseline_mape"] is not None]
        wins = sum(1 for r in rs if r["beats_baseline"])
        mean_v = sum(vals) / len(vals)
        base_v = f"{sum(bvals) / len(bvals):.1f}%" if bvals else "—"
        cls = "ok" if mean_v < 8 else ("warn" if mean_v < 15 else "bad")
        judge = ("類似店法を使う価値がある" if wins > len(rs) / 2
                 else "全店平均で代用したほうがよい")
        mrows.append(
            f'<tr><td class="l">{esc(mcfg.metric_label(met))}</td>'
            f'<td><span class="tag {cls}">±{mean_v:.1f}%</span></td>'
            f'<td>{base_v}</td><td>{wins} / {len(rs)}</td>'
            f'<td class="l">{esc(judge)}</td></tr>'
        )

    worst = sorted(rows, key=lambda r: -r["mape"])[:8]
    wrows = ""
    for r in worst:
        base_v = "—" if r["baseline_mape"] is None else f'{r["baseline_mape"]:.1f}%'
        wrows += (
            f'<tr><td class="l">{esc(a.idpos.leaf(r["unit"]))}</td>'
            f'<td class="l">{esc(r["metric_label"])}</td>'
            f'<td>±{r["mape"]:.1f}%</td><td>{base_v}</td>'
            f'<td>{r["n_folds"]}</td></tr>'
        )

    scan = ""
    if not a.level_scan.empty:
        ls = a.level_scan[a.level_scan["metric"] == "pi"]
        if not ls.empty:
            srows = ""
            for _, r in ls.iterrows():
                base_v = ("—" if pd.isna(r["全店平均の誤差%"])
                          else f'{r["全店平均の誤差%"]:.1f}%')
                cvv = ("—" if pd.isna(r["週次変動の中央値%"])
                       else f'±{r["週次変動の中央値%"]:.1f}%')
                srows += (
                    f'<tr><td class="l">{esc(r["粒度"])}</td><td>{int(r["単位数"])}</td>'
                    f'<td>±{r["LOO平均誤差%"]:.1f}%</td><td>{base_v}</td>'
                    f'<td>{cvv}</td></tr>'
                )
            scan = f"""
<h3>どの粒度まで下げられるか</h3>
<p class="sub">粒度を下げるほど提案は具体的になりますが、1単位あたりの数字が小さくなり、
5店舗では当たらなくなります。その境目を実測したものです（指標: PI値）。</p>
<table><thead><tr><th class="l">粒度</th><th>単位数</th><th>LOO平均誤差</th>
<th>全店平均の誤差</th><th>週次変動（1店の中のブレ）</th></tr></thead>
<tbody>{srows}</tbody></table>
<div class="note">「週次変動」は同じ店の同じカテゴリーが週ごとにどれだけ振れるかです。
<strong>LOO誤差がこれを下回ることは原理的にありません</strong>。
LOO誤差が週次変動に近い粒度までは使えますが、大きく上回る粒度では
予測しているのではなく店舗差のノイズを拾っているだけです。</div>
"""

    dirs = ""
    for met, dr in a.directions.items():
        if not dr.findings:
            continue
        strong = [f for f in dr.findings if f.strength == "根拠あり"][:12]
        if not strong:
            continue
        frows = "".join(
            f'<tr><td class="l">{esc(f.var_label)}</td>'
            f'<td class="l">{esc(a.idpos.leaf(f.unit))}</td>'
            f'<td><span class="tag {"ok" if f.direction == "正" else "warn"}">{esc(f.direction)}</span></td>'
            f'<td>{f.n_same_direction} / {len(a.stores)}</td>'
            f'<td>{f.concordant} / {f.total_pairs}</td>'
            f'<td>{f.median_slope * 100:+.1f}%</td></tr>'
            for f in strong
        )
        dirs += f"""
<h4>{esc(mcfg.metric_label(met))} と商圏変数の関係</h4>
<p class="sub">{esc(dr.note)}</p>
<table><thead><tr><th class="l">商圏変数</th><th class="l">{esc(a.idpos.level_label())}</th>
<th>方向</th><th>同傾向の店舗数</th><th>一致ペア</th><th>1σあたりの変化</th></tr></thead>
<tbody>{frows}</tbody></table>"""

    c = a.confidence
    out = [i for i in a.ranges if i.out_of_range]
    orows = "".join(
        f'<tr><td class="l">{esc(i.label)}</td>'
        f'<td class="l">{esc(a.cfg.axes[i.axis]["label"].split("（")[0]) if i.axis else "参考"}</td>'
        f'<td>{fnum(i.value, 2)}</td><td>{fnum(i.vmin, 2)}</td><td>{fnum(i.vmax, 2)}</td>'
        f'<td><span class="tag {"bad" if i.axis else "warn"}">{esc(i.status)}</span></td></tr>'
        for i in out
    ) or '<tr><td colspan="6" class="l muted">範囲外の指標はありません</td></tr>'

    warn_html = "".join(f'<div class="note warn">{esc(w)}</div>' for w in a.warnings)
    ccls = {"高": "note", "中〜高": "note", "中": "note warn", "低": "note bad"}.get(
        c["level"], "note warn")

    # 立ち上がりカーブ
    ramp_tbl = ""
    r = getattr(a, "ramp", None)
    if r is not None and r.curves:
        rc = r.curves.get("pi")
        if rc is not None and rc.usable:
            frows = "".join(
                f'<tr><td>{w}週目</td><td>{rc.factors[w]:.2f}</td>'
                f'<td>{"—" if rc.spread.get(w) is None or pd.isna(rc.spread.get(w)) else f"±{rc.spread[w]:.0%}"}</td></tr>'
                for w in sorted(rc.factors)
            )
            arows = "".join(
                f'<tr><td class="l">{esc(x.store_name)}</td>'
                f'<td class="l">{esc(a.idpos.leaf(x.unit))}</td>'
                f'<td>{x.observed:,.2f}</td><td>{x.factor:.3f}</td>'
                f'<td><strong>{x.adjusted:,.2f}</strong></td>'
                f'<td>{x.lift_pct:+.1f}%</td></tr>'
                for x in r.adjustments if x.metric == "pi"
            )
            ramp_tbl = f"""
<h3>開店からの立ち上がりと、未成熟店の補正</h3>
<p class="sub">成熟期（{rc.mature_from}〜{rc.mature_to}週）に届いた{rc.n_contributors}店
（{esc("・".join(rc.contributors))}）から、開店何週目に成熟水準の何倍かを測りました。
開店から日が浅い店は、これで割り戻して成熟水準を推定し、予測の材料に戻しています。</p>
<div class="cards"><div class="card" style="flex:0 0 280px">
<table><thead><tr><th>開店からの週</th><th>成熟水準比</th><th>店舗間のばらつき</th></tr></thead>
<tbody>{frows}</tbody></table></div>
<div class="card" style="flex:1 1 420px">
<table><thead><tr><th class="l">店舗</th><th class="l">単位</th><th>観測</th>
<th>係数</th><th>補正後</th><th>差</th></tr></thead><tbody>{arows}</tbody></table>
</div></div>
<div class="note warn">補正後の値は<strong>実測ではなく推定</strong>です。
カーブを測れた店が{rc.n_contributors}店しかないうちは、形がその店の個性に引きずられます。
補正を当てた店が類似店に選ばれた場合、予測はその分だけ不確かになります。</div>
"""
    if not ramp_tbl:
        ramp_tbl = ('<div class="note warn">立ち上がりカーブを推定できませんでした'
                    '（成熟期まで到達した店が無い、または open_date が未入力）。'
                    '開店から日の浅い店は水準が測れず、予測の材料から外れます。</div>')

    mt = a.idpos.maturity
    if mt is None or mt.empty:
        maturity_tbl = ('<div class="note warn">商圏マスタに open_date が無いため、'
                        '開店直後の週を除外できていません。'
                        '転換時期が店ごとに違う場合、水準の比較が歪みます。</div>')
    else:
        names = a.idpos.store_names
        mrows2 = "".join(
            f'<tr><td class="l">{esc(names.get(str(r["store_id"]), r["store_id"]))}</td>'
            f'<td>{esc(r["open_date"] or "—")}</td>'
            f'<td>{"—" if pd.isna(r["weeks_since_open"]) else int(r["weeks_since_open"])}</td>'
            f'<td>{int(r["weeks_excluded"])}</td>'
            f'<td><strong>{int(r["weeks_used"])}</strong></td>'
            f'<td><span class="tag {"bad" if r["weeks_used"] < 8 else "ok"}">'
            f'{"水準が不安定" if r["weeks_used"] < 8 else "使える"}</span></td></tr>'
            for _, r in mt.sort_values("weeks_used", ascending=False).iterrows()
        )
        maturity_tbl = (
            '<table><thead><tr><th class="l">店舗</th><th>オープン日</th>'
            '<th>経過週</th><th>除外した週</th><th>水準に使った週</th>'
            '<th>判定</th></tr></thead>'
            f'<tbody>{mrows2}</tbody></table>'
        )

    return f"""
<h2>4. 数字の根拠と正確性</h2>

<h3>既存店の成熟度</h3>
<p class="sub">転換・開店の時期が店によって違います。開店直後は需要が跳ねるため、
各店のオープンから一定週を水準の計算から外しています。
残った週が少ない店は、水準そのものが不安定です。</p>
{maturity_tbl}

{ramp_tbl}

<h3>LOO検証（1店抜きの実測誤差）</h3>
<p class="sub">既存{len(a.stores)}店を1店ずつ「新店だと思って」残りから予測し、実績と比べました。
これが本ツールで出せる唯一の正直な精度です。検証点は指標あたり
{len(a.stores)} × {len(a.idpos.units)}単位 = {len(a.stores) * len(a.idpos.units)}件。</p>
<table><thead><tr><th class="l">指標</th><th>類似店法の誤差</th><th>全店平均の誤差</th>
<th>勝った単位数</th><th class="l">判定</th></tr></thead><tbody>{"".join(mrows)}</tbody></table>

<h4>特に当たっていない組み合わせ</h4>
<table><thead><tr><th class="l">{esc(a.idpos.level_label())}</th><th class="l">指標</th>
<th>LOO誤差</th><th>全店平均の誤差</th><th>検証点</th></tr></thead><tbody>{wrows}</tbody></table>
<div class="note bad">ここに出ているものは、予測値を信じないでください。
既存店ですら当てられていない組み合わせです。</div>
{scan}

<h3>新店の指標が既存店の範囲に収まっているか</h3>
<div class="{ccls}"><strong>総合的な信頼度: {esc(c["level"])}</strong><br>{esc(c["message"])}</div>
<table><thead><tr><th class="l">指標</th><th class="l">用途</th><th>新店</th>
<th>既存 最小</th><th>既存 最大</th><th>判定</th></tr></thead><tbody>{orows}</tbody></table>

<h3>商圏変数とカテゴリーの関係（仮説）</h3>
{dirs or '<p class="muted">基準を満たす組み合わせはありませんでした。</p>'}
{warn_html}
"""


def _sec_risk(a) -> str:
    shown = [r for r in a.risks if r.level != "—"]
    if not shown:
        return ("<h2>5. 苦戦が予想されるカテゴリー</h2>"
                "<p>判定ルールに引っかかるカテゴリーはありませんでした。</p>")
    rows = []
    for r in shown:
        cls = {"要対策": "bad", "警戒": "warn", "注意": ""}[r.level]
        ccls = {"低": "bad", "中": "warn", "高": "ok"}[r.confidence]
        fl = "".join(
            f'<li><span class="tag {"bad" if f.severity >= 2 else "warn"}">{esc(f.kind)}</span> '
            f'{esc(f.message)}</li>' for f in r.business_flags
        ) or '<li class="muted">—</li>'
        ul = "".join(f'<li>{esc(f.message)}</li>' for f in r.uncertainty_flags)
        ul_html = (f'<details><summary>この判定の不確かさ（{len(r.uncertainty_flags)}件）</summary>'
                   f'<ul style="margin:4px 0;padding-left:16px;font-size:11.5px">{ul}</ul></details>'
                   if ul else "")
        rows.append(
            f'<tr><td class="l">{esc(a.idpos.parent_of(r.unit, "line") or r.line)}</td>'
            f'<td class="l"><strong>{esc(a.idpos.leaf(r.unit))}</strong></td>'
            f'<td><span class="tag {cls}">{esc(r.level)}</span></td>'
            f'<td><span class="tag {ccls}">{esc(r.confidence)}</span></td>'
            f'<td class="l"><ul style="margin:0;padding-left:16px">{fl}</ul>{ul_html}</td>'
            f'<td class="l">{esc(r.action)}</td></tr>'
        )
    n_low = sum(1 for r in shown if r.confidence == "低")
    return f"""
<h2>5. 苦戦が予想されるカテゴリー</h2>
<p class="sub">「苦戦」は商売上のリスク（水準が低い／商圏が範囲外／競合が重い／粗利貢献が小さい）だけで
判定しています。<strong>「予測が読めない」ことは苦戦ではない</strong>ので、別列の「判定の確からしさ」に分けました。</p>
<table><thead><tr><th class="l">ライン</th><th class="l">{esc(a.idpos.level_label())}</th>
<th>苦戦</th><th>判定の<br>確からしさ</th><th class="l">根拠</th>
<th class="l">開店前にやること</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<div class="note warn">「苦戦」は<strong>既存店と比べて低く出る見込み</strong>であって、赤字になるという意味ではありません。<br>
確からしさ「低」が{n_low}件あります。これらはLOO誤差や週次のブレが大きく、
<strong>判定そのものが当てになりません</strong>。開店後の実績で確認してください。</div>
"""


def _sec_followup(a) -> str:
    v = a.mcfg.verification
    rows = "".join(
        f'<tr><td><span class="tag {"bad" if c["priority"] == "最優先" else "warn" if c["priority"] == "高" else ""}">'
        f'{esc(c["priority"])}</span></td><td class="l">{esc(c["item"])}</td>'
        f'<td class="l">{esc(c["timing"])}</td><td class="l">{esc(c["action"])}</td></tr>'
        for c in a.checklist
    )
    rg = ""
    if a.rural_gaps:
        grows = "".join(
            f'<tr><td class="l">{esc(a.idpos.leaf(r["category"]))}</td>'
            f'<td>{fnum(r["rural_share"], 2)}</td><td>{fnum(r["urban_mean"], 2)}</td>'
            f'<td>{fnum(r["urban_min"], 2)}〜{fnum(r["urban_max"], 2)}</td>'
            f'<td>{r["diff_pt"]:+.1f}%</td>'
            f'<td class="l"><span class="tag {"warn" if not r["inside_urban_range"] else ""}">'
            f'{esc(r["note"])}</span></td></tr>'
            for r in a.rural_gaps[:10]
        )
        rg = f"""
<h3>郊外店 vs 都市部店（参考）</h3>
<table><thead><tr><th class="l">{esc(a.idpos.level_label())}</th><th>郊外店</th>
<th>都市部 平均</th><th>都市部 レンジ</th><th>差</th><th class="l">判定</th></tr></thead>
<tbody>{grows}</tbody></table>
<div class="note bad">郊外店は1店しかないため、ここの差は「郊外という立地の効果」と
「その1店の個性」を区別できていません。</div>"""

    return f"""
<h2>6. 開店後の答え合わせと精度向上</h2>
<div class="note"><strong>やり方</strong><br>
1. 本レポート作成時に予測が <code>output/predictions_*.json</code> に保存されています。<br>
2. 新店の週次IDPOSがたまったら <code>python run_report.py verify</code> を実行します。<br>
3. 開店直後の{v.get("exclude_first_weeks", 2)}週はご祝儀需要が乗るため自動で除外され、
{v.get("min_weeks_for_judgement", 4)}週未満は「判定保留」になります。<br>
4. 開店月の実績は、既存店の同じ月の季節指数で補正してから比較します。<br>
5. 予測から外れていても、週次のブレで説明できる範囲なら「ブレの範囲」と判定します。
   毎週データを足すほど、この幅が狭まって判定が確定します。</div>
<table><thead><tr><th>優先度</th><th class="l">やること</th><th class="l">タイミング</th>
<th class="l">ズレていたときの打ち手</th></tr></thead><tbody>{rows}</tbody></table>
{rg}
"""

# ---------------------------------------------------------------- 出力

def render_html(a) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    n_weeks = int(a.idpos.panel.groupby("store_id")["week"].nunique().median()) \
        if len(a.idpos.panel) else 0
    inputs = (f"既存店 {len(a.stores)}店 ／ 週次IDPOS 中央値{n_weeks}週 ／ "
              f"分析粒度 {esc(a.idpos.level_label())} ／ 商圏定義 {esc(a.cfg.trade_area_definition)}")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>新店 低温カテゴリー予測レポート｜{esc(a.new_name)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
<h1>新店 低温カテゴリー予測レポート — {esc(a.new_name)}</h1>
<p class="sub">{inputs} ／ 作成 {now}</p>
</header>
<div class="note bad">{DISCLAIMER}</div>
{_sec_summary(a)}
{_sec_predictions(a)}
{_sec_methods(a)}
{_sec_customers(a)}
{_sec_accuracy(a)}
{_sec_risk(a)}
{_sec_followup(a)}
<footer>
shoken_analyzer v0.2.0 が自動生成しました。
項目定義・軸の重み・判定閾値は config/columns.yaml、
対象カテゴリーと指標は config/metrics.yaml で変更できます。<br>
既存店が{len(a.stores)}店である限り、本レポートの予測は仮説です。
開店後のIDPOSで必ず検証してください。
</footer>
</div></body></html>"""


def write_html(a, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(a), encoding="utf-8")
    return path


def render_verify_html(v, mcfg) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    cls = {"想定内": "ok", "ブレの範囲": "", "上振れ": "warn",
           "下振れ": "bad", "判定保留": "", "データなし": ""}
    rows = "".join(
        f'<tr><td class="l">{esc(r.unit.split(" > ")[0])}</td>'
        f'<td class="l"><strong>{esc(r.unit.split(" > ")[-1])}</strong></td>'
        f'<td class="l">{esc(r.metric_label)}</td>'
        f'<td>{fnum(r.predicted, 2)}</td>'
        f'<td>{fnum(r.pred_low, 2)}〜{fnum(r.pred_high, 2)}</td>'
        f'<td>{fnum(r.actual_raw, 2)}</td>'
        f'<td>{fnum(r.actual_adjusted, 2)}'
        f'<br><span class="muted" style="font-size:11px">季節指数 {fnum(r.season_index, 2)}</span></td>'
        f'<td>{"—" if r.diff_pct is None else f"{r.diff_pct:+.1f}%"}</td>'
        f'<td><span class="tag {cls.get(r.verdict, "")}">{esc(r.verdict)}</span></td>'
        f'<td class="l" style="font-size:11.5px">{esc(r.comment)}</td></tr>'
        for r in v.rows
    )
    summary = "／".join(f"{k} {n}件" for k, n in v.summary.items())
    warns = "".join(f'<div class="note warn">{esc(w)}</div>' for w in v.warnings)
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>予実検証｜{esc(v.store_name)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><h1>予実検証 — {esc(v.store_name)}</h1>
<p class="sub">予測作成 {esc(v.predicted_at)} ／ 実績 {v.weeks_observed}週
（うち開店直後{v.excluded_first_weeks}週を除外） ／ 検証 {now}</p></header>
<div class="note"><strong>内訳: {esc(summary)}</strong><br>
実績は「素の平均」と「季節補正後」の両方を出しています。
予測は既存店の年間平均から作られているので、比較すべきは<strong>季節補正後</strong>です。</div>
{warns}
<table><thead><tr><th class="l">ライン</th><th class="l">単位</th><th class="l">指標</th>
<th>予測</th><th>予測区間</th><th>実績（素）</th><th>実績（季節補正後）</th>
<th>差</th><th>判定</th><th class="l">コメント</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="note">「下振れ」が出たカテゴリーは、品揃え・売価・棚位置・競合のどれが
効いているかを切り分けてください。「ブレの範囲」は週数が増えれば確定します。<br>
週次IDPOSを足して再実行するたびに、判定は精度を増します。
26週以上たまったら、この店を既存店マスタに6店目として追加してください。</div>
<footer>shoken_analyzer v0.2.0</footer>
</div></body></html>"""


def write_verify_html(v, mcfg, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_verify_html(v, mcfg), encoding="utf-8")
    return path


def write_excel(a, path: str | Path) -> Path:
    """同じ内容をExcelで出す。シートはレポートの章立てに対応。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg, mcfg, idpos = a.cfg, a.mcfg, a.idpos

    disclaimer = pd.DataFrame([{"注意事項": t} for t in [
        "本レポートの数値は既存店の実績の加重平均であり、モデルの予測値ではありません。",
        "店舗数 < 商圏変数の数のため、回帰・GBM等の学習は行っていません。",
        "週次IDPOSを増やしても店舗数は増えないため、この制約は解消しません。",
        "「正確性」はすべてLOO（1店抜き）検証で実測した誤差です。",
        "郊外店が1店のみのため、郊外寄りの新店に対する根拠は特に弱くなります。",
        "開店後のIDPOSによる検証を前提に、初期値の出発点としてお使いください。",
    ]])
    summary = pd.DataFrame([
        {"項目": "新店", "値": a.new_name},
        {"項目": "分析粒度", "値": idpos.level_label()},
        {"項目": "商圏定義", "値": cfg.trade_area_definition},
        {"項目": "都市部/郊外 判定", "値": a.urban_new.verdict},
        {"項目": "都市度スコア", "値": a.urban_new.score},
        {"項目": "採用した類似店", "値": "・".join(p.store_name for p in a.peers)},
        {"項目": "信頼度", "値": a.confidence["level"]},
        {"項目": "信頼度の根拠", "値": a.confidence["message"]},
        *[{"項目": f"軸スコア {cfg.axes[ax]['label']}", "値": v}
          for ax, v in a.similarity.new_axis_profile.items()],
        *[{"項目": "注意", "値": w} for w in a.warnings],
    ])
    ranking = pd.DataFrame([
        {"順位": s.rank, "店舗": s.store_name, "類似度": s.similarity, "距離": s.distance,
         "立地タイプ": a.urban_stores[s.store_id].verdict,
         **{f"距離_{ax}": s.axis_distance.get(ax) for ax in cfg.axes},
         "採用": "○" if s.store_id in {p.store_id for p in a.peers} else ""}
        for s in a.similarity.ranking
    ])
    preds = pd.DataFrame([
        {"ライン": idpos.parent_of(p.unit, "line"), "単位": idpos.leaf(p.unit),
         "パス": p.unit, "指標": mcfg.metric_label(p.metric),
         "予測": p.point, "区間下限": p.low, "区間上限": p.high,
         "類似店レンジ下限": p.peer_low, "類似店レンジ上限": p.peer_high,
         "既存全店 中央値": p.all_median, "既存全店 最小": p.all_low,
         "既存全店 最大": p.all_high,
         "LOO誤差%": p.loo_mape, "全店平均の誤差%": p.loo_baseline_mape,
         "週次変動": p.weekly_cv, "最小週数": p.n_weeks_min,
         "類似店": "・".join(f"{c.store_name}({c.weight:.0%})" for c in p.peers),
         "根拠": p.basis}
        for p in a.predictions
    ])
    loo_rows = pd.DataFrame([
        {"対象店": f.held_out_name, "単位": idpos.leaf(f.unit), "パス": f.unit,
         "指標": mcfg.metric_label(f.metric), "実績": f.actual,
         "類似店法の予測": f.predicted, "誤差%": f.err_pct,
         "全店平均の予測": f.baseline, "全店平均の誤差%": f.baseline_err_pct,
         "採用した類似店": "・".join(f.peers)}
        for f in a.loo.folds
    ])
    ranges = pd.DataFrame([
        {"指標": i.label, "軸": i.axis or "参考", "新店": i.value,
         "既存最小": i.vmin, "既存最大": i.vmax, "判定": i.status}
        for i in a.ranges
    ])
    risks = pd.DataFrame([
        {"ライン": r.line, "単位": idpos.leaf(r.unit), "パス": r.unit,
         "苦戦判定": r.level, "リスク点": r.score,
         "判定の確からしさ": r.confidence, "不確実点": r.uncertainty,
         "根拠": " / ".join(f"[{f.kind}] {f.message}" for f in r.business_flags),
         "不確かさ": " / ".join(f.message for f in r.uncertainty_flags),
         "開店前にやること": r.action}
        for r in a.risks if r.level != "—"
    ])
    age = pd.DataFrame([
        {"年代": r.band,
         "予測": r.blended if r.blended is not None else r.peer_based,
         "類似店ベース": r.peer_based, "商圏補正ベース": r.area_based,
         "商圏の年齢構成": r.area_share, "来店バイアス": r.bias_mean,
         "店舗間ばらつき": r.bias_cv}
        for r in (a.age_mix.rows if a.age_mix else [])
    ])
    age_unit = pd.DataFrame([
        {"ライン": idpos.parent_of(u, "line"), "単位": idpos.leaf(u), "年代": r.band,
         "予測": r.blended if r.blended is not None else r.peer_based}
        for u, am in sorted(a.age_mix_by_unit.items()) for r in am.rows
    ])
    directions = pd.DataFrame([
        {"指標": mcfg.metric_label(met), "商圏変数": f.var_label,
         "単位": idpos.leaf(f.unit), "方向": f.direction,
         "同傾向の店舗数": f.n_same_direction, "一致ペア": f.concordant,
         "総ペア": f.total_pairs, "1σあたり変化%": f.median_slope * 100,
         "根拠": f.strength, "同じ動きの変数": "、".join(f.cluster_members)}
        for met, dr in a.directions.items() for f in dr.findings
    ])
    checklist = pd.DataFrame(a.checklist)
    conv_ratio = pd.DataFrame([
        {"単位": idpos.leaf(cr.unit), "パス": cr.unit,
         "指標": mcfg.metric_label(cr.metric), "変化率中央値": cr.ratio,
         "店舗間最小": cr.lo, "店舗間最大": cr.hi, "店舗数": cr.n_stores,
         "1店抜き誤差%": cr.loo_mape,
         **{f"倍率_{n}": v for n, v in cr.by_store.items()}}
        for cr in (a.conversion.ratios.values() if a.conversion else [])
    ])
    maturity = (a.idpos.maturity.assign(
        店舗=lambda d: d["store_id"].map(lambda x: idpos.store_names.get(str(x), x)))
        if a.idpos.maturity is not None and not a.idpos.maturity.empty
        else pd.DataFrame())

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        disclaimer.to_excel(xw, sheet_name="0_注意事項", index=False)
        summary.to_excel(xw, sheet_name="1_商圏と類似店", index=False)
        ranking.to_excel(xw, sheet_name="1b_類似店ランキング", index=False)
        preds.to_excel(xw, sheet_name="2_予測", index=False)
        if not conv_ratio.empty:
            conv_ratio.to_excel(xw, sheet_name="2b_転換前後の変化率", index=False)
        if not a.method_compare.empty:
            a.method_compare.to_excel(xw, sheet_name="2c_手法比較", index=False)
        if not age.empty:
            age.to_excel(xw, sheet_name="3_顧客層", index=False)
        if not age_unit.empty:
            age_unit.to_excel(xw, sheet_name="3b_カテゴリ別顧客層", index=False)
        loo_rows.to_excel(xw, sheet_name="4_LOO検証", index=False)
        if not a.level_scan.empty:
            a.level_scan.to_excel(xw, sheet_name="4b_粒度別精度", index=False)
        ranges.to_excel(xw, sheet_name="4c_範囲チェック", index=False)
        if not maturity.empty:
            maturity.to_excel(xw, sheet_name="4e_店舗の成熟度", index=False)
        if not directions.empty:
            directions.to_excel(xw, sheet_name="4d_方向性仮説", index=False)
        if not risks.empty:
            risks.to_excel(xw, sheet_name="5_苦戦予想", index=False)
        checklist.to_excel(xw, sheet_name="6_チェックリスト", index=False)
    return path


def write_verify_excel(v, mcfg, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([
        {"ライン": r.unit.split(" > ")[0], "単位": r.unit.split(" > ")[-1],
         "パス": r.unit, "指標": r.metric_label,
         "予測": r.predicted, "区間下限": r.pred_low, "区間上限": r.pred_high,
         "実績(素)": r.actual_raw, "実績(季節補正後)": r.actual_adjusted,
         "季節指数": r.season_index, "差%": r.diff_pct,
         "週次ノイズ許容幅%": r.noise_pct, "使用週数": r.n_weeks_used,
         "判定": r.verdict, "コメント": r.comment}
        for r in v.rows
    ])
    meta = pd.DataFrame([{"項目": "店舗", "値": v.store_name},
                         {"項目": "予測作成", "値": v.predicted_at},
                         {"項目": "実績週数", "値": v.weeks_observed},
                         {"項目": "除外した開店直後の週数", "値": v.excluded_first_weeks},
                         *[{"項目": "注意", "値": w} for w in v.warnings]])
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        meta.to_excel(xw, sheet_name="0_前提", index=False)
        df.to_excel(xw, sheet_name="1_予実", index=False)
    return path
