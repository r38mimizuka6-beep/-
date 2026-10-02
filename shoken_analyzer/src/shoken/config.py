"""設定ファイルの読み込みと、設定から引ける小さなヘルパー。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Variable:
    key: str
    source: str
    label: str
    group: str
    kind: str
    enabled: bool = True

    @property
    def numeric(self) -> bool:
        return self.kind != "text"


@dataclass
class Config:
    raw: dict[str, Any]
    path: Path
    variables: dict[str, Variable] = field(default_factory=dict)

    # ---- 読み込み ----
    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        cfg = cls(raw=raw, path=path)
        for item in raw.get("variables", []):
            v = Variable(**item)
            if v.enabled:
                cfg.variables[v.key] = v
        return cfg

    # ---- 列名 ----
    @property
    def id_key(self) -> str:
        return self.raw["meta"]["id_column"]["key"]

    @property
    def id_source(self) -> str:
        return self.raw["meta"]["id_column"]["source"]

    @property
    def name_key(self) -> str:
        return self.raw["meta"]["name_column"]["key"]

    @property
    def name_source(self) -> str:
        return self.raw["meta"]["name_column"]["source"]

    @property
    def trade_area_definition(self) -> str:
        return self.raw["meta"].get("trade_area_definition", "(未設定)")

    def rename_map(self) -> dict[str, str]:
        """実データの列名 -> 内部キー"""
        m = {self.id_source: self.id_key, self.name_source: self.name_key}
        for v in self.variables.values():
            m[v.source] = v.key
        return m

    # ---- 軸 ----
    @property
    def axes(self) -> dict[str, dict[str, Any]]:
        return self.raw["axes"]

    def axis_weights(self, overrides: dict[str, float] | None = None) -> dict[str, float]:
        w = {k: float(v.get("weight", 1.0)) for k, v in self.axes.items()}
        if overrides:
            for k, val in overrides.items():
                if k not in w:
                    raise KeyError(f"未知の軸です: {k} (指定できるのは {sorted(w)})")
                w[k] = float(val)
        total = sum(w.values())
        if total <= 0:
            raise ValueError("軸の重みの合計が0です。少なくとも1つは正の値にしてください。")
        return w

    @property
    def derived(self) -> list[dict[str, str]]:
        return self.raw.get("derived", [])

    @property
    def urban_rural(self) -> dict[str, Any]:
        return self.raw["urban_rural"]

    @property
    def member_buckets(self) -> dict[str, Any]:
        return self.raw.get("member_compare_buckets", {})

    @property
    def report(self) -> dict[str, Any]:
        return self.raw.get("report", {})

    def label(self, key: str) -> str:
        if key in self.variables:
            return self.variables[key].label
        for d in self.derived:
            if d["key"] == key:
                return d["label"]
        return key

    def axis_of(self, key: str) -> str | None:
        for axis, spec in self.axes.items():
            if key in spec.get("variables", []):
                return axis
        return None

    def all_model_keys(self) -> list[str]:
        """軸に使う全変数（派生を含む）。"""
        keys: list[str] = []
        for spec in self.axes.values():
            for k in spec.get("variables", []):
                if k not in keys:
                    keys.append(k)
        return keys


_SAFE_FUNCS = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt}


def eval_formula(formula: str, values: dict[str, float]) -> float | None:
    """derived の式を評価する。使える名前は values と最小限の関数だけ。"""
    env = {k: v for k, v in values.items() if isinstance(v, (int, float))}
    try:
        result = eval(formula, {"__builtins__": {}}, {**_SAFE_FUNCS, **env})  # noqa: S307
    except (NameError, TypeError, ZeroDivisionError, ValueError):
        return None
    if result is None or (isinstance(result, float) and not math.isfinite(result)):
        return None
    return float(result)
