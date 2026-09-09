"""お気に入り(watchlist)・保有ポジションの管理ロジック

旧 tracker_manager.py からPySide6 GUI(テーブル描画・右クリックメニュー等)を除いた
実データロジックのみを抽出したもの。CLIの `kabutan watch` / ポジション管理コマンドから使う。
"""
import re
from datetime import datetime

from .database import Database

_CODE_RE = re.compile(r"^\d{4}$")


def _validate_code(code: str) -> str:
    code = code.strip()
    if not _CODE_RE.match(code):
        raise ValueError("銘柄コードは4桁の数字で指定してください")
    return code


def add_to_watchlist(db: Database, code: str, name: str | None = None, price: float | None = None) -> None:
    """お気に入りに追加する。name/priceが未指定ならDB内の最新レコードから補完する"""
    code = _validate_code(code)
    latest = db.get_latest_record(code)
    if name is None:
        name = latest["name"] if latest else code
    if price is None:
        price = latest["price"] if latest else 0.0
    db.add_tracked_stock(code, name, datetime.now().strftime("%Y-%m-%d"), price)


def remove_from_watchlist(db: Database, code: str) -> None:
    db.remove_tracked_stock(_validate_code(code))


def list_watchlist_with_returns(db: Database) -> list[dict]:
    """お気に入り銘柄を、追跡開始時からの騰落率(降順)でソートして返す。

    各要素には squeeze_score / squeeze_stars / progress_rate 等の最新指標も付与する。
    """
    enriched = []
    for item in db.list_tracked_stocks():
        item = dict(item)
        latest = db.get_latest_record(item["code"])

        tracked_price = item["tracked_price"]
        latest_price = (latest["price"] if latest else None) or item["latest_price"] or tracked_price
        item["latest_price"] = latest_price

        if tracked_price and tracked_price > 0 and latest_price is not None:
            item["diff_pct"] = ((latest_price - tracked_price) / tracked_price) * 100.0
        else:
            item["diff_pct"] = 0.0

        item["progress_rate"] = latest["progress_rate"] if latest else None
        item["progress_quarter"] = latest["progress_quarter"] if latest else None
        item["progress_excess"] = latest["progress_excess"] if latest else None

        score, stars = db.get_squeeze_score(item["code"])
        item["squeeze_score"] = score
        item["squeeze_stars"] = stars

        enriched.append(item)

    enriched.sort(key=lambda x: x["diff_pct"], reverse=True)
    return enriched


def qualifies_for_auto_tracking(db: Database, code: str) -> bool:
    """クオンツ評価が高い銘柄かどうかを判定する(自動お気に入り登録の判定基準)。

    条件: 決算進捗超過率(progress_excess)がプラス、またはスクイーズスコアが3以上。
    """
    latest = db.get_latest_record(code)
    if not latest or not latest["price"] or latest["price"] <= 0:
        return False

    progress_excess = latest["progress_excess"]
    has_high_progress = progress_excess is not None and progress_excess > 0

    squeeze_score, _ = db.get_squeeze_score(code)
    has_high_squeeze = squeeze_score >= 3

    return has_high_progress or has_high_squeeze


def add_or_update_position(db: Database, code: str, purchase_price: float, shares: int) -> dict:
    """ポジションを登録・更新する。銘柄名はDB内の最新レコードから自動取得する。"""
    code = _validate_code(code)

    if purchase_price <= 0:
        raise ValueError("購入単価は正の数値を指定してください")
    if shares <= 0:
        raise ValueError("株数は正の整数を指定してください")

    latest = db.get_latest_record(code)
    name = latest["name"] if latest else code

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.add_position(code, name, purchase_price, shares, timestamp)
    return {"code": code, "name": name, "purchase_price": purchase_price, "shares": shares}


def remove_position(db: Database, code: str) -> None:
    db.remove_position(_validate_code(code))


def list_positions_with_pnl(db: Database) -> list[dict]:
    """保有ポジションを最新株価・損益・損益率つきで返す"""
    results = []
    for pos in db.list_positions():
        pos = dict(pos)
        latest = db.get_latest_record(pos["code"])
        latest_price = latest["price"] if latest else None
        pos["latest_price"] = latest_price

        if latest_price is not None:
            pos["profit"] = (latest_price - pos["purchase_price"]) * pos["shares"]
            pos["profit_pct"] = ((latest_price - pos["purchase_price"]) / pos["purchase_price"]) * 100.0
        else:
            pos["profit"] = None
            pos["profit_pct"] = None

        results.append(pos)
    return results
