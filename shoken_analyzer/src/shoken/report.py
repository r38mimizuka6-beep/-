"""HTML / Excel レポートの出力。"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import pandas as pd

from .pipeline import Analysis

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
本レポートは既存5店舗の商圏データと売上実績だけを根拠にした<strong>仮説</strong>です。
店舗数が変数の数より少ないため、回帰やGBMなどの統計モデルによる「商圏→売上構成比」の
予測は行っていません。出している数値はすべて
<strong>(1) 類似店の実績値</strong>、<strong>(2) 店舗間の差の方向</strong>、
<strong>(3) 既存店の実績レンジ</strong> のいずれかであり、統計的に検定された予測値ではありません。
開店後のIDPOSによる検証を前提に、初期値の出発点としてお使いください。
"""


# ---------------------------------------------------------------- 各セクション

def _sec_summary(a: Analysis) -> str:
    cfg, u = a.cfg, a.urban_new
    rows = []
    for axis, spec in cfg.axes.items():
        z = a.similarity.new_axis_profile.get(axis)
        w = a.similarity.weights[axis]
        vars_used = "、".join(cfg.label(k) for k in spec["variables"])
        if z is None:
            reading = "この軸の入力が揃っていません"
        elif z > 0.75:
            reading = "既存5店の中では明確に高い側"
        elif z > 0.25:
            reading = "やや高い側"
        elif z > -0.25:
            reading = "既存5店の平均付近"
        elif z > -0.75:
            reading = "やや低い側"
        else:
            reading = "既存5店の中では明確に低い側"
        rows.append(
            f'<tr><td class="l">{esc(spec["label"])}</td>'
            f'<td class="l">{_axis_bar(z)}</td>'
            f'<td class="l">{esc(reading)}</td>'
            f"<td>{w:.2f}</td>"
            f'<td class="l muted">{esc(vars_used)}</td></tr>'
        )

    ucls = {"都市部寄り": "ok", "中間": "warn", "郊外寄り": "bad"}.get(u.verdict, "warn")
    detail_rows = []
    for d in u.details:
        score = "—" if d["score"] is None else f'{d["score"]:.2f}'
        detail_rows.append(
            f'<tr><td class="l">{esc(d["label"])}</td><td>{fnum(d["value"], 2)}</td>'
            f'<td>{fnum(d.get("rural_at"), 0)}</td><td>{fnum(d.get("urban_at"), 0)}</td>'
            f'<td>{score}</td><td>{d["weight"]:.1f}</td></tr>'
        )
    detail = "".join(detail_rows)

    rural_n = sum(1 for x in a.urban_stores.values() if x.is_rural)
    urban_n = sum(1 for x in a.urban_stores.values() if x.verdict == "都市部寄り")

    return f"""
<h2>1. 新店の商圏サマリ</h2>
<p class="sub">商圏定義: {esc(cfg.trade_area_definition)} ／ 対象: {esc(a.new_name)}</p>
<div class="cards">
  <div class="card"><div class="k">都市部／郊外の判定</div>
    <div class="v"><span class="tag {ucls}">{esc(u.verdict)}</span></div>
    <div class="muted" style="font-size:11.5px">都市度スコア {fnum(u.score, 2)}（1.00=都市部／0.00=郊外）
    ・判定に使えた指標 {u.used_rules}/{u.total_rules}</div></div>
  <div class="card"><div class="k">既存店の内訳</div>
    <div class="v">都市部 {urban_n} ／ 郊外 {rural_n}</div>
    <div class="muted" style="font-size:11.5px">全{len(a.stores)}店</div></div>
  <div class="card"><div class="k">判定の信頼度</div>
    <div class="v">{esc(a.confidence["level"])}</div>
    <div class="muted" style="font-size:11.5px">範囲外 {a.confidence["n_out"]} / {a.confidence["n_checked"]} 指標</div></div>
</div>

<h3>3軸での位置づけ</h3>
<p class="sub">値は既存5店を基準にした標準化スコア（0=5店平均、±1=標準偏差1つ分）。</p>
<table><thead><tr><th class="l">軸</th><th class="l">新店の位置（低 ← 5店平均 → 高）</th>
<th class="l">読み方</th><th>重み</th><th class="l">構成変数</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>

<details><summary>都市部／郊外 判定の内訳を見る</summary>
<table><thead><tr><th class="l">指標</th><th>新店の値</th><th>郊外の目安</th>
<th>都市部の目安</th><th>スコア</th><th>重み</th></tr></thead>
<tbody>{detail}</tbody></table>
<p class="sub">各指標を「郊外の目安=0／都市部の目安=1」に線形変換し、重み付き平均したものが都市度スコアです。
閾値は config/columns.yaml の urban_rural で変更できます。</p></details>
"""


def _sec_ranking(a: Analysis) -> str:
    cfg = a.cfg
    picked = {p.store_id for p in a.peers}
    head = "".join(f'<th>{esc(cfg.axes[ax]["label"].split("（")[0])}</th>' for ax in cfg.axes)
    rows = []
    for s in a.similarity.ranking:
        tag = '<span class="tag pick">採用</span>' if s.store_id in picked else ""
        ud = a.urban_stores.get(s.store_id)
        axis_cells = "".join(
            f'<td>{fnum(s.axis_distance.get(ax), 2)}<br>'
            f'<span class="muted" style="font-size:11px">{esc(s.coverage.get(ax, ""))}</span></td>'
            for ax in cfg.axes
        )
        rows.append(
            f'<tr><td>{s.rank}</td><td class="l">{esc(s.store_name)} {tag}</td>'
            f'<td class="l"><span class="tag">{esc(ud.verdict if ud else "—")}</span></td>'
            f"<td><strong>{s.similarity:.1f}</strong></td><td>{s.distance:.3f}</td>{axis_cells}</tr>"
        )

    reasons = []
    for p in a.peers:
        gaps = p.top_gaps(cfg, a.similarity.z_new, a.similarity.z_stores[p.store_id])
        close = [(ax, d) for ax, d in p.axis_distance.items() if d is not None]
        close.sort(key=lambda t: t[1])
        near = "、".join(f"{cfg.axes[ax]['label'].split('（')[0]}（距離{d:.2f}）" for ax, d in close[:2])
        gl = "".join(
            f'<li>{esc(g["label"])}：{esc(g["direction"])}（差 {g["z_gap"]:.2f}σ'
            f'{"／" + esc(cfg.axes[g["axis"]]["label"].split("（")[0]) if g["axis"] else ""}）</li>'
            for g in gaps
        )
        reasons.append(
            f'<h3>第{p.rank}位: {esc(p.store_name)}（類似度 {p.similarity:.1f}）</h3>'
            f"<p><strong>近い理由</strong>: {esc(near)} が新店に最も近い。</p>"
            f"<p><strong>残っている差（上位4件）</strong>:</p><ul>{gl}</ul>"
        )

    wtxt = "、".join(f"{cfg.axes[k]['label'].split('（')[0]}={v:.2f}" for k, v in a.similarity.weights.items())
    skipped = ""
    if a.similarity.skipped:
        skipped = (f'<div class="note warn">既存店間でばらつきが無く、距離計算から外した変数: '
                   f'{esc("、".join(cfg.label(k) for k in a.similarity.skipped))}</div>')

    return f"""
<h2>2. 類似店ランキングと選定理由</h2>
<p class="sub">距離は3軸の標準化距離（小さいほど似ている）。類似度は 100/(1+距離)。軸の重み: {esc(wtxt)}</p>
{skipped}
<table><thead><tr><th>順位</th><th class="l">既存店</th><th class="l">立地タイプ</th>
<th>類似度</th><th>距離</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<p class="sub">軸ごとのセルは「距離（上段）／計算に使えた変数の数（下段）」です。</p>
{"".join(reasons)}
<div class="note">類似度の数値そのものに統計的な意味はありません。5店の中での相対的な近さの順位だけを使ってください。
重みを変えると順位が入れ替わる場合があります（<code>--weight</code> オプションで再計算できます）。</div>
"""


def _sec_range(a: Analysis) -> str:
    c = a.confidence
    out = [i for i in a.ranges if i.out_of_range]
    missing = [i for i in a.ranges if i.status in ("新店データなし", "既存店データなし")]

    cls = {"高": "note", "中〜高": "note", "中": "note warn", "低": "note bad"}.get(c["level"], "note warn")
    if out:
        rows = "".join(
            f'<tr><td class="l">{esc(i.label)}</td>'
            f'<td class="l">{esc(a.cfg.axes[i.axis]["label"].split("（")[0]) if i.axis else "<span class=muted>参考</span>"}</td>'
            f"<td>{fnum(i.value, 2)}</td><td>{fnum(i.vmin, 2)}</td><td>{fnum(i.vmax, 2)}</td>"
            f'<td><span class="tag {"bad" if i.axis else "warn"}">{esc(i.status)}</span></td>'
            f"<td>{'∞' if i.overshoot == float('inf') else f'{i.overshoot:.2f}'}</td></tr>"
            for i in out
        )
        table = f"""<table><thead><tr><th class="l">指標</th><th class="l">用途</th>
<th>新店の値</th><th>既存5店 最小</th><th>既存5店 最大</th><th>判定</th><th>はみ出し幅</th>
</tr></thead><tbody>{rows}</tbody></table>
<p class="sub">「はみ出し幅」は既存5店のレンジ幅を1としたときの超過量。1.0なら、レンジと同じ幅だけ外側にあるという意味です。</p>"""
    else:
        table = "<p>範囲外の指標はありません。</p>"

    miss = ""
    if missing:
        miss = (f'<div class="note warn">値が埋まっていない指標が{len(missing)}件あります: '
                f'{esc("、".join(i.label for i in missing[:12]))}'
                f'{" ほか" if len(missing) > 12 else ""}。'
                "これらは類似度・範囲チェックの対象外です。埋めるほど判定は安定します。</div>")

    warn_html = "".join(f'<div class="note warn">{esc(w)}</div>' for w in a.warnings)

    return f"""
<h2>3. 範囲外の指標と信頼度</h2>
<div class="{cls}"><strong>総合的な信頼度: {esc(c["level"])}</strong><br>{esc(c["message"])}</div>
{warn_html}
<h3>既存5店の最小〜最大から外れた指標</h3>
{table}
{miss}
<div class="note">範囲外の指標は「既存店で起きたことがない状況」です。その指標が効くカテゴリについては、
類似店の実績をそのまま当てないでください。特に3軸に使う指標が範囲外の場合、類似店の選定自体が外挿になります。</div>
"""


def _sec_direction(a: Analysis) -> str:
    cfg = a.cfg
    dr = a.directions
    n_pairs = len(a.stores) * (len(a.stores) - 1) // 2

    if not dr.findings:
        body = "<p>基準を満たす組み合わせはありませんでした。5店舗では珍しいことではありません。</p>"
    else:
        strong = [f for f in dr.findings if f.strength == "根拠あり"]
        weak = [f for f in dr.findings if f.strength == "参考"]

        def mk(rows):
            out = []
            for f in rows:
                axis = (esc(cfg.axes[f.axis]["label"].split("（")[0]) if f.axis
                        else '<span class="muted">参考変数</span>')
                same = f'<strong>{f.n_same_direction}</strong> / {len(a.stores)}'
                cluster = ""
                if f.cluster_members:
                    cluster = (f'<br><span class="muted" style="font-size:11px">同じ動き: '
                               f'{esc("、".join(f.cluster_members[:4]))}'
                               f'{" ほか" if len(f.cluster_members) > 4 else ""}</span>')
                out.append(
                    f'<tr><td class="l">{esc(f.var_label)}{cluster}</td><td class="l">{axis}</td>'
                    f'<td class="l">{esc(f.category)}</td>'
                    f'<td><span class="tag {"ok" if f.direction == "正" else "warn"}">{esc(f.direction)}</span></td>'
                    f"<td>{same}</td><td>{f.concordant} / {f.total_pairs}</td>"
                    f"<td>{f.median_slope * 100:+.2f}pt</td></tr>"
                )
            return "".join(out)

        head = (f'<tr><th class="l">商圏変数</th><th class="l">軸</th><th class="l">カテゴリ</th>'
                f'<th>方向</th><th>同傾向の店舗数</th><th>一致ペア</th><th>変化量の目安</th></tr>')
        empty = '<tr><td colspan="7" class="l muted">該当なし</td></tr>'
        body = f"""
<h3>根拠あり（全{n_pairs}ペアで符号が一致、かつ4店以上が同方向）</h3>
<table><thead>{head}</thead><tbody>{mk(strong[:30]) or empty}</tbody></table>
{f'<p class="sub">{len(strong)}件中 上位30件を表示しています（Excel出力には全件入ります）。</p>' if len(strong) > 30 else ""}
<h3>参考（1ペアだけ符号が逆、かつ3店以上が同方向）</h3>
<table><thead>{head}</thead><tbody>{mk(weak[:20]) or empty}</tbody></table>
"""

    clusters = ""
    if dr.clusters:
        rows = "".join(
            f'<tr><td class="l">{esc(c["representative"])}</td>'
            f'<td class="l muted">{esc("、".join(c["members"]))}</td></tr>'
            for c in dr.clusters
        )
        clusters = f"""
<details><summary>まとめた変数グループ（5店舗ではほぼ同じ動きをするため区別できない）を見る</summary>
<table><thead><tr><th class="l">代表として表に出した変数</th>
<th class="l">まとめられた変数（同じ根拠を重複して数えないため非表示）</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="sub">ここでまとめられた変数同士は、5店舗のデータでは区別できません。
表に出ている代表変数が原因だとは言えず、同じグループのどれが効いているかは判別不能です。</p></details>"""

    rg = ""
    if a.rural_gaps:
        rows = "".join(
            f'<tr><td class="l">{esc(r["category"])}</td><td>{fpct(r["rural_share"])}</td>'
            f'<td>{fpct(r["urban_mean"])}</td><td>{fpct(r["urban_min"])}〜{fpct(r["urban_max"])}</td>'
            f'<td>{r["diff_pt"]:+.2f}pt</td>'
            f'<td class="l"><span class="tag {"warn" if not r["inside_urban_range"] else ""}">{esc(r["note"])}</span></td></tr>'
            for r in a.rural_gaps[:15]
        )
        rg = f"""
<h3>郊外店 vs 都市部店の構成比差（立地の影響を受けやすいカテゴリの候補）</h3>
<table><thead><tr><th class="l">カテゴリ</th><th>郊外店</th><th>都市部4店 平均</th>
<th>都市部4店 レンジ</th><th>差</th><th class="l">判定</th></tr></thead><tbody>{rows}</tbody></table>
<div class="note bad">郊外店は1店しかないため、ここに出る差は「その1店の個性」と「郊外という立地の効果」を
区別できていません。カテゴリの当たりをつけるための材料であって、根拠ではありません。</div>
"""

    return f"""
<h2>4. 商圏差とカテゴリ構成比差の方向性（仮説）</h2>
<p class="sub">全{n_pairs}ペア（{len(a.stores)}店の総当たり）について
「商圏変数が大きい側は、そのカテゴリ構成比も大きいか」を数えたものです。回帰係数ではありません。</p>
<div class="note warn"><strong>この表の読み方と限界</strong><br>
{esc(dr.note)}<br>
「同傾向の店舗数」は、その変数が平均より高い（低い）店で、カテゴリ構成比も平均より高い（低い）店の数です。
平均付近にいる店はどちらにも数えません。<br>
<strong>5店舗では因果も有意性も言えません。</strong>
「同傾向の店舗数」が多いものだけを、現場感覚と突き合わせて仮説として扱ってください。</div>
{body}
{clusters}
{rg}
"""


def _sec_member(a: Analysis) -> str:
    if not a.member_gaps:
        return """
<h2>4-2. 会員構成比と商圏年齢構成のズレ</h2>
<p class="muted">会員構成比ファイルが指定されていないため、この分析はスキップしました。</p>"""

    blocks = []
    for g in a.member_gaps:
        rows = "".join(
            f'<tr><td class="l">{esc(r.bucket)}</td><td>{fpct(r.member_share)}</td>'
            f"<td>{fpct(r.area_share)}</td>"
            f'<td>{"—" if r.gap_pt is None else f"{r.gap_pt * 100:+.1f}pt"}</td>'
            f'<td class="l muted">{esc(r.lean)}</td></tr>'
            for r in g.rows
        )
        gm = "／".join(f"{k} {v:.0%}" for k, v in g.gender_mix.items())
        blocks.append(
            f"<h3>{esc(g.store_name)}</h3>"
            f'<p class="sub">会員の性別構成: {esc(gm) or "—"} ／ '
            f"ズレの大きさ（総変動距離）: <strong>{fpct(g.tvd)}</strong> ／ "
            f"商圏統計の信頼度: <strong>{esc(g.reliability)}</strong></p>"
            f'<table><thead><tr><th class="l">年代</th><th>会員構成比</th><th>商圏年齢構成比</th>'
            f'<th>ズレ</th><th class="l">読み方</th></tr></thead><tbody>{rows}</tbody></table>'
        )

    return f"""
<h2>4-2. 会員構成比と商圏年齢構成のズレ</h2>
<p class="sub">商圏統計の年齢構成は15歳以上で再正規化して比較しています（会員に未就学・小学生が含まれないため）。</p>
<div class="note">ズレが大きい店ほど「商圏に住んでいる人」と「実際に来ている人」が違います。
新店でも同程度のズレが起きると見て、商圏統計から直接カテゴリを決めるのは避けてください。
なおIDPOS会員には居住地が紐づいていないため、このズレには「商圏外からの来店」と
「商圏内で来ていない層」が混ざっています。分離はできません。</div>
{"".join(blocks)}
"""


def _sec_shelf(a: Analysis) -> str:
    if not a.shelf:
        return "<h2>5. 初期棚割の提案</h2><p>類似店の売上構成比が取得できませんでした。</p>"

    peer_names = [p.store_name for p in a.peers]
    cols = "".join(f'<th>{esc(n)}</th>' for n in peer_names)
    rows = []
    for p in a.shelf:
        dcls = {"増やす": "ok", "減らす": "bad", "据え置き": ""}[p.direction]
        peer_cells = "".join(f"<td>{fpct(p.peer_shares.get(n))}</td>" for n in peer_names)
        margin = (f"{fpct(p.margin_low)}〜{fpct(p.margin_high)}"
                  if p.margin_low is not None else "—")
        rows.append(
            f'<tr><td class="l">{esc(p.category_major or "")}</td>'
            f'<td class="l"><strong>{esc(p.category)}</strong></td>'
            f"<td><strong>{fpct(p.share_low)}〜{fpct(p.share_high)}</strong></td>"
            f"{peer_cells}<td>{fpct(p.all_store_mean)}</td>"
            f'<td>{fpt(p.delta_vs_all_pt)}</td>'
            f'<td><span class="tag {dcls}">{esc(p.direction)}</span></td>'
            f"<td>{margin}</td>"
            f'<td class="l muted" style="font-size:11.5px">{esc(p.note)}</td></tr>'
        )

    total_lo = sum(p.share_low for p in a.shelf)
    total_hi = sum(p.share_high for p in a.shelf)

    return f"""
<h2>5. 初期棚割の提案</h2>
<p class="sub">類似店 {esc("・".join(peer_names))} の実績を、新店の初期値の「幅」として使います。
増減の方向は「類似店の中央値 − 既存{len(a.stores)}店平均」で判断（±0.5pt以内は据え置き）。</p>
<table><thead><tr><th class="l">大分類</th><th class="l">カテゴリ</th>
<th>提案レンジ</th>{cols}<th>既存{len(a.stores)}店平均</th><th>平均との差</th>
<th>方向</th><th>粗利率レンジ</th><th class="l">メモ</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<p class="sub">レンジ合計: {fpct(total_lo)} 〜 {fpct(total_hi)}（下限・上限をそれぞれ単純合算したものなので100%にはなりません）。
実際の棚割は合計100%に収まるよう、優先カテゴリから配分してください。</p>
<div class="note warn">このレンジは<strong>類似店{len(a.peers)}店の実績値そのもの</strong>であり、新店の予測値ではありません。
新店固有の条件（売場面積、競合、価格政策）が類似店と違う分は、この表には反映されていません。
面積が大きく違う場合は、構成比ではなく絶対額ベースでの再検討が必要です。</div>
"""


def _sec_checklist(a: Analysis) -> str:
    rows = "".join(
        f'<tr><td><span class="tag {"bad" if c["priority"] == "最優先" else "warn" if c["priority"] == "高" else ""}">'
        f'{esc(c["priority"])}</span></td>'
        f'<td class="l">{esc(c["item"])}</td><td class="l">{esc(c["timing"])}</td>'
        f'<td class="l">{esc(c["action"])}</td></tr>'
        for c in a.checklist
    )
    return f"""
<h2>6. 開店後にIDPOSで検証・修正すべき項目</h2>
<table><thead><tr><th>優先度</th><th class="l">検証項目</th><th class="l">タイミング</th>
<th class="l">ズレていたときの打ち手</th></tr></thead><tbody>{rows}</tbody></table>
<div class="note">本レポートの価値は「開店時点で何も無いよりマシな初期値を出すこと」までです。
IDPOSが貯まった時点で、ここの数値は実績に置き換えてください。
新店を6店目として既存店マスタに追加すると、次の新店の判定が少しずつ安定します。</div>
"""


# ---------------------------------------------------------------- 出力

def render_html(a: Analysis) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    inputs = (f"既存店 {len(a.stores)}店 ／ 商圏定義 {esc(a.cfg.trade_area_definition)} ／ "
              f"設定ファイル {esc(a.cfg.path.name)}")
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>類似店判定レポート｜{esc(a.new_name)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header>
<h1>類似店判定レポート — {esc(a.new_name)}</h1>
<p class="sub">{inputs} ／ 作成 {now}</p>
</header>
<div class="note bad">{DISCLAIMER}</div>
{_sec_summary(a)}
{_sec_ranking(a)}
{_sec_range(a)}
{_sec_direction(a)}
{_sec_member(a)}
{_sec_shelf(a)}
{_sec_checklist(a)}
<footer>
本レポートは shoken_analyzer v0.1.0 が自動生成しました。
判定ロジックの閾値・軸の重み・項目定義はすべて config/columns.yaml で変更できます。<br>
既存店が5店である限り、本レポートの内容は仮説の域を出ません。
</footer>
</div></body></html>"""


def write_html(a: Analysis, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(a), encoding="utf-8")
    return path


def write_excel(a: Analysis, path: str | Path) -> Path:
    """同じ内容をExcelで出す。シートはレポートの章立てに対応。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = a.cfg

    summary = pd.DataFrame([
        {"項目": "新店", "値": a.new_name},
        {"項目": "商圏定義", "値": cfg.trade_area_definition},
        {"項目": "都市部/郊外 判定", "値": a.urban_new.verdict},
        {"項目": "都市度スコア", "値": a.urban_new.score},
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
    ranges = pd.DataFrame([
        {"指標": i.label, "軸": i.axis or "参考", "新店": i.value,
         "既存最小": i.vmin, "既存最大": i.vmax, "判定": i.status, "はみ出し幅": i.overshoot}
        for i in a.ranges
    ])
    directions = pd.DataFrame([
        {"商圏変数": f.var_label, "軸": f.axis or "参考", "カテゴリ": f.category,
         "方向": f.direction, "一致ペア": f.concordant, "総ペア": f.total_pairs,
         "同傾向の店舗数": f.n_same_direction, "変化量pt": f.median_slope * 100,
         "根拠": f.strength, "同じ動きの変数": "、".join(f.cluster_members)}
        for f in a.directions.findings
    ])
    shelf = pd.DataFrame([
        {"大分類": p.category_major, "カテゴリ": p.category,
         "提案レンジ下限": p.share_low, "提案レンジ上限": p.share_high, "中央値": p.share_mid,
         **{f"類似店_{k}": v for k, v in p.peer_shares.items()},
         "既存全店平均": p.all_store_mean, "平均との差pt": p.delta_vs_all_pt,
         "方向": p.direction, "粗利率下限": p.margin_low, "粗利率上限": p.margin_high,
         "メモ": p.note}
        for p in a.shelf
    ])
    member = pd.DataFrame([
        {"店舗": g.store_name, "年代": r.bucket, "会員構成比": r.member_share,
         "商圏年齢構成比": r.area_share, "ズレpt": None if r.gap_pt is None else r.gap_pt * 100,
         "総変動距離": g.tvd, "商圏統計の信頼度": g.reliability}
        for g in a.member_gaps for r in g.rows
    ])
    rural = pd.DataFrame(a.rural_gaps)
    checklist = pd.DataFrame(a.checklist)
    disclaimer = pd.DataFrame([{"注意事項": t} for t in [
        "本レポートは既存5店舗のみを根拠にした仮説であり、統計的に検定された予測ではありません。",
        "店舗数 < 変数の数のため、回帰・GBM等のモデル学習は行っていません。",
        "出している数値は (1)類似店の実績値 (2)店舗間の差の方向 (3)既存店の実績レンジ のいずれかです。",
        "郊外店は1店のみのため、郊外寄りの新店に対する根拠は特に弱くなります。",
        "開店後のIDPOSによる検証を前提に、初期値の出発点としてお使いください。",
    ]])

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        disclaimer.to_excel(xw, sheet_name="0_注意事項", index=False)
        summary.to_excel(xw, sheet_name="1_商圏サマリ", index=False)
        ranking.to_excel(xw, sheet_name="2_類似店ランキング", index=False)
        ranges.to_excel(xw, sheet_name="3_範囲チェック", index=False)
        directions.to_excel(xw, sheet_name="4_方向性仮説", index=False)
        if not rural.empty:
            rural.to_excel(xw, sheet_name="4b_郊外vs都市部", index=False)
        if not member.empty:
            member.to_excel(xw, sheet_name="4c_会員ズレ", index=False)
        shelf.to_excel(xw, sheet_name="5_初期棚割提案", index=False)
        checklist.to_excel(xw, sheet_name="6_IDPOSチェックリスト", index=False)
    return path
