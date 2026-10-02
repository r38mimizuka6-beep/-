#!/usr/bin/env python3
"""ダミーの既存5店舗＋新店1店を生成する。

実データが来るまでの動作確認用。値は実在の店舗のものではないが、
商圏レポートの実物（自転車10分圏・都市部住宅地）の水準に合わせてある。
売上構成比は「商圏変数に反応する」ように作ってあるので、方向性分析が
意味のある出力を返すことを確認できる。
"""

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent
rng = np.random.default_rng(20251002)

# store_id, 店名, タイプ
STORES = [
    # 都市部4店
    dict(store_id="S01", store_name="都心A店（住宅地・単身多）", kind="urban_single",
         pop_total=204_382, pop_density=14_800, hh_total=109_917, hh_avg_size=1.86,
         age_share_0_14=0.113, age_share_15_29=0.216, age_share_30_44=0.237,
         age_share_45_64=0.269, age_share_65plus=0.166,
         age_cmp_15_29=0.216, age_cmp_30_49=0.33, age_cmp_50_69=0.267, age_cmp_70plus=0.074,
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
         pb_ratio=0.11, store_format="都市型SM"),

    dict(store_id="S02", store_name="駅前B店（オフィス・繁華街）", kind="urban_office",
         pop_total=98_400, pop_density=18_200, hh_total=62_100, hh_avg_size=1.58,
         age_share_0_14=0.072, age_share_15_29=0.268, age_share_30_44=0.271,
         age_share_45_64=0.250, age_share_65plus=0.139,
         age_cmp_15_29=0.268, age_cmp_30_49=0.351, age_cmp_50_69=0.249, age_cmp_70plus=0.06,
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
         pb_ratio=0.08, store_format="駅前小型SM"),

    dict(store_id="S03", store_name="住宅街C店（子育てファミリー）", kind="urban_family",
         pop_total=142_600, pop_density=9_400, hh_total=58_900, hh_avg_size=2.42,
         age_share_0_14=0.148, age_share_15_29=0.166, age_share_30_44=0.248,
         age_share_45_64=0.277, age_share_65plus=0.161,
         age_cmp_15_29=0.166, age_cmp_30_49=0.33, age_cmp_50_69=0.288, age_cmp_70plus=0.068,
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
         pb_ratio=0.14, store_format="郊外型SM（市街地）"),

    dict(store_id="S04", store_name="旧市街D店（高齢・徒歩）", kind="urban_senior",
         pop_total=88_200, pop_density=11_100, hh_total=44_600, hh_avg_size=1.98,
         age_share_0_14=0.089, age_share_15_29=0.151, age_share_30_44=0.196,
         age_share_45_64=0.284, age_share_65plus=0.280,
         age_cmp_15_29=0.151, age_cmp_30_49=0.258, age_cmp_50_69=0.312, age_cmp_70plus=0.131,
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
         pb_ratio=0.17, store_format="都市型SM"),

    # 郊外1店
    dict(store_id="S05", store_name="郊外E店（車商圏）", kind="rural_car",
         pop_total=46_300, pop_density=1_850, hh_total=17_200, hh_avg_size=2.69,
         age_share_0_14=0.132, age_share_15_29=0.138, age_share_30_44=0.213,
         age_share_45_64=0.288, age_share_65plus=0.229,
         age_cmp_15_29=0.138, age_cmp_30_49=0.284, age_cmp_50_69=0.322, age_cmp_70plus=0.124,
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
         pb_ratio=0.19, store_format="郊外型SM"),
]

NEW_STORE = dict(
    store_id="NEW01", store_name="新店（仮称）○○店", kind="new",
    pop_total=168_900, pop_density=12_300, hh_total=81_400, hh_avg_size=2.07,
    age_share_0_14=0.124, age_share_15_29=0.198, age_share_30_44=0.251,
    age_share_45_64=0.265, age_share_65plus=0.162,
    age_cmp_15_29=0.198, age_cmp_30_49=0.334, age_cmp_50_69=0.283, age_cmp_70plus=0.072,
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
    pb_ratio=0.13, store_format="都市型SM",
)

# ---------------- カテゴリ別売上構成比 ----------------
# base: 既存店の平均的な構成比
# drivers: 商圏変数が標準偏差1つ分動いたときの構成比の変化（pt）
CATEGORIES = [
    # (大分類, カテゴリ, base%, {driver: pt/sd})
    ("生鮮", "青果",       12.8, {"hh_share_with_child": 0.9, "day_night_ratio": -0.8, "share_car": 0.6}),
    ("生鮮", "精肉",        9.6, {"hh_share_with_child": 1.0, "hh_share_single": -0.9}),
    ("生鮮", "鮮魚",        6.9, {"age_share_65plus": 1.2, "hh_share_single": -0.7}),
    ("惣菜", "惣菜・弁当",  11.4, {"hh_share_single": 1.6, "day_night_ratio": 1.1, "hh_share_with_child": -0.6}),
    ("惣菜", "ベーカリー",   3.1, {"day_night_ratio": 0.4, "hh_share_with_child": 0.3}),
    ("日配", "牛乳・乳製品", 5.2, {"hh_share_with_child": 0.8, "age_share_65plus": -0.3}),
    ("日配", "和日配",       4.4, {"age_share_65plus": 0.9, "hh_share_single": -0.4}),
    ("日配", "洋日配",       3.3, {"hh_share_single": 0.4, "hh_share_with_child": 0.3}),
    ("日配", "冷凍食品",     4.6, {"hh_share_single": 1.1, "share_car": 0.4}),
    ("グロサリー", "米・麺",  4.1, {"share_car": 1.0, "hh_share_single": -0.8}),
    ("グロサリー", "調味料",  3.8, {"hh_share_with_child": 0.5, "hh_share_single": -0.5}),
    ("グロサリー", "加工食品", 6.2, {"share_car": 0.6, "hh_share_with_child": 0.4}),
    ("グロサリー", "菓子",    5.7, {"hh_share_with_child": 1.1, "age_share_65plus": -0.8}),
    ("飲料・酒", "飲料",      7.3, {"day_night_ratio": 0.9, "share_car": 0.8}),
    ("飲料・酒", "酒類",      5.1, {"day_night_ratio": 1.0, "hh_share_single": 0.7, "age_share_65plus": -0.4}),
    ("非食品", "日用品",      4.2, {"share_car": 1.1, "hh_share_with_child": 0.4}),
    ("非食品", "ヘルス＆ビューティ", 1.8, {"hh_share_single": 0.4, "comp_drug_1km": -0.5}),
    ("非食品", "その他非食品", 0.5, {}),
]

MARGIN = {  # 粗利率の基準値
    "青果": 0.305, "精肉": 0.288, "鮮魚": 0.265, "惣菜・弁当": 0.415, "ベーカリー": 0.395,
    "牛乳・乳製品": 0.218, "和日配": 0.252, "洋日配": 0.271, "冷凍食品": 0.284,
    "米・麺": 0.172, "調味料": 0.246, "加工食品": 0.231, "菓子": 0.258,
    "飲料": 0.205, "酒類": 0.168, "日用品": 0.221, "ヘルス＆ビューティ": 0.312,
    "その他非食品": 0.335,
}

AGE_BANDS = ["10代", "20代", "30代", "40代", "50代", "60代", "70代以上"]
# 会員構成比の作り方: 商圏年齢構成に、店タイプごとの来店バイアスをかける
MEMBER_BASE = {
    "urban_single": [0.02, 0.19, 0.24, 0.20, 0.16, 0.12, 0.07],
    "urban_office": [0.03, 0.25, 0.26, 0.19, 0.14, 0.09, 0.04],
    "urban_family": [0.03, 0.11, 0.22, 0.23, 0.18, 0.14, 0.09],
    "urban_senior": [0.02, 0.08, 0.13, 0.17, 0.20, 0.21, 0.19],
    "rural_car":    [0.02, 0.09, 0.16, 0.20, 0.20, 0.18, 0.15],
}


def main() -> None:
    master = pd.DataFrame(STORES).drop(columns=["kind"])
    master["day_night_ratio"] = (
        pd.DataFrame(STORES)["daytime_pop"] / pd.DataFrame(STORES)["nighttime_pop"]
    ).round(4)
    new = pd.DataFrame([NEW_STORE]).drop(columns=["kind"])
    new["day_night_ratio"] = round(NEW_STORE["daytime_pop"] / NEW_STORE["nighttime_pop"], 4)

    cols = list(master.columns)
    master.to_csv(OUT / "store_master.csv", index=False, encoding="utf-8-sig")
    new[cols].to_csv(OUT / "new_store.csv", index=False, encoding="utf-8-sig")

    # 売上構成比: base + Σ(係数 × 標準化した商圏変数) + 微小ノイズ
    drivers = sorted({d for _, _, _, dd in CATEGORIES for d in dd})
    z = master[drivers].astype(float)
    z = (z - z.mean()) / z.std(ddof=1)

    rows = []
    for i, store in enumerate(STORES):
        raw = {}
        for major, cat, base, dd in CATEGORIES:
            val = base + sum(coef * float(z.iloc[i][d]) for d, coef in dd.items())
            raw[cat] = max(val + rng.normal(0, 0.12), 0.2)
        total = sum(raw.values())
        for major, cat, base, dd in CATEGORIES:
            share = raw[cat] / total
            margin = MARGIN[cat] + rng.normal(0, 0.008)
            sales = share * (store["pop_total"] * 1_900)  # ダミーの年商
            rows.append({
                "store_id": store["store_id"], "category_major": major, "category": cat,
                "sales_share": round(share, 5),
                "gross_margin_rate": round(margin, 4),
                "sales_amount": int(sales),
                "gross_profit": int(sales * margin),
            })
    pd.DataFrame(rows).to_csv(OUT / "sales_mix.csv", index=False, encoding="utf-8-sig")

    # 会員構成比（男女×年代）
    mrows = []
    for store in STORES:
        base = np.array(MEMBER_BASE[store["kind"]], dtype=float)
        base = base / base.sum()
        female_bias = 0.62 if store["kind"] != "urban_office" else 0.54
        for g, gshare in (("女", female_bias), ("男", 1 - female_bias)):
            tilt = 1.0 if g == "女" else 0.92
            vals = base * np.array([tilt ** k for k in range(len(base))])
            vals = vals / vals.sum() * gshare
            for band, v in zip(AGE_BANDS, vals):
                mrows.append({"store_id": store["store_id"], "gender": g,
                              "age_band": band, "member_share": round(float(v), 5)})
    pd.DataFrame(mrows).to_csv(OUT / "member_mix.csv", index=False, encoding="utf-8-sig")

    print("生成しました:")
    for f in ("store_master.csv", "new_store.csv", "sales_mix.csv", "member_mix.csv"):
        print("  ", OUT / f)


if __name__ == "__main__":
    main()
