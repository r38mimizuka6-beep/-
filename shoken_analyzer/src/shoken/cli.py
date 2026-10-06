"""コマンドラインインターフェース。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .io_loader import InputError
from .metrics_config import MetricsConfig
from .pipeline import run_analysis
from .report import write_excel, write_html, write_verify_excel, write_verify_html
from .templates import write_templates

ROOT = Path(__file__).resolve().parents[2]
CFG = ROOT / "config" / "columns.yaml"
MCFG = ROOT / "config" / "metrics.yaml"
MAP = ROOT / "config" / "extract_shoken_report.yaml"
SAMPLE = ROOT / "data" / "sample"
LEVEL_CHOICES = ["line", "department", "category", "subcategory"]


def _weights(values: list[str] | None) -> dict[str, float]:
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
        description="新店の商圏データから、低温カテゴリーの粗利率・PI値・顧客層を予測します。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""使用例:
  python run_report.py demo
  python run_report.py demo --level subcategory
  python run_report.py template --out data/templates
  python run_report.py extract --xlsx 商圏/*.xlsx --out data/master_auto.csv
  python run_report.py search-plan --xlsx 商圏/*.xlsx --out data/search
  python run_report.py report --master m.csv --new n.csv \\
      --idpos IDPOS.csv --margin URE_ZAIKO.csv --level category
  # 業態転換リニューアルの場合（転換前後法が使える・推奨）
  python run_report.py report --master m.csv --new n.csv \\
      --idpos 転換後IDPOS.csv      --margin 転換後URE.csv \\
      --prior-idpos 転換前IDPOS.csv --prior-margin 転換前URE.csv \\
      --baseline-idpos 新店の転換前IDPOS.csv --baseline-margin 新店の転換前URE.csv
  python run_report.py verify --prediction output/predictions_0199.json \\
      --idpos 新店IDPOS.csv --margin 新店URE_ZAIKO.csv \\
      --ref-idpos 既存IDPOS.csv --ref-margin 既存URE_ZAIKO.csv
  python run_report.py weights --master m.csv --idpos IDPOS.csv --margin URE.csv
""")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(x):
        x.add_argument("--config", default=str(CFG))
        x.add_argument("--metrics", default=str(MCFG))

    r = sub.add_parser("report", help="予測レポートを出力する")
    common(r)
    r.add_argument("--master", required=True, help="既存店の商圏マスタ CSV/Excel")
    r.add_argument("--new", required=True, help="新店の商圏情報（1行）")
    r.add_argument("--idpos", required=True, nargs="+",
                   help="既存店の週次IDPOS CSV（種類が複数あるなら並べて渡す）")
    r.add_argument("--margin", nargs="+", help="既存店の週次 売上在庫（粗利）CSV（複数可）")
    r.add_argument("--customers", help="店舗CD×年週×レジ通過客数のCSV（任意。渡すと粗利PIが出せる）")
    r.add_argument("--prior-idpos", help="既存店の【業態転換前】の週次IDPOS")
    r.add_argument("--prior-margin", help="既存店の【業態転換前】の週次 売上在庫")
    r.add_argument("--prior-customers", help="既存店の【業態転換前】の客数CSV")
    r.add_argument("--baseline-idpos", help="新店（同一立地）の【転換前】の週次IDPOS")
    r.add_argument("--baseline-margin", help="新店（同一立地）の【転換前】の週次 売上在庫")
    r.add_argument("--baseline-customers", help="新店（同一立地）の【転換前】の客数CSV")
    r.add_argument("--search", help="店舗名検索で埋めた search_profile.csv")
    r.add_argument("--level", choices=LEVEL_CHOICES, help="分析粒度（既定: metrics.yaml の設定）")
    r.add_argument("--out", default="output/report.html")
    r.add_argument("--excel", help="Excelの出力先")
    r.add_argument("--prediction-out", help="予測の保存先JSON（既定: output/predictions_<店舗CD>.json）")
    r.add_argument("--weight", action="append", metavar="軸名=数値")
    r.add_argument("--top", type=int, help="類似店として採用する店舗数")
    r.add_argument("--no-level-scan", action="store_true", help="粒度別精度の計測を省く")

    v = sub.add_parser("verify", help="保存した予測と新店の実績を突き合わせる")
    common(v)
    v.add_argument("--prediction", required=True, help="report で保存された予測JSON")
    v.add_argument("--idpos", required=True, nargs="+", help="新店の週次IDPOS")
    v.add_argument("--margin", nargs="+", help="新店の週次 売上在庫（粗利）（複数可）")
    v.add_argument("--ref-idpos", required=True, nargs="+",
                   help="既存店の週次IDPOS（季節指数を借りる）")
    v.add_argument("--ref-margin", help="既存店の週次 売上在庫")
    v.add_argument("--level", choices=LEVEL_CHOICES)
    v.add_argument("--out", default="output/verify.html")
    v.add_argument("--excel", help="Excelの出力先")

    w = sub.add_parser("weights", help="軸の重みを振ってLOO誤差の変化を見る")
    common(w)
    w.add_argument("--master", required=True)
    w.add_argument("--idpos", required=True, nargs="+")
    w.add_argument("--margin", nargs="+")
    w.add_argument("--level", choices=LEVEL_CHOICES)

    d2 = sub.add_parser("dashboard", help="自己完結のHTMLダッシュボードを書き出す")
    common(d2)
    d2.add_argument("--master", required=True)
    d2.add_argument("--new", required=True)
    d2.add_argument("--idpos", required=True, nargs="+")
    d2.add_argument("--margin", nargs="+")
    d2.add_argument("--search")
    d2.add_argument("--level", choices=LEVEL_CHOICES)
    d2.add_argument("--warehouse", help="蓄積状況タブに出す warehouse ディレクトリ")
    d2.add_argument("--out", default="output/dashboard.html")

    t = sub.add_parser("template", help="入力CSVテンプレートを書き出す")
    t.add_argument("--out", default="data/templates")
    t.add_argument("--config", default=str(CFG))

    e = sub.add_parser("extract", help="商圏レポートExcelから商圏マスタの行を作る")
    e.add_argument("--xlsx", nargs="+", required=True)
    e.add_argument("--map", default=str(MAP))
    e.add_argument("--out", default="data/extracted_master.csv")
    e.add_argument("--demand-out", help="JICFS中分類別の商圏需要も出す場合の出力先")

    s = sub.add_parser("search-plan", help="店舗名から検索すべき項目のひな形を書き出す")
    s.add_argument("--xlsx", nargs="+", required=True, help="商圏レポート（ファイル名が店舗名）")
    s.add_argument("--out", default="data/search")

    d = sub.add_parser("demo", help="同梱のダミーデータで一通り動かす")
    d.add_argument("--level", choices=LEVEL_CHOICES, default="category")
    d.add_argument("--out", default="output/demo_report.html")
    d.add_argument("--excel", default="output/demo_report.xlsx")
    d.add_argument("--weight", action="append", metavar="軸名=数値")
    d.add_argument("--no-level-scan", action="store_true")
    d.add_argument("--skip-verify", action="store_true")
    return p


def _print_summary(a) -> None:
    print(f"\n新店: {a.new_name}（{a.new_id}）／ 粒度: {a.idpos.level_label()}"
          f"（{len(a.idpos.units)}単位）")
    if a.urban_new.score is not None:
        print(f"立地判定: {a.urban_new.verdict}（都市度 {a.urban_new.score:.2f}）")
    print("類似店: " + " / ".join(
        f"第{p.rank}位 {p.store_name}（類似度{p.similarity:.1f}）" for p in a.peers))
    if a.loo.mape:
        import numpy as np
        for met in a.idpos.metrics:
            vals = [v for (u, m), v in a.loo.mape.items() if m == met]
            base = [v for (u, m), v in a.loo.baseline_mape.items() if m == met]
            if not vals:
                continue
            btxt = f" / 全店平均 {np.mean(base):.1f}%" if base else ""
            print(f"  LOO誤差 {a.mcfg.metric_label(met)}: ±{np.mean(vals):.1f}%{btxt}")
    if a.conversion is not None and a.conversion.predictions:
        import numpy as np
        errs = [c.ratio.loo_mape for c in a.conversion.predictions
                if c.ratio.loo_mape is not None]
        print(f"転換前後法: {len(a.conversion.paired_stores)}店の前後ペアから算出"
              + (f"／変化率の1店抜き誤差 ±{np.mean(errs):.1f}%" if errs else ""))
        if not a.method_compare.empty:
            win = (a.method_compare["better"] == "転換前後法").sum()
            print(f"  実測誤差で転換前後法が勝った組み合わせ: "
                  f"{win} / {len(a.method_compare)}")
    risky = [r for r in a.risks if r.level in ("要対策", "警戒")]
    if risky:
        print("苦戦予想: " + "、".join(
            f"{a.idpos.leaf(r.unit)}({r.level})" for r in risky[:8]))
    for w in a.warnings:
        print(f"[注意] {w}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.cmd == "template":
            for f in write_templates(Config.load(args.config), args.out):
                print(f"書き出しました: {f}")
            return 0

        if args.cmd == "extract":
            from .extract_xlsx import extract_category_demand, extract_many
            df = extract_many(args.xlsx, args.map, args.out)
            print(f"{len(df)}店舗を書き出しました: {args.out}")
            empty = [c for c in df.columns if df[c].isna().all()]
            print(f"  値が入った列: {len(df.columns) - len(empty)} / {len(df.columns)}")
            if empty:
                print("  検索・手入力が必要な列: " + "、".join(empty))
                print("  → python run_report.py search-plan --xlsx ... で調査メモを作れます")
            if args.demand_out:
                dd = extract_category_demand(args.xlsx[0], args.map)
                Path(args.demand_out).parent.mkdir(parents=True, exist_ok=True)
                dd.to_csv(args.demand_out, index=False, encoding="utf-8-sig")
                print(f"商圏カテゴリ需要: {args.demand_out}（{len(dd)}行）")
            return 0

        if args.cmd == "search-plan":
            from .search_profile import make_search_plan
            csv_path, md_path, names = make_search_plan(args.xlsx, args.out)
            print(f"店舗名を{len(names)}件取り出しました: {'、'.join(names)}")
            print(f"  調査メモ: {md_path}")
            print(f"  記入用CSV: {csv_path}")
            print("  調べた結果をCSVに書き込み、report --search で渡してください。")
            return 0

        if args.cmd == "weights":
            from .idpos import load_idpos
            from .io_loader import load_store_master
            from .predict import weight_sensitivity
            cfg, mcfg = Config.load(args.config), MetricsConfig.load(args.metrics)
            stores = load_store_master(args.master, cfg)
            idpos = load_idpos(args.idpos, args.margin, mcfg, level=args.level)
            stores = stores[stores[cfg.id_key].astype(str).isin(idpos.store_ids)]
            df = weight_sensitivity(stores, cfg, idpos, mcfg)
            print(df.to_string(index=False))
            spread = df["mean_mape"].max() - df["mean_mape"].min()
            print(f"\n最良と最悪の差: {spread:.2f}pt")
            if spread < 1.0:
                print("誤差の地形はほぼ平坦です。重みを変えても結果は変わりません。"
                      "既定の等重みのままで構いません。")
            else:
                print("重みで差が出ますが、検証点は5店分しかありません。"
                      "ここで最良の重みを選ぶこと自体が過学習になり得ます。"
                      "現場の感覚と合う重みを選んでください。")
            return 0

        if args.cmd == "verify":
            from .idpos import load_idpos
            from .verify import verify_predictions
            mcfg = MetricsConfig.load(args.metrics)
            actual = load_idpos(args.idpos, args.margin, mcfg, level=args.level)
            ref = load_idpos(args.ref_idpos, args.ref_margin, mcfg, level=args.level)
            v = verify_predictions(args.prediction, actual, mcfg, ref)
            print(f"予実検証: {write_verify_html(v, mcfg, args.out)}")
            if args.excel:
                print(f"Excel: {write_verify_excel(v, mcfg, args.excel)}")
            print(f"\n{v.store_name} / 実績{v.weeks_observed}週"
                  f"（開店直後{v.excluded_first_weeks}週を除外）")
            for k, n in v.summary.items():
                print(f"  {k}: {n}件")
            for r in v.rows:
                if r.verdict in ("下振れ", "上振れ"):
                    print(f"  [{r.verdict}] {r.unit} / {r.metric_label}: "
                          f"予測 {r.predicted:,.2f} → 実績 {r.actual_adjusted:,.2f}"
                          f"（{r.diff_pct:+.1f}%）")
            for w in v.warnings:
                print(f"[注意] {w}")
            return 0

        if args.cmd == "dashboard":
            from .dashboard_html import write_dashboard
            a = run_analysis(
                config_path=args.config, metrics_path=args.metrics,
                master_path=args.master, new_store_path=args.new,
                idpos_path=args.idpos, margin_path=args.margin,
                search_path=args.search, level=args.level, level_scan=False,
            )
            cov = None
            if args.warehouse:
                from .warehouse import Warehouse
                cov = Warehouse(args.warehouse).coverage()
            path = write_dashboard(a, args.out, coverage=cov)
            print(f"HTMLダッシュボード: {path}")
            _print_summary(a)
            return 0

        if args.cmd == "demo":
            a = run_analysis(
                config_path=CFG, metrics_path=MCFG,
                master_path=SAMPLE / "store_master.csv",
                new_store_path=SAMPLE / "new_store.csv",
                idpos_path=SAMPLE / "idpos_sample.csv.gz",
                margin_path=SAMPLE / "ure_zaiko_sample.csv.gz",
                level=args.level,
                weight_overrides=_weights(args.weight),
                level_scan=not args.no_level_scan,
            )
        else:
            a = run_analysis(
                config_path=args.config, metrics_path=args.metrics,
                master_path=args.master, new_store_path=args.new,
                idpos_path=args.idpos, margin_path=args.margin,
                customers_path=args.customers,
                prior_idpos_path=args.prior_idpos, prior_margin_path=args.prior_margin,
                prior_customers_path=args.prior_customers,
                baseline_idpos_path=args.baseline_idpos,
                baseline_margin_path=args.baseline_margin,
                baseline_customers_path=args.baseline_customers,
                search_path=args.search, level=args.level,
                weight_overrides=_weights(args.weight), top_n=args.top,
                level_scan=not args.no_level_scan,
            )
    except InputError as e:
        print(f"\n[入力エラー]\n{e}\n", file=sys.stderr)
        return 2

    from .verify import save_predictions
    print(f"HTMLレポート: {write_html(a, args.out)}")
    if args.cmd == "demo":
        from .dashboard_html import write_dashboard
        from .warehouse import Warehouse
        whdir = ROOT / "warehouse"
        cov = Warehouse(whdir).coverage() if whdir.exists() else None
        print("HTMLダッシュボード: "
              f"{write_dashboard(a, 'output/demo_dashboard.html', coverage=cov)}")
    if getattr(args, "excel", None):
        print(f"Excelレポート: {write_excel(a, args.excel)}")
    pred_path = (getattr(args, "prediction_out", None)
                 or f"output/predictions_{a.new_id}.json")
    save_predictions(a.predictions, store_name=a.new_name, store_id=a.new_id,
                     path=pred_path, axis_weights=a.similarity.weights,
                     extra={"level": a.idpos.level, "peers": [p.store_name for p in a.peers]})
    print(f"予測を保存: {pred_path}（開店後に verify で突き合わせます）")
    _print_summary(a)

    if args.cmd == "demo" and not getattr(args, "skip_verify", False):
        from .idpos import load_idpos
        from .verify import verify_predictions
        actual = load_idpos(SAMPLE / "idpos_newstore_actual.csv.gz",
                            SAMPLE / "ure_zaiko_newstore_actual.csv.gz",
                            a.mcfg, level=a.idpos.level)
        v = verify_predictions(pred_path, actual, a.mcfg, a.idpos)
        print(f"\n（デモ）新店の実績12週で答え合わせ: "
              f"{write_verify_html(v, a.mcfg, 'output/demo_verify.html')}")
        for k, n in v.summary.items():
            print(f"  {k}: {n}件")
    return 0
