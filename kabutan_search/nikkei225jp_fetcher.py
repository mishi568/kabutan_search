"""nikkei225jp.com からの市場全体マクロデータ取得

kabutan.jp/JPX公式データを補完する第三のデータ源。日経225指数ベースのPER/PBR、
騰落レシオ、信用残高、主体別売買動向、空売り比率、NT倍率などを提供する。
kabutan.jpには一切アクセスしない。

各ページは共通のテーブル構造(id="datatbl", ヘッダーは<th>, データ行は<td>で
1列目が<time>タグの日付)を持つため、テーブル抽出自体は共通化し、列ごとの
意味づけ(パース)だけをページ別に行う。
"""
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from .nikkei_database import NikkeiDatabase
from .ranking_common import clean_numeric

BASE_URL = "https://nikkei225jp.com/data/"

PAGE_URLS = {
    "PER": BASE_URL + "per.php",
    "SHUTAI": BASE_URL + "shutai.php",
    "SINYOU": BASE_URL + "sinyou.php",
    "KARAURI": BASE_URL + "karauri.php",
    "TOURAKU": BASE_URL + "touraku.php",
    "NT": BASE_URL + "nt.php",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def fetch_page(key: str, session: requests.Session | None = None) -> str:
    """指定ページのHTMLを取得する。keyはPAGE_URLSのキー。"""
    session = session or _session()
    resp = session.get(PAGE_URLS[key], timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def _parse_datatbl_rows(html: str) -> list[list[str]]:
    """id="datatbl" テーブルのデータ行を、セルのテキストのリストとして返す(新しい日付順)。

    ヘッダー行(<th>を含む行)は除外する。1列目は<time>タグ内の日付文字列。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="datatbl")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        if tr.find("th") is not None:
            continue
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells and cells[0]:
            rows.append(cells)
    return rows


def parse_per(html: str) -> list[dict]:
    """per.php(日本株225 PER PBR)を解析する。

    列: 日付/日本株225/変化/プライム出来高/PER/PBR/EPS/BPS/益回り/配当利回り/日本国債利回り
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 11:
            continue
        records.append({
            "date": cells[0],
            "price": clean_numeric(cells[1]),
            "per": clean_numeric(cells[4]),
            "pbr": clean_numeric(cells[5]),
            "eps": clean_numeric(cells[6]),
            "bps": clean_numeric(cells[7]),
            "earnings_yield": clean_numeric(cells[8]),
            "dividend_yield": clean_numeric(cells[9]),
            "jgb_yield": clean_numeric(cells[10]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_per(nikkei_db: NikkeiDatabase, session: requests.Session | None = None) -> dict:
    try:
        html = fetch_page("PER", session)
    except requests.RequestException as e:
        return {"success": False, "error": f"per.php の取得に失敗しました: {e}"}

    records = parse_per(html)
    if not records:
        return {"success": False, "error": "per.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei_per_record(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【日本225 PER/PBR】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def sync_all(nikkei_db: NikkeiDatabase, session: requests.Session | None = None) -> dict:
    """nikkei225jp.comの全ページを同期する。現時点ではper.phpのみ実装済み。"""
    session = session or _session()
    return {"per": sync_per(nikkei_db, session)}
