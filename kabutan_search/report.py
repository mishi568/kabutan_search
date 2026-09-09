"""分析結果のレポート出力(Markdown)

SPECIFICATION.md 6節に対応する。analyze_stocks()とセクター分析の結果を
1つのMarkdownファイルにまとめる。
"""
from datetime import datetime
from pathlib import Path

from . import margin_analysis
from .database import Database
from .nikkei_database import NikkeiDatabase
from .sector_divergence_analyzer import calculate_sector_divergence

DEFAULT_REPORT_DIR = Path("reports")


def generate_markdown_report(db: Database, nikkei_db: NikkeiDatabase, only_improving: bool = True) -> str:
    rows = margin_analysis.analyze_stocks(db, only_improving=only_improving)
    sec_results = calculate_sector_divergence(db)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"# 需給分析レポート ({now_str})", "", f"対象銘柄数: {len(rows)}件", ""]

    lines.append("## 銘柄別 需給分析")
    lines.append("| コード | 銘柄名 | 株価 | 25日乖離 | 28週騰落 | 信用倍率 | 28週変化率 | 踏み上げ | 総合評価 |")
    lines.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | :---: | :--- |")
    for r in rows:
        ratio_str = f"{r['latest_ratio']:.2f}倍" if r["latest_ratio"] is not None else "--"
        price_str = f"¥{r['price']:,}" if r["price"] else "--"
        lines.append(
            f"| {r['code']} | {r['name']} | {price_str} | {r['deviation_25']:+.2f}% | "
            f"{r['price_change_28w']:+.2f}% | {ratio_str} | {r['ratio_change_28w']:+.1f}% | "
            f"{r['squeeze_stars']} | {r['evaluation']} |"
        )
    lines.append("")

    if sec_results:
        lines.append("## セクターモメンタム")
        lines.append("| 業種 | 騰落率 | Zスコア | シグナル |")
        lines.append("| :--- | ---: | ---: | :--- |")
        for s in sec_results:
            chg = s["change_pct"] or 0.0
            lines.append(f"| {s['sector_name']} | {chg:+.2f}% | {s['z_score']:+.2f} | {s['signal_badge']} |")
        lines.append("")

    return "\n".join(lines)


def save_report(
    db: Database,
    nikkei_db: NikkeiDatabase,
    only_improving: bool = True,
    out_dir: Path = DEFAULT_REPORT_DIR,
) -> Path:
    """レポートを reports/YYYY-MM-DD.md に保存し、そのパスを返す"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    content = generate_markdown_report(db, nikkei_db, only_improving=only_improving)
    path = out_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    path.write_text(content, encoding="utf-8")
    return path
