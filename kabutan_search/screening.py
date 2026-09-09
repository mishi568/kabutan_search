"""Stage1: 値上がり優位性スクリーニング

詳細データ(信用残・決算等)を取得する前に、軽量なデータだけで値上がり優位性のある銘柄を
絞り込む。SPECIFICATION.md 2節(2段階データ収集フロー)に対応する。

候補の発掘元:
  1. お気に入り・保有ポジション銘柄(常時監視対象)
  2. 株探「出来高急増銘柄」ランキング(tansaku.py) — 玉集めの兆候を検知
  3. セクターランキング(sector_data_manager) + 既知の直近株価データ — 所属セクターが
     好調で、かつ既に把握している騰落率が高い銘柄

「信用倍率が低い(売り長=踏み上げ期待)」ランキングは対象ページ未確定のため未実装
(SPECIFICATION.md 8節)。ページが決まり次第、同様のソースとして追加できる。
"""
from datetime import datetime

from . import sector_data_manager, tansaku
from .database import Database
from .sector_divergence_analyzer import calculate_sector_divergence

# 踏み上げ初動(1)・モメンタム急騰(2)のセクターを「勢いのあるセクター」とみなす
HOT_SECTOR_SIGNAL_RANKS = (1, 2)
# セクターが好調な銘柄について、これ以上の当日騰落率(%)であれば候補とする
MIN_PRICE_CHANGE_PCT = 3.0


def screen(db: Database, fetch_sectors: bool = True, include_volume_surge: bool = True) -> list[dict]:
    """当日の値上がり優位性候補を抽出し、candidate_stocksテーブルへ保存して返す。"""
    today = datetime.now().strftime("%Y-%m-%d")

    if fetch_sectors:
        sector_data_manager.fetch_sector_ranking(db)

    sec_results = calculate_sector_divergence(db)
    hot_sectors = {s["sector_name"] for s in sec_results if s["signal_rank"] in HOT_SECTOR_SIGNAL_RANKS}

    candidates: dict[str, dict] = {}

    # 1. 常時監視対象(お気に入り・ポジション)
    for code in db.get_tracked_codes():
        candidates[code] = {"code": code, "screening_score": 1000.0, "reason": "お気に入り登録銘柄(常時監視)"}
    for pos in db.list_positions():
        candidates.setdefault(
            pos["code"], {"code": pos["code"], "screening_score": 1000.0, "reason": "保有ポジション銘柄(常時監視)"}
        )

    # 2. 出来高急増ランキング(玉集めの兆候)
    if include_volume_surge:
        for rec in tansaku.fetch_volume_surge():
            code = rec["code"]
            if code in candidates:
                continue
            vol_chg = rec["volume_change_pct"] or 0.0
            candidates[code] = {
                "code": code,
                "screening_score": vol_chg,
                "reason": f"出来高急増(前日比+{vol_chg:.0f}%): 玉集めの兆候",
            }

    # 3. セクターモメンタム + 既知の値上がり率
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
