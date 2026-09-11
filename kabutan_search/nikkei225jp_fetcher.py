"""nikkei225jp.com からの市場全体マクロデータ取得

kabutan.jp/JPX公式データを補完する第三のデータ源。日経225指数ベースのPER/PBR、
騰落レシオ、信用残高、主体別売買動向、空売り比率、NT倍率などを提供する。
kabutan.jpには一切アクセスしない。

各ページは共通のテーブル構造(id="datatbl"または"sumTBL"等、ヘッダーは<th>、
データ行は<td>で1列目が日付)を持つため、テーブル抽出自体は共通化し、列ごとの
意味づけ(パース)だけをページ別に行う。

【重要】このサイトのテーブルはJavaScriptが実行された後にDOMへ書き込まれる
(document.write()や外部データファイルの読み込み結果を元にJSが描画する)ため、
素のHTTP GET(requests)では空のテーブルしか取得できない。実際にブラウザで
ページを開いて保存したHTMLでは値が入っているが、requestsでの取得では
「データ行を抽出できませんでした」という失敗になることを運用環境での実行で確認した。
そのためPlaywright(ヘッドレスブラウザ)でJSを実行させてから取得する。
"""
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

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
    "SAITEI": BASE_URL + "saitei.php",
    "FUTURES": BASE_URL + "futures.php",
    "VIX": BASE_URL + "vix.php",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
PAGE_LOAD_TIMEOUT_MS = 30000


class FetchError(Exception):
    """ページ取得(ブラウザでの読み込み)に失敗したことを表す。"""


class Nikkei225jpSession:
    """nikkei225jp.comのJS描画済みページをPlaywrightで取得するセッション。

    ブラウザの起動はコストが高いため、sync_all()での一連の取得中は
    同じインスタンス(同じブラウザ)を使い回す想定。使い終わったらclose()すること。
    """

    def __init__(self) -> None:
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch()
            self._page = self._browser.new_page(user_agent=USER_AGENT)
        except Exception:
            self._playwright.stop()
            raise

    def get_html(self, url: str) -> str:
        try:
            response = self._page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        except PlaywrightError as e:
            raise FetchError(f"ページの読み込みに失敗しました: {e}") from e
        if response is not None and not response.ok:
            raise FetchError(f"HTTPエラー: status={response.status}")
        # document.write等の同期的なDOM書き込みが確実に終わるよう少し待つ
        self._page.wait_for_timeout(500)
        return self._page.content()

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()


def _session() -> Nikkei225jpSession:
    return Nikkei225jpSession()


def fetch_page(key: str, session: Nikkei225jpSession | None = None) -> str:
    """指定ページのJS描画済みHTMLを取得する。keyはPAGE_URLSのキー。"""
    own_session = session is None
    sess = session or _session()
    try:
        return sess.get_html(PAGE_URLS[key])
    finally:
        if own_session:
            sess.close()


def _parse_datatbl_rows(html: str, table_id: str = "datatbl") -> list[list[str]]:
    """指定id(既定"datatbl")のテーブルのデータ行を、セルのテキストのリストとして返す(新しい日付順)。

    ヘッダー行(<th>を含む行)は除外する(ページによってはヘッダーが複数箇所に
    重複しているが、すべて<th>を含むのでまとめて除外される)。1列目は<time>タグ内
    または素のテキストの日付文字列。セル内の改行・入れ子要素はスペース区切りで結合する。
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id=table_id)
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


def sync_shutai(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("SHUTAI", session)
    except FetchError as e:
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


def sync_sinyou(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("SINYOU", session)
    except FetchError as e:
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


def sync_karauri(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("KARAURI", session)
    except FetchError as e:
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


def parse_touraku(html: str) -> list[dict]:
    """touraku.php(騰落レシオ90営業日日本市況)を解析する。

    列: 日付/日本225/変化/プライム出来高(百万株)/値上がり銘柄数/値下がり銘柄数/
        騰落レシオ(25日)/騰落レシオ(15日)/騰落レシオ(10日)/騰落レシオ(6日)
    末尾に「更新は15時半過ぎに速報...」のような注記行が含まれるため、日付形式で
    厳密に判定して除外する。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 10 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1]),
            "price_change": clean_numeric(cells[2]),
            "prime_volume": clean_numeric(cells[3]),
            "advancing_count": clean_numeric(cells[4]),
            "declining_count": clean_numeric(cells[5]),
            "touraku_25d": clean_numeric(cells[6]),
            "touraku_15d": clean_numeric(cells[7]),
            "touraku_10d": clean_numeric(cells[8]),
            "touraku_6d": clean_numeric(cells[9]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_touraku(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("TOURAKU", session)
    except FetchError as e:
        return {"success": False, "error": f"touraku.php の取得に失敗しました: {e}"}

    records = parse_touraku(html)
    if not records:
        return {"success": False, "error": "touraku.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei_touraku_record(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【騰落レシオ】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def parse_nt(html: str) -> list[dict]:
    """nt.php(NT倍率 日本225・TOPIX・JPX400・ドル円)を解析する。

    列: 日付/NT倍率/NJ倍率/JT倍率/日本225/日経%/TOPIX/TPX%/JPX400/JPX%/為替ドル円
    NT倍率=日経平均/TOPIX、NJ倍率=日経平均/JPX400、JT倍率=JPX400/TOPIX。
    為替ドル円は当日終値未確定時に空欄になることがある。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 11 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "nt_ratio": clean_numeric(cells[1]),
            "nj_ratio": clean_numeric(cells[2]),
            "jt_ratio": clean_numeric(cells[3]),
            "nikkei_price": clean_numeric(cells[4]),
            "nikkei_change_pct": _parse_weekly_change_pct(cells[5]),
            "topix_price": clean_numeric(cells[6]),
            "topix_change_pct": _parse_weekly_change_pct(cells[7]),
            "jpx400_price": clean_numeric(cells[8]),
            "jpx400_change_pct": _parse_weekly_change_pct(cells[9]),
            "usdjpy": clean_numeric(cells[10]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_nt(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("NT", session)
    except FetchError as e:
        return {"success": False, "error": f"nt.php の取得に失敗しました: {e}"}

    records = parse_nt(html)
    if not records:
        return {"success": False, "error": "nt.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_nt_ratio(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【NT倍率】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def parse_saitei(html: str) -> list[dict]:
    """saitei.php(裁定買い残／裁定売り残)の日次(株数ベース)テーブルを解析する。

    列: 日付/日本225/変化/プライム売買代金(億円)/買い残(千株)/売り残(千株)/差引(千株)/差引前比(千株)
    ページには週次(金額・億円)ビューもあるが、表は外部JSデータファイル
    (`DAILY`変数、別途読み込まれるscriptファイル)を元にJavaScriptが描画しており、
    静的HTMLの保存内容には切り替え時点で表示されていた側のみが焼き付く。
    日次・株数ベースのみ対応する。
    売買代金セルは整数部と小数部の間にスペースが入る表示崩れがあるため除去してから解析する。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 8 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1]),
            "price_change": clean_numeric(cells[2]),
            "prime_trading_value": clean_numeric(cells[3].replace(" ", "")),
            "buy_shares": clean_numeric(cells[4]),
            "sell_shares": clean_numeric(cells[5]),
            "net_shares": clean_numeric(cells[6]),
            "net_change": clean_numeric(cells[7]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_saitei(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("SAITEI", session)
    except FetchError as e:
        return {"success": False, "error": f"saitei.php の取得に失敗しました: {e}"}

    records = parse_saitei(html)
    if not records:
        return {"success": False, "error": "saitei.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_arbitrage(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【裁定買い残/売り残】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def parse_futures(html: str) -> list[dict]:
    """futures.php(週次建玉数手口)の「週次サマリー」テーブル(id="sumTBL")を解析する。

    列: 日付/日本225/変化/(外資系証券: 買建/売建/ネット/前週比)/
        (国内系証券: 買建/売建/ネット/前週比)/(個人系ネット証券: 買建/売建/ネット/前週比)
    外資系証券のネット建玉は海外機関投資家の先物ポジション方向を示す代表的な指標。
    ページには個別証券会社別の建玉(最新週のみ)・価格帯別バイアスのテーブルもあるが、
    時系列データではないため未対応(週次サマリーのみ実装)。
    """
    records = []
    for cells in _parse_datatbl_rows(html, table_id="sumTBL"):
        if len(cells) < 15 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1]),
            "price_change": clean_numeric(cells[2]),
            "foreign_buy": clean_numeric(cells[3]),
            "foreign_sell": clean_numeric(cells[4]),
            "foreign_net": clean_numeric(cells[5]),
            "foreign_net_change": clean_numeric(cells[6]),
            "domestic_buy": clean_numeric(cells[7]),
            "domestic_sell": clean_numeric(cells[8]),
            "domestic_net": clean_numeric(cells[9]),
            "domestic_net_change": clean_numeric(cells[10]),
            "retail_buy": clean_numeric(cells[11]),
            "retail_sell": clean_numeric(cells[12]),
            "retail_net": clean_numeric(cells[13]),
            "retail_net_change": clean_numeric(cells[14]),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_futures(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("FUTURES", session)
    except FetchError as e:
        return {"success": False, "error": f"futures.php の取得に失敗しました: {e}"}

    records = parse_futures(html)
    if not records:
        return {"success": False, "error": "futures.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_futures_broker(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【週次建玉数手口】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def parse_vix(html: str) -> list[dict]:
    """vix.php(恐怖指数、日本VI・VSTOXX・VIX)を解析する。

    列: 日付/日本225/変化/プライム出来高(百万株)/日本VI(日経恐怖指数)/
        VSTOXX(欧州恐怖指数)/VIX(米国恐怖指数)
    日本市場休場日は価格・日本VIが「-」になるが、海外指数(VSTOXX/VIX)は
    値が入ることがある。整数部・小数部間のスペース崩れを除去してから解析する。
    """
    records = []
    for cells in _parse_datatbl_rows(html):
        if len(cells) < 7 or not _is_daily_date(cells[0]):
            continue
        records.append({
            "date": _normalize_date(cells[0]),
            "price": clean_numeric(cells[1].replace(" ", "")),
            "price_change": clean_numeric(cells[2].replace(" ", "")),
            "prime_volume": clean_numeric(cells[3]),
            "japan_vi": clean_numeric(cells[4].replace(" ", "")),
            "vstoxx": clean_numeric(cells[5].replace(" ", "")),
            "vix": clean_numeric(cells[6].replace(" ", "")),
            "timestamp": datetime.now().isoformat(),
        })
    return records


def sync_vix(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("VIX", session)
    except FetchError as e:
        return {"success": False, "error": f"vix.php の取得に失敗しました: {e}"}

    records = parse_vix(html)
    if not records:
        return {"success": False, "error": "vix.php からデータ行を抽出できませんでした。"}

    for rec in records:
        nikkei_db.upsert_nikkei225jp_fear_index(rec)

    return {
        "success": True,
        "rows_saved": len(records),
        "message": f"【恐怖指数】{records[0]['date']} 時点までの{len(records)}件を取り込みました。",
    }


def sync_per(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    try:
        html = fetch_page("PER", session)
    except FetchError as e:
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


def sync_all(nikkei_db: NikkeiDatabase, session: Nikkei225jpSession | None = None) -> dict:
    """nikkei225jp.comの全9ページ(per/shutai/sinyou/karauri/touraku/nt/saitei/futures/vix)を同期する。

    ブラウザの起動は重いため、9ページすべてで同じセッション(同じブラウザ)を使い回す。
    """
    own_session = session is None
    session = session or _session()
    try:
        return {
            "per": sync_per(nikkei_db, session),
            "shutai": sync_shutai(nikkei_db, session),
            "sinyou": sync_sinyou(nikkei_db, session),
            "karauri": sync_karauri(nikkei_db, session),
            "touraku": sync_touraku(nikkei_db, session),
            "nt": sync_nt(nikkei_db, session),
            "saitei": sync_saitei(nikkei_db, session),
            "futures": sync_futures(nikkei_db, session),
            "vix": sync_vix(nikkei_db, session),
        }
    finally:
        if own_session:
            session.close()
