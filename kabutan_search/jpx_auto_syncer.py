"""JPX(日本取引所グループ)公式データの自動取得

kabutan.jp/warning/?mode=9_1 と同様、JPX公式サイトから空売り集計・信用取引残高・
投資部門別売買動向の最新ファイルを見つけてダウンロードする。

注意: ダウンロードしたExcel/PDFファイルの解析(DB取り込み)は `jpx_file_importer.py`
(未移植)に依存する。このモジュールは「最新ファイルを見つけてダウンロードする」ところまでを
担当し、ダウンロード済みファイルのパスを返す。解析ロジックが揃ったら sync_all() の戻り値の
ファイルパスをインポーターに渡す形で連結する想定。
"""
import os
import re
import urllib.parse
from pathlib import Path

import requests

DEFAULT_CACHE_DIR = Path("jpx_cache")

JPX_URLS = {
    "SHORT_SELLING": "https://www.jpx.co.jp/markets/statistics-equities/short-selling/index.html",
    "MARGIN_POSITIONS": "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html",
    "INVESTOR_TRENDS": "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html",
    "SHORT_POSITIONS": "https://www.jpx.co.jp/markets/public/short-selling/index.html",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 25


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _download_file(session: requests.Session, url: str, cache_dir: Path, filename: str | None = None) -> Path:
    if not filename:
        filename = os.path.basename(urllib.parse.urlparse(url).path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = cache_dir / filename
    resp = session.get(url, timeout=DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    local_path.write_bytes(resp.content)
    return local_path


def _find_links(session: requests.Session, page_url: str) -> list[str]:
    from bs4 import BeautifulSoup

    resp = session.get(page_url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return [urllib.parse.urljoin(page_url, a["href"]) for a in soup.find_all("a", href=True)]


def fetch_short_selling_files(cache_dir: Path = DEFAULT_CACHE_DIR, session: requests.Session | None = None) -> dict:
    """空売り集計の最新ファイル(全体-m.pdf、業種別-g.pdf)を見つけてダウンロードする"""
    session = session or _session()
    try:
        links = _find_links(session, JPX_URLS["SHORT_SELLING"])
    except requests.RequestException as e:
        return {"success": False, "error": f"空売り集計ページの取得に失敗しました: {e}"}

    overall_url = next((u for u in links if "-m.pdf" in u.lower()), None)
    sector_url = next((u for u in links if "-g.pdf" in u.lower()), None)

    downloaded = []
    try:
        if overall_url:
            downloaded.append(str(_download_file(session, overall_url, cache_dir)))
        if sector_url:
            downloaded.append(str(_download_file(session, sector_url, cache_dir)))
    except requests.RequestException as e:
        return {"success": False, "error": f"空売り集計ファイルのダウンロードに失敗しました: {e}"}

    if not downloaded:
        return {"success": False, "error": "空売り集計の最新ファイルリンクが見つかりませんでした。"}
    return {"success": True, "files": downloaded}


def fetch_margin_positions_file(cache_dir: Path = DEFAULT_CACHE_DIR, session: requests.Session | None = None) -> dict:
    """信用取引残高の最新ファイル(mtdailyk*.xls/.xlsx、無ければ.pdf)を見つけてダウンロードする"""
    session = session or _session()
    try:
        links = _find_links(session, JPX_URLS["MARGIN_POSITIONS"])
    except requests.RequestException as e:
        return {"success": False, "error": f"信用取引残高ページの取得に失敗しました: {e}"}

    margin_url = next((u for u in links if "mtdailyk" in u.lower() and u.lower().endswith((".xls", ".xlsx"))), None)
    if not margin_url:
        margin_url = next((u for u in links if "mtdailyk" in u.lower() and u.lower().endswith(".pdf")), None)

    if not margin_url:
        return {"success": False, "error": "信用取引残高の最新ファイルリンクが見つかりませんでした。"}

    try:
        path = _download_file(session, margin_url, cache_dir)
    except requests.RequestException as e:
        return {"success": False, "error": f"信用取引残高ファイルのダウンロードに失敗しました: {e}"}

    return {"success": True, "files": [str(path)]}


def fetch_investor_trends_file(cache_dir: Path = DEFAULT_CACHE_DIR, session: requests.Session | None = None) -> dict:
    """投資部門別売買状況の最新ファイル(stock_val_*.xls、無ければstock_vol_*.xls)を見つけてダウンロードする"""
    session = session or _session()
    try:
        links = _find_links(session, JPX_URLS["INVESTOR_TRENDS"])
    except requests.RequestException as e:
        return {"success": False, "error": f"投資部門別売買状況ページの取得に失敗しました: {e}"}

    trend_url = next((u for u in links if "stock_val" in u.lower() and u.lower().endswith((".xls", ".xlsx"))), None)
    if not trend_url:
        trend_url = next((u for u in links if "stock_vol" in u.lower() and u.lower().endswith((".xls", ".xlsx"))), None)

    if not trend_url:
        return {"success": False, "error": "投資部門別売買状況の最新ファイルリンクが見つかりませんでした。"}

    try:
        path = _download_file(session, trend_url, cache_dir)
    except requests.RequestException as e:
        return {"success": False, "error": f"投資部門別売買動向ファイルのダウンロードに失敗しました: {e}"}

    return {"success": True, "files": [str(path)]}


def fetch_short_positions_file(cache_dir: Path = DEFAULT_CACHE_DIR, session: requests.Session | None = None) -> dict:
    """空売り残高に関する情報(機関投資家の個別銘柄空売りポジション、0.5%以上)の最新ファイルを取得する。

    ダウンロードURLの一部(添付ID)は日付から予測できないため、一覧ページをスキャンして
    「YYYYMMDD_Short_Positions.xls」形式のリンクのうち最初のもの(最新日付)を採用する。
    """
    session = session or _session()
    try:
        links = _find_links(session, JPX_URLS["SHORT_POSITIONS"])
    except requests.RequestException as e:
        return {"success": False, "error": f"空売り残高情報ページの取得に失敗しました: {e}"}

    position_url = next((u for u in links if re.search(r"\d{8}_Short_Positions\.xlsx?$", u, re.IGNORECASE)), None)
    if not position_url:
        return {"success": False, "error": "空売り残高情報の最新ファイルリンクが見つかりませんでした。"}

    try:
        path = _download_file(session, position_url, cache_dir)
    except requests.RequestException as e:
        return {"success": False, "error": f"空売り残高情報ファイルのダウンロードに失敗しました: {e}"}

    return {"success": True, "files": [str(path)]}


def sync_all(cache_dir: Path = DEFAULT_CACHE_DIR) -> dict:
    """JPX公式サイトから4種類のデータファイルをすべてダウンロードする。

    ファイルの中身の解析・DB取り込みは行わない(jpx_file_importer.py待ち)。
    """
    session = _session()
    result = {
        "short_selling": fetch_short_selling_files(cache_dir, session),
        "margin_positions": fetch_margin_positions_file(cache_dir, session),
        "investor_trends": fetch_investor_trends_file(cache_dir, session),
        "short_positions": fetch_short_positions_file(cache_dir, session),
    }
    result["success"] = any(r["success"] for r in result.values())
    return result
