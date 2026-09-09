"""Stage1: 値上がり優位性スクリーニング

詳細データ(信用残・決算等)を取得する前に、軽量なデータだけで値上がり優位性のある銘柄を
絞り込む。SPECIFICATION.md 2節(2段階データ収集フロー)に対応する。

候補の発掘元:
  1. お気に入り・保有ポジション銘柄(常時監視対象)
  2. 株探「出来高急増銘柄」ランキング(tansaku.py) — 玉集めの兆候を検知
  3. 株探「株価注意報」の信用残ランキング(margin_ranking.py) — 信用売り残減少(踏み上げ
     進行中)・信用期日到来銘柄(強制的な売買圧力)
  4. JPX公式の週次信用取引残高データ(jpx_margin_positions) — 信用倍率が低い(売り長=
     踏み上げ期待)銘柄。kabutan.jpに一切アクセスしない(要: 事前に `jpx-sync` コマンド)
  5. セクターランキング(sector_data_manager) + 既知の直近株価データ — 所属セクターが
     好調で、かつ既に把握している騰落率が高い銘柄
"""
from datetime import datetime

from . import margin_ranking, sector_data_manager, tansaku
from .database import Database
from .nikkei_database import NikkeiDatabase
from .sector_divergence_analyzer import calculate_sector_divergence

# 踏み上げ初動(1)・モメンタム急騰(2)のセクターを「勢いのあるセクター」とみなす
HOT_SECTOR_SIGNAL_RANKS = (1, 2)
# セクターが好調な銘柄について、これ以上の当日騰落率(%)であれば候補とする
MIN_PRICE_CHANGE_PCT = 3.0
# この倍率以下を「信用倍率が低い(売り長)」とみなす
MAX_MARGIN_RATIO = 1.5


def screen(
    db: Database,
    nikkei_db: NikkeiDatabase | None = None,
    fetch_sectors: bool = True,
    include_volume_surge: bool = True,
    include_margin_ranking: bool = True,
    include_low_margin_ratio: bool = True,
) -> list[dict]:
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

    # 3. 信用売り残減少ランキング(踏み上げ進行中) + 信用期日到来銘柄(強制的な売買圧力)
    if include_margin_ranking:
        for rec in margin_ranking.fetch_ranking(margin_ranking.MODE_SHORT_DECREASE):
            code = rec["code"]
            if code in candidates:
                continue
            ratio = rec["margin_ratio"]
            ratio_str = f"{ratio:.2f}倍" if ratio is not None else "--"
            candidates[code] = {
                "code": code,
                "screening_score": 700.0,
                "reason": f"信用売り残減少中(信用倍率{ratio_str}): 踏み上げ進行中",
            }

        for mode, label in ((margin_ranking.MODE_DUE_HIGH, "高値"), (margin_ranking.MODE_DUE_LOW, "安値")):
            for rec in margin_ranking.fetch_ranking(mode):
                code = rec["code"]
                if code in candidates:
                    continue
                candidates[code] = {
                    "code": code,
                    "screening_score": 600.0,
                    "reason": f"信用期日到来({label}基準): 強制的な売買圧力の可能性",
                }

    # 4. JPX公式データ: 信用倍率が低い銘柄(売り長=踏み上げ期待)
    if include_low_margin_ratio and nikkei_db is not None:
        for pos in nikkei_db.get_low_margin_ratio_positions(max_ratio=MAX_MARGIN_RATIO):
            code = pos["code"]
            if code in candidates:
                continue
            ratio = pos["margin_ratio"]
            candidates[code] = {
                "code": code,
                "screening_score": 500.0 * (MAX_MARGIN_RATIO - ratio + 0.1),  # 倍率が低いほど高スコア
                "reason": f"JPX信用倍率{ratio:.2f}倍(売り長=踏み上げ期待)",
            }

    # 5. セクターモメンタム + 既知の値上がり率
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
