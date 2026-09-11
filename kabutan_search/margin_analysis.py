"""信用需給分析: スクイーズスコア算出・需給評価ラベル・Geminiプロンプト生成

SPECIFICATION.md 4節(コアスコアリング&分析ロジック)・5.1節(需給分析プロンプト)に対応する。
これが需給分析の中核モジュール。
"""
import re
import statistics
from datetime import datetime

from .database import Database
from .nikkei_database import NikkeiDatabase
from .sector_divergence_analyzer import calculate_sector_divergence
from .stock_quant_metrics import (
    calculate_days_to_cover,
    calculate_ma25_slope_and_trend,
    calculate_margin_changes_multi_period,
    evaluate_quant_flags,
)

MAX_PROMPT_ROWS = 100


def clean_cross_trade_and_calculate_median(weekly_records, has_yutai="なし", settlement_month=None):
    """優待クロス(つなぎ売り)ノイズの検知・補間 + 4週ローリング中央値平滑化(SPECIFICATION.md 4.5節)。

    1. 動的スパイク検知: Ut/Ut-1 >= 2.0 AND Ut+1/Ut <= 0.5 の売残急増→急減パターン
    2. 優待月静的検知: 決算月/中間月の14日以降に売残が1.5倍以上に急増(優待ありなら2倍以上)
    3. 補間: ノイズ値を (前週 + 翌週) / 2 で補間
    4. 4週ローリング中央値平滑化を買残・売残・倍率に適用

    Returns: (cleaned_records, real_latest_ratio, real_ratio_change_28w, has_cross_anomaly)
    """
    chrono = list(reversed(weekly_records))
    n = len(chrono)
    if n == 0:
        return [], None, 0.0, False

    has_cross_anomaly = False

    for i in range(n):
        rec = dict(chrono[i])
        sell = rec.get("sell")
        buy = rec.get("buy")
        date_str = rec.get("date", "")

        is_spike = False
        if sell is not None and sell > 0:
            prev_sell = chrono[i - 1].get("sell") if i > 0 else None
            next_sell = chrono[i + 1].get("sell") if i + 1 < n else None

            if prev_sell is not None and prev_sell > 0:
                if next_sell is not None and next_sell > 0:
                    if (sell / prev_sell >= 2.0) and (next_sell / sell <= 0.5):
                        is_spike = True

                if not is_spike and (sell / prev_sell >= 1.5):
                    try:
                        d_obj = datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d")
                        if d_obj.day >= 14:
                            sm = None
                            if settlement_month is not None:
                                try:
                                    sm = int(settlement_month)
                                except (ValueError, TypeError):
                                    pass
                            if sm is not None:
                                is_vesting_month = (d_obj.month == sm) or (d_obj.month == ((sm + 6 - 1) % 12 + 1))
                                if is_vesting_month and (has_yutai == "あり" or (sell / prev_sell >= 2.0)):
                                    is_spike = True
                            elif has_yutai == "あり" and (sell / prev_sell >= 2.0):
                                is_spike = True
                    except (ValueError, TypeError):
                        pass

        if is_spike:
            has_cross_anomaly = True
            rec["is_cross_spike"] = True
            prev_sell = chrono[i - 1].get("sell") if i > 0 else None
            next_sell = chrono[i + 1].get("sell") if i + 1 < n else None
            if prev_sell is not None and next_sell is not None:
                clean_s = (prev_sell + next_sell) / 2.0
            elif prev_sell is not None:
                clean_s = prev_sell
            elif next_sell is not None:
                clean_s = next_sell
            else:
                clean_s = sell
            rec["clean_sell"] = clean_s
            rec["clean_ratio"] = (buy / clean_s) if (buy is not None and clean_s > 0) else None
        else:
            rec["is_cross_spike"] = False
            rec["clean_sell"] = sell
            rec["clean_ratio"] = rec.get("ratio")

        chrono[i] = rec

    cleaned_records = []
    for i in range(n):
        rec = dict(chrono[i])
        window = chrono[max(0, i - 3):i + 1]
        valid_ratios = [w["clean_ratio"] for w in window if w.get("clean_ratio") is not None]
        rec["rolling_median_ratio"] = statistics.median(valid_ratios) if valid_ratios else rec.get("clean_ratio")
        cleaned_records.append(rec)

    final_weekly_records = list(reversed(cleaned_records))

    real_latest_ratio = final_weekly_records[0].get("rolling_median_ratio")
    oldest_median = final_weekly_records[-1].get("rolling_median_ratio") if final_weekly_records else None

    real_ratio_change_28w = 0.0
    if real_latest_ratio is not None and oldest_median is not None and oldest_median > 0:
        real_ratio_change_28w = ((real_latest_ratio - oldest_median) / oldest_median) * 100.0

    return final_weekly_records, real_latest_ratio, real_ratio_change_28w, has_cross_anomaly


def _build_weekly_records(price: float, history_rows, margin_rows) -> list[dict]:
    """週次信用残高レコードを組み立てる。price/volumeが欠損している週は日次履歴から補完する。"""
    history_by_date = {h["date"]: h for h in history_rows}
    weekly_records = []
    for mr in margin_rows:
        d_str = mr["date"]
        m_price = mr["price"] if mr["price"] and mr["price"] > 0 else None
        m_vol = mr["volume"] if mr["volume"] is not None else None
        if m_price is None or m_vol is None:
            h = history_by_date.get(d_str)
            if h:
                if m_price is None and h["price"] and h["price"] > 0:
                    m_price = h["price"]
                if m_vol is None and h["volume"]:
                    m_vol = h["volume"]
        if m_price is None:
            m_price = price
        weekly_records.append({
            "date": d_str,
            "price": m_price,
            "buy": mr["margin_buy"],
            "sell": mr["margin_sell"],
            "ratio": mr["margin_ratio"],
            "volume": m_vol,
        })
    return weekly_records


def _days_until_earnings(earnings_date):
    """決算発表予定日までの日数と、警戒レベルに応じたバッジ文字列を返す"""
    if not earnings_date:
        return None, ""
    m = re.search(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})", str(earnings_date).strip())
    if not m:
        return None, ""
    y_str, m_str, d_str = m.groups()
    cur_dt = datetime.now()
    target_year = int(y_str) if y_str else cur_dt.year
    try:
        target_dt = datetime(target_year, int(m_str), int(d_str))
    except ValueError:
        return None, ""

    delta = (target_dt.date() - cur_dt.date()).days
    if delta < 0:
        status = f"通過({abs(delta)}日前)"
    elif delta == 0:
        status = "⚡ 本日発表！"
    elif delta <= 7:
        status = f"🚨 あと{delta}日 (決算跨ぎ超危険)"
    elif delta <= 14:
        status = f"⚠️ あと{delta}日 (跨ぎ警戒)"
    else:
        status = f"あと{delta}日"
    return delta, status


def analyze_stock(db: Database, code: str) -> dict | None:
    """1銘柄分の需給分析データを算出する。データが無ければNoneを返す。"""
    history = db.get_recent_stock_history(code, limit=160)
    if not history:
        return None

    latest = history[0]
    name = latest["name"]
    price = latest["price"]
    sector_val = latest["sector"]

    prices = [h["price"] for h in history if h["price"] is not None]
    volumes = [h["volume"] for h in history if h["volume"] is not None]

    # 25日移動平均乖離率
    deviation_25 = 0.0
    if prices:
        ma25 = sum(prices[:25]) / min(len(prices), 25)
        if ma25:
            deviation_25 = ((price - ma25) / ma25) * 100.0

    # 短期急騰(5日・10日ROC)・上値抵抗線接近の判定
    roc_5d = ((price - prices[5]) / prices[5]) * 100.0 if len(prices) > 5 and prices[5] > 0 else 0.0
    roc_10d = ((price - prices[10]) / prices[10]) * 100.0 if len(prices) > 10 and prices[10] > 0 else 0.0
    is_rapid_surge = roc_5d >= 12.0 or roc_10d >= 18.0

    is_near_resistance = False
    if len(prices) >= 20:
        high_60d = max(prices[:60])
        if high_60d > 0 and price >= high_60d * 0.97:
            is_near_resistance = True

    # 28週騰落率
    price_change_28w = 0.0
    if len(prices) >= 140:
        price_28w = prices[139]
        if price_28w > 0:
            price_change_28w = ((price - price_28w) / price_28w) * 100.0
    elif len(prices) > 1 and prices[-1] > 0:
        price_change_28w = ((price - prices[-1]) / prices[-1]) * 100.0

    # 出来高5日平均・変化率
    vol_avg_5 = 0.0
    vol_change_percent = 0.0
    if len(volumes) >= 5:
        vol_avg_5 = sum(volumes[:5]) / 5.0
        if len(volumes) >= 10:
            vol_avg_prev = sum(volumes[5:10]) / 5.0
            if vol_avg_prev > 0:
                vol_change_percent = ((vol_avg_5 - vol_avg_prev) / vol_avg_prev) * 100.0

    # 週次信用残高(28週)
    margin_rows = db.get_recent_margin_history(code, limit=28)
    weekly_margin_records = _build_weekly_records(price, history, margin_rows)

    latest_buy = latest_sell = latest_ratio = None
    if weekly_margin_records:
        latest_buy = weekly_margin_records[0]["buy"]
        latest_sell = weekly_margin_records[0]["sell"]
        latest_ratio = weekly_margin_records[0]["ratio"]

    progress_val = latest["progress_rate"]
    progress_quarter = latest["progress_quarter"]
    progress_excess = latest["progress_excess"]
    earnings_date = latest["earnings_date"]
    has_yutai = latest["has_yutai"] or "なし"
    cagr_rev = latest["cagr_revenue_3y"]
    cagr_prof = latest["cagr_profit_3y"]
    consec_prof = latest["consecutive_profit_growth"]
    vwap_val = latest["vwap"]
    settlement_month = latest["settlement_month"]
    pbr_val = latest["pbr"]
    dividend_yield_val = latest["dividend_yield"]

    days_until_earnings, earnings_status_str = _days_until_earnings(earnings_date)

    cleaned_weekly, real_latest_ratio, real_ratio_change_28w, is_cross_trade_spike = clean_cross_trade_and_calculate_median(
        weekly_margin_records, has_yutai=has_yutai, settlement_month=settlement_month
    )

    is_yutai_cross_suspected = False
    if has_yutai == "あり" and latest_ratio is not None and latest_ratio <= 0.5:
        if settlement_month is not None:
            try:
                cur_m = datetime.now().month
                sm = int(settlement_month)
                interim_sm = (sm + 6 - 1) % 12 + 1
                if cur_m in (sm, interim_sm):
                    is_yutai_cross_suspected = True
            except (ValueError, TypeError):
                pass
        else:
            is_yutai_cross_suspected = True
    if is_cross_trade_spike:
        is_yutai_cross_suspected = True

    sample_weekly = cleaned_weekly if cleaned_weekly else weekly_margin_records

    ratio_28w = None
    if len(sample_weekly) >= 28:
        ratio_28w = sample_weekly[27]["ratio"]
    elif len(sample_weekly) > 1:
        ratio_28w = sample_weekly[-1]["ratio"]

    ratio_change_28w = 0.0
    if latest_ratio is not None and ratio_28w is not None and ratio_28w > 0:
        ratio_change_28w = ((latest_ratio - ratio_28w) / ratio_28w) * 100.0

    ratio_trend_str = " -> ".join(
        f"{r['ratio']:.2f}" for r in reversed(sample_weekly[:4]) if r.get("ratio") is not None
    )
    weekly_summary_str = (
        f"最新: {latest_ratio:.2f}倍 (28週前: {ratio_28w:.2f}倍, 28週変化率: {ratio_change_28w:+.1f}%)"
        if (latest_ratio and ratio_28w) else "--"
    )

    is_falling_knife = price_change_28w < -25.0 and ratio_change_28w > 50.0
    if not is_falling_knife and deviation_25 < -15.0 and ratio_change_28w > 30.0:
        is_falling_knife = True
    is_shikori_decline = price_change_28w < -15.0 and ratio_change_28w > 20.0

    # スクイーズスコア(★1〜5) SPECIFICATION.md 4.1節
    squeeze_score = 1
    eval_ratio = real_latest_ratio if real_latest_ratio is not None else latest_ratio
    eval_ratio_change_28w = real_ratio_change_28w if real_latest_ratio is not None else ratio_change_28w
    eval_latest_ratio = latest_ratio

    if eval_ratio is not None:
        if eval_ratio <= 0.8:
            squeeze_score += 3
        elif eval_ratio <= 1.5:
            squeeze_score += 2
        elif eval_ratio <= 3.0:
            squeeze_score += 1

    if eval_ratio_change_28w < -5.0:
        squeeze_score += 1
        if eval_ratio_change_28w < -15.0:
            squeeze_score += 1

    if vol_change_percent > 30.0:
        squeeze_score += 1
        if vol_change_percent > 100.0:
            squeeze_score += 1

    if is_falling_knife or is_shikori_decline:
        squeeze_score = 1

    squeeze_score = min(5, max(1, squeeze_score))
    squeeze_stars = "★" * squeeze_score

    # 総合需給評価ラベル SPECIFICATION.md 4.2節(優先順位順に判定)
    if is_falling_knife:
        evaluation = "⚠️落ちるナイフ(権利落ち)"
    elif is_shikori_decline:
        evaluation = "⚠️しこり下落(ナイフ警戒)"
    elif is_yutai_cross_suspected:
        evaluation = "⚠️優待クロス補正済"
    elif is_rapid_surge:
        evaluation = "⚠️短期急騰(高値掴み警戒)"
    elif is_near_resistance:
        evaluation = "⚠️上値抵抗線接近(-3%以内)"
    elif squeeze_score >= 4:
        evaluation = "💥 超踏み上げ"
    elif squeeze_score == 3:
        evaluation = "🚀 高期待"
    elif eval_latest_ratio is not None and eval_latest_ratio < 1.5:
        evaluation = "🔥 需給良好"
    elif eval_ratio_change_28w < 0:
        evaluation = "📈 改善傾向"
    else:
        evaluation = "💤 需給拮抗"

    if days_until_earnings is not None and 0 <= days_until_earnings <= 14 and "⚠️" not in evaluation:
        evaluation += " (⚠️決算間近)"

    dtc_dict = calculate_days_to_cover(latest_buy, latest_sell, vol_avg_5)
    margin_change_dict = calculate_margin_changes_multi_period(sample_weekly)
    ma25_dict = calculate_ma25_slope_and_trend(prices)

    price_prev_week = sample_weekly[1]["price"] if len(sample_weekly) > 1 and sample_weekly[1].get("price") else None
    vol_this_week = sample_weekly[0].get("volume") if sample_weekly else None
    vol_prev_week = sample_weekly[1].get("volume") if len(sample_weekly) > 1 else None

    flags_dict = evaluate_quant_flags(
        weekly_records=sample_weekly,
        price_latest=price,
        price_prev_week=price_prev_week,
        vol_this_week=vol_this_week,
        vol_prev_week=vol_prev_week,
        earnings_date_val=earnings_date,
        has_yutai=has_yutai,
        settlement_month=settlement_month,
        margin_ratio=latest_ratio,
        daily_prices=prices,
        ma25_trend_info=ma25_dict,
    )

    vwap_deviation = ((price - vwap_val) / vwap_val) * 100.0 if (vwap_val and price) else None

    return {
        "code": code, "name": name, "price": price, "sector": sector_val,
        "deviation_25": deviation_25, "roc_5d": roc_5d, "roc_10d": roc_10d,
        "is_rapid_surge": is_rapid_surge, "is_near_resistance": is_near_resistance,
        "price_change_28w": price_change_28w,
        "latest_ratio": latest_ratio, "ratio_28w": ratio_28w, "ratio_change_28w": ratio_change_28w,
        "latest_buy": latest_buy, "latest_sell": latest_sell,
        "vol_avg_5": vol_avg_5, "vol_change_percent": vol_change_percent,
        "progress_rate": progress_val, "progress_quarter": progress_quarter, "progress_excess": progress_excess,
        "earnings_date": earnings_date, "days_until_earnings": days_until_earnings,
        "earnings_status_str": earnings_status_str,
        "has_yutai": has_yutai, "is_yutai_cross_suspected": is_yutai_cross_suspected,
        "is_falling_knife": is_falling_knife, "is_shikori_decline": is_shikori_decline,
        "squeeze_score": squeeze_score, "squeeze_stars": squeeze_stars, "evaluation": evaluation,
        "ratio_trend": ratio_trend_str, "weekly_summary": weekly_summary_str,
        "weekly_records": sample_weekly,
        "cagr_revenue_3y": cagr_rev, "cagr_profit_3y": cagr_prof, "consecutive_profit_growth": consec_prof,
        "vwap": vwap_val, "vwap_deviation": vwap_deviation,
        "pbr": pbr_val, "dividend_yield": dividend_yield_val,
        "days_to_cover_buy_str": dtc_dict["days_to_cover_buy_str"],
        "days_to_cover_sell_str": dtc_dict["days_to_cover_sell_str"],
        "summary_buy_change_str": margin_change_dict["summary_buy_str"],
        "summary_sell_change_str": margin_change_dict["summary_sell_str"],
        "short_cover_exhausted": flags_dict["short_cover_exhausted"],
        "tob_mbo_suspected": flags_dict["tob_mbo_suspected"],
        "earnings_risk": flags_dict["earnings_risk"],
        "yutai_cross_suspected": flags_dict["yutai_cross_suspected"],
        "trend_broken": flags_dict["trend_broken"],
        "ma25_slope": ma25_dict["ma25_slope"], "ma25_slope_str": ma25_dict["ma25_slope_str"],
        "trend_broken_badge": ma25_dict["trend_broken_badge"],
        "flags_summary_str": flags_dict["flags_summary_str"],
    }


def analyze_stocks(db: Database, only_improving: bool = True, persist_scores: bool = True) -> list[dict]:
    """全銘柄の需給分析を行う(25列相当のデータ)。

    only_improving=Trueなら需給が改善している銘柄のみに絞り込む(優待クロス疑い・短期急騰・
    上値抵抗線接近の銘柄は除外し、28週倍率が低下傾向 または 倍率1.5倍未満の銘柄のみ残す)。
    persist_scores=Trueの場合、算出したsqueeze_scoreを各銘柄の最新レコードへ書き戻す。
    """
    tracked_codes = db.get_tracked_codes()
    results = []

    for code in db.get_distinct_stock_codes():
        row = analyze_stock(db, code)
        if row is None:
            continue

        if only_improving:
            is_improving = row["ratio_change_28w"] < 0
            is_low_ratio = row["latest_ratio"] is not None and row["latest_ratio"] < 1.5
            if row["is_yutai_cross_suspected"] or row["is_rapid_surge"] or row["is_near_resistance"]:
                continue
            if not (is_improving or is_low_ratio):
                continue

        row["is_favorite"] = code in tracked_codes
        results.append(row)

        if persist_scores:
            latest = db.get_latest_record(code)
            if latest:
                db.upsert_stock_record({"date": latest["date"], "code": code, "squeeze_score": row["squeeze_score"]})

    return results


# ---------------------------------------------------------------------------
# Geminiプロンプト生成 (SPECIFICATION.md 5.1節)
# ---------------------------------------------------------------------------

def _format_sector_signal_section(sec_results: list[dict]) -> tuple[str, dict]:
    if not sec_results:
        return "", {}
    sector_signal_map = {s["sector_name"]: s for s in sec_results}

    squeeze_secs = [s for s in sec_results if s["signal_type"] == "踏み上げ初動"]
    momentum_secs = [s for s in sec_results if s["signal_type"] == "モメンタム急騰"]
    recovery_secs = [s for s in sec_results if s["signal_type"] == "反発兆候"]

    lines = [
        "### 📊 【前提】33業種別・初動検知＆空売りダイバージェンス状況(セクターモメンタム)",
        "※東証33業種の空売り比率と20日騰落率Zスコアから、大口資金流入や空売り踏み上げ(ショートカバー)の初動が発生している業種セクターを特定したデータです。",
        "",
    ]
    if squeeze_secs:
        lines.append("- 🚀 **【踏み上げ初動セクター】**: " + ", ".join(
            f"**{s['sector_name']}** (Z: {s['z_score']:+.2f}, 空売り: {s['short_ratio']:.1f}%)" for s in squeeze_secs
        ))
    if momentum_secs:
        lines.append("- ⚡ **【モメンタム急騰セクター】**: " + ", ".join(
            f"**{s['sector_name']}** (Z: {s['z_score']:+.2f}, 空売り: {s['short_ratio']:.1f}%)" for s in momentum_secs
        ))
    if recovery_secs:
        lines.append("- 📈 **【反発兆候セクター】**: " + ", ".join(
            f"**{s['sector_name']}** (Z: {s['z_score']:+.2f}, 空売り: {s['short_ratio']:.1f}%)" for s in recovery_secs
        ))

    lines.append("")
    lines.append("| 業種セクター | 空売り比率 (%) | 20日騰落率 (%) | モメンタム Zスコア | 初動シグナル判定 |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    for s in sec_results:
        chg_val = s.get("change_pct") or 0.0
        lines.append(f"| {s['sector_name']} | {s['short_ratio']:.1f}% | {chg_val:+.2f}% | {s['z_score']:+.2f} | {s['signal_badge']} |")

    return "\n".join(lines), sector_signal_map


def _format_sector_margin_section(sec_margin_rows: list[dict]) -> tuple[str, dict]:
    if not sec_margin_rows:
        return "", {}
    sector_margin_map = {r["sector"]: r for r in sec_margin_rows}

    top_improving = [r for r in sec_margin_rows if (r["buy_reduced_pct"] or 0) >= 70.0]
    heavy = [r for r in sec_margin_rows if (r["buy_reduced_pct"] or 0) < 40.0]

    lines = [
        "### 🏢 【前提】東証33業種セクター別 信用需給好転ランキング(買い残減少・しこり玉整理状況)",
        "※各業種に属する個別銘柄の信用残高を集計し、「買い残が減少(しこり玉消化・需給好転)した銘柄の割合」をランキング化したデータです。",
        "",
    ]
    if top_improving:
        lines.append("- 🌟 **【信用需給が大幅好転(買い残整理進行)しているセクター】**: " + ", ".join(
            f"**{r['sector']}** (買残減少率: {r['buy_reduced_pct']}%, 減少: {r['buy_reduced_count']}/{r['stock_count']}銘柄, 平均倍率: {r['avg_margin_ratio']:.1f}倍)"
            for r in top_improving
        ))
    if heavy:
        lines.append("- ⚠️ **【買い残が重く需給整理が遅れているセクター】**: " + ", ".join(
            f"**{r['sector']}** (買残減少率: {r['buy_reduced_pct']}%, 平均倍率: {r['avg_margin_ratio']:.1f}倍)" for r in heavy
        ))

    lines.append("")
    lines.append("| 業種セクター | 登録銘柄数 | 買残減少 銘柄数 | 買残減少率 (%) | 売残増加 銘柄数 | 平均信用倍率 | 需給ステータス |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for r in sec_margin_rows:
        pct_val = r["buy_reduced_pct"] or 0.0
        avg_rat = r["avg_margin_ratio"] or 0.0
        if pct_val >= 80.0:
            st = "🌟 需給極良"
        elif pct_val >= 60.0:
            st = "✨ 好転傾向"
        elif avg_rat < 3.0:
            st = "⚡ 低倍率"
        elif pct_val < 40.0:
            st = "⚠️ 買残過多"
        else:
            st = "⚪ 中立"
        lines.append(f"| {r['sector']} | {r['stock_count']} | {r['buy_reduced_count']} | {pct_val:.1f}% | {r['sell_increased_count']} | {avg_rat:.2f}倍 | {st} |")

    return "\n".join(lines), sector_margin_map


def _format_jpx_section(nikkei_db: NikkeiDatabase) -> str:
    """JPX公式データ(空売り比率・投資部門別売買動向)の要約セクションを生成する。

    旧NikkeiDatabaseManager.generate_jpx_prompt_section()の原文は入手できなかったため、
    新DBスキーマ(jpx_short_selling / jpx_investor_trends)から新規に組み立てている。
    """
    short_selling = nikkei_db.get_latest_jpx_short_selling()
    investor_trends = nikkei_db.get_latest_jpx_investor_trends()

    lines = ["## 📡 JPX公式データ(空売り比率・投資部門別売買動向)"]
    if short_selling:
        lines.append(f"- **空売り比率**: {(short_selling['short_selling_ratio'] or 0):.2f}% (基準日: {short_selling['date']})")
    else:
        lines.append("- 空売り比率: データなし")

    n225jp_short = nikkei_db.get_latest_nikkei225jp_short_selling()
    if n225jp_short:
        lines.append(
            f"- **空売り比率(nikkei225jp.com版、上記JPX値の補完・裏取り用)**: "
            f"合計{(n225jp_short['short_ratio_total'] or 0):.1f}% "
            f"(価格規制あり{(n225jp_short['short_ratio_regulated'] or 0):.1f}% / "
            f"価格規制なし{(n225jp_short['short_ratio_non_regulated'] or 0):.1f}%) "
            f"(基準日: {n225jp_short['date']})"
        )

    if investor_trends:
        lines.append(f"- **投資部門別売買動向** (基準日: {investor_trends['date']})")
        lines.append(f"  - 外国人: {(investor_trends['foreign_net'] or 0):+,.1f}億円")
        lines.append(f"  - 個人: {(investor_trends['individual_net'] or 0):+,.1f}億円")
        lines.append(f"  - 信託銀行: {(investor_trends['trust_bank_net'] or 0):+,.1f}億円")
    else:
        lines.append("- 投資部門別売買動向: データなし")

    n225jp_trends = nikkei_db.get_latest_nikkei225jp_investor_trends()
    if n225jp_trends:
        lines.append(
            f"- **投資部門別売買動向(nikkei225jp.com版、週次、上記JPX値の補完・裏取り用)** (基準週: {n225jp_trends['date']})"
        )
        lines.append(f"  - 海外: {(n225jp_trends['foreign_net'] or 0):+,.1f}億円")
        lines.append(
            f"  - 個人計: {(n225jp_trends['individual_net'] or 0):+,.1f}億円 "
            f"(現金: {(n225jp_trends['individual_cash_net'] or 0):+,.1f}億円 / "
            f"信用: {(n225jp_trends['individual_margin_net'] or 0):+,.1f}億円)"
        )
        lines.append(f"  - 信託銀行: {(n225jp_trends['trust_bank_net'] or 0):+,.1f}億円")
        lines.append(
            f"  - 事業法人: {(n225jp_trends['business_corp_net'] or 0):+,.1f}億円 / "
            f"投資信託: {(n225jp_trends['investment_trust_net'] or 0):+,.1f}億円"
        )

    return "\n".join(lines)


def _format_nikkei_section(nikkei_db: NikkeiDatabase) -> str:
    latest_per = nikkei_db.get_latest_nikkei_per_record()
    latest_touraku = nikkei_db.get_latest_nikkei_touraku_record()
    latest_margin = nikkei_db.get_latest_nikkei_margin_record()

    per_data = nikkei_db.get_nikkei_per_records()
    touraku_data = nikkei_db.get_nikkei_touraku_records()
    margin_data = nikkei_db.get_nikkei_margin_records()

    lp_date = latest_per["date"] if latest_per else "--"
    lp_price = (latest_per["price"] or 0.0) if latest_per else 0.0
    lp_per = (latest_per["per"] or 0.0) if latest_per else 0.0
    lp_pbr = (latest_per["pbr"] or 0.0) if latest_per else 0.0
    lp_eps = (latest_per["eps"] or 0.0) if latest_per else 0.0
    lp_eyield = latest_per["earnings_yield"] if latest_per else None
    lp_dyield = latest_per["dividend_yield"] if latest_per else None
    lp_jgb = latest_per["jgb_yield"] if latest_per else None

    lt_r25 = (latest_touraku["touraku_25d"] or 0.0) if latest_touraku else 0.0

    lm_date = latest_margin["date"] if latest_margin else "--"
    lm_buy = (latest_margin["margin_buy"] or 0.0) if latest_margin else 0.0
    lm_sell = (latest_margin["margin_sell"] or 0.0) if latest_margin else 0.0
    lm_ratio = (latest_margin["margin_ratio"] or 0.0) if latest_margin else 0.0
    lm_profit_loss = latest_margin["profit_loss_ratio"] if latest_margin else None

    trend_lines = ["### 1. 日経平均株価 & PER 推移 (直近10営業日)"]
    if per_data:
        for item in per_data[-10:]:
            trend_lines.append(f"- {item['date']}: {(item['price'] or 0):,.2f}円 (PER: {(item['per'] or 0):.2f}倍, EPS: {(item['eps'] or 0):,.2f}円)")
    else:
        trend_lines.append("- データなし")
    trend_lines.append("")

    trend_lines.append("### 2. 東証プライム騰落レシオ推移 (直近10営業日)")
    if touraku_data:
        for item in touraku_data[-10:]:
            trend_lines.append(f"- {item['date']}: 25日騰落レシオ {(item['touraku_25d'] or 0):.2f}% (日経平均: {(item['price'] or 0):,.2f}円)")
    else:
        trend_lines.append("- データなし")
    trend_lines.append("")

    trend_lines.append("### 3. 信用残高金額推移 (直近5週分)")
    if margin_data:
        for item in margin_data[-5:]:
            pl_val = item["profit_loss_ratio"]
            pl_str = f", 信用評価損益率 {pl_val:+.2f}%" if pl_val is not None else ""
            trend_lines.append(
                f"- {item['date']}: 買い残 {(item['margin_buy'] or 0):,.1f}億円, 売り残 {(item['margin_sell'] or 0):,.1f}億円, "
                f"倍率 {(item['margin_ratio'] or 0):.2f}倍{pl_str} (終値: {(item['price'] or 0):,.2f}円)"
            )
    else:
        trend_lines.append("- データなし")

    jpx_section_str = _format_jpx_section(nikkei_db)
    trend_str = "\n".join(trend_lines)

    yield_spread_line = ""
    if lp_eyield is not None and lp_jgb is not None:
        spread = lp_eyield - lp_jgb
        yield_spread_line = (
            f"- **株式益回り-国債利回りスプレッド(イールドスプレッド)**: {spread:+.2f}pt "
            f"(益回り{lp_eyield:.2f}% / 配当利回り{(lp_dyield or 0):.2f}% / 日本国債利回り{lp_jgb:.2f}%) "
            f"※プラス幅が大きいほど株式の相対的な割安感(バリュエーション上の下値サポート)が強い\n"
        )

    profit_loss_line = ""
    if lm_profit_loss is not None:
        profit_loss_line = (
            f"- **信用評価損益率 (東証全体)**: {lm_profit_loss:+.2f}% (基準日: {lm_date}) "
            f"※信用買い方全体の含み損益率。大きくマイナスなほど追証・投げ売り圧力が蓄積している目安(逆張り的な反発の芽にもなり得る)\n"
        )

    nt_ratio_line = ""
    latest_nt = nikkei_db.get_latest_nikkei225jp_nt_ratio()
    if latest_nt:
        usdjpy_str = f", ドル円{latest_nt['usdjpy']:.2f}円" if latest_nt["usdjpy"] is not None else ""
        nt_ratio_line = (
            f"- **NT倍率(日経平均/TOPIX)**: {(latest_nt['nt_ratio'] or 0):.2f} (基準日: {latest_nt['date']}{usdjpy_str}) "
            f"※上昇=値がさ・輸出関連の値嵩株優位(大型株物色)、下降=TOPIX型・内需/中小型株優位への資金シフトの目安\n"
        )

    arbitrage_line = ""
    latest_arb = nikkei_db.get_latest_nikkei225jp_arbitrage()
    if latest_arb and latest_arb["net_shares"] is not None:
        arbitrage_line = (
            f"- **裁定買い残-売り残差引 (株数ベース)**: {latest_arb['net_shares']:,.0f}千株 "
            f"(買い残{(latest_arb['buy_shares'] or 0):,.0f}千株 / 売り残{(latest_arb['sell_shares'] or 0):,.0f}千株、基準日: {latest_arb['date']}) "
            f"※裁定買い残(現物買い・先物売りの裁定ポジション)は将来の機械的な現物売り圧力(裁定解消売り)の潜在量。"
            f"高水準なほど相場全体の上値が重くなりやすく、SQ(特別清算指数)前後で急変動しやすい\n"
        )

    futures_broker_line = ""
    latest_fb = nikkei_db.get_latest_nikkei225jp_futures_broker()
    if latest_fb and latest_fb["foreign_net"] is not None:
        futures_broker_line = (
            f"- **日経225先物 週次建玉(外資系証券ネット)**: {latest_fb['foreign_net']:+,.0f}枚 "
            f"(買建{(latest_fb['foreign_buy'] or 0):,.0f} / 売建{(latest_fb['foreign_sell'] or 0):,.0f}、基準週: {latest_fb['date']}) "
            f"※海外機関投資家の先物ポジション方向。ネット売り(マイナス)が大きいほど海外勢が弱気(ヘッジ売り優勢)、"
            f"ネット買いが大きいほど強気(先高観)の目安\n"
        )

    return f"""## 🌐 【前提】全体相場環境データ(日経平均・JPX公式需給データ)
- **最新株価**: {lp_price:,.2f} 円 (基準日: {lp_date})
- **PER**: {lp_per:.2f} 倍 (EPS: {lp_eps:,.2f} 円)
- **PBR**: {lp_pbr:.2f} 倍
{yield_spread_line}- **騰落レシオ (25日)**: {lt_r25:.2f}%
- **信用買い残 (東証全体)**: {lm_buy:,.1f} 億円 (基準日: {lm_date})
- **信用売り残 (東証全体)**: {lm_sell:,.1f} 億円 (基準日: {lm_date})
- **信用倍率 (東証全体)**: {lm_ratio:.2f} 倍
{profit_loss_line}{nt_ratio_line}{arbitrage_line}{futures_broker_line}
📈 直近の推移データ (過去の時系列トレンド)
{trend_str}

{jpx_section_str}"""


def _format_data_row(row: dict, sector_signal_map: dict, sector_margin_map: dict) -> str:
    sector_raw = row.get("sector") or ""
    sec_info = sector_signal_map.get(sector_raw)
    sec_margin_info = sector_margin_map.get(sector_raw)
    if sec_info is None:
        for k, v in sector_signal_map.items():
            if k in sector_raw or sector_raw in k:
                sec_info = v
                break
    if sec_margin_info is None:
        for k, v in sector_margin_map.items():
            if k in sector_raw or sector_raw in k:
                sec_margin_info = v
                break

    sector_details = []
    if sec_info:
        sector_details.append(f"Z:{sec_info['z_score']:+.2f}")
        sector_details.append(sec_info["signal_badge"])
    if sec_margin_info and sec_margin_info.get("buy_reduced_pct") is not None:
        sector_details.append(f"買残減率:{sec_margin_info['buy_reduced_pct']}%")

    sector_disp = f"{sector_raw} ({', '.join(sector_details)})" if (sector_raw and sector_details) else (sector_raw or "--")
    row["sector_disp"] = sector_disp

    price_str = f"¥{row['price']:,}" if row["price"] else "--"
    dev_str = f"{row['deviation_25']:+.2f}%"
    p28w_str = f"{row['price_change_28w']:+.2f}%"
    buy_sell_str = (
        f"{row['latest_buy']:,} / {row['latest_sell']:,}"
        if row["latest_buy"] is not None and row["latest_sell"] is not None else "--"
    )
    ratio_str = f"({row['latest_ratio']:.2f}倍)" if row["latest_ratio"] is not None else "(--)"
    vol_chg_str = f"{row['vol_change_percent']:+.1f}% ({int(row['vol_avg_5']):,}株)" if row["vol_avg_5"] else "--"
    progress_str = f"{row['progress_rate']:.1f}%({row['progress_quarter']})" if row["progress_rate"] is not None else "--"
    earnings_str = (
        f"{row['earnings_date'] or '--'} ({row['earnings_status_str']})" if row["earnings_status_str"] else (row["earnings_date"] or "--")
    )
    pbr_str = f"{row['pbr']:.2f}倍" if row["pbr"] is not None else "--"
    yield_str = f"{row['dividend_yield']:.2f}%" if row["dividend_yield"] is not None else "--"
    valuation_str = f"{pbr_str} / {yield_str}"
    vwap_dev_str = f"{row['vwap_deviation']:+.2f}%" if row["vwap_deviation"] is not None else "--"

    return (
        f"| {row['code']} | {row['name']} | {sector_disp} | {price_str} | {dev_str} | {row['ma25_slope_str']} | {p28w_str} | "
        f"{row['days_to_cover_buy_str']} | {row['days_to_cover_sell_str']} | {row['summary_buy_change_str']} | "
        f"{row['summary_sell_change_str']} | {row['flags_summary_str']} | {buy_sell_str} {ratio_str} | {vol_chg_str} | "
        f"{progress_str} | {earnings_str} | {row['has_yutai']} | {valuation_str} | {vwap_dev_str} | {row['squeeze_stars']} | {row['evaluation']} |"
    )


def _format_institutional_note(nikkei_db: NikkeiDatabase, code: str) -> str:
    """機関投資家空売りポジション(JPX、0.5%ルール)・EDINET大量保有報告(5%ルール)の要約を返す。

    どちらもkabutan.jpにはない情報源で、`jpx-sync`/`edinet-sync`で事前に同期しておく必要がある。
    データが無い銘柄は空文字を返す(セクション自体を省略する)。
    """
    short_positions = nikkei_db.get_jpx_short_positions(code)
    large_holdings = nikkei_db.get_edinet_large_holdings(code)

    lines = []
    if short_positions:
        latest_date = short_positions[-1]["date"]
        latest_batch = [p for p in short_positions if p["date"] == latest_date]
        total_ratio = sum(p["short_position_ratio"] or 0.0 for p in latest_batch)
        holders = ", ".join(
            f"{p['holder_name']}({(p['short_position_ratio'] or 0):.2f}%)" for p in latest_batch
        )
        lines.append(
            f"- **機関投資家空売りポジション**(基準日: {latest_date}, 合計{total_ratio:.2f}%, {len(latest_batch)}社): {holders}"
        )
    if large_holdings:
        top = large_holdings[0]
        ratio = top["holding_ratio"]
        ratio_str = f"{ratio:.2f}%" if ratio is not None else "--"
        lines.append(
            f"- **EDINET大量保有報告**(提出日: {top['submission_date']}): {top['holder_name']} "
            f"保有割合{ratio_str} ({top['report_type'] or '--'})"
        )

    if not lines:
        return ""
    return "\n".join(["#### 📌 機関投資家動向(JPX空売りポジション・EDINET大量保有報告)"] + lines)


def _format_weekly_detail_block(row: dict, nikkei_db: NikkeiDatabase | None = None) -> str:
    weekly_records = row["weekly_records"]
    institutional_note = _format_institutional_note(nikkei_db, row["code"]) if nikkei_db is not None else ""

    if not weekly_records:
        if not institutional_note:
            return ""
        sector_disp = row.get("sector_disp") or row.get("sector") or "--"
        return f"#### 【{row['code']}】{row['name']} ({sector_disp})\n\n{institutional_note}"

    sector_disp = row.get("sector_disp") or row.get("sector") or "--"
    lines = [
        f"#### 【{row['code']}】{row['name']} ({sector_disp}) 過去28週間 週次信用残高・株数時系列推移",
        f"- **消化日数**: 買い玉消化日数: {row['days_to_cover_buy_str']} / 売り玉消化日数: {row['days_to_cover_sell_str']}",
        f"- **期間別増減率**: 買い残: [{row['summary_buy_change_str']}] / 売り残: [{row['summary_sell_change_str']}]",
        f"- **25MA傾き(5日比)**: {row['ma25_slope_str']} / **トレンド判定**: {row['trend_broken_badge']}",
        f"- **需給判定フラグ**: {row['flags_summary_str']}",
        "| 日付 | 終値 | 前週比 | 売り残 (株) | 売残変動 | 買い残 (株) | 信用倍率 | 実質倍率(4週中央値) | 注記 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for i, r in enumerate(weekly_records):
        d_str = r.get("date", "--")
        p_str = f"¥{r['price']:,.1f}" if r.get("price") else "--"
        s_str = f"{r.get('sell', 0):,}" if r.get("sell") is not None else "--"
        b_str = f"{r.get('buy', 0):,}" if r.get("buy") is not None else "--"
        rat_str = f"{r['ratio']:.2f}倍" if r.get("ratio") is not None else "--"
        med_rat = r.get("rolling_median_ratio")
        med_rat_str = f"{med_rat:.2f}倍" if med_rat is not None else rat_str

        price_chg_str = "--"
        if i + 1 < len(weekly_records) and r.get("price") and weekly_records[i + 1].get("price") and weekly_records[i + 1]["price"] > 0:
            pct = ((r["price"] - weekly_records[i + 1]["price"]) / weekly_records[i + 1]["price"]) * 100
            price_chg_str = f"{pct:+.1f}%"

        sell_chg_str = "--"
        if i + 1 < len(weekly_records) and r.get("sell") is not None and weekly_records[i + 1].get("sell") is not None:
            prev_sell = weekly_records[i + 1]["sell"]
            if prev_sell > 0:
                sell_chg_str = f"{((r['sell'] - prev_sell) / prev_sell) * 100:+.1f}%"
            elif r["sell"] > 0:
                sell_chg_str = "新規"

        flags = []
        if r.get("is_cross_spike"):
            flags.append("⚠️権利クロス急増(補正済)")

        try:
            d_parsed = datetime.strptime(d_str.split(" ")[0], "%Y-%m-%d")
            is_cross_period = d_parsed.day >= 14
            if is_cross_period and r.get("sell") is not None and i + 1 < len(weekly_records) and weekly_records[i + 1].get("sell") is not None:
                prev_sell = weekly_records[i + 1]["sell"]
                if (prev_sell > 0 and r["sell"] >= prev_sell * 2.0) or (prev_sell == 0 and r["sell"] > 0):
                    if "⚠️権利クロス急増(補正済)" not in flags:
                        flags.append("⚠️権利クロス疑い")
        except (ValueError, TypeError):
            pass

        if i + 1 < len(weekly_records) and r.get("price") and weekly_records[i + 1].get("price") and weekly_records[i + 1]["price"] > 0:
            prev_p = weekly_records[i + 1]["price"]
            pct_val = ((r["price"] - prev_p) / prev_p) * 100.0
            pct_abs = abs(pct_val)
            curr_vol, prev_vol = r.get("volume"), weekly_records[i + 1].get("volume")
            is_vol_surged = bool(curr_vol and prev_vol and prev_vol > 0 and curr_vol / prev_vol >= 3.0)
            if pct_val > 20:
                if is_vol_surged:
                    flags.append("🔴TOB/MBO疑い(商い急増)")
                elif pct_abs > 25:
                    flags.append("⚠️急激な乱高下(仕手・材料)")
            elif pct_val < -25:
                flags.append("🔴異常急落")

        lines.append(f"| {d_str} | {p_str} | {price_chg_str} | {s_str} | {sell_chg_str} | {b_str} | {rat_str} | {med_rat_str} | {' '.join(flags)} |")

    if institutional_note:
        lines.append("")
        lines.append(institutional_note)

    return "\n".join(lines)


def generate_ranking_prompt(
    db: Database,
    nikkei_db: NikkeiDatabase,
    only_improving: bool = True,
    max_rows: int = MAX_PROMPT_ROWS,
) -> str:
    """需給分析データを元に、Gemini向けCoT分析プロンプト(買い推奨TOP5選定)を生成する。

    SPECIFICATION.md 5.1節の7ステップ思考プロセスに対応する。
    """
    rows = analyze_stocks(db, only_improving=only_improving)
    if not rows:
        return ""
    rows = rows[:max_rows]

    sec_results = calculate_sector_divergence(db)
    sector_section, sector_signal_map = _format_sector_signal_section(sec_results)

    sec_margin_rows = db.get_sector_margin_summary()
    sector_margin_section, sector_margin_map = _format_sector_margin_section(sec_margin_rows)

    data_rows = [_format_data_row(r, sector_signal_map, sector_margin_map) for r in rows]
    weekly_detail_blocks = [b for b in (_format_weekly_detail_block(r, nikkei_db) for r in rows) if b]

    data_table_str = "\n".join(data_rows)
    weekly_details_str = "\n\n".join(weekly_detail_blocks) if weekly_detail_blocks else "週次信用残の時系列詳細データなし"
    nikkei_section = _format_nikkei_section(nikkei_db)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    max_prompt_rows = len(rows)

    return f"""あなたはプロのトップ金融クオンツアナリスト、および株式需給スペシャリストです。
提供された以下の「【前提】全体相場環境データ(日経平均・JPX公式需給データ)」、「33業種別・初動検知＆空売りダイバージェンス状況」、「東証33業種セクター別 信用需給好転ランキング」、および「クオンツ信用需給・消化日数・25MA傾き・フラグデータ」を極めて緻密に分析し、中期(1〜4週間)スイングトレードにおける【買い推奨ランキング Top 5】および相場解説レポートを作成してください。

📅【分析基準日時およびデータ定義の徹底】
- **分析実行日時**: {now_str}
- **最新株価・騰落率・乖離率・出来高・VWAP・25MA傾き**: 上記の分析実行日における【リアルタイム(当日終値)】データです。
- **週次信用残高・信用倍率(東証発表値)**: 毎週火曜日の大引け後に日本取引所グループ(JPX)より開示される【前週金曜日時点】の確定値データです。

🎯【新規算出クオンツ指標＆フラグの定義と判定ルール】
1. **25日線の傾き判定(MA25 Slope 5d)**:
   - `当日の25日線数値 - 5営業日前の25日線数値` を計算。マイナスであれば「下向き」と判定。傾きがプラス(上向き)で推移しサポートされている銘柄、または25日線を力強く上抜けブレイクアウトした銘柄を高評価。
2. **下降トレンド崩れフラグ (`trend_broken: True`)**:
   - `現在値 < 25日線` かつ `25日線が下向き` の場合に付与。下降トレンド突入・下落加速リスクが極めて高いため、**【スイング買い推奨から厳格に除外】**。
3. **買い玉消化日数(Days to Cover Long) ＝ 最新信用買い残高(株) ÷ 直近5日平均出来高(株)**:
   - 需給のしこり度を測定する指標。消化日数が**5日以下**の銘柄は上値が極めて軽く、買い戻しや新規買いで急騰しやすい。逆に**20日以上**の銘柄はしこり玉が重く、戻り売りに押されやすいため減点対象。
4. **信用倍率の評価方針(水準ではなく増減トレンド重視)**:
   - 信用倍率の「水準(絶対値)」ではなく「増減トレンド」を確認してください。倍率が高くても「買い残が減少し始めているか(しこり玉の消化が進んでいるか)」を最重視し、減少トレンドにあれば高く評価してください。
5. **バリュエーション(配当利回りとPBR)の安全域の併用**:
   - 需給分析に加えて、配当利回りとPBRの安全域を併用してください。下値リスクの限定された銘柄を評価に加味してください。
6. **売り玉消化日数(Days to Cover Short) ＝ 最新信用売り残高(株) ÷ 直近5日平均出来高(株)**:
   - ショートカバー(踏み上げ)の継続圧力を測定。消化日数が長く、かつ売り残が増加傾向にある銘柄は強烈な踏み上げ相場に発展する可能性が高い。
7. **4週・12週・28週 買い残／売り残増減率(%)**:
   - 直近4週(短期)、12週(中期)、28週(長期・約半年)の各スパンにおける株数増減率。短期だけでなく**12週・28週でも買い残が大幅減少(マイナス)**している銘柄は、真のしこり玉消化完了銘柄として最高評価。
8. **4大クオンツフラグによる厳格な自動除外・選別ルール**:
   - 📉 **`trend_broken` (トレンド崩れ - 25MA下向き+現在値割れ)**: **【買い推奨から除外】**
   - ❌ **`short_cover_exhausted` (燃料枯渇 - 4週連続売減)**: **【ランキングから除外・降格】**
   - 🔴 **`tob_mbo_suspected` (TOB/MBO疑い - 出来高300%超+株価20%超上昇)**: **【ランキングから除外】**
   - ⚠️ **`earnings_risk` (決算マタギ危険 - 10営業日以内)**: **【スイング買い推奨から除外】**
   - ⚠️ **`yutai_cross_suspected` (優待クロス疑い - 確定当月+倍率0.5倍以下)**: **【ランキングから除外】**
9. **セクターモメンタム & 需給好転の追い風(参考情報)**:
   - 所属セクターの「Zスコア(モメンタム乖離)」や「セクター買残減少率(%)」は**参考情報として加味**してください。**個別銘柄固有の需給指標を最重視**し、セクター追い風だけで個別銘柄を過大評価しないでください。逆に、セクター全体が不調でも個別需給が優秀な銘柄は正当に評価してください。
10. **機関投資家動向(JPX空売りポジション・EDINET大量保有報告、記載がある銘柄のみ)**:
   - 各銘柄の週次詳細データ末尾に「📌 機関投資家動向」がある場合、それはkabutan.jpの信用残データとは別の情報源(JPXの0.5%以上空売りポジション開示、EDINETの5%以上大量保有報告書)です。複数機関投資家による空売りポジション集中は将来の踏み上げ材料として、大量保有報告書(特に直近の新規提出・保有割合増加)は大口資金の関心の高さを示す補強材料として、それぞれ加点評価に加味してください。記載が無い銘柄は単に情報源が無いだけであり、減点対象にはしないでください。

思考の罠に陥らず、ステップバイステップで論理的に深く推論(Chain-of-Thought)した上で、客観的な数値根拠を元に結論を導き出してください。

---

{nikkei_section}

---

{sector_section}

---

{sector_margin_section}

---

### 1. 分析対象 信用需給・消化日数・25MA傾き・クオンツ指標データ一覧(上位{max_prompt_rows}件)
| コード | 銘柄名 | 業種 (初動シグナル/Zスコア/買残減率) | 最新株価 | 25日乖離 | 25MA傾き(5日比) | 28週騰落 | 買玉消化日数 | 売玉消化日数 | 買残増減(4w/12w/28w) | 売残増減(4w/12w/28w) | 需給フラグ | 信用買残/売残(倍率) | 出来高変化率(5日平均) | 経常益進捗 | 決算日 | 株主優待 | PBR/利回り | VWAP乖離 | 踏み上げ | 総合評価 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{data_table_str}

---

### 2. 各銘柄の過去28週間(約半年間)週次信用残高・株数時系列データ詳細(売残・買残株数推移)
※以下に分析対象銘柄の過去28週間における【日付・終値・売り残(株)・買い残(株)・信用倍率】の完全な時系列明細を提示します。AIアナリストは必ず一週ごとの実際の株数変化(分母の売残増加による見せかけの改善か、分子の買残激減による真の改善か)を算出して分析に使用してください。

{weekly_details_str}

---

### 3. 分析・推論プロセス(思考のステップ)
以下のステップに沿って深く考察し、レポートを構築してください。

**【ステップ1】「全体相場環境(日経平均・JPXデータ)」の分析と「大局判断(買うか見送りか)およびその割合」の提示**
- 提供された「【前提】全体相場環境データ」および「JPX公式 需給・マクロデータ」に基づき、現在の日本株市場全体の相場観をマクロ的・時系列的な視点から詳細に分析してください。
- 現在の全体相場において、積極的に個別株を「買う」べき局面か慎重に「見送り」とすべき局面か、大局的な投資判断とその判断割合(例: 「買う 70% / 見送り 30%」)を明確に記載してください。

**【ステップ2】「33業種別 セクターモメンタム (Zスコア) & 信用需給好転バランス」の深層判定**
- 「【前提】33業種別・初動検知＆空売りダイバージェンス状況」および「【前提】東証33業種セクター別 信用需給好転ランキング(買残減少率)」を精査し、どのセクターで買い残整理(しこり玉消化)が進み、大口の資金流入(Zスコア高値)が起きているかを把握してください。
- ただし、セクター全体の追い風は**あくまで参考情報(補助的な加点要素)**です。好転セクターに属していても個別需給が悪い銘柄は評価せず、逆にセクターが不調でも個別銘柄の需給・テクニカルが優秀であれば正当に高評価してください。

**【ステップ3】「消化日数(買い玉/売り玉)」と「多期間増減率(4w/12w/28w)」による真の需給選別**
- **買い玉消化日数**が短く、**4w/12w/28wの全期間で買い残が大幅減少**している「しこり玉解消・上値軽快株」を抽出します。
- 逆に、買い玉消化日数が20日超で買い残が減っていない銘柄、または売残が4週連続減少して「❌燃料枯渇」となっている銘柄を厳格に除外・降格してください。

**【ステップ4】「4大クオンツフラグ」と「決算マタギリスク」の排除**
- 「⚠️決算マタギ危険(10営業日以内)」「🔴TOB/MBO疑い」「⚠️優待クロス疑い」のフラグが付いている銘柄はすべて買い推奨から除外してください。

**【ステップ5】「株価トレンド(25日乖離/28週騰落/25MA傾き)」および「trend_broken(トレンド崩れ)」の評価**
- 25日線の傾き(5日比)および「📉トレンド崩れ(trend_broken: True)」フラグを精査し、25MAが下向きかつ株価が下回っている銘柄は、リバウンド狙いであっても下降トレンドリスクが高いため買い推奨から除外してください。25日線が上向きでサポートとして機能している銘柄、または25日線を明確に上抜けブレイクアウトした銘柄を高評価とします。

**【ステップ6】期待値を極大化する【買い推奨ランキング TOP 5】**
- ステップ1〜5を踏まえ、中期(1〜4週間)でのスイングを前提とし、「消化日数の軽さ」「4w/12w/28wでの確実なしこり玉消化」「トレンド崩れなし (trend_broken: False)」「25MA上向き/サポート」「フラグリスクなし」を最重視し、「所属セクターの需給好転・追い風」は補助的な加点要素として加味しつつ、銘柄を5つ厳選し、1位から5位までランク付けしてください。
- ⚠️【最重要】分析システムが正常にパースして自動登録できるように、各銘柄について以下の【厳格なフォーマット】を一字一句違わず完全に守って出力してください。

【出力フォーマット】
第1位：[4桁の半角銘柄コード] [銘柄名]
選定理由: [所属セクターの需給好転・Zスコア状況、買い玉/売り玉消化日数、期間別増減率(過去28週間の買い残激減等)、25日線傾き・トレンド状況、決算進捗率、および出来高や価格面での決定的な理由]
アクションプラン:
エントリー推奨帯: [下限価格]円〜[上限価格]円
ターゲット目標値: [目標価格]円
撤退損切りライン: [損切り価格]円

第2位：[4桁の半角銘柄コード] [銘柄名]
選定理由: ...
アクションプラン:
エントリー推奨帯: ...
ターゲット目標値: ...
撤退損切りライン: ...

(※第5位まで同様に記述してください。)

**【ステップ7】警戒すべき【見せかけの倍率改善・燃料枯渇・決算マタギ・トレンド崩れ・罠】銘柄**
- 「📉トレンド崩れ(25MA下向き＋割れ)」、「❌燃料枯渇(4週連続売減)」や「買玉消化日数が重すぎる銘柄」、「決算マタギ危険銘柄」を具体的に名指しで指摘し、なぜエントリーを避けるべきか明快に解説してください。

---

### 4. 出力フォーマット
上記の「ステップ1」から「ステップ7」の見出しに従って、プロのクオンツ需給アナリストが作成した客観的・説得力のある最高品質のレポートとして回答してください。

1. **【全体の相場観と大局判断】**
   - **日経平均・JPX需給データのGemini分析コメント**: PER/株価時系列、騰落レシオ、信用需給、JPX空売り集計・投資部門別動向に基づく分析
   - **業種別セクターの需給判断コメント**: どの業種セクターで買い残整理(しこり玉解消)が進み好需給となっているかの参考情報。ただし個別銘柄のランキングにおいてはセクター追い風より個別需給指標を優先すること
   - **大局判断(買うか見送りか)とその判断割合**: (例:買う 70% / 見送り 30% などのパーセンテージ)
   - **個別株へのアプローチ方針**: 全体相場およびセクター需給動向を踏まえた個別銘柄選定方針
2. **【買い推奨ランキング TOP 5】**
   - (ステップ6で指定した【厳格なフォーマット】に従い、第1位から第5位まで出力)
3. **【警戒・除外すべき罠銘柄(トレンド崩れ・燃料枯渇・見せかけの倍率改善等)】**
   - (ステップ7に基づく、トレンド崩れ銘柄・燃料枯渇銘柄・錯覚の改善銘柄・決算マタギ銘柄等の解説)
"""
