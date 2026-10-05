"""ダッシュボードの色。

状態色（良い／注意／悪い）は必ず文字ラベルと一緒に使う。色だけで意味を運ばない。
明るい面（Streamlitの既定テーマ）向けに検証済み。
"""

GOOD = "#15803d"
WARN = "#ca8a04"
BAD = "#b91c1c"
ACCENT = "#1d4ed8"
MUTED = "#9aa2ab"
GRID = "#e3e6ea"
INK = "#1b1f23"

# 誤差の大きさ → 状態色と文字ラベル（色だけで判断させない）
def accuracy_band(mape: float | None) -> tuple[str, str]:
    if mape is None:
        return MUTED, "未測定"
    if mape < 8:
        return GOOD, "当たる"
    if mape < 15:
        return WARN, "幅をもって見る"
    return BAD, "当たらない"


def verdict_color(verdict: str) -> str:
    return {"想定内": GOOD, "ブレの範囲": MUTED, "上振れ": WARN,
            "下振れ": BAD, "判定保留": MUTED, "データなし": MUTED}.get(verdict, MUTED)
