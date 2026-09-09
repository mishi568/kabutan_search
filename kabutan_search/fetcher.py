"""株探(kabutan.jp)個別銘柄ページの取得オーケストレーション

parser.pyの解析ロジックと組み合わせて、実際にページを取得してDBへ保存する。
アクセス制限ページを検知した場合は RateLimitedError を送出する(SPECIFICATION.md 12節)。
"""
from datetime import datetime

from . import parser
from .database import Database
from .http_client import create_session

REQUEST_TIMEOUT = 15


class RateLimitedError(Exception):
    """株探のアクセス制限ページを検知した"""


def fetch_stock(db: Database, code: str, session=None) -> dict:
    """個別銘柄のトップページ+決算ページを取得・解析し、DBへ保存して結果を返す"""
    session = session or create_session()

    top_url = f"https://kabutan.jp/stock/?code={code}"
    top_resp = session.get(top_url, timeout=REQUEST_TIMEOUT)
    if top_resp.status_code in (403, 429):
        raise RateLimitedError(
            f"株探への接続が拒否されました(HTTP {top_resp.status_code})。Bot対策の可能性があります。"
            "しばらく時間をおいて再試行してください。"
        )
    top_resp.raise_for_status()

    if parser.is_rate_limited(top_resp.text):
        raise RateLimitedError("株探のアクセス制限ページを検知しました。しばらく待ってから再試行してください。")

    data = parser.parse_top_page(top_resp.text)
    if data is None:
        raise ValueError(f"{code}: 有効な銘柄ページが見つかりませんでした。")
    if data.get("error") == "rate_limit_blocked":
        raise RateLimitedError("株探のアクセス制限ページを検知しました。しばらく待ってから再試行してください。")

    finance_resp = session.get(f"https://kabutan.jp/stock/finance?code={code}", timeout=REQUEST_TIMEOUT)
    if finance_resp.ok and not parser.is_rate_limited(finance_resp.text):
        data = parser.parse_finance_details(finance_resp.text, data)

    now = datetime.now()
    data["date"] = now.strftime("%Y-%m-%d")
    data["url"] = f"https://kabutan.jp/stock/?code={code}"
    data["timestamp"] = now.strftime("%Y-%m-%d %H:%M:%S")

    db.upsert_stock_record(data)
    return data
