"""株探「株価注意報」の信用残ランキング取得

kabutan.jp/warning/?mode=7_1〜7_6 (信用残の増減ランキング・期日到来銘柄) を取得する。
Stage1スクリーニング(SPECIFICATION.md 2節)の候補発掘に使う。
"""
from bs4 import BeautifulSoup

from .http_client import create_session
from .ranking_common import clean_numeric, is_regular_stock_code

WARNING_URL = "https://kabutan.jp/warning/"
REQUEST_TIMEOUT = 10

# 信用残増減ランキング(7_1〜7_4は共通の表構造)
MODE_SHORT_INCREASE = "7_1"  # 信用売り残の増加ランキング
MODE_BUY_INCREASE = "7_2"  # 信用買い残の増加ランキング
MODE_SHORT_DECREASE = "7_3"  # 信用売り残の減少ランキング(踏み上げ進行中の兆候)
MODE_BUY_DECREASE = "7_4"  # 信用買い残の減少ランキング(しこり玉解消の兆候)
# 期日到来銘柄(7_5/7_6は共通の別の表構造)
MODE_DUE_HIGH = "7_5"  # 信用【高値】期日到来銘柄
MODE_DUE_LOW = "7_6"  # 信用【安値】期日到来銘柄

_CHANGE_RANKING_MODES = {MODE_SHORT_INCREASE, MODE_BUY_INCREASE, MODE_SHORT_DECREASE, MODE_BUY_DECREASE}
_DUE_DATE_MODES = {MODE_DUE_HIGH, MODE_DUE_LOW}


def _parse_change_ranking_table(html: str) -> list[dict]:
    """mode=7_1/7_2/7_3/7_4共通の表構造を解析する。

    列: コード/銘柄名/市場/(アイコン2)/株価/(空)/前日比/前日比率/出来高/信用倍率/
        残高(売り残 or 買い残)/対前週増減幅
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="stock_table")
    if not table:
        return []
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

    records = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 13:
            continue
        code = cells[0].get_text(strip=True)
        market = cells[2].get_text(strip=True)
        if not is_regular_stock_code(code, market):
            continue
        records.append({
            "code": code,
            "name": cells[1].get_text(strip=True),
            "market": market,
            "price": clean_numeric(cells[5].get_text(strip=True)),
            "price_change": clean_numeric(cells[7].get_text(strip=True)),
            "price_change_pct": clean_numeric(cells[8].get_text(strip=True)),
            "volume": clean_numeric(cells[9].get_text(strip=True)),
            "margin_ratio": clean_numeric(cells[10].get_text(strip=True)),
            "margin_position": clean_numeric(cells[11].get_text(strip=True)),
            "margin_position_change": clean_numeric(cells[12].get_text(strip=True)),
        })
    return records


def _parse_due_date_table(html: str) -> list[dict]:
    """mode=7_5/7_6共通の表構造を解析する。

    列: コード/銘柄名/市場/(アイコン2)/株価/(空)/前日比率/売り残/買い残/信用倍率/
        52週高値(または安値)/その日付
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="stock_table")
    if not table:
        return []
    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

    records = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 13:
            continue
        code = cells[0].get_text(strip=True)
        market = cells[2].get_text(strip=True)
        if not is_regular_stock_code(code, market):
            continue
        records.append({
            "code": code,
            "name": cells[1].get_text(strip=True),
            "market": market,
            "price": clean_numeric(cells[5].get_text(strip=True)),
            "price_change_pct": clean_numeric(cells[7].get_text(strip=True)),
            "margin_sell": clean_numeric(cells[8].get_text(strip=True)),
            "margin_buy": clean_numeric(cells[9].get_text(strip=True)),
            "margin_ratio": clean_numeric(cells[10].get_text(strip=True)),
            "reference_price": clean_numeric(cells[11].get_text(strip=True)),
            "reference_date": cells[12].get_text(strip=True),
        })
    return records


def fetch_ranking(mode: str, session=None) -> list[dict]:
    """指定modeの信用残ランキングを取得する(MODE_*定数を指定)"""
    if mode not in _CHANGE_RANKING_MODES and mode not in _DUE_DATE_MODES:
        raise ValueError(f"未対応のmodeです: {mode}")

    session = session or create_session()
    resp = session.get(WARNING_URL, params={"mode": mode}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    if mode in _DUE_DATE_MODES:
        return _parse_due_date_table(resp.text)
    return _parse_change_ranking_table(resp.text)
