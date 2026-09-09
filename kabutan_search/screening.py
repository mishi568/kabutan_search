"""Stage1: 値上がり優位性スクリーニング

詳細データ(信用残・決算等)を取得する前に、軽量なデータだけで値上がり優位性のある銘柄を
絞り込む。SPECIFICATION.md 2節(2段階データ収集フロー)に対応する。

現状の実装は「セクターランキング(sector_data_manager、既に軽量取得済み)」と
「既知の直近株価データ(過去に取得済みのstock_records)」のみを使った判定。
個別銘柄の値上がり率ランキングページのスクレイピングは未実装
(SPECIFICATION.md 8節の未確定事項: 対象ページのURLが未確定のため)。
そのページが決まり次第、_screen_by_ranking_page() のようなソースを追加して
候補選定の精度を上げられる。
"""
from datetime import datetime

from . import sector_data_manager
from .database import Database
from .sector_divergence_analyzer import calculate_sector_divergence

# 踏み上げ初動(1)・モメンタム急騰(2)のセクターを「勢いのあるセクター」とみなす
HOT_SECTOR_SIGNAL_RANKS = (1, 2)
# セクターが好調な銘柄について、これ以上の当日騰落率(%)であれば候補とする
MIN_PRICE_CHANGE_PCT = 3.0


def screen(db: Database, fetch_sectors: bool = True) -> list[dict]:
    """当日の値上がり優位性候補を抽出し、candidate_stocksテーブルへ保存して返す。

    候補となる条件(いずれか):
      1. お気に入り・保有ポジション銘柄 (常時Stage2監視対象。値上がり判定は不問)
      2. 直近の株価データが既にあり、所属セクターが「踏み上げ初動/モメンタム急騰」で、
         かつ直近の騰落率がMIN_PRICE_CHANGE_PCT以上
    """
    today = datetime.now().strftime("%Y-%m-%d")

    if fetch_sectors:
        sector_data_manager.fetch_sector_ranking(db)

    sec_results = calculate_sector_divergence(db)
    hot_sectors = {s["sector_name"] for s in sec_results if s["signal_rank"] in HOT_SECTOR_SIGNAL_RANKS}

    candidates: dict[str, dict] = {}

    for code in db.get_tracked_codes():
        candidates[code] = {"code": code, "screening_score": 100.0, "reason": "お気に入り登録銘柄(常時監視)"}
    for pos in db.list_positions():
        candidates.setdefault(
            pos["code"], {"code": pos["code"], "screening_score": 100.0, "reason": "保有ポジション銘柄(常時監視)"}
        )

    for code in db.get_distinct_stock_codes():
        if code in candidates:
            continue
        latest = db.get_latest_record(code)
        if not latest or not latest["sector"] or latest["sector"] not in hot_sectors:
            continue
        change_pct = latest["price_change_percent"]
        if change_pct is not None and change_pct >= MIN_PRICE_CHANGE_PCT:
            candidates[code] = {
                "code": code,
                "screening_score": change_pct,
                "reason": f"セクターモメンタム({latest['sector']}) + 騰落率{change_pct:+.1f}%",
            }

    for c in candidates.values():
        c["date"] = today
        db.upsert_candidate_stock(c)

    return sorted(candidates.values(), key=lambda x: x["screening_score"], reverse=True)
