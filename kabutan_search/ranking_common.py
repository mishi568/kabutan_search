"""tansaku.py / margin_ranking.py 共通のランキング表パース用ヘルパー"""
import re

# 5桁のETF/ETNコード等(例: "404A")は対象外。通常株式は4桁の数字コードのみ
CODE_RE = re.compile(r"^\d{4}$")

# ETF/ETN/REIT等の市場区分(例: "東Ｅ")は個別企業の需給分析の対象外。
# 4桁コードでもこれらの市場に属する銘柄(投資信託・ETF等)が多数混在するため、
# コード形式だけでなく市場表記でも除外する。
_NON_STOCK_MARKET_MARKERS = ("Ｅ",)


def is_regular_stock_market(market: str) -> bool:
    """ETF/ETN等ではない、通常の株式市場(プライム/スタンダード/グロース等)かどうか"""
    return not any(marker in market for marker in _NON_STOCK_MARKET_MARKERS)


def is_regular_stock_code(code: str, market: str) -> bool:
    """需給分析の対象になる通常株式コードかどうか(コード形式 + 市場区分の両方で判定)"""
    return bool(CODE_RE.match(code)) and is_regular_stock_market(market)


def clean_numeric(text: str):
    if not text:
        return None
    text = text.replace(",", "").replace("+", "").replace("%", "").strip()
    if text in ("－", "-", ""):
        return None
    try:
        return float(text)
    except ValueError:
        return None
