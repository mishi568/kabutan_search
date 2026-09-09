"""33業種セクターのモメンタムZスコア・ダイバージェンスシグナル算出

SPECIFICATION.md 4.6節に対応する。
"""
import numpy as np

from .database import Database


def calculate_sector_divergence(
    db: Database,
    target_date: str | None = None,
    n_days: int = 20,
    z_threshold: float = 1.5,
    short_ratio_threshold: float = 45.0,
) -> list[dict]:
    """target_date時点の33業種すべてについてZスコアとシグナルを算出する。

    Z-Score = (R_t - mu) / sigma
      R_t: 当日の業種騰落率、mu・sigma: 過去n_days間の騰落率の平均・標準偏差

    シグナル:
      🔥 踏み上げ初動   — 空売り比率 >= threshold AND Z >= z_threshold
      🚀 モメンタム急騰 — Z >= z_threshold (空売り比率 < threshold)
      ⚡ 需給好転・反発兆候 — 空売り比率 >= threshold AND 0.8 <= Z < z_threshold
      ⚠️ 空売り過熱     — 空売り比率 >= threshold AND Z < 0.8
      ⚪ 通常           — それ以外
    """
    if not target_date:
        target_date = db.get_latest_sector_date()
    if not target_date:
        return []

    current_sectors = db.get_sector_daily_records(target_date)
    if not current_sectors:
        return []

    analyzed_results = []

    for sec in current_sectors:
        code = sec["sector_code"]
        name = sec["sector_name"]
        current_pct = sec["change_pct"]
        short_ratio = sec["short_ratio"]
        price = sec["price"]
        p_change = sec["price_change"]

        history = db.get_sector_history(code, limit=n_days + 5)
        hist_records = [h for h in history if h["date"] <= target_date]
        pct_series = [h["change_pct"] for h in hist_records if h["change_pct"] is not None]

        if len(pct_series) >= 2:
            mu = float(np.mean(pct_series))
            sigma = float(np.std(pct_series, ddof=1))
            if sigma < 1e-4:
                sigma = 0.5  # ゼロ除算回避のフォールバック
        else:
            mu = 0.0
            sigma = 1.0

        z_score = (current_pct - mu) / sigma if current_pct is not None else 0.0

        short_series = [h["short_ratio"] for h in hist_records[-5:] if h["short_ratio"] is not None]
        avg_short_5d = float(np.mean(short_series)) if short_series else (short_ratio or 0.0)
        effective_short = short_ratio if short_ratio is not None else avg_short_5d

        is_high_short = effective_short >= short_ratio_threshold
        is_high_z = z_score >= z_threshold

        if is_high_short and is_high_z:
            signal_type, signal_badge, signal_rank = "踏み上げ初動", "🔥 踏み上げ初動 (強烈リバーサル)", 1
        elif is_high_z:
            signal_type, signal_badge, signal_rank = "モメンタム急騰", "🚀 モメンタム急騰", 2
        elif is_high_short and z_score >= 0.8:
            signal_type, signal_badge, signal_rank = "反発兆候", "⚡ 需給好転・反発兆候", 3
        elif is_high_short:
            signal_type, signal_badge, signal_rank = "空売り過熱", "⚠️ 空売り過熱 (反発待機)", 4
        else:
            signal_type, signal_badge, signal_rank = "通常", "⚪ 通常", 5

        # 空売り比率が高いほど、Zスコアがプラスに高いほどスコアが上がる合成指標
        comp_score = (effective_short / 40.0) * max(0.0, z_score + 1.0)

        analyzed_results.append({
            "date": target_date,
            "sector_code": code,
            "sector_name": name,
            "price": price,
            "price_change": p_change,
            "change_pct": current_pct,
            "short_ratio": effective_short,
            "short_ratio_5d_avg": avg_short_5d,
            "mu_pct": mu,
            "sigma_pct": sigma,
            "z_score": round(z_score, 2),
            "divergence_score": round(comp_score, 2),
            "signal_type": signal_type,
            "signal_badge": signal_badge,
            "signal_rank": signal_rank,
            "per": sec["per"],
            "pbr": sec["pbr"],
            "yield_val": sec["yield_val"],
            "stock_count": sec["stock_count"],
        })

    analyzed_results.sort(key=lambda x: (x["signal_rank"], -x["divergence_score"], -(x["change_pct"] or -999)))
    return analyzed_results
