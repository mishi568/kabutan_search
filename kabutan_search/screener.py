"""ROE/PBR散布図スクリーニング

既に詳細データ(PER・PBR・信用残・VWAP等)を取得済みの銘柄に対して、
流動性・踏み上げ燃料・需給・トレンド・VWAP乖離でフィルタする(Stage2以降で使用)。
"""
import datetime

from .database import Database
from .stock_quant_metrics import calculate_ma25_slope_and_trend


def calculate_roe(pbr, per):
    """ROE(%) = PBR / PER * 100。PERが無効(0以下)または欠損の場合はNoneを返す"""
    if pbr is None or per is None:
        return None
    try:
        pbr_val, per_val = float(pbr), float(per)
        if per_val <= 0:
            return None
        return round((pbr_val / per_val) * 100.0, 2)
    except (ValueError, TypeError):
        return None


def is_dummy_supply_month(settlement_month, current_month=None):
    """当月が権利確定月(決算月または中間月)かどうかを判定する"""
    if settlement_month is None:
        return False
    if current_month is None:
        current_month = datetime.datetime.now().month
    try:
        m = int(settlement_month)
        interim_m = (m + 6 - 1) % 12 + 1
        return current_month == m or current_month == interim_m
    except (ValueError, TypeError):
        return False


def process_and_filter_stocks(
    db: Database,
    current_month: int | None = None,
    min_trading_value_e: float = 3.0,
    min_volume_w: float = 10.0,
    max_margin_ratio: float = 3.0,
    vwap_cutoff: float = 30.0,
) -> list[dict]:
    """全銘柄の最新レコードにROE・25MA・フィルタ結果を付与して返す。

    Parameters:
        current_month: 権利確定月判定に使う評価月(省略時は実行時の月)
        min_trading_value_e: 最低売買代金(億円)
        min_volume_w: 最低出来高(万株)
        max_margin_ratio: 信用倍率の上限
        vwap_cutoff: VWAP乖離率の許容幅(%)
    """
    if current_month is None:
        current_month = datetime.datetime.now().month

    raw_stocks = [dict(row) for row in db.get_latest_records_for_screening()]
    processed_stocks = []

    for stock in raw_stocks:
        per = stock.get("per")
        pbr = stock.get("pbr")
        price = stock.get("price")
        vwap = stock.get("vwap")
        volume = stock.get("volume")
        trading_value = stock.get("trading_value")  # 千円単位
        margin_sell = stock.get("margin_sell")
        margin_buy = stock.get("margin_buy")
        margin_ratio = stock.get("margin_ratio")
        issued_shares = stock.get("issued_shares")
        settlement_month = stock.get("settlement_month")

        # A. ROE算出(赤字PERは除外)
        if per is None or per <= 0:
            continue
        roe = calculate_roe(pbr, per)
        if roe is None:
            continue
        stock["roe"] = roe

        # B. VWAP乖離率
        vwap_dev = None
        if price is not None and vwap is not None and vwap > 0:
            vwap_dev = round(((price - vwap) / vwap) * 100.0, 2)
        stock["vwap_deviation"] = vwap_dev

        # C. 25MA・75MAトレンド判定
        history = db.get_stock_close_prices(stock["code"], limit=75)
        prices = [h["price"] for h in history if h["price"] is not None]

        ma25 = ma75 = ma25_slope = None
        trend_broken = False
        trend_status = "Unknown"

        if prices:
            ma_res = calculate_ma25_slope_and_trend(prices)
            ma25 = ma_res.get("ma25_today")
            ma25_slope = ma_res.get("ma25_slope")
            trend_broken = ma_res.get("trend_broken", False)

            if len(prices) >= 75:
                ma75 = sum(prices[:75]) / 75.0

            if price is not None:
                if trend_broken:
                    trend_status = "📉トレンド崩れ"
                elif ma25 is not None and ma75 is not None:
                    if price < ma25 and price < ma75:
                        trend_status = "様子見"
                    elif price > ma25 and price > ma75:
                        trend_status = "上昇"
                    else:
                        trend_status = "レンジ"
                elif ma25 is not None:
                    trend_status = "上昇" if price > ma25 else "様子見"

        stock["ma25"] = round(ma25, 2) if ma25 else None
        stock["ma75"] = round(ma75, 2) if ma75 else None
        stock["ma25_slope"] = ma25_slope
        stock["trend_broken"] = trend_broken
        stock["trend_status"] = trend_status

        # D. フィルタ判定(True=通過)

        # 1. 流動性フィルタ(売買代金が下限以上 または 出来高が下限以上)
        # trading_valueは千円単位。1億円 = 100,000千円。volumeは株数。1万株 = 10,000株。
        passed_liquidity = (
            (trading_value is not None and trading_value >= min_trading_value_e * 100000)
            or (volume is not None and volume >= min_volume_w * 10000)
        )

        # 2. 踏み上げ燃料フィルタ(信用売り残が5万株以上 かつ 発行済株式数の0.5%以上)
        passed_squeeze = False
        if margin_sell is not None and margin_sell >= 50000:
            if issued_shares is not None and issued_shares > 0:
                if (margin_sell / issued_shares) >= 0.005:
                    passed_squeeze = True

        # 3. 上値圧迫フィルタ(信用倍率が上限未満。データ欠損時は通過扱い)
        passed_overhang = margin_ratio is None or margin_ratio < max_margin_ratio

        # 4. 優待クロスによるダミー需給の判定(踏み上げ評価を無効化)
        is_dummy = is_dummy_supply_month(settlement_month, current_month)
        if is_dummy:
            passed_squeeze = False

        # 5. トレンドフィルタ(様子見・トレンド崩れ・不明を除外)
        passed_trend = trend_status not in ["様子見", "📉トレンド崩れ", "Unknown"]

        # 6. VWAP乖離フィルタ(±vwap_cutoff%以上の乖離を除外)
        passed_vwap = vwap_dev is None or abs(vwap_dev) < vwap_cutoff

        stock["filter_liquidity"] = passed_liquidity
        stock["filter_squeeze"] = passed_squeeze
        stock["filter_overhang"] = passed_overhang
        stock["filter_trend"] = passed_trend
        stock["filter_vwap"] = passed_vwap
        stock["is_dummy_supply"] = is_dummy

        processed_stocks.append(stock)

    return processed_stocks
