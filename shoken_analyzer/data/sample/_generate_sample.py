#!/usr/bin/env python3
"""ダミーデータの生成（既存5店＋新店1店、低温ディビジョンの週次IDPOS）。

実データと同じ形式で出す。
  - 商圏マスタ   : store_master.csv / new_store.csv
  - IDPOS       : idpos_sample.csv.gz      （年週 × 店舗 × 階層 × 顧客種類 × 年代）
  - 粗利        : ure_zaiko_sample.csv.gz  （年週 × 店舗 × 階層）
文字コードはcp932、年週の表記も実データに合わせて2種類にしてある。

売上は商圏変数に反応するように作ってあるので、方向性分析とLOO検証が
意味のある出力を返すことを確認できる。
"""

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
rng = np.random.default_rng(20251002)

N_WEEKS = 52
START_YEAR, START_WEEK = 2025, 40

# ---------------------------------------------------------------- 店舗
STORES = [
    dict(store_id="0101", store_name="都心A店", kind="urban_single", open_date="2025-11-28",
         pop_total=204_382, pop_density=14_800, hh_total=109_917, hh_avg_size=1.86,
         age_share_0_14=0.113, age_share_15_29=0.216, age_share_30_44=0.237,
         age_share_45_64=0.269, age_share_65plus=0.166,
         age_dec_0s=0.077, age_dec_10s=0.080, age_dec_20s=0.172, age_dec_30s=0.158,
         age_dec_40s=0.166, age_dec_50s=0.138, age_dec_60s=0.087, age_dec_70plus=0.122,
         hh_share_single=0.553, hh_share_with_child=0.069, hh_share_senior=0.204,
         housing_share_owned=0.337, housing_share_rent=0.564, housing_share_apart=0.806,
         hh_income_avg=556, income_share_u300=0.275, income_share_700p=0.251,
         daytime_pop=102_201, nighttime_pop=194_605, worker_pop=44_370, student_pop=13_838,
         share_walk=0.46, share_bike=0.38, share_car=0.06, share_train=0.10,
         parking_spaces=18, nearest_station_m=260, station_daily_users=58_000,
         frontage_traffic=9_800,
         comp_sm_1km=6, comp_sm_3km=19, comp_cvs_1km=22, comp_drug_1km=7,
         comp_discount_1km=2, nearest_comp_m=240, nearest_comp_price=3,
         own_store_overlap=0.18,
         fac_school=6, fac_nursery=14, fac_university=2, fac_hospital=5, fac_clinic=95,
         fac_factory=3, fac_office_workers=44_370, fac_apartment_units=88_500,
         sales_floor_sqm=980, fresh_strength=3, deli_strength=4, price_level=3,
         pb_ratio=0.11, store_format="都市型SM", weekly_customers=26_000),

    dict(store_id="0102", store_name="駅前B店", kind="urban_office", open_date="2026-02",
         pop_total=98_400, pop_density=18_200, hh_total=62_100, hh_avg_size=1.58,
         age_share_0_14=0.072, age_share_15_29=0.268, age_share_30_44=0.271,
         age_share_45_64=0.250, age_share_65plus=0.139,
         age_dec_0s=0.049, age_dec_10s=0.063, age_dec_20s=0.228, age_dec_30s=0.178,
         age_dec_40s=0.173, age_dec_50s=0.141, age_dec_60s=0.088, age_dec_70plus=0.080,
         hh_share_single=0.655, hh_share_with_child=0.041, hh_share_senior=0.152,
         housing_share_owned=0.241, housing_share_rent=0.672, housing_share_apart=0.905,
         hh_income_avg=612, income_share_u300=0.248, income_share_700p=0.311,
         daytime_pop=182_500, nighttime_pop=96_300, worker_pop=121_400, student_pop=9_200,
         share_walk=0.52, share_bike=0.14, share_car=0.04, share_train=0.30,
         parking_spaces=8, nearest_station_m=90, station_daily_users=142_000,
         frontage_traffic=14_500,
         comp_sm_1km=9, comp_sm_3km=27, comp_cvs_1km=41, comp_drug_1km=12,
         comp_discount_1km=3, nearest_comp_m=130, nearest_comp_price=4,
         own_store_overlap=0.26,
         fac_school=3, fac_nursery=8, fac_university=4, fac_hospital=3, fac_clinic=138,
         fac_factory=1, fac_office_workers=121_400, fac_apartment_units=52_300,
         sales_floor_sqm=620, fresh_strength=2, deli_strength=5, price_level=4,
         pb_ratio=0.08, store_format="駅前小型SM", weekly_customers=31_000),

    dict(store_id="0103", store_name="住宅街C店", kind="urban_family", open_date="2026-04",
         pop_total=142_600, pop_density=9_400, hh_total=58_900, hh_avg_size=2.42,
         age_share_0_14=0.148, age_share_15_29=0.166, age_share_30_44=0.248,
         age_share_45_64=0.277, age_share_65plus=0.161,
         age_dec_0s=0.098, age_dec_10s=0.096, age_dec_20s=0.120, age_dec_30s=0.152,
         age_dec_40s=0.178, age_dec_50s=0.154, age_dec_60s=0.104, age_dec_70plus=0.098,
         hh_share_single=0.338, hh_share_with_child=0.138, hh_share_senior=0.228,
         housing_share_owned=0.586, housing_share_rent=0.332, housing_share_apart=0.552,
         hh_income_avg=681, income_share_u300=0.182, income_share_700p=0.372,
         daytime_pop=118_300, nighttime_pop=140_100, worker_pop=38_200, student_pop=18_900,
         share_walk=0.31, share_bike=0.44, share_car=0.19, share_train=0.06,
         parking_spaces=62, nearest_station_m=680, station_daily_users=31_000,
         frontage_traffic=11_200,
         comp_sm_1km=4, comp_sm_3km=14, comp_cvs_1km=13, comp_drug_1km=6,
         comp_discount_1km=2, nearest_comp_m=520, nearest_comp_price=3,
         own_store_overlap=0.09,
         fac_school=11, fac_nursery=22, fac_university=1, fac_hospital=4, fac_clinic=62,
         fac_factory=6, fac_office_workers=38_200, fac_apartment_units=31_400,
         sales_floor_sqm=1_380, fresh_strength=4, deli_strength=3, price_level=3,
         pb_ratio=0.14, store_format="郊外型SM（市街地）", weekly_customers=24_000),

    dict(store_id="0104", store_name="旧市街D店", kind="urban_senior", open_date="2026-07",
         pop_total=88_200, pop_density=11_100, hh_total=44_600, hh_avg_size=1.98,
         age_share_0_14=0.089, age_share_15_29=0.151, age_share_30_44=0.196,
         age_share_45_64=0.284, age_share_65plus=0.280,
         age_dec_0s=0.058, age_dec_10s=0.062, age_dec_20s=0.120, age_dec_30s=0.108,
         age_dec_40s=0.140, age_dec_50s=0.158, age_dec_60s=0.139, age_dec_70plus=0.215,
         hh_share_single=0.452, hh_share_with_child=0.052, hh_share_senior=0.361,
         housing_share_owned=0.512, housing_share_rent=0.401, housing_share_apart=0.631,
         hh_income_avg=448, income_share_u300=0.372, income_share_700p=0.168,
         daytime_pop=74_600, nighttime_pop=86_900, worker_pop=26_100, student_pop=6_400,
         share_walk=0.63, share_bike=0.24, share_car=0.08, share_train=0.05,
         parking_spaces=26, nearest_station_m=740, station_daily_users=19_000,
         frontage_traffic=6_900,
         comp_sm_1km=5, comp_sm_3km=16, comp_cvs_1km=15, comp_drug_1km=9,
         comp_discount_1km=1, nearest_comp_m=310, nearest_comp_price=2,
         own_store_overlap=0.14,
         fac_school=5, fac_nursery=9, fac_university=0, fac_hospital=6, fac_clinic=88,
         fac_factory=9, fac_office_workers=26_100, fac_apartment_units=24_800,
         sales_floor_sqm=860, fresh_strength=4, deli_strength=3, price_level=2,
         pb_ratio=0.17, store_format="都市型SM", weekly_customers=18_000),

    dict(store_id="0105", store_name="郊外E店", kind="rural_car", open_date="2026-08",
         pop_total=46_300, pop_density=1_850, hh_total=17_200, hh_avg_size=2.69,
         age_share_0_14=0.132, age_share_15_29=0.138, age_share_30_44=0.213,
         age_share_45_64=0.288, age_share_65plus=0.229,
         age_dec_0s=0.086, age_dec_10s=0.084, age_dec_20s=0.098, age_dec_30s=0.128,
         age_dec_40s=0.158, age_dec_50s=0.160, age_dec_60s=0.126, age_dec_70plus=0.160,
         hh_share_single=0.221, hh_share_with_child=0.112, hh_share_senior=0.342,
         housing_share_owned=0.781, housing_share_rent=0.158, housing_share_apart=0.214,
         hh_income_avg=524, income_share_u300=0.244, income_share_700p=0.238,
         daytime_pop=39_800, nighttime_pop=45_600, worker_pop=14_700, student_pop=5_100,
         share_walk=0.06, share_bike=0.11, share_car=0.81, share_train=0.02,
         parking_spaces=240, nearest_station_m=3_400, station_daily_users=4_200,
         frontage_traffic=21_600,
         comp_sm_1km=1, comp_sm_3km=5, comp_cvs_1km=4, comp_drug_1km=3,
         comp_discount_1km=2, nearest_comp_m=1_900, nearest_comp_price=2,
         own_store_overlap=0.02,
         fac_school=4, fac_nursery=6, fac_university=0, fac_hospital=2, fac_clinic=21,
         fac_factory=18, fac_office_workers=14_700, fac_apartment_units=3_900,
         sales_floor_sqm=1_950, fresh_strength=5, deli_strength=3, price_level=2,
         pb_ratio=0.19, store_format="郊外型SM", weekly_customers=15_000),
]

NEW_STORE = dict(
    store_id="0199", store_name="新店（仮称）○○店", kind="new", open_date="2026-11",
    pop_total=168_900, pop_density=12_300, hh_total=81_400, hh_avg_size=2.07,
    age_share_0_14=0.124, age_share_15_29=0.198, age_share_30_44=0.251,
    age_share_45_64=0.265, age_share_65plus=0.162,
    age_dec_0s=0.082, age_dec_10s=0.080, age_dec_20s=0.156, age_dec_30s=0.148,
    age_dec_40s=0.170, age_dec_50s=0.150, age_dec_60s=0.098, age_dec_70plus=0.116,
    hh_share_single=0.448, hh_share_with_child=0.098, hh_share_senior=0.211,
    housing_share_owned=0.431, housing_share_rent=0.478, housing_share_apart=0.712,
    hh_income_avg=598, income_share_u300=0.231, income_share_700p=0.289,
    daytime_pop=131_200, nighttime_pop=165_400, worker_pop=47_800, student_pop=15_600,
    share_walk=0.39, share_bike=0.41, share_car=0.11, share_train=0.09,
    parking_spaces=34, nearest_station_m=430, station_daily_users=46_000,
    frontage_traffic=10_400,
    comp_sm_1km=5, comp_sm_3km=17, comp_cvs_1km=18, comp_drug_1km=8,
    comp_discount_1km=2, nearest_comp_m=380, nearest_comp_price=3,
    own_store_overlap=0.12,
    fac_school=8, fac_nursery=17, fac_university=1, fac_hospital=4, fac_clinic=79,
    fac_factory=5, fac_office_workers=47_800, fac_apartment_units=61_200,
    sales_floor_sqm=1_120, fresh_strength=4, deli_strength=4, price_level=3,
    pb_ratio=0.13, store_format="都市型SM", weekly_customers=22_000,
)

# ---------------------------------------------------------------- 低温の階層
# (ライン, 部門, カテゴリー, [サブカテゴリー], 基準PI(円/客), 基準粗利率, ドライバ, 主購買年代)
HIER = [
    ("和日配", "日配和", "納豆",     ["小粒", "ひきわり"],       11.0, 0.262,
     {"age_dec_60s": 0.9, "age_dec_70plus": 0.8, "hh_share_single": -0.5}, [50, 60, 70]),
    ("和日配", "日配和", "豆腐",     ["充填", "もめん・絹"],     14.5, 0.248,
     {"age_dec_70plus": 1.0, "hh_share_senior": 0.7}, [50, 60, 70]),
    ("和日配", "日配和", "練物",     ["ちくわ", "かまぼこ"],      8.2, 0.291,
     {"age_dec_70plus": 1.2, "hh_avg_size": 0.4}, [60, 70]),
    ("洋日配", "日配飲料", "牛乳",   ["ホームユース", "パーソナル"], 22.0, 0.218,
     {"hh_share_with_child": 1.1, "age_dec_30s": 0.6}, [30, 40, 50]),
    ("洋日配", "日配洋", "ヨーグルト", ["プレーン", "小型カップ"], 18.5, 0.254,
     {"hh_share_with_child": 0.8, "age_dec_40s": 0.5}, [30, 40, 50]),
    ("洋日配", "日配洋", "デザート", ["プリン", "ゼリー"],       9.8, 0.301,
     {"age_dec_20s": 0.7, "hh_share_single": 0.6}, [20, 30, 40]),
    ("フローズン", "冷凍食品", "冷凍麺", ["うどん", "パスタ"],    13.2, 0.276,
     {"hh_share_single": 1.2, "share_car": 0.5}, [20, 30, 40]),
    ("フローズン", "冷凍食品", "冷凍米飯", ["炒飯", "おにぎり"],   9.4, 0.288,
     {"hh_share_single": 1.0, "day_night_ratio": 0.6}, [20, 30, 40]),
    ("フローズン", "冷凍食品", "アイス", ["マルチ", "パーソナル"], 15.6, 0.312,
     {"hh_share_with_child": 0.7, "age_dec_20s": 0.5}, [20, 30, 40]),
    ("精肉", "精肉", "牛肉",         ["国産", "輸入"],          28.0, 0.242,
     {"hh_income_avg": 1.0, "hh_share_with_child": 0.6}, [40, 50, 60]),
    ("精肉", "精肉", "豚肉",         ["こま・切落し", "ブロック"], 32.5, 0.268,
     {"hh_avg_size": 1.2, "hh_share_with_child": 0.8}, [40, 50, 60]),
    ("精肉", "精肉", "鶏肉",         ["もも", "むね・ささみ"],    21.0, 0.284,
     {"hh_avg_size": 0.9, "age_dec_30s": 0.4}, [30, 40, 50]),
    ("パン", "パン", "食パン",       ["角食", "山食"],          16.8, 0.332,
     {"day_night_ratio": 0.5, "hh_share_with_child": 0.6}, [30, 40, 50, 60]),
    ("パン", "パン", "菓子パン",     ["菓子パン", "蒸しパン"],    19.4, 0.368,
     {"age_dec_20s": 0.8, "worker_pop": 0.6}, [20, 30, 40]),
    ("パン", "パン", "惣菜パン",     ["調理パン", "サンド"],      12.1, 0.392,
     {"day_night_ratio": 1.1, "hh_share_single": 0.7}, [20, 30, 40]),
]

# 業態転換の効果（転換後 ÷ 転換前）。店ごとに ±8% ばらつかせる。
CONVERSION = {
    "和日配":     {"pi": 1.05, "gm": 0.92},
    "洋日配":     {"pi": 1.10, "gm": 0.95},
    "フローズン": {"pi": 1.25, "gm": 0.98},
    "精肉":       {"pi": 1.30, "gm": 0.90},
    "パン":       {"pi": 1.15, "gm": 0.97},
}

# ダミーの平均単価（円）。数量PIから売上を作るために使う。
UNIT_PRICE = {
    "納豆": 110, "豆腐": 95, "練物": 180,
    "牛乳": 230, "ヨーグルト": 165, "デザート": 140,
    "冷凍麺": 220, "冷凍米飯": 260, "アイス": 180,
    "牛肉": 680, "豚肉": 420, "鶏肉": 350,
    "食パン": 160, "菓子パン": 130, "惣菜パン": 180,
}

# サブカテゴリーの下のセグメント（商品階層の6段目）
SEGMENTS = {
    ("牛乳", "ホームユース"):   ["無調整・調整牛乳", "低脂肪・加工乳"],
    ("牛乳", "パーソナル"):     ["無調整・調整牛乳", "乳飲料"],
    ("納豆", "小粒"):           ["小粒", "極小粒"],
    ("納豆", "ひきわり"):       ["ひきわり"],
}
DEFAULT_SEGMENTS = ["主力", "その他"]


def segments_of(cat: str, sub: str) -> list[str]:
    return SEGMENTS.get((cat, sub), DEFAULT_SEGMENTS)


AGE_BANDS = ["10代", "20代", "30代", "40代", "50代", "60代", "70代以上"]
AGE_KEY = {"10代": "age_dec_10s", "20代": "age_dec_20s", "30代": "age_dec_30s",
           "40代": "age_dec_40s", "50代": "age_dec_50s", "60代": "age_dec_60s",
           "70代以上": "age_dec_70plus"}
# 来店バイアス: 商圏にいる人がそのまま来るわけではない（若年は来にくい）
VISIT_BIAS = {"10代": 0.25, "20代": 0.70, "30代": 1.15, "40代": 1.30,
              "50代": 1.25, "60代": 1.15, "70代以上": 0.95}
# 月別の季節指数（低温ディビジョン共通の素）
SEASON = {1: 1.04, 2: 0.97, 3: 1.00, 4: 0.98, 5: 1.00, 6: 1.01,
          7: 1.06, 8: 1.08, 9: 1.00, 10: 0.98, 11: 1.00, 12: 1.12}
SEASON_CAT = {"アイス": {7: 1.55, 8: 1.62, 6: 1.30, 9: 1.15, 1: 0.62, 2: 0.60, 12: 0.70},
              "冷凍麺": {7: 1.22, 8: 1.25, 1: 0.95},
              "豆腐": {12: 1.20, 1: 1.12, 7: 1.08}}


import datetime as _dt


def all_weeks(start=(2024, 40), n=140) -> list[tuple[int, int]]:
    out, y, w = [], *start
    for _ in range(n):
        out.append((y, w))
        w += 1
        if w > 52:
            w, y = 1, y + 1
    return out


def week_monday(y: int, w: int) -> _dt.date:
    try:
        return _dt.date.fromisocalendar(y, min(w, 52), 1)
    except ValueError:
        return _dt.date.fromisocalendar(y, 52, 1)


def month_of(y: int, w: int) -> int:
    return week_monday(y, w).month


def zscores(stores: list[dict], keys: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(stores)[keys].astype(float)
    return (df - df.mean()) / df.std(ddof=1)


_BASE = ["年週", "ゾーンCD", "ゾーン", "エリアCD", "エリア", "チームCD", "チーム",
         "店舗CD", "店舗", "事業部CD", "事業部", "ディビジョンCD", "ディビジョン",
         "ラインCD", "ライン", "部門CD", "部門", "カテゴリーCD", "カテゴリー",
         "サブカテゴリーCD", "サブカテゴリー", "セグメントCD", "セグメント"]
# 数量・客数つきエクスポート
QTY_COLS = _BASE + ["顧客種類", "売上数量", "売上税抜金額(円)",
                    "POS客数", "ID客数", "PI値"]
# 年代つきエクスポート
AGE_COLS = _BASE + ["顧客種類", "年代", "売上税抜金額(円)", "PI値"]
MG_COLS = ["年週", "ゾーンCD", "ゾーン名", "エリアCD", "エリア名", "店舗CD", "店舗名",
           "ディビジョンCD", "ディビジョン名", "ラインCD", "ライン名",
           "部門CD", "部門名", "カテゴリーCD", "カテゴリー名",
           "サブカテゴリーCD", "サブカテゴリー名", "セグメントCD", "セグメント名",
           "販売荒利高(千円)", "販売荒利率"]


def main() -> None:
    for st in STORES + [NEW_STORE]:
        st["day_night_ratio"] = st["daytime_pop"] / st["nighttime_pop"]
    drivers = sorted({d for *_, dd, _ in HIER for d in dd})
    z = zscores(STORES, drivers)
    base = pd.DataFrame(STORES)[drivers].astype(float)
    z_new = (pd.Series({k: NEW_STORE[k] for k in drivers}, dtype=float)
             - base.mean()) / base.std(ddof=1)

    master = pd.DataFrame(STORES).drop(columns=["kind", "weekly_customers"])
    new = pd.DataFrame([NEW_STORE]).drop(columns=["kind", "weekly_customers"])
    master.to_csv(OUT / "store_master.csv", index=False, encoding="utf-8-sig")
    new[list(master.columns)].to_csv(OUT / "new_store.csv", index=False,
                                     encoding="utf-8-sig")

    weeks = all_weeks()
    today = _dt.date(2026, 10, 2)
    weeks = [(y, w) for (y, w) in weeks if week_monday(y, w) <= today]

    qty_rows, age_rows, mg_rows = [], [], []

    def emit(store: dict, zrow, weeks_for_store=None) -> None:
        weeks_ = weeks_for_store or weeks
        cust = store["weekly_customers"]
        # 客数は店舗×週で1つ。明細ごとに振ると PI の分母が揃わなくなる。
        week_customers = {yw: int(cust * rng.normal(1.0, 0.06)) for yw in weeks_}
        _od = store["open_date"]
        opened = _dt.date.fromisoformat(_od if len(_od) > 7 else _od + "-01")
        # 店ごとの転換効果のゆらぎ
        conv_jitter = {line: rng.normal(1.0, 0.08) for line, *_ in
                       {(h[0],) for h in HIER}}
        for li, (line, dept, cat, subs, base_pi, base_gm, dd, _ages) in enumerate(HIER):
            mult = 1.0 + sum(coef * float(zrow[d]) for d, coef in dd.items()) * 0.12
            mult = max(mult, 0.35)
            gm_store = base_gm * (1 + 0.04 * float(zrow.get("hh_income_avg", 0.0)))
            cv = CONVERSION[line]
            jit = conv_jitter.get(line, 1.0)
            w_age = np.array([max(store[AGE_KEY[b]], 1e-6) * VISIT_BIAS[b]
                              for b in AGE_BANDS])
            w_age = w_age / w_age.sum()
            for si, sub in enumerate(subs):
                sub_share = 0.62 if si == 0 else 0.38
                segs = segments_of(cat, sub)
                seg_w = [0.65, 0.35][:len(segs)] if len(segs) > 1 else [1.0]
                seg_w = [w / sum(seg_w) for w in seg_w]
                for gi, (seg, sw) in enumerate(zip(segs, seg_w)):
                    share = sub_share * sw
                    for (y, wk) in weeks_:
                        d = week_monday(y, wk)
                        post = d >= opened
                        if not post and (opened - d).days > 365:
                            continue
                        m = month_of(y, wk)
                        s_idx = SEASON[m] * SEASON_CAT.get(cat, {}).get(m, 1.0)
                        if post:
                            elapsed = (d - opened).days // 7
                            boost = 1.0 + max(0.0, 0.35 - 0.11 * elapsed)
                        else:
                            boost = 1.0
                        pi_f = (cv["pi"] * jit) if post else 1.0
                        gm_f = cv["gm"] if post else 1.0
                        noise = rng.normal(1.0, 0.055)
                        week_cust = week_customers[(y, wk)]
                        pi_total = base_pi * share * mult * s_idx * pi_f * boost * noise
                        qty = pi_total * week_cust / 1000
                        price = UNIT_PRICE[cat] * rng.normal(1.0, 0.03)
                        sales_total = qty * price
                        # 買上率と1人当たり点数に分解（数量PI = 買上率 × 点数 × 1000）
                        upb = 1.05 + 0.5 * share + rng.normal(0, 0.05)
                        buyers = max(1, int(round(qty / upb)))
                        meta = (f"{y}{wk:02d}", "0077", "STリテール", "0306", "ST第一",
                                "0445", "ST第一", store["store_id"], store["store_name"],
                                "0011", "第三事業部", "0054", "低温",
                                f"{li:04d}", line, f"{li:04d}", dept,
                                f"{li:04d}{si}", cat, f"{si:04d}", sub,
                                f"{gi:04d}", seg)
                        # ビュー1: 数量・客数つき（年代なし）
                        qty_rows.append(meta + ("会員", int(round(qty)),
                                                int(round(sales_total)), buyers,
                                                int(buyers * 0.83),
                                                round(qty / week_cust * 1000, 3)))
                        # ビュー2: 年代つき（数量・客数なし）
                        wk_w = np.clip(w_age * rng.normal(1.0, 0.04, size=len(w_age)),
                                       1e-6, None)
                        wk_w = wk_w / wk_w.sum()
                        for b, ww in zip(AGE_BANDS, wk_w):
                            age_rows.append(meta + ("会員", b,
                                                    int(round(sales_total * ww)),
                                                    round(qty / week_cust * 1000 * ww, 3)))
                        age_rows.append(meta + ("非会員", "不明",
                                                int(round(sales_total * 0.22)),
                                                round(qty / week_cust * 1000 * 0.22, 3)))
                        gm = float(np.clip(gm_store * gm_f * rng.normal(1.0, 0.035),
                                           0.05, 0.60))
                        mg_rows.append((
                            f"{y}年{wk}週", "0077", "STリテール", "0306", "ST第一",
                            store["store_id"], store["store_name"],
                            "0054", "低温", f"{li:04d}", line, f"{li:04d}", dept,
                            f"{li:04d}{si}", cat, f"{si:04d}", sub, f"{gi:04d}", seg,
                            round(sales_total * 1.22 * gm / 1000, 1), f"{gm * 100:.2f}%",
                        ))

    for i2, st in enumerate(STORES):
        emit(st, z.iloc[i2])
    # 新店はオープンが先なので、答え合わせデモ用に8週分だけ先の週も作る
    future = all_weeks()
    future = [(y, w) for (y, w) in future
              if week_monday(y, w) <= _dt.date(2027, 1, 25)]
    emit(NEW_STORE, z_new, future)

    qty_df = pd.DataFrame(qty_rows, columns=QTY_COLS)
    age_df = pd.DataFrame(age_rows, columns=AGE_COLS)
    mg_df = pd.DataFrame(mg_rows, columns=MG_COLS)

    opens = {s["store_id"]: _dt.date.fromisoformat(
        s["open_date"] if len(s["open_date"]) > 7 else s["open_date"] + "-01")
        for s in STORES + [NEW_STORE]}
    nid = NEW_STORE["store_id"]

    def monday_of(yw: str) -> _dt.date:
        return week_monday(int(yw[:4]), int(yw[4:]))

    for df in (qty_df, age_df):
        df["_d"] = df["年週"].map(monday_of)
        df["_post"] = [d >= opens[c] for d, c in zip(df["_d"], df["店舗CD"])]
    mg_df["_d"] = mg_df["年週"].map(
        lambda v: week_monday(int(v.split("年")[0]), int(v.split("年")[1].rstrip("週"))))
    mg_df["_post"] = [d >= opens[c] for d, c in zip(mg_df["_d"], mg_df["店舗CD"])]

    def dump(df, mask, cols, name):
        df.loc[mask, cols].to_csv(OUT / name, index=False, encoding="cp932")
        return int(mask.sum())

    ex_q, ex_a, ex_m = (qty_df["店舗CD"] != nid, age_df["店舗CD"] != nid,
                        mg_df["店舗CD"] != nid)
    out = [
        ("idpos_qty_sample.csv.gz",
         dump(qty_df, ex_q & qty_df["_post"], QTY_COLS, "idpos_qty_sample.csv.gz")),
        ("idpos_age_sample.csv.gz",
         dump(age_df, ex_a & age_df["_post"], AGE_COLS, "idpos_age_sample.csv.gz")),
        ("ure_zaiko_sample.csv.gz",
         dump(mg_df, ex_m & mg_df["_post"], MG_COLS, "ure_zaiko_sample.csv.gz")),
        ("idpos_qty_newstore_actual.csv.gz",
         dump(qty_df, ~ex_q & qty_df["_post"], QTY_COLS,
              "idpos_qty_newstore_actual.csv.gz")),
        ("idpos_age_newstore_actual.csv.gz",
         dump(age_df, ~ex_a & age_df["_post"], AGE_COLS,
              "idpos_age_newstore_actual.csv.gz")),
        ("ure_zaiko_newstore_actual.csv.gz",
         dump(mg_df, ~ex_m & mg_df["_post"], MG_COLS,
              "ure_zaiko_newstore_actual.csv.gz")),
        # 転換前データ。実運用では手元に無い前提だが、転換前後法の動作確認用に出す。
        ("idpos_qty_prior_sample.csv.gz",
         dump(qty_df, ex_q & ~qty_df["_post"], QTY_COLS,
              "idpos_qty_prior_sample.csv.gz")),
        ("ure_zaiko_prior_sample.csv.gz",
         dump(mg_df, ex_m & ~mg_df["_post"], MG_COLS, "ure_zaiko_prior_sample.csv.gz")),
        ("idpos_qty_newstore_baseline.csv.gz",
         dump(qty_df, ~ex_q & ~qty_df["_post"], QTY_COLS,
              "idpos_qty_newstore_baseline.csv.gz")),
        ("ure_zaiko_newstore_baseline.csv.gz",
         dump(mg_df, ~ex_m & ~mg_df["_post"], MG_COLS,
              "ure_zaiko_newstore_baseline.csv.gz")),
    ]

    print("生成しました:")
    print(f"   store_master.csv ({len(master)}行) / new_store.csv (1行)")
    for name, n in out:
        print(f"   {name}  ({n:,}行)")


if __name__ == "__main__":
    main()
