"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .io_loader import InputError
from .pipeline import run_analysis
from .report import write_excel, write_html
from .templates import write_templates

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "columns.yaml"
DEFAULT_MAP = ROOT / "config" / "extract_shoken_report.yaml"
SAMPLE = ROOT / "data" / "sample"


def _parse_weights(values: list[str] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for v in values or []:
        if "=" not in v:
            raise SystemExit(f"--weight は 軸名=数値 の形で指定してください: {v}")
        k, s = v.split("=", 1)
        try:
            out[k.strip()] = float(s)
        except ValueError:
            raise SystemExit(f"重みが数値ではありません: {v}") from None
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_report.py",
        description="新店の商圏データから類似店判定レポートを出力します。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""使用例:
  python run_report.py demo
  python run_report.py template --out data/templates
  python run_report.py extract --xlsx 店A.xlsx 店B.xlsx --out master_auto.csv
  python run_report.py report --master m.csv --new n.csv --sales s.csv --member b.csv \\
      --weight daynight=2.0 --weight mobility=0.5 --out output/report.html
""")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="レポートを出力する")
    r.add_argument("--master", required=True, help="既存店マスタ CSV/Excel")
    r.add_argument("--new", required=True, help="新店の商圏情報 CSV/Excel（1行）")
    r.add_argument("--sales", required=True, help="カテゴリ別売上構成比・粗利 CSV")
    r.add_argument("--member", help="会員の性別年代構成比 CSV（任意）")
    r.add_argument("--config", default=str(DEFAULT_CONFIG), help="項目定義ファイル")
    r.add_argument("--out", default="output/report.html", help="HTMLの出力先")
    r.add_argument("--excel", help="Excelも出す場合の出力先")
    r.add_argument("--weight", action="append", metavar="軸名=数値",
                   help="軸の重み（daynight / household / mobility）。複数指定可")
    r.add_argument("--top", type=int, help="類似店として採用する店舗数（既定2）")

    t = sub.add_parser("template", help="入力CSVテンプレートを書き出す")
    t.add_argument("--out", default="data/templates")
    t.add_argument("--config", default=str(DEFAULT_CONFIG))

    e = sub.add_parser("extract", help="商圏レポートExcelから既存店マスタの行を作る")
    e.add_argument("--xlsx", nargs="+", required=True, help="商圏レポートのブック（1店舗=1ファイル）")
    e.add_argument("--map", default=str(DEFAULT_MAP), help="抽出マップ")
    e.add_argument("--out", default="data/extracted_master.csv")
    e.add_argument("--demand-out", help="JICFS中分類別の商圏需要も出す場合の出力先CSV")

    d = sub.add_parser("demo", help="同梱のダミーデータでレポートを出す")
    d.add_argument("--out", default="output/demo_report.html")
    d.add_argument("--excel", default="output/demo_report.xlsx")
    d.add_argument("--weight", action="append", metavar="軸名=数値")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.cmd == "template":
            cfg = Config.load(args.config)
            for f in write_templates(cfg, args.out):
                print(f"書き出しました: {f}")
            return 0

        if args.cmd == "extract":
            from .extract_xlsx import extract_category_demand, extract_many
            df = extract_many(args.xlsx, args.map, args.out)
            print(f"{len(df)}店舗を書き出しました: {args.out}")
            filled = [c for c in df.columns if df[c].notna().any()]
            print(f"  値が入った列: {len(filled)} / {len(df.columns)}")
            empty = [c for c in df.columns if df[c].isna().all()]
            if empty:
                print("  手入力が必要な列: " + "、".join(empty))
            if args.demand_out:
                dd = extract_category_demand(args.xlsx[0], args.map)
                Path(args.demand_out).parent.mkdir(parents=True, exist_ok=True)
                dd.to_csv(args.demand_out, index=False, encoding="utf-8-sig")
                print(f"商圏カテゴリ需要を書き出しました: {args.demand_out}（{len(dd)}行）")
            return 0

        if args.cmd == "demo":
            a = run_analysis(
                config_path=DEFAULT_CONFIG,
                master_path=SAMPLE / "store_master.csv",
                new_store_path=SAMPLE / "new_store.csv",
                sales_path=SAMPLE / "sales_mix.csv",
                member_path=SAMPLE / "member_mix.csv",
                weight_overrides=_parse_weights(args.weight),
            )
        else:
            a = run_analysis(
                config_path=args.config,
                master_path=args.master,
                new_store_path=args.new,
                sales_path=args.sales,
                member_path=args.member,
                weight_overrides=_parse_weights(args.weight),
                top_n=args.top,
            )
    except InputError as e:
        print(f"\n[入力エラー]\n{e}\n", file=sys.stderr)
        return 2

    html_path = write_html(a, args.out)
    print(f"HTMLレポート: {html_path}")
    if getattr(args, "excel", None):
        print(f"Excelレポート: {write_excel(a, args.excel)}")

    print(f"\n新店: {a.new_name}")
    print(f"立地判定: {a.urban_new.verdict}（都市度 {a.urban_new.score:.2f}）"
          if a.urban_new.score is not None else "立地判定: 判定不能")
    print("類似店:")
    for p in a.peers:
        print(f"  第{p.rank}位 {p.store_name}  類似度 {p.similarity:.1f} / 距離 {p.distance:.3f}")
    print(f"信頼度: {a.confidence['level']} — {a.confidence['message']}")
    for w in a.warnings:
        print(f"[注意] {w}")
    return 0
