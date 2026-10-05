"""蓄積データの保管庫。

判定したい新店の商圏データとは分けて、既存店の実績をここに貯める。
データはこのフォルダから外に出ない（ダッシュボードもローカルで動く）。

  warehouse/
    master/store_master.csv     既存店の商圏マスタ
    idpos/<元のファイル名>       週次IDPOS（週単位で追記）
    margin/<元のファイル名>      週次 売上在庫
    search/search_profile.csv   店舗名検索で埋めた項目
    weather/weather.csv         店舗×週の平均気温
    predictions/*.json          出力した予測（答え合わせ用）
    outcomes/*.json             予実検証の結果履歴
    state.json                  学習したパラメータと履歴
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SUBDIRS = ["master", "idpos", "margin", "search", "weather", "predictions", "outcomes"]


@dataclass
class IngestResult:
    kind: str
    filename: str
    stored_as: str | None
    rows: int
    stores: list[str] = field(default_factory=list)
    weeks: list[str] = field(default_factory=list)
    status: str = "ok"          # ok / duplicate / replaced / error
    message: str = ""


@dataclass
class Coverage:
    frame: pd.DataFrame          # store_id × year_week の有無
    stores: list[str]
    weeks: list[str]
    gaps: list[str]              # 欠けている店舗×週の説明


class Warehouse:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        for d in SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    # ---------------- パス ----------------
    @property
    def master_path(self) -> Path:
        return self.root / "master" / "store_master.csv"

    @property
    def search_path(self) -> Path:
        return self.root / "search" / "search_profile.csv"

    @property
    def weather_path(self) -> Path:
        return self.root / "weather" / "weather.csv"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def idpos_files(self) -> list[Path]:
        return sorted((self.root / "idpos").glob("*.csv*"))

    def margin_files(self) -> list[Path]:
        return sorted((self.root / "margin").glob("*.csv*"))

    def prediction_files(self) -> list[Path]:
        return sorted((self.root / "predictions").glob("*.json"))

    def outcome_files(self) -> list[Path]:
        return sorted((self.root / "outcomes").glob("*.json"))

    # ---------------- 取り込み ----------------
    def ingest(self, kind: str, filename: str, data: bytes) -> IngestResult:
        """IDPOS / 粗利ファイルを蓄積する。同じ中身の再投入は弾く。"""
        if kind not in ("idpos", "margin"):
            raise ValueError(f"kind は idpos か margin です: {kind}")
        digest = hashlib.sha256(data).hexdigest()[:12]
        dest_dir = self.root / kind
        safe = Path(filename).name
        dest = dest_dir / f"{Path(safe).stem}__{digest}{''.join(Path(safe).suffixes)}"

        for existing in dest_dir.glob(f"*__{digest}*"):
            return IngestResult(kind, filename, existing.name, 0,
                                status="duplicate",
                                message="同じ内容のファイルが既に入っています。何もしませんでした。")
        dest.write_bytes(data)
        try:
            info = self._peek(dest)
        except Exception as e:                              # noqa: BLE001
            dest.unlink(missing_ok=True)
            return IngestResult(kind, filename, None, 0, status="error",
                                message=f"読み込めませんでした: {e}")
        return IngestResult(kind, filename, dest.name, info["rows"],
                            info["stores"], info["weeks"], "ok",
                            f"{info['rows']:,}行を取り込みました。")

    def put_text(self, kind: str, filename: str, data: bytes) -> Path:
        """マスタ・検索・気温など、1枚で上書きするもの。"""
        mapping = {"master": self.master_path, "search": self.search_path,
                   "weather": self.weather_path}
        if kind not in mapping:
            raise ValueError(f"kind が不正です: {kind}")
        dest = mapping[kind]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            bak = dest.with_suffix(dest.suffix + f".bak{dt.datetime.now():%Y%m%d%H%M%S}")
            shutil.copy2(dest, bak)
        dest.write_bytes(data)
        return dest

    @staticmethod
    def _peek(path: Path) -> dict:
        for enc in ("cp932", "utf-8-sig", "utf-8"):
            try:
                df = pd.read_csv(path, encoding=enc, dtype=str)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("文字コードを判別できません")
        cols = {c.strip(): c for c in df.columns}
        sc = cols.get("店舗CD") or cols.get("店舗コード")
        wc = cols.get("年週")
        weeks: list[str] = []
        if wc:
            # 年週の表記はファイルによって違う（202640 / 2026年40週）。
            # 揃えてからでないと、同じ週を別物として数えてしまう。
            from .idpos import parse_year_week
            seen = set()
            for raw in df[wc].astype(str).str.strip().unique():
                y, w = parse_year_week(raw)
                if y and w:
                    seen.add(f"{y}{w:02d}")
            weeks = sorted(seen)
        return {
            "rows": len(df),
            "stores": sorted(df[sc].astype(str).str.strip().unique()) if sc else [],
            "weeks": weeks,
        }

    # ---------------- 状況 ----------------
    def coverage(self) -> Coverage:
        """どの店舗のどの週が入っているかを一覧にする。"""
        rows = []
        for kind, files in (("idpos", self.idpos_files()), ("margin", self.margin_files())):
            for f in files:
                try:
                    info = self._peek(f)
                except Exception:                            # noqa: BLE001
                    continue
                for s in info["stores"]:
                    for w in info["weeks"]:
                        rows.append({"kind": kind, "store_id": s, "year_week": w})
        if not rows:
            return Coverage(pd.DataFrame(), [], [], ["まだ何も入っていません。"])
        df = pd.DataFrame(rows).drop_duplicates()
        piv = (df.assign(v=1)
               .pivot_table(index="store_id", columns="year_week", values="v",
                            aggfunc="max", fill_value=0))
        stores = sorted(piv.index.astype(str))
        weeks = sorted(str(c) for c in piv.columns)

        gaps = []
        idp = set(map(tuple, df[df["kind"] == "idpos"][["store_id", "year_week"]].values))
        mgn = set(map(tuple, df[df["kind"] == "margin"][["store_id", "year_week"]].values))
        only_i, only_m = idp - mgn, mgn - idp
        if only_i:
            gaps.append(f"IDPOSはあるが粗利が無い 店舗×週: {len(only_i)}件"
                        "（粗利率・粗利PIがその週だけ欠けます）")
        if only_m:
            gaps.append(f"粗利はあるがIDPOSが無い 店舗×週: {len(only_m)}件")
        for s in stores:
            ws = sorted(df[df["store_id"] == s]["year_week"].unique())
            if len(ws) >= 2:
                missing = _missing_weeks(ws)
                if missing:
                    gaps.append(f"店舗{s}: 週の抜け {len(missing)}件"
                                f"（{'、'.join(missing[:5])}{' ほか' if len(missing) > 5 else ''}）")
        return Coverage(piv, stores, weeks, gaps)

    # ---------------- 状態（学習したもの） ----------------
    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {"version": 1, "history": [], "params": {}, "proposals": []}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def save_state(self, state: dict) -> Path:
        state["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        return self.state_path

    def append_history(self, entry: dict) -> dict:
        state = self.load_state()
        entry["at"] = dt.datetime.now().isoformat(timespec="seconds")
        state.setdefault("history", []).append(entry)
        self.save_state(state)
        return state


def _missing_weeks(weeks: list[str]) -> list[str]:
    """年週の連番の抜けを探す。52週/年として扱う。"""
    def to_i(w: str) -> int | None:
        w = str(w).strip()
        digits = "".join(ch for ch in w if ch.isdigit())
        if len(digits) < 5:
            return None
        y, n = int(digits[:4]), int(digits[4:6] if len(digits) >= 6 else digits[4:])
        return y * 52 + n
    idx = sorted(i for i in (to_i(w) for w in weeks) if i)
    if len(idx) < 2:
        return []
    have = set(idx)
    out = []
    for i in range(idx[0], idx[-1] + 1):
        if i not in have:
            out.append(f"{i // 52}年{i % 52 or 52}週")
    return out
