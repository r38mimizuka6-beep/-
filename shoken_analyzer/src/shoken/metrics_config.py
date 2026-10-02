"""metrics.yaml（予測対象カテゴリーと指標の定義）の読み込み。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TargetCategory:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Metric:
    key: str
    source: str
    label: str
    kind: str              # pi / rate
    required: bool = False
    higher_is_better: bool = True
    digits: int = 2


@dataclass
class DerivedMetric:
    key: str
    label: str
    formula: str
    digits: int = 2
    note: str = ""


@dataclass
class MetricsConfig:
    raw: dict[str, Any]
    path: Path
    categories: list[TargetCategory]
    metrics: list[Metric]
    derived: list[DerivedMetric]

    @classmethod
    def load(cls, path: str | Path) -> "MetricsConfig":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(
            raw=raw,
            path=path,
            categories=[TargetCategory(**c) for c in raw["target_categories"]],
            metrics=[Metric(**m) for m in raw["metrics"]],
            derived=[DerivedMetric(**d) for d in raw.get("derived_metrics", [])],
        )

    # ---- 参照 ----
    @property
    def category_names(self) -> list[str]:
        return [c.name for c in self.categories]

    @property
    def idpos(self) -> dict[str, Any]:
        return self.raw["idpos"]

    @property
    def prediction(self) -> dict[str, Any]:
        return self.raw["prediction"]

    @property
    def risk(self) -> dict[str, Any]:
        return self.raw["risk"]

    @property
    def verification(self) -> dict[str, Any]:
        return self.raw["verification"]

    @property
    def pi_definition(self) -> str:
        return self.raw["meta"].get("pi_definition", "")

    def alias_map(self) -> dict[str, str]:
        """実データの表記 -> 正式なカテゴリー名"""
        m: dict[str, str] = {}
        for c in self.categories:
            m[_norm(c.name)] = c.name
            for a in c.aliases:
                m[_norm(a)] = c.name
        return m

    def metric(self, key: str) -> Metric | DerivedMetric | None:
        for m in self.metrics:
            if m.key == key:
                return m
        for d in self.derived:
            if d.key == key:
                return d
        return None

    def metric_label(self, key: str) -> str:
        m = self.metric(key)
        return m.label if m else key

    def all_metric_keys(self) -> list[str]:
        return [m.key for m in self.metrics] + [d.key for d in self.derived]


def _norm(s: str) -> str:
    return str(s).strip().replace("　", "").replace(" ", "").lower()


def normalize_category(value: str, alias_map: dict[str, str]) -> str | None:
    return alias_map.get(_norm(value))
