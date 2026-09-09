"""kabutan_search CLIエントリーポイント

SPECIFICATION.md 4節のコマンド構成に対応する。各コマンドは実行して終了する(常駐しない)。
"""
import argparse
import sys
from pathlib import Path

from . import edinet_holdings_fetcher, fetcher, gemini_prompt, margin_analysis, report, screening, sector_data_manager, tracker
from .database import Database
from .nikkei_database import NikkeiDatabase
from .sector_divergence_analyzer import calculate_sector_divergence


def _databases(args: argparse.Namespace) -> tuple[Database, NikkeiDatabase]:
    return Database(args.db), NikkeiDatabase(args.nikkei_db)


def cmd_fetch(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    try:
        data = fetcher.fetch_stock(db, args.code)
    except (fetcher.RateLimitedError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return
    print(f"{data['code']} {data['name']}: 株価{data['price']}円 を保存しました。")


def _report_bulk_result(result: dict) -> None:
    msg = f"完了: 成功{len(result['succeeded'])}件 / 失敗{len(result['failed'])}件"
    if result["stopped_early"]:
        msg += f" (アクセス制限のため中断: {result.get('error', '')})"
    print(msg)


def cmd_fetch_watchlist(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    codes = sorted(db.get_tracked_codes() | {p["code"] for p in db.list_positions()})
    if not codes:
        print("お気に入り・ポジション登録銘柄がありません。先に `watch add <code>` してください。")
        return
    print(f"{len(codes)}件を取得します(株探への配慮のため間隔を空けます)...")
    result = fetcher.bulk_fetch(
        db, codes, on_progress=lambda code, data, i, n: print(f"  [{i}/{n}] {code} {data['name']}")
    )
    _report_bulk_result(result)


def cmd_fetch_candidates(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    candidates = screening.screen(db, fetch_sectors=not args.no_sector_fetch)
    codes = [c["code"] for c in candidates]
    if not codes:
        print("Stage1候補銘柄がありません。")
        return
    print(f"Stage1候補 {len(codes)}件を取得します(株探への配慮のため間隔を空けます)...")
    result = fetcher.bulk_fetch(
        db, codes, on_progress=lambda code, data, i, n: print(f"  [{i}/{n}] {code} {data['name']}")
    )
    _report_bulk_result(result)


def cmd_sector(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    records = sector_data_manager.fetch_sector_ranking(db)
    print(f"{len(records)}件のセクターデータを取得しました。")
    for s in calculate_sector_divergence(db):
        chg = s["change_pct"] or 0.0
        print(f"  {s['sector_name']}: {s['signal_badge']} (Z={s['z_score']:+.2f}, 騰落率={chg:+.2f}%)")


def cmd_screen(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    candidates = screening.screen(db)
    print(f"{len(candidates)}件の候補銘柄:")
    for c in candidates:
        print(f"  {c['code']}: score={c['screening_score']:.1f} ({c['reason']})")


def cmd_analyze(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    rows = margin_analysis.analyze_stocks(db, only_improving=not args.all)
    print(f"{len(rows)}件を分析しました。")
    for r in rows[: args.limit]:
        print(f"  {r['code']} {r['name']}: {r['squeeze_stars']} {r['evaluation']}")


def cmd_report(args: argparse.Namespace) -> None:
    db, nikkei_db = _databases(args)
    path = report.save_report(db, nikkei_db, only_improving=not args.all, out_dir=Path(args.out))
    print(f"レポートを保存しました: {path}")


def cmd_gemini_prompt(args: argparse.Namespace) -> None:
    db, nikkei_db = _databases(args)
    if args.kind == "ranking":
        text = margin_analysis.generate_ranking_prompt(db, nikkei_db, only_improving=not args.all)
    else:
        text = gemini_prompt.generate_portfolio_prompt(db)

    if not text:
        print("生成対象のデータがありません。", file=sys.stderr)
        return

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"プロンプトを保存しました: {args.out}")
    else:
        print(text)


def cmd_gemini_import(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    text = Path(args.file).read_text(encoding="utf-8")
    registered = gemini_prompt.register_recommendations(db, text)
    print(f"{len(registered)}件の推奨銘柄を登録しました。")
    for r in registered:
        print(f"  {r['code']} {r['name']}")


def cmd_watch_add(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    tracker.add_to_watchlist(db, args.code)
    print(f"{args.code} をお気に入りに追加しました。")


def cmd_watch_remove(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    tracker.remove_from_watchlist(db, args.code)
    print(f"{args.code} をお気に入りから削除しました。")


def cmd_watch_list(args: argparse.Namespace) -> None:
    db, _ = _databases(args)
    for w in tracker.list_watchlist_with_returns(db):
        print(f"  {w['code']} {w['name']}: {w['diff_pct']:+.2f}% {w['squeeze_stars']}")


def cmd_edinet_sync(args: argparse.Namespace) -> None:
    _, nikkei_db = _databases(args)
    result = edinet_holdings_fetcher.sync(nikkei_db, api_key=args.api_key)
    for msg in result["messages"]:
        print(msg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kabutan", description="株探信用需給分析CLI")
    parser.add_argument("--db", default="kabutan_stock.db", help="kabutan_stock.dbのパス")
    parser.add_argument("--nikkei-db", default="nikkei_data.db", help="nikkei_data.dbのパス")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="個別銘柄ページを取得してDBに保存")
    p.add_argument("code", help="4桁の証券コード")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("fetch-watchlist", help="お気に入り・ポジション銘柄をまとめて取得(配慮付き)")
    p.set_defaults(func=cmd_fetch_watchlist)

    p = sub.add_parser("fetch-candidates", help="Stage1候補銘柄をまとめて取得(配慮付き)")
    p.add_argument("--no-sector-fetch", action="store_true", help="screen実行時のセクターデータ再取得をスキップ")
    p.set_defaults(func=cmd_fetch_candidates)

    p = sub.add_parser("sector", help="セクターランキングを取得しモメンタムを表示")
    p.set_defaults(func=cmd_sector)

    p = sub.add_parser("screen", help="Stage1: 値上がり優位性候補を抽出")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("analyze", help="Stage2: 需給分析を実行")
    p.add_argument("--all", action="store_true", help="需給改善銘柄以外も含めて全銘柄表示")
    p.add_argument("--limit", type=int, default=20, help="表示件数")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("report", help="需給分析レポートをファイル出力")
    p.add_argument("--all", action="store_true")
    p.add_argument("--out", default="reports", help="出力先ディレクトリ")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("gemini-prompt", help="Gemini用プロンプトを生成")
    p.add_argument("--kind", choices=["ranking", "portfolio"], default="ranking")
    p.add_argument("--all", action="store_true")
    p.add_argument("--out", help="保存先ファイル(省略時は標準出力)")
    p.set_defaults(func=cmd_gemini_prompt)

    p = sub.add_parser("gemini-import", help="Geminiの応答を貼り付けたファイルを読み込み登録")
    p.add_argument("file")
    p.set_defaults(func=cmd_gemini_import)

    watch = sub.add_parser("watch", help="お気に入り管理")
    watch_sub = watch.add_subparsers(dest="watch_command", required=True)
    p = watch_sub.add_parser("add")
    p.add_argument("code")
    p.set_defaults(func=cmd_watch_add)
    p = watch_sub.add_parser("remove")
    p.add_argument("code")
    p.set_defaults(func=cmd_watch_remove)
    p = watch_sub.add_parser("list")
    p.set_defaults(func=cmd_watch_list)

    p = sub.add_parser("edinet-sync", help="EDINET大量保有報告書を同期")
    p.add_argument("--api-key", help="EDINET APIキー(省略時は環境変数EDINET_API_KEY)")
    p.set_defaults(func=cmd_edinet_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
