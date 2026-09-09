"""Geminiプロンプト生成・応答パース

SPECIFICATION.md 5節に対応する。
- generate_portfolio_prompt: 保有ポジション・お気に入り・直近ログを統合したレポート生成(5.2節)
- parse_gemini_recommendations / register_recommendations: Geminiの推奨銘柄回答をパースしてDB登録(5.3節)
- generate_grading_prompt: 登録済み推奨銘柄の採点用プロンプト生成
"""
import re
from datetime import datetime

from . import tracker
from .database import Database
from .parser import clean_numeric

_REC_PATTERN = re.compile(
    r"(?:\[(\d{4})\]|[\(（](\d{4})[\)）]|(?:第\s*\d+\s*位\s*[：:]?\s*|^\s*|(?<=\n)\s*|\b)(\d{4})(?=\s+[^0-9\s]{2,}))"
)


def _parse_price_range(text: str) -> tuple[float | None, float | None]:
    """「1,535円〜1,550円」のような表記から (下限, 上限) を取り出す。単一値の場合は同じ値を返す"""
    if not text:
        return None, None
    parts = re.split(r"[〜~\-]", text)
    values = [v for v in (clean_numeric(p) for p in parts) if v is not None]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    return values[0], values[-1]


def parse_gemini_recommendations(text: str) -> list[dict]:
    """Geminiが返した推奨銘柄テキストをパースする(DB登録は行わない純粋関数)。

    「第1位：[3151] バイタルＫＳ / 選定理由: ... / エントリー推奨帯: 1,535円〜1,550円 /
    ターゲット目標値: 1,850円 / 撤退損切りライン: 1,460円」のような形式を想定。
    """
    matches = list(_REC_PATTERN.finditer(text))
    results = []

    for i, match in enumerate(matches):
        code = next(g for g in match.groups() if g is not None)

        start_idx = match.start()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start_idx:end_idx]

        header_offset = len(match.group(0))
        after_code_text = block[header_offset:].strip()
        name_match = re.match(r"^([^\n\r\(（\s　]+)", after_code_text)
        name = name_match.group(1).strip() if name_match else "不明"

        entry_match = re.search(
            r"エントリー推奨(?:帯)?:\s*(.*?)(?=(?:ターゲット目標|損切り|撤退損切り|第\s*\d+\s*位|$))", block, re.DOTALL
        )
        entry_range_text = entry_match.group(1).strip() if entry_match else ""

        target_match = re.search(
            r"ターゲット目標(?:値)?:\s*(.*?)(?=(?:エントリー推奨|損切り|撤退損切り|第\s*\d+\s*位|$))", block, re.DOTALL
        )
        target_text = target_match.group(1).strip() if target_match else ""

        stop_match = re.search(
            r"(?:撤退)?損切りライン:\s*(.*?)(?=(?:エントリー推奨|ターゲット目標|第\s*\d+\s*位|$))", block, re.DOTALL
        )
        stop_text = stop_match.group(1).strip() if stop_match else ""

        reason_match = re.search(
            r"選定理由:\s*(.*?)(?=(?:エントリー推奨|ターゲット目標|損切り|撤退損切り|アクションプラン|第\s*\d+\s*位|$))",
            block,
            re.DOTALL,
        )
        reason = reason_match.group(1).strip() if reason_match else ""

        entry_low, entry_high = _parse_price_range(entry_range_text)

        results.append({
            "code": code,
            "name": name,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "target_price": clean_numeric(target_text),
            "stop_loss": clean_numeric(stop_text),
            "reason": reason,
        })

    return results


def register_recommendations(db: Database, text: str) -> list[dict]:
    """パース結果をDBに登録する。

    同日中に同じ銘柄が既に登録されている場合は reg_price を0(未取得)にリセットし、
    後続のStage2取得で最新価格が入るのを待つ(同日再貼り付けへの対応)。
    """
    parsed = parse_gemini_recommendations(text)
    if not parsed:
        return []

    today_str = datetime.now().strftime("%Y-%m-%d")
    existing_today_codes = {r["code"] for r in db.list_gemini_recommendations() if r["date"] == today_str}

    registered = []
    for rec in parsed:
        code = rec["code"]
        if code in existing_today_codes:
            reg_price = 0.0
        else:
            latest = db.get_latest_record(code)
            reg_price = latest["price"] if latest and latest["price"] else 0.0

        record = {
            "date": today_str,
            "code": code,
            "name": rec["name"],
            "reg_price": reg_price,
            "entry_low": rec["entry_low"],
            "entry_high": rec["entry_high"],
            "target_price": rec["target_price"],
            "stop_loss": rec["stop_loss"],
            "reason": rec["reason"],
        }
        record["id"] = db.add_gemini_recommendation(record)
        registered.append(record)

    return registered


def generate_grading_prompt(db: Database) -> str:
    """登録済みのGemini推奨銘柄について、現状評価・採点を依頼するプロンプトを生成する"""
    recs = db.list_gemini_recommendations()
    if not recs:
        return ""

    lines = [
        "以下は、私が登録している推奨銘柄のリストです。",
        "現在の株価や推奨帯、目標値、損切りラインを参考に、各銘柄について現在の状況を評価・採点し、"
        "今後の見通しに関するアドバイスをお願いします。\n",
        "【登録されている推奨銘柄一覧】",
    ]

    for rec in recs:
        latest = db.get_latest_record(rec["code"])
        latest_price = latest["price"] if latest else None
        reg_price = rec["reg_price"]

        reg_price_str = f"{reg_price:,.1f}円" if reg_price else "不明/取得中"
        latest_price_str = f"{latest_price:,.1f}円" if latest_price else "不明/取得中"

        ratio_str = "不明"
        if reg_price and latest_price:
            ratio_str = f"{((latest_price - reg_price) / reg_price) * 100:+.2f}%"

        entry_str = "不明"
        if rec["entry_low"] is not None and rec["entry_high"] is not None:
            entry_str = f"{rec['entry_low']:,.0f}円〜{rec['entry_high']:,.0f}円"

        lines.append(f"■ {rec['name']} ({rec['code']})")
        lines.append(f"  - 登録日: {rec['date']}")
        lines.append(f"  - 登録時価格: {reg_price_str}")
        lines.append(f"  - 現在値: {latest_price_str} (騰落率: {ratio_str})")
        lines.append(f"  - エントリー推奨帯: {entry_str}")
        lines.append(f"  - ターゲット目標値: {rec['target_price']}")
        lines.append(f"  - 撤退損切りライン: {rec['stop_loss']}")
        if rec["reason"]:
            lines.append(f"  - 選定理由:\n    {rec['reason']}")
        lines.append("")

    lines.append("【分析・採点の依頼内容】")
    lines.append(
        "1. 各銘柄の現在値が、登録時価格・エントリー推奨帯・ターゲット目標値・撤退損切りラインに対して"
        "どのような位置にあるかを整理し、それぞれの「投資判断」と「5段階評価(S,A,B,C,D)」を採点してください。"
    )
    lines.append("2. 最新のテクニカル指標や市場トレンド、選定理由を考慮して、注目すべき強気・弱気銘柄の見通しとアドバイスを教えてください。")
    lines.append("3. 今後のトレード戦略への具体的なアクションプランを提案してください。")

    return "\n".join(lines)


def _format_positions_table(positions: list[dict]) -> str:
    if not positions:
        return "| (データなし) | | | | | | |"
    rows = []
    for p in positions:
        latest_str = f"{p['latest_price']:,.0f}" if p["latest_price"] is not None else "--"
        profit_str = f"{p['profit']:+,.0f}" if p["profit"] is not None else "--"
        pct_str = f"{p['profit_pct']:+.2f}%" if p["profit_pct"] is not None else "--"
        rows.append(
            f"| {p['code']} | {p['name']} | {p['purchase_price']:,.0f} | {p['shares']:,} | "
            f"{latest_str} | {profit_str} | {pct_str} |"
        )
    return "\n".join(rows)


def _format_watchlist_table(watchlist: list[dict]) -> str:
    if not watchlist:
        return "| (データなし) | | | | |"
    rows = []
    for w in watchlist:
        parts = []
        if w["progress_rate"] is not None and w["progress_quarter"]:
            parts.append(f"{w['progress_rate']:.0f}%({w['progress_quarter']})")
        if w["squeeze_score"]:
            parts.append(f"★{w['squeeze_score']}")
        progress_str = " / ".join(parts) if parts else "--"
        rows.append(f"| {w['code']} | {w['name']} | {progress_str} | {w['diff_pct']:+.2f}% |")
    return "\n".join(rows)


def generate_portfolio_prompt(db: Database, recent_logs: list[str] | None = None) -> str:
    """保有ポジション・お気に入り・直近ログを統合したMarkdownレポートを生成する"""
    positions_table_str = _format_positions_table(tracker.list_positions_with_pnl(db))
    favorites_table_str = _format_watchlist_table(tracker.list_watchlist_with_returns(db))

    recent_logs = [line.strip() for line in (recent_logs or []) if line.strip()][-15:]
    recent_logs_str = "\n".join(recent_logs) if recent_logs else "(ログ履歴はありません)"

    return f"""# 株式投資分析用データレポート (Gemini連携用)

このレポートは、現在の保有ポジション、お気に入り銘柄、およびシステムの動作・操作ログを含んでいます。
これらを基に、現在の投資状況に対する分析、個別の銘柄に関する洞察(特に需給や進捗)、および次のアクションの提案を行ってください。

## 💼 保有ポジション
| コード | 銘柄名 | 購入単価 | 株数 | 現在値 | 損益 | 損益率 |
| :---: | :--- | ---: | ---: | ---: | ---: | ---: |
{positions_table_str}

## ⭐ お気に入り銘柄
| コード | 銘柄名 | 進捗/需給 | 騰落率 |
| :---: | :--- | :---: | ---: |
{favorites_table_str}

## 📝 直近のアクティビティログ
```text
{recent_logs_str}
```

---
**AIへの指示事項 & 分析ガイドライン:**
1. **保有ポジションの評価とリスク管理**: ポジションの全体的な損益状況を評価し、利確・損切りの目安を含めた助言をしてください。
2. **お気に入り銘柄の分析**: 「進捗/需給」および「騰落率」に注目し、強気・弱気の兆候がある銘柄をピックアップしてください。
3. **優待クロス(つなぎ売り)ダミーの厳格な排除**:
   - 四半期末(3, 6, 9, 12月)に限らず、全月(特に2月・8月決算の小売・外食産業や、15日・20日締め銘柄含む)の第3〜第4週(14日以降)における信用倍率の低下(売り長化・売り残急増)は、株主優待や配当のタダ取りを狙う一時的な「優待クロス(つなぎ売り)」である可能性が極めて高いです。これらは権利落ち日に現渡し決済で相殺され、実際の買い戻し圧力(ショートスクイーズ)を生まないため、踏み上げ期待の推奨ランキングから完全に除外するか、注意喚起をしてください。
4. **TOB/MBOコーポレートアクションと仕手株乱高下の区別**:
   - 株価急騰(+20%以上)に加え、出来高が前週比+300%超などの商い急増を伴っている銘柄はTOBへのサヤ寄せ・MBOの可能性が高いため、通常の踏み上げ推奨から除外して注記してください。出来高急増を伴わない乱高下は仕手・材料株リスクとして扱ってください。
5. **過熱感のある高乖離銘柄の排除**:
   - 25日移動平均線乖離率が極端に高い(目安として+25%以上、あるいは短期間で株価が急激に乖離した銘柄など)銘柄は、出来高減少が「過熱感の冷却(日柄調整)」ではなく「買い手の息切れ(ドテン天井)」を意味するリスクが高いため、中期スイング推奨のTOP3などには含めず、高値掴み防止の警戒・回避銘柄に分類してください。
6. **しこり玉判定の多角化**:
   - 単日のVWAPとの乖離(株価が当日のVWAPを上回っているか)だけで「しこり玉が整理されて上値が軽い」と安易に判断せず、中長期の信用残(信用買い残の改ざん整理状況)や過去数ヶ月の価格帯別出来高の壁を考慮して、真の上値の軽さを評価してください。
7. **直近アクティビティからの文脈把握**: 直近のログから、ユーザーの検索キーワードや同期状況を把握し、それに基づいたアドバイスを提供してください。
"""
