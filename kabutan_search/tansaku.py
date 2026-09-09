"""株探「銘柄探検」ランキングページの取得

Stage1スクリーニング(SPECIFICATION.md 2節)の候補発掘に使う、軽量なランキングデータの
取得元。kabutan.jp/tansaku/ 配下の各種ランキングページに対応する。
"""
from bs4 import BeautifulSoup

from .http_client import create_session
from .ranking_common import clean_numeric, is_regular_stock_code

TANSAKU_URL = "https://kabutan.jp/tansaku/"
VOLUME_SURGE_MODE = "2_0311"
REQUEST_TIMEOUT = 10


def _parse_ranking_table(html: str) -> list[dict]:
    """stock_table形式のランキング表を解析する(出来高急増ランキング等で共通のレイアウト)。

    列: コード / 銘柄名 / 市場 / (アイコン2列) / 株価 / (空列) / 前日比 / 出来高 /
        出来高前日比率 / PER / PBR / 利回り
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
            continue  # ETF/ETN/REIT等は除外(投資信託類が出来高急増で頻出するため)
        records.append({
            "code": code,
            "name": cells[1].get_text(strip=True),
            "market": market,
            "price": clean_numeric(cells[5].get_text(strip=True)),
            "price_change": clean_numeric(cells[7].get_text(strip=True)),
            "volume": clean_numeric(cells[8].get_text(strip=True)),
            "volume_change_pct": clean_numeric(cells[9].get_text(strip=True)),
            "per": clean_numeric(cells[10].get_text(strip=True)),
            "pbr": clean_numeric(cells[11].get_text(strip=True)),
            "yield_val": clean_numeric(cells[12].get_text(strip=True)),
        })
    return records


def fetch_volume_surge(pages: tuple[int, ...] = (1,), session=None) -> list[dict]:
    """出来高急増銘柄ランキング(kabutan.jp/tansaku/?mode=2_0311)を取得する。

    株探側で既に「出来高が前日比200%以上増加 + 流動性条件」を満たす銘柄に絞られているため、
    軽量な玉集め検知データソースとして使える。デフォルトは1ページ(15件)のみ取得。
    """
    session = session or create_session()
    all_records: list[dict] = []
    for page in pages:
        params = {"mode": VOLUME_SURGE_MODE}
        if page > 1:
            params.update({"market": 0, "capitalization": -1, "dispmode": "normal", "page": page})
        resp = session.get(TANSAKU_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        all_records.extend(_parse_ranking_table(resp.text))
    return all_records
