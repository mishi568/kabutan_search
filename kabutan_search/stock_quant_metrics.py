"""クオンツ指標・需給フラグ・25日線トレンド判定の算出

SPECIFICATION.md 4.3節(クオンツ指標)・4.4節(4大クオンツフラグ)に対応する。
"""
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


def calculate_days_to_cover(
    latest_buy: Optional[float],
    latest_sell: Optional[float],
    vol_avg_5d: Optional[float],
) -> Dict[str, Any]:
    """買い玉消化日数・売り玉消化日数を算出する。

    買い玉消化日数 = 最新信用買い残高(株) ÷ 直近5日平均出来高(株)
    売り玉消化日数 = 最新信用売り残高(株) ÷ 直近5日平均出来高(株)
    """
    if not vol_avg_5d or vol_avg_5d <= 0:
        return {
            "days_to_cover_buy": None,
            "days_to_cover_sell": None,
            "days_to_cover_buy_str": "--",
            "days_to_cover_sell_str": "--",
        }

    dtc_buy = (latest_buy / vol_avg_5d) if (latest_buy is not None and latest_buy >= 0) else None
    dtc_sell = (latest_sell / vol_avg_5d) if (latest_sell is not None and latest_sell >= 0) else None

    return {
        "days_to_cover_buy": round(dtc_buy, 2) if dtc_buy is not None else None,
        "days_to_cover_sell": round(dtc_sell, 2) if dtc_sell is not None else None,
        "days_to_cover_buy_str": f"{dtc_buy:.1f}日" if dtc_buy is not None else "--",
        "days_to_cover_sell_str": f"{dtc_sell:.1f}日" if dtc_sell is not None else "--",
    }


def calculate_margin_changes_multi_period(weekly_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """直近4週・12週・28週における買い残・売り残の増減率(%)を算出する。

    weekly_records: 日付降順(最新がインデックス0)の週次レコードのリスト
    """
    res = {
        "buy_change_4w": None, "buy_change_12w": None, "buy_change_28w": None,
        "sell_change_4w": None, "sell_change_12w": None, "sell_change_28w": None,
        "buy_change_4w_str": "--", "buy_change_12w_str": "--", "buy_change_28w_str": "--",
        "sell_change_4w_str": "--", "sell_change_12w_str": "--", "sell_change_28w_str": "--",
        "summary_buy_str": "--",
        "summary_sell_str": "--",
    }

    if not weekly_records:
        return res

    latest = weekly_records[0]
    latest_buy = latest.get("buy")
    latest_sell = latest.get("sell")

    def _calc_pct(curr, prev):
        if curr is not None and prev is not None and prev > 0:
            return ((curr - prev) / prev) * 100.0
        return None

    def _fmt_pct(val):
        return f"{val:+.1f}%" if val is not None else "--"

    if len(weekly_records) > 4:
        r4 = weekly_records[4]
        res["buy_change_4w"] = _calc_pct(latest_buy, r4.get("buy"))
        res["sell_change_4w"] = _calc_pct(latest_sell, r4.get("sell"))
    elif len(weekly_records) > 1:
        r_old = weekly_records[-1]
        res["buy_change_4w"] = _calc_pct(latest_buy, r_old.get("buy"))
        res["sell_change_4w"] = _calc_pct(latest_sell, r_old.get("sell"))

    if len(weekly_records) > 12:
        r12 = weekly_records[12]
        res["buy_change_12w"] = _calc_pct(latest_buy, r12.get("buy"))
        res["sell_change_12w"] = _calc_pct(latest_sell, r12.get("sell"))

    if len(weekly_records) > 27:
        r28 = weekly_records[min(27, len(weekly_records) - 1)]
        res["buy_change_28w"] = _calc_pct(latest_buy, r28.get("buy"))
        res["sell_change_28w"] = _calc_pct(latest_sell, r28.get("sell"))

    res["buy_change_4w_str"] = _fmt_pct(res["buy_change_4w"])
    res["buy_change_12w_str"] = _fmt_pct(res["buy_change_12w"])
    res["buy_change_28w_str"] = _fmt_pct(res["buy_change_28w"])
    res["sell_change_4w_str"] = _fmt_pct(res["sell_change_4w"])
    res["sell_change_12w_str"] = _fmt_pct(res["sell_change_12w"])
    res["sell_change_28w_str"] = _fmt_pct(res["sell_change_28w"])

    res["summary_buy_str"] = f"4w:{res['buy_change_4w_str']}/12w:{res['buy_change_12w_str']}/28w:{res['buy_change_28w_str']}"
    res["summary_sell_str"] = f"4w:{res['sell_change_4w_str']}/12w:{res['sell_change_12w_str']}/28w:{res['sell_change_28w_str']}"

    for k in ["buy_change_4w", "buy_change_12w", "buy_change_28w", "sell_change_4w", "sell_change_12w", "sell_change_28w"]:
        if res[k] is not None:
            res[k] = round(res[k], 2)

    return res


def calculate_ma25_slope_and_trend(daily_prices: List[float]) -> Dict[str, Any]:
    """25日移動平均線(25MA)と5営業日前の25MA、傾き、トレンド崩れフラグを算出する。

    ・当日の25日線 = 当日を含む直近25営業日の終値平均
    ・5営業日前の25日線 = 5営業日前を含む直近25営業日の終値平均
    ・25日線の傾き = 当日の25日線 - 5営業日前の25日線(マイナスなら下向き)
    ・現在値 < 25日線 かつ 25日線が下向き の場合 trend_broken = True
    """
    res = {
        "ma25_today": None,
        "ma25_5d_ago": None,
        "ma25_slope": None,
        "ma25_slope_str": "--",
        "ma25_is_downward": False,
        "trend_broken": False,
        "trend_broken_badge": "",
    }

    if not daily_prices:
        return res

    price_today = daily_prices[0]
    if price_today is None or price_today <= 0:
        return res

    n_prices = len(daily_prices)
    if n_prices < 25:
        ma25_today = sum(daily_prices) / float(n_prices)
        res["ma25_today"] = round(ma25_today, 2)
        if price_today < ma25_today:
            res["trend_broken_badge"] = "⚠️25MA割れ"
        return res

    ma25_today = sum(daily_prices[0:25]) / 25.0
    res["ma25_today"] = round(ma25_today, 2)

    if n_prices >= 30:
        ma25_5d_ago = sum(daily_prices[5:30]) / 25.0
    else:
        sub = daily_prices[5:]
        ma25_5d_ago = (sum(sub) / float(len(sub))) if sub else ma25_today

    res["ma25_5d_ago"] = round(ma25_5d_ago, 2)

    ma25_slope = ma25_today - ma25_5d_ago
    res["ma25_slope"] = round(ma25_slope, 2)
    res["ma25_slope_str"] = f"{ma25_slope:+.1f}円"

    ma25_is_downward = ma25_slope < 0.0
    res["ma25_is_downward"] = ma25_is_downward

    trend_broken = (price_today < ma25_today) and ma25_is_downward
    res["trend_broken"] = trend_broken

    if trend_broken:
        res["trend_broken_badge"] = "📉トレンド崩れ(25MA下向+割れ)"
    elif price_today < ma25_today:
        res["trend_broken_badge"] = "⚠️25MA割れ(横這/上向)"
    elif ma25_is_downward:
        res["trend_broken_badge"] = "⚠️25MA下向き"
    else:
        res["trend_broken_badge"] = "📈上昇トレンド(25MA上向)"

    return res


def evaluate_quant_flags(
    weekly_records: List[Dict[str, Any]],
    price_latest: Optional[float],
    price_prev_week: Optional[float],
    vol_this_week: Optional[float],
    vol_prev_week: Optional[float],
    earnings_date_val: Any,
    has_yutai: str,
    settlement_month: Optional[int],
    margin_ratio: Optional[float],
    current_date: Optional[date] = None,
    daily_prices: Optional[List[float]] = None,
    ma25_trend_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """4大クオンツフラグ + トレンド崩れを判定する(SPECIFICATION.md 4.4節)。

    1. short_cover_exhausted: 直近4週間、売り残が連続して減少しているか
    2. tob_mbo_suspected: 週間出来高が前週比300%超 かつ 株価が前週比20%超上昇
    3. earnings_risk: 決算発表予定日まで営業日数10日以内か
    4. yutai_cross_suspected: 優待権利月 かつ 信用倍率0.5倍以下
    5. trend_broken: 現在値 < 25日線 かつ 25日線が下向き
    """
    if current_date is None:
        current_date = datetime.now().date()
    current_month = current_date.month

    # 1. short_cover_exhausted (燃料枯渇)
    short_cover_exhausted = False
    if weekly_records and len(weekly_records) >= 5:
        sells = [r.get("sell") for r in weekly_records[:5]]
        if all(s is not None and s >= 0 for s in sells):
            if sells[0] < sells[1] < sells[2] < sells[3] < sells[4]:
                short_cover_exhausted = True

    # 2. tob_mbo_suspected (TOB/MBO疑い)
    tob_mbo_suspected = False
    vol_ratio = 0.0
    price_gain_pct = 0.0
    if vol_this_week and vol_prev_week and vol_prev_week > 0:
        vol_ratio = vol_this_week / vol_prev_week
    if price_latest and price_prev_week and price_prev_week > 0:
        price_gain_pct = ((price_latest - price_prev_week) / price_prev_week) * 100.0
    if vol_ratio > 3.0 and price_gain_pct > 20.0:
        tob_mbo_suspected = True

    # 3. earnings_risk (決算マタギ危険)
    earnings_risk = False
    business_days_until_earnings = None
    earnings_badge_str = ""

    if earnings_date_val:
        try:
            if isinstance(earnings_date_val, (datetime, date)):
                target_dt = earnings_date_val if isinstance(earnings_date_val, date) else earnings_date_val.date()
            else:
                s = str(earnings_date_val).strip()
                m = re.search(r"(?:(\d{4})[-/])?(\d{1,2})[-/](\d{1,2})", s)
                if m:
                    y_part, m_part, d_part = m.groups()
                    year_val = int(y_part) if y_part else current_date.year
                    target_dt = date(year_val, int(m_part), int(d_part))
                else:
                    target_dt = None

            if target_dt and target_dt >= current_date:
                b_days = int(np.busday_count(current_date, target_dt))
                business_days_until_earnings = b_days
                if b_days <= 10:
                    earnings_risk = True
                    earnings_badge_str = f"⚠️マタギ危険(あと{b_days}営業日)"
        except (ValueError, TypeError):
            pass

    # 4. yutai_cross_suspected (優待クロス疑い)
    yutai_cross_suspected = False
    is_yutai = has_yutai == "あり" or has_yutai is True
    if is_yutai and margin_ratio is not None and margin_ratio <= 0.5:
        if settlement_month is not None:
            try:
                sm = int(settlement_month)
                interim_sm = (sm + 6 - 1) % 12 + 1
                if current_month in {sm, interim_sm}:
                    yutai_cross_suspected = True
            except (ValueError, TypeError):
                yutai_cross_suspected = True
        else:
            yutai_cross_suspected = True

    # 5. trend_broken (25日線傾き判定)
    trend_broken = False
    ma25_slope = None
    ma25_is_downward = False
    ma25_slope_str = "--"
    trend_broken_badge = ""

    if ma25_trend_info:
        trend_broken = ma25_trend_info.get("trend_broken", False)
        ma25_slope = ma25_trend_info.get("ma25_slope")
        ma25_is_downward = ma25_trend_info.get("ma25_is_downward", False)
        ma25_slope_str = ma25_trend_info.get("ma25_slope_str", "--")
        trend_broken_badge = ma25_trend_info.get("trend_broken_badge", "")
    elif daily_prices:
        ma_info = calculate_ma25_slope_and_trend(daily_prices)
        trend_broken = ma_info.get("trend_broken", False)
        ma25_slope = ma_info.get("ma25_slope")
        ma25_is_downward = ma_info.get("ma25_is_downward", False)
        ma25_slope_str = ma_info.get("ma25_slope_str", "--")
        trend_broken_badge = ma_info.get("trend_broken_badge", "")

    flag_tags = []
    if short_cover_exhausted:
        flag_tags.append("❌燃料枯渇(4週連続売減)")
    if tob_mbo_suspected:
        flag_tags.append("🔴TOB/MBO疑い(商い急増+20%超)")
    if earnings_risk:
        flag_tags.append("⚠️決算マタギ危険(10営業日以内)")
    if yutai_cross_suspected:
        flag_tags.append("⚠️優待クロス疑い(低倍率ダミー)")
    if trend_broken:
        flag_tags.append("📉トレンド崩れ(25MA下向+割れ)")

    return {
        "short_cover_exhausted": short_cover_exhausted,
        "tob_mbo_suspected": tob_mbo_suspected,
        "earnings_risk": earnings_risk,
        "yutai_cross_suspected": yutai_cross_suspected,
        "trend_broken": trend_broken,
        "ma25_slope": ma25_slope,
        "ma25_slope_str": ma25_slope_str,
        "ma25_is_downward": ma25_is_downward,
        "trend_broken_badge": trend_broken_badge,
        "business_days_until_earnings": business_days_until_earnings,
        "earnings_badge_str": earnings_badge_str,
        "flags_summary_str": " ".join(flag_tags) if flag_tags else "特記事項なし",
        "flag_tags": flag_tags,
    }


def add_quant_metrics_to_dataframe(
    df_stocks: pd.DataFrame,
    df_sector: Optional[pd.DataFrame] = None,
    current_date: Optional[date] = None,
) -> pd.DataFrame:
    """個別銘柄DataFrameにクオンツ指標・需給フラグを算出し、セクター集計データをJOINする。

    df_stocks の各行は code/price/margin_buy/margin_sell/margin_ratio/vol_avg_5d/
    weekly_records/daily_prices/earnings_date/has_yutai/settlement_month/sector 等を持つ想定。
    df_sector は sector(またはsector_name)・z_score等のセクター別集計データ。
    """
    if df_stocks is None or df_stocks.empty:
        return pd.DataFrame()

    df = df_stocks.copy()
    if current_date is None:
        current_date = datetime.now().date()

    def _calc_dtc(row):
        latest_buy = row.get("margin_buy")
        latest_sell = row.get("margin_sell")
        vol_5d = row.get("vol_avg_5d") or row.get("volume_avg_5d") or row.get("volume")
        return calculate_days_to_cover(latest_buy, latest_sell, vol_5d)

    dtc_results = df.apply(_calc_dtc, axis=1)
    df["days_to_cover_buy"] = [r["days_to_cover_buy"] for r in dtc_results]
    df["days_to_cover_sell"] = [r["days_to_cover_sell"] for r in dtc_results]
    df["days_to_cover_buy_str"] = [r["days_to_cover_buy_str"] for r in dtc_results]
    df["days_to_cover_sell_str"] = [r["days_to_cover_sell_str"] for r in dtc_results]

    def _calc_margin_changes(row):
        w_records = row.get("weekly_records")
        if not isinstance(w_records, list):
            w_records = []
        return calculate_margin_changes_multi_period(w_records)

    mc_results = df.apply(_calc_margin_changes, axis=1)
    for key in [
        "buy_change_4w", "buy_change_12w", "buy_change_28w",
        "sell_change_4w", "sell_change_12w", "sell_change_28w",
        "buy_change_4w_str", "buy_change_12w_str", "buy_change_28w_str",
        "sell_change_4w_str", "sell_change_12w_str", "sell_change_28w_str",
    ]:
        df[key] = [r[key] for r in mc_results]
    df["summary_buy_change_str"] = [r["summary_buy_str"] for r in mc_results]
    df["summary_sell_change_str"] = [r["summary_sell_str"] for r in mc_results]

    def _calc_ma25_trend(row):
        d_prices = row.get("daily_prices") or row.get("history_prices") or row.get("prices")
        if not isinstance(d_prices, list):
            d_prices = []
        return calculate_ma25_slope_and_trend(d_prices)

    ma25_results = df.apply(_calc_ma25_trend, axis=1)
    for key in ["ma25_today", "ma25_5d_ago", "ma25_slope", "ma25_slope_str", "ma25_is_downward", "trend_broken", "trend_broken_badge"]:
        df[key] = [r[key] for r in ma25_results]

    def _calc_flags(row):
        w_records = row.get("weekly_records")
        if not isinstance(w_records, list):
            w_records = []
        price_latest = row.get("price")
        price_prev = row.get("price_prev_week")
        if price_prev is None and len(w_records) > 1 and w_records[1].get("price"):
            price_prev = w_records[1]["price"]

        vol_this = row.get("vol_this_week")
        if vol_this is None and len(w_records) > 0 and w_records[0].get("volume"):
            vol_this = w_records[0]["volume"]

        vol_prev = row.get("vol_prev_week")
        if vol_prev is None and len(w_records) > 1 and w_records[1].get("volume"):
            vol_prev = w_records[1]["volume"]

        d_prices = row.get("daily_prices") or row.get("history_prices") or row.get("prices")
        if not isinstance(d_prices, list):
            d_prices = []

        return evaluate_quant_flags(
            weekly_records=w_records,
            price_latest=price_latest,
            price_prev_week=price_prev,
            vol_this_week=vol_this,
            vol_prev_week=vol_prev,
            earnings_date_val=row.get("earnings_date"),
            has_yutai=row.get("has_yutai", "なし"),
            settlement_month=row.get("settlement_month"),
            margin_ratio=row.get("margin_ratio"),
            current_date=current_date,
            daily_prices=d_prices,
        )

    flag_results = df.apply(_calc_flags, axis=1)
    df["short_cover_exhausted"] = [r["short_cover_exhausted"] for r in flag_results]
    df["tob_mbo_suspected"] = [r["tob_mbo_suspected"] for r in flag_results]
    df["earnings_risk"] = [r["earnings_risk"] for r in flag_results]
    df["yutai_cross_suspected"] = [r["yutai_cross_suspected"] for r in flag_results]
    df["trend_broken"] = [r["trend_broken"] for r in flag_results]
    df["business_days_until_earnings"] = [r["business_days_until_earnings"] for r in flag_results]
    df["earnings_badge_str"] = [r["earnings_badge_str"] for r in flag_results]
    df["flags_summary_str"] = [r["flags_summary_str"] for r in flag_results]

    if df_sector is not None and not df_sector.empty and "sector" in df.columns:
        sec_df = df_sector.copy()
        if "sector_name" in sec_df.columns and "sector" not in sec_df.columns:
            sec_df = sec_df.rename(columns={"sector_name": "sector"})

        rename_dict = {}
        if "z_score" in sec_df.columns and "sector_Z_score" not in sec_df.columns:
            rename_dict["z_score"] = "sector_Z_score"
        if "buy_reduced_pct" in sec_df.columns and "sector_buy_balance_trend" not in sec_df.columns:
            rename_dict["buy_reduced_pct"] = "sector_buy_balance_trend"
        if "signal_type" in sec_df.columns and "sector_signal" not in sec_df.columns:
            rename_dict["signal_type"] = "sector_signal"
        if rename_dict:
            sec_df = sec_df.rename(columns=rename_dict)

        join_cols = ["sector"]
        for col in ["sector_Z_score", "sector_buy_balance_trend", "sector_signal", "signal_badge"]:
            if col in sec_df.columns and col not in join_cols:
                join_cols.append(col)

        sec_df_subset = sec_df[join_cols].drop_duplicates(subset=["sector"])
        df = pd.merge(df, sec_df_subset, on="sector", how="left")

    return df
