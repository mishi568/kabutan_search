"""33業種セクターランキングの取得

kabutan.jp/warning/?mode=9_1 (ページ1〜3) から日次の業種別データを取得しDBへ保存する。
Stage1スクリーニング(SPECIFICATION.md 2節)の主要データソース。
"""
import re
from datetime import datetime

from bs4 import BeautifulSoup

from .database import Database
from .http_client import create_session

SECTOR_RANKING_URL = "https://kabutan.jp/warning/?mode=9_1"
REQUEST_TIMEOUT = 10


def _clean_float(val_str: str):
    if not val_str:
        return None
    cleaned = val_str.replace(",", "").replace("%", "").replace("+", "").strip()
    if cleaned in ["－", "-", "", "N/A"]:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_int(val_str: str):
    if not val_str:
        return None
    cleaned = val_str.replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return None


def _extract_date(soup: BeautifulSoup) -> str | None:
    for elem in soup.find_all(["div", "span", "p", "h2", "h3"]):
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", elem.get_text())
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def _parse_ranking_page(html: str) -> tuple[list[dict], str | None]:
    """1ページ分のHTMLから業種レコードのリストを抽出する(date/timestampは未設定)"""
    soup = BeautifulSoup(html, "html.parser")
    detected_date = _extract_date(soup)

    table = soup.find("table", class_="stock_table")
    if not table:
        return [], detected_date

    records = []
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["th", "td"])
        if len(cells) < 10:
            continue
        # col 0: セクターコード, 1: 業種名, 2: 銘柄数, 4: 価格指数,
        # 6: 前日比, 7: 騰落率, 8: PER, 9: PBR, 10: 利回り
        records.append({
            "sector_code": cells[0].get_text(strip=True),
            "sector_name": cells[1].get_text(strip=True),
            "stock_count": _clean_int(cells[2].get_text(strip=True)),
            "price": _clean_float(cells[4].get_text(strip=True)),
            "price_change": _clean_float(cells[6].get_text(strip=True)),
            "change_pct": _clean_float(cells[7].get_text(strip=True)),
            "per": _clean_float(cells[8].get_text(strip=True)) if len(cells) > 8 else None,
            "pbr": _clean_float(cells[9].get_text(strip=True)) if len(cells) > 9 else None,
            "yield_val": _clean_float(cells[10].get_text(strip=True)) if len(cells) > 10 else None,
        })
    return records, detected_date


def fetch_sector_ranking(
    db: Database,
    custom_date: str | None = None,
    pages: tuple[int, ...] = (1, 2, 3),
    session=None,
) -> list[dict]:
    """kabutan.jp/warning/?mode=9_1 の全ページを取得し、DBに保存して返す"""
    session = session or create_session()

    all_records: list[dict] = []
    detected_date = custom_date
    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for page in pages:
        resp = session.get(SECTOR_RANKING_URL, params={"page": page}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        records, page_date = _parse_ranking_page(resp.text)
        if page_date and not detected_date:
            detected_date = page_date
        all_records.extend(records)

    date_str = detected_date or datetime.now().strftime("%Y-%m-%d")
    for record in all_records:
        record["date"] = date_str
        record["timestamp"] = now_ts
        db.upsert_sector_daily_record(record)

    return all_records
