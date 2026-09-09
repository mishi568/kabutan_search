"""株探(kabutan.jp)個別銘柄ページの取得オーケストレーション

parser.pyの解析ロジックと組み合わせて、実際にページを取得してDBへ保存する。
アクセス制限ページを検知した場合は RateLimitedError を送出する(SPECIFICATION.md 12節)。
"""
import random
import time
from datetime import datetime

from . import parser
from .database import Database
from .http_client import create_session

REQUEST_TIMEOUT = 15

# 複数銘柄取得時の配慮設定(SPECIFICATION.md 8節: アクセス制限回避)
DEFAULT_MIN_DELAY = 3.0
DEFAULT_MAX_DELAY = 8.0
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_PAUSE_RANGE = (30.0, 60.0)
DEFAULT_DAILY_LIMIT = 1000


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


def bulk_fetch(
    db: Database,
    codes: list[str],
    min_delay: float = DEFAULT_MIN_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_pause_range: tuple[float, float] = DEFAULT_BATCH_PAUSE_RANGE,
    daily_limit: int = DEFAULT_DAILY_LIMIT,
    session=None,
    on_progress=None,
) -> dict:
    """複数銘柄を、株探への配慮(リクエスト間ランダムディレイ・お茶休憩・日次上限)をしながら
    順に取得する。

    アクセス制限ページを検知した時点で即座に中断する(全銘柄を巡回する前に止める)。
    on_progress(code, data, index, total) が指定されていれば1件成功するごとに呼ばれる。
    """
    session = session or create_session()
    result = {"succeeded": [], "failed": [], "stopped_early": False}

    codes = codes[:daily_limit]
    total = len(codes)

    for i, code in enumerate(codes):
        try:
            data = fetch_stock(db, code, session=session)
            result["succeeded"].append(code)
            if on_progress:
                on_progress(code, data, i + 1, total)
        except RateLimitedError as e:
            result["stopped_early"] = True
            result["error"] = str(e)
            break
        except ValueError as e:
            result["failed"].append({"code": code, "error": str(e)})

        if i + 1 < total:
            if (i + 1) % batch_size == 0:
                time.sleep(random.uniform(*batch_pause_range))  # お茶休憩
            else:
                time.sleep(random.uniform(min_delay, max_delay))

    return result
