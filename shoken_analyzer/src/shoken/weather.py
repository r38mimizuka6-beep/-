"""気温の取得と、カテゴリーの気温感応度。

低温カテゴリーは気温で大きく動く（アイス、冷凍麺、鍋物、ヨーグルトなど）。
週次実績を気温で説明できる分だけ、予測誤差の見かけ上の大きさが減り、
開店直後の実績が「暑かっただけ」なのか「本当に強い/弱い」のかを分けられる。

取得元は Open-Meteo（APIキー不要）。過去の実測と先の予報の両方が取れる。
ネットワークが使えない環境でも動くよう、手作業のCSVを読む経路も用意してある。

  weather.csv の列: store_id, year_week, temp_mean[, temp_max, temp_min]

注意: この取得処理は開発環境から外部へ出られないため、実接続での検証が
できていない。ダッシュボードの「接続テスト」で最初に確かめること。
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT = 20


@dataclass
class WeatherFetch:
    ok: bool
    daily: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, temp_mean...
    source: str = ""
    message: str = ""


def _get_json(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(f"{url}?{q}", timeout=TIMEOUT) as r:   # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def check_connection() -> WeatherFetch:
    """外部接続が使えるかを、小さなリクエストで確かめる。"""
    today = dt.date.today()
    try:
        data = _get_json(ARCHIVE_URL, {
            "latitude": 35.69, "longitude": 139.69,
            "start_date": (today - dt.timedelta(days=10)).isoformat(),
            "end_date": (today - dt.timedelta(days=8)).isoformat(),
            "daily": "temperature_2m_mean", "timezone": "Asia/Tokyo",
        })
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return WeatherFetch(False, message=f"外部へ接続できませんでした: {e}")
    except json.JSONDecodeError as e:
        return WeatherFetch(False, message=f"応答を解釈できませんでした: {e}")
    daily = data.get("daily") or {}
    if "time" not in daily or "temperature_2m_mean" not in daily:
        return WeatherFetch(False, message=f"想定した項目がありません: {sorted(daily)}")
    return WeatherFetch(True, source="open-meteo",
                        message=f"接続できました（{len(daily['time'])}日分を取得）")


def geocode(place: str) -> tuple[float, float] | None:
    """店舗名や住所から緯度経度を引く。失敗したら None。"""
    try:
        data = _get_json(GEOCODE_URL, {"name": place, "count": 1,
                                       "language": "ja", "country": "JP"})
    except Exception:                                        # noqa: BLE001
        return None
    hits = data.get("results") or []
    if not hits:
        return None
    return float(hits[0]["latitude"]), float(hits[0]["longitude"])


def fetch_daily(lat: float, lon: float, start: dt.date, end: dt.date) -> WeatherFetch:
    """期間の日別平均気温。過去は実測、未来は予報。"""
    today = dt.date.today()
    frames, sources, errors = [], [], []

    def _pull(url: str, s: dt.date, e: dt.date, tag: str) -> None:
        if s > e:
            return
        try:
            data = _get_json(url, {
                "latitude": lat, "longitude": lon,
                "start_date": s.isoformat(), "end_date": e.isoformat(),
                "daily": ["temperature_2m_mean", "temperature_2m_max",
                          "temperature_2m_min"],
                "timezone": "Asia/Tokyo",
            })
        except Exception as ex:                              # noqa: BLE001
            errors.append(f"{tag}: {ex}")
            return
        d = data.get("daily") or {}
        if "time" not in d:
            errors.append(f"{tag}: 応答に time がありません")
            return
        frames.append(pd.DataFrame({
            "date": pd.to_datetime(d["time"]),
            "temp_mean": d.get("temperature_2m_mean"),
            "temp_max": d.get("temperature_2m_max"),
            "temp_min": d.get("temperature_2m_min"),
        }))
        sources.append(tag)

    _pull(ARCHIVE_URL, start, min(end, today - dt.timedelta(days=1)), "実測")
    if end >= today:
        _pull(FORECAST_URL, max(start, today), end, "予報")

    if not frames:
        return WeatherFetch(False, message="／".join(errors) or "取得できませんでした")
    df = pd.concat(frames).dropna(subset=["date"]).sort_values("date")
    return WeatherFetch(True, df.reset_index(drop=True), "＋".join(sources),
                        f"{len(df)}日分を取得しました"
                        + (f"（一部失敗: {'／'.join(errors)}）" if errors else ""))


def to_weekly(daily: pd.DataFrame, store_id: str) -> pd.DataFrame:
    """日別気温を ISO年週 の平均にまとめる。"""
    if daily.empty:
        return pd.DataFrame(columns=["store_id", "year_week", "temp_mean"])
    d = daily.copy()
    iso = pd.to_datetime(d["date"]).dt.isocalendar()
    d["year_week"] = iso["year"].astype(int).astype(str) + iso["week"].astype(int).map("{:02d}".format)
    g = d.groupby("year_week", as_index=False).agg(
        temp_mean=("temp_mean", "mean"), temp_max=("temp_max", "mean"),
        temp_min=("temp_min", "mean"), n_days=("date", "count"))
    g.insert(0, "store_id", str(store_id))
    return g


def load_weather_csv(path) -> pd.DataFrame:
    """手作業で用意した気温CSVを読む（ネットが使えない場合の経路）。"""
    for enc in ("utf-8-sig", "cp932"):
        try:
            df = pd.read_csv(path, encoding=enc, dtype={"store_id": str})
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("文字コードを判別できません")
    df.columns = [str(c).strip() for c in df.columns]
    need = {"store_id", "year_week", "temp_mean"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"気温CSVに必須列がありません: {sorted(missing)}")
    df["store_id"] = df["store_id"].astype(str).str.strip()
    df["year_week"] = df["year_week"].astype(str).str.strip()
    df["temp_mean"] = pd.to_numeric(df["temp_mean"], errors="coerce")
    return df


# ---------------------------------------------------------------- 感応度

@dataclass
class TempSensitivity:
    unit: str
    metric: str
    slope_pct_per_deg: float     # 気温1℃あたりの変化（%）
    r2: float
    n_weeks: int
    n_stores: int

    @property
    def meaningful(self) -> bool:
        return self.n_weeks >= 20 and self.r2 >= 0.15 and abs(self.slope_pct_per_deg) >= 0.5


def estimate_sensitivity(
    panel: pd.DataFrame, weather: pd.DataFrame, metrics: list[str],
    *, min_weeks: int = 20,
) -> list[TempSensitivity]:
    """週次パネルに気温を結合し、単位ごとに気温感応度を測る。

    店舗ごとの水準差を消すため、各店の平均で割った相対値に対して回帰する。
    """
    if panel.empty or weather.empty:
        return []
    p = panel.copy()
    p["year_week"] = (p["year"].astype("Int64").astype(str)
                      + p["week"].astype("Int64").map(lambda x: f"{x:02d}"
                                                      if pd.notna(x) else ""))
    w = weather[["store_id", "year_week", "temp_mean"]].dropna()
    d = p.merge(w, on=["store_id", "year_week"], how="inner")
    if d.empty:
        return []

    out: list[TempSensitivity] = []
    for unit, g in d.groupby("unit"):
        for metric in metrics:
            if metric not in g.columns:
                continue
            sub = g[["store_id", "temp_mean", metric]].dropna()
            if len(sub) < min_weeks:
                continue
            base = sub.groupby("store_id")[metric].transform("mean")
            sub = sub[base > 0]
            if len(sub) < min_weeks:
                continue
            y = (sub[metric] / base[sub.index]).to_numpy()
            x = sub["temp_mean"].to_numpy()
            if np.std(x) < 1e-6:
                continue
            slope, intercept = np.polyfit(x, y, 1)
            pred = slope * x + intercept
            ss_res = float(((y - pred) ** 2).sum())
            ss_tot = float(((y - y.mean()) ** 2).sum())
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            out.append(TempSensitivity(
                unit=str(unit), metric=metric,
                slope_pct_per_deg=float(slope * 100), r2=float(r2),
                n_weeks=len(sub), n_stores=int(sub["store_id"].nunique()),
            ))
    out.sort(key=lambda s: -abs(s.slope_pct_per_deg))
    return out


def adjust_for_temperature(
    value: float, observed_temp: float, baseline_temp: float,
    sens: TempSensitivity | None,
) -> tuple[float, str]:
    """観測値を「平年並みの気温だったら」に補正する。"""
    if sens is None or not sens.meaningful or not np.isfinite(observed_temp) \
            or not np.isfinite(baseline_temp):
        return value, "気温補正なし"
    gap = observed_temp - baseline_temp
    factor = 1 + sens.slope_pct_per_deg / 100 * gap
    if factor <= 0:
        return value, "気温補正なし（係数が不正）"
    return value / factor, (
        f"気温補正: 観測期間は平年より{gap:+.1f}℃、"
        f"感応度{sens.slope_pct_per_deg:+.1f}%/℃ → {1 / factor:.3f}倍で補正"
    )
