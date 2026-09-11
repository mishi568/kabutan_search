"""nikkei225jp.com からの市場全体マクロデータ取得

kabutan.jp/JPX公式データを補完する第三のデータ源。日経225指数ベースのPER/PBR、
騰落レシオ、信用残高、主体別売買動向、空売り比率、NT倍率などを提供する。
kabutan.jpには一切アクセスしない。

各ページは共通のテーブル構造(id="datatbl", ヘッダーは<th>, データ行は<td>で
1列目が<time>タグの日付)を持つため、テーブル抽出自体は共通化し、列ごとの
意味づけ(パース)だけをページ別に行う。
"""
import re
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

    ヘッダー行(<th>を含む行)は除外する(ページによってはヘッダーが複数箇所に
    重複しているが、すべて<th>を含むのでまとめて除外される)。1列目は<time>タグ内
    または素のテキストの日付文字列。セル内の改行・入れ子要素はスペース区切りで結合する。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="datatbl")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        if tr.find("th") is not None:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cells and cells[0]:
            rows.append(cells)
    return rows


_DATE_CELL_RE = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")


def _is_daily_date(text: str) -> bool:
    """"2026/09/04" のような日次日付セルか判定する("2026 年計" のような年間集計行を除外する)。"""
    return bool(_DATE_CELL_RE.match(text))


def _normalize_date(text: str) -> str:
    """"2026/09/04" のようなスラッシュ区切りの日付をISO形式("2026-09-04")に正規化する。"""
    return text.replace("/", "-")


def _parse_weekly_change_pct(text: str) -> float | None:
    """"▼ 2.09 %" のような週次変化率セルを符号付きの数値に変換する(▼=マイナス、−=ゼロ)。"""
    m = re.search(r"([\d,]+\.?\d*)", text)
    if not m:
        return None
    value = clean_numeric(m.group(1))
    if value is None:
        return None
    return -value if "▼" in text else value


def parse_per(html: str) -> list[dict]:
    """per.php(日本株225 PER PBR)を解析する。

    列: 日付/日本株225/変化/プライム出来高/PER/PBR/EPS/BPS/益回り/配当利回り/日本国債利回り
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 11 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
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


def _oku(value: float | None) -> float | None:
    """百万円単位の値を億円単位に変換する(shutai.phpのデフォルト表示単位が百万円のため)。"""
    return None if value is None else value / 100.0


def parse_shutai(html: str) -> list[dict]:
    """shutai.php(投資主体別売買状況、週次)を解析する。単位は百万円→億円に変換して保存する。

    列: 日付/日本225/変化(週)/海外/証券自己/個人計/個人(現金)/個人(信用)/投資信託/
        事業法人/その他法人/信託銀行/生保損保/都銀地銀
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 14 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1]),
            "price_change_pct": _parse_weekly_change_pct(cells[2]),
            "foreign_net": _oku(clean_numeric(cells[3])),
            "dealer_net": _oku(clean_numeric(cells[4])),
            "individual_net": _oku(clean_numeric(cells[5])),
            "individual_cash_net": _oku(clean_numeric(cells[6])),
            "individual_margin_net": _oku(clean_numeric(cells[7])),
            "investment_trust_net": _oku(clean_numeric(cells[8])),
            "business_corp_net": _oku(clean_numeric(cells[9])),
            "other_corp_net": _oku(clean_numeric(cells[10])),
            "trust_bank_net": _oku(clean_numeric(cells[11])),
            "insurance_net": _oku(clean_numeric(cells[12])),
            "bank_net": _oku(clean_numeric(cells[13])),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_shutai(nikkei_db: NikkeiDatabase, session: requests.Session | None = None) -> dict:
    try:
        html = fetch_page("SHUTAI", session)
    except requests.RequestException as e:
        return {"success": False, "error": f"shutai.php の取得に失敗しました: {e}"}

    records = parse_shutai(html)
    if not records:
        return {"success": False, "error": "shutai.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_investor_trends(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【投資主体別売買状況(nikkei225jp.com)】{records[0]['date']} 週時点までの{len(records)}件を取り込みました。",
    }


def parse_sinyou(html: str) -> list[dict]:
    """sinyou.php(評価損益率 信用残日本市況)を解析する。

    列: 日付/売り残枚数(千株)/売り残金額(億円)/売り残変化(%)/買い残枚数(千株)/
        買い残金額(億円)/買い残変化(%)/信用倍率/信用評価率
    金額(億円)セルは整数部と小数部の間にスペースが入る表示崩れがあるため除去してから解析する。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 9 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "margin_sell_shares": clean_numeric(cells[1]),
            "margin_sell": clean_numeric(cells[2].replace(" ", "")),
            "margin_sell_change_pct": clean_numeric(cells[3]),
            "margin_buy_shares": clean_numeric(cells[4]),
            "margin_buy": clean_numeric(cells[5].replace(" ", "")),
            "margin_buy_change_pct": clean_numeric(cells[6]),
            "margin_ratio": clean_numeric(cells[7]),
            "profit_loss_ratio": clean_numeric(cells[8]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_sinyou(nikkei_db: NikkeiDatabase, session: requests.Session | None = None) -> dict:
    try:
        html = fetch_page("SINYOU", session)
    except requests.RequestException as e:
        return {"success": False, "error": f"sinyou.php の取得に失敗しました: {e}"}

    records = parse_sinyou(html)
    if not records:
        return {"success": False, "error": "sinyou.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei_margin_record(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【信用残高(日本市況)】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def parse_karauri(html: str) -> list[dict]:
    """karauri.php(空売り比率90営業日日本市況)を解析する。

    列: 日付/日本225/変化/プライム売買代金(億円)/プライム出来高(百万株)/
        空売り比率合計/空売り比率(価格規制あり)/空売り比率(価格規制なし)
    価格・変化・売買代金セルは整数部と小数部の間にスペースが入る表示崩れがあるため
    除去してから解析する。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 8 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1].replace(" ", "")),
            "price_change": clean_numeric(cells[2].replace(" ", "")),
            "prime_trading_value": clean_numeric(cells[3].replace(" ", "")),
            "prime_volume": clean_numeric(cells[4]),
            "short_ratio_total": clean_numeric(cells[5]),
            "short_ratio_regulated": clean_numeric(cells[6]),
            "short_ratio_non_regulated": clean_numeric(cells[7]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_karauri(nikkei_db: NikkeiDatabase, session: requests.Session | None = None) -> dict:
    try:
        html = fetch_page("KARAURI", session)
    except requests.RequestException as e:
        return {"success": False, "error": f"karauri.php の取得に失敗しました: {e}"}

    records = parse_karauri(html)
    if not records:
        return {"success": False, "error": "karauri.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_short_selling(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【空売り比率(nikkei225jp.com)】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


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
    """nikkei225jp.comの全ページを同期する。touraku/ntは未実装。"""
    session = session or _session()
    return {
        "per": sync_per(nikkei_db, session),
        "shutai": sync_shutai(nikkei_db, session),
        "sinyou": sync_sinyou(nikkei_db, session),
        "karauri": sync_karauri(nikkei_db, session),
    }
