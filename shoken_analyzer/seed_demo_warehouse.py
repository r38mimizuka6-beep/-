#!/usr/bin/env python3
"""同梱のダミーデータで warehouse/ を作る（ダッシュボードの動作確認用）。"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from shoken.warehouse import Warehouse  # noqa: E402

S = ROOT / "data" / "sample"
wh = Warehouse(ROOT / "warehouse")
for f in (S / "idpos_sample.csv.gz",):
    print(wh.ingest("idpos", f.name, f.read_bytes()).message)
for f in (S / "ure_zaiko_sample.csv.gz",):
    print(wh.ingest("margin", f.name, f.read_bytes()).message)
wh.put_text("master", "store_master.csv", (S / "store_master.csv").read_bytes())
print("商圏マスタを登録しました")
cov = wh.coverage()
print(f"蓄積: {len(cov.stores)}店舗 × {len(cov.weeks)}週")
