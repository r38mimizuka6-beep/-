"""商圏差とカテゴリ構成比差の関係を、方向（正/負）と一致数で出す。

5店舗では回帰もGBMも使えないので、数えるだけにする。出すのは3つ。
  (1) 同傾向の店舗数  : 5店のうち何店で「変数の偏り」と「構成比の偏り」の符号が揃うか
  (2) 一致ペア数      : 全10ペアのうち何ペアで符号が揃うか
  (3) 変化量の目安    : 変数が標準偏差1つ分動いたときの構成比の変化（中央値）

係数も p値も出さない。さらに、
  ・5店舗では多くの変数が互いにほぼ同じ動きをする（共線性）ため、
    相関の高い変数をまとめて代表1本だけを出す。
  ・偶然そう見える件数（期待値）を計算して併記する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from math import comb

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class DirectionFinding:
    variable: str
    var_label: str
    axis: str | None
    category: str
    direction: str            # 正 / 負
    concordant: int           # 符号が一致したペア数
    total_pairs: int
    n_same_direction: int     # 符号が揃った店舗数
    n_stores: int             # 判定に使えた店舗数
    median_slope: float       # Δ構成比pt / Δ変数(標準化) の中央値
    strength: str             # 根拠あり / 参考
    cluster_members: list[str] = field(default_factory=list)  # 同じ動きをする他の変数

    @property
    def consistency(self) -> float:
        return self.concordant / self.total_pairs if self.total_pairs else 0.0


@dataclass
class DirectionResult:
    findings: list[DirectionFinding]
    n_tests: int                   # 代表変数 × カテゴリ の組み合わせ数
    expected_by_chance: float      # 偶然この基準を満たす件数の期待値
    clusters: list[dict]           # 共線性でまとめた変数グループ
    note: str


# ---------------------------------------------------------------- 共線性

def _cluster_variables(
    stores: pd.DataFrame, var_keys: list[str], cfg: Config, *, threshold: float = 0.95
) -> tuple[list[str], dict[str, list[str]]]:
    """5店舗でほぼ同じ動きをする変数を1本にまとめる。

    5点しかないので相関は簡単に1.0近くになる。別々の根拠として並べると
    「同じ1つの事実」を何度も数えてしまうので、代表を1本だけ残す。
    軸に使う変数を優先して代表にする。
    """
    z = stores[var_keys].apply(pd.to_numeric, errors="coerce")
    z = (z - z.mean()) / z.std(ddof=1)
    z = z.loc[:, z.notna().sum() >= 3]
    usable = list(z.columns)
    # 軸変数を先に見ることで、代表が軸変数になりやすくする
    usable.sort(key=lambda k: (cfg.axis_of(k) is None, k))

    reps: list[str] = []
    members: dict[str, list[str]] = {}
    for key in usable:
        placed = False
        for rep in reps:
            pair = z[[rep, key]].dropna()
            if len(pair) < 3:
                continue
            r = pair[rep].corr(pair[key])
            if pd.notna(r) and abs(r) >= threshold:
                members[rep].append(key)
                placed = True
                break
        if not placed:
            reps.append(key)
            members[key] = []
    return reps, members


# ---------------------------------------------------------------- 本体

def analyze_directions(
    stores: pd.DataFrame,
    sales_mix: pd.DataFrame,
    cfg: Config,
    *,
    min_abs_share_diff: float = 0.005,   # 0.5pt未満の差はノイズ扱い
    min_pairs: int = 6,
    cluster_threshold: float = 0.95,
) -> DirectionResult:
    id_key = cfg.id_key
    share = sales_mix.pivot_table(index=id_key, columns="category",
                                  values="sales_share", aggfunc="sum")
    ids = [s for s in stores[id_key].astype(str) if s in share.index]
    if len(ids) < 4:
        return DirectionResult([], 0, 0.0, [],
                               "店舗数が足りず、方向性の判定はできませんでした。")

    svals = stores.set_index(id_key).loc[ids]
    candidates = [
        k for k in list(cfg.variables) + [d["key"] for d in cfg.derived]
        if k in svals.columns and (k not in cfg.variables or cfg.variables[k].numeric)
    ]
    candidates = [
        k for k in candidates
        if pd.to_numeric(svals[k], errors="coerce").std(ddof=1) not in (0, np.nan)
        and np.isfinite(pd.to_numeric(svals[k], errors="coerce").std(ddof=1) or np.nan)
    ]
    reps, members = _cluster_variables(svals.reset_index(), candidates, cfg,
                                       threshold=cluster_threshold)

    cat_z = share.loc[ids]
    cat_mean, cat_sd = cat_z.mean(), cat_z.std(ddof=1)

    findings: list[DirectionFinding] = []
    n_tests = 0
    for var in reps:
        col = pd.to_numeric(svals[var], errors="coerce")
        sd = col.std(ddof=1)
        if not np.isfinite(sd) or sd == 0:
            continue
        vdev = (col - col.mean()) / sd
        for cat in share.columns:
            n_tests += 1
            pos = neg = 0
            slopes, used = [], set()
            for a, b in combinations(ids, 2):
                va, vb = col.get(a), col.get(b)
                sa, sb = share.at[a, cat], share.at[b, cat]
                if pd.isna(va) or pd.isna(vb) or pd.isna(sa) or pd.isna(sb):
                    continue
                dv, ds = (va - vb) / sd, sa - sb
                if abs(dv) < 1e-9 or abs(ds) < min_abs_share_diff:
                    continue
                if dv * ds > 0:
                    pos += 1
                else:
                    neg += 1
                slopes.append(ds / dv)
                used.update((a, b))

            total = pos + neg
            if total < min_pairs:
                continue
            concordant = max(pos, neg)
            direction = "正" if pos >= neg else "負"

            # 何店舗で同じ傾向か: 各店の「変数の偏り」と「構成比の偏り」の符号が揃う店数
            sgn = 1 if direction == "正" else -1
            same = 0
            for s in ids:
                dv = vdev.get(s)
                cs = share.at[s, cat]
                if pd.isna(dv) or pd.isna(cs) or cat_sd[cat] == 0:
                    continue
                cd = (cs - cat_mean[cat]) / cat_sd[cat]
                if abs(dv) < 0.25 or abs(cd) < 0.25:   # 平均付近の店は賛成にも反対にも数えない
                    continue
                if sgn * dv * cd > 0:
                    same += 1

            if concordant == total and same >= 4:
                strength = "根拠あり"
            elif concordant >= total - 1 and same >= 3:
                strength = "参考"
            else:
                continue

            findings.append(DirectionFinding(
                variable=var, var_label=cfg.label(var), axis=cfg.axis_of(var),
                category=str(cat), direction=direction, concordant=concordant,
                total_pairs=total, n_same_direction=same, n_stores=len(used),
                median_slope=float(np.median(slopes)), strength=strength,
                cluster_members=[cfg.label(m) for m in members.get(var, [])],
            ))

    findings.sort(key=lambda f: (f.strength != "根拠あり", -f.n_same_direction,
                                 -f.consistency, -abs(f.median_slope)))

    # 符号がランダムだと仮定したときに、この基準を満たす件数の期待値
    n_pairs = len(ids) * (len(ids) - 1) // 2
    p_perfect = 2 / (2 ** n_pairs) if n_pairs else 0.0
    p_one_off = (2 * comb(n_pairs, 1)) / (2 ** n_pairs) if n_pairs else 0.0
    expected = n_tests * (p_perfect + p_one_off)

    clusters = [
        {"representative": cfg.label(r), "members": [cfg.label(m) for m in members[r]]}
        for r in reps if members.get(r)
    ]
    note = (
        f"候補{len(candidates)}変数のうち、5店舗での動きがほぼ同じ（相関{cluster_threshold:.2f}以上）な"
        f"ものをまとめて{len(reps)}変数に集約し、{len(share.columns)}カテゴリとの"
        f"{n_tests}通りを判定しました。符号が完全にランダムでも、この基準を満たす組み合わせは"
        f"{expected:.1f}件ほど出ます。抽出された{len(findings)}件のうち、"
        f"それだけは中身のない当たりだと見てください。"
    )
    return DirectionResult(findings, n_tests, expected, clusters, note)


def urban_vs_rural_gaps(
    stores: pd.DataFrame, sales_mix: pd.DataFrame, cfg: Config, rural_ids: list[str]
) -> list[dict]:
    """郊外店と都市部店の構成比差。立地の影響を受けやすいカテゴリの候補。

    郊外は1店しかないので、これは仮説の材料であって検証結果ではない。
    """
    id_key = cfg.id_key
    share = sales_mix.pivot_table(index=id_key, columns="category",
                                  values="sales_share", aggfunc="sum")
    urban_ids = [s for s in share.index if s not in rural_ids]
    rural = [s for s in rural_ids if s in share.index]
    if not rural or len(urban_ids) < 2:
        return []

    rows = []
    for cat in share.columns:
        u = share.loc[urban_ids, cat].dropna()
        r = share.loc[rural, cat].dropna()
        if u.empty or r.empty:
            continue
        diff = float(r.mean() - u.mean())
        inside = bool(u.min() <= r.mean() <= u.max())
        rows.append({
            "category": str(cat),
            "rural_share": float(r.mean()),
            "urban_mean": float(u.mean()),
            "urban_min": float(u.min()),
            "urban_max": float(u.max()),
            "diff_pt": diff * 100,
            "inside_urban_range": inside,
            "note": "都市部4店の範囲内（差は小さい）" if inside else "都市部4店の範囲外（立地差の候補）",
        })
    rows.sort(key=lambda d: -abs(d["diff_pt"]))
    return rows
