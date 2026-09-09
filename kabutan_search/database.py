"""kabutan_stock.db の管理: スキーマ定義・自己修復マイグレーション・CRUD

SPECIFICATION.md 3.1 節のテーブル定義に対応する。
"""
import sqlite3
from pathlib import Path

from .db_common import ensure_columns

DEFAULT_DB_PATH = Path("kabutan_stock.db")

STOCK_RECORD_COLUMNS = {
    # 基本
    "date": "TEXT NOT NULL",
    "code": "TEXT NOT NULL",
    "name": "TEXT",
    "price": "REAL",
    "price_change": "REAL",
    "price_change_percent": "REAL",
    "url": "TEXT",
    "timestamp": "TEXT",
    # OHLCV
    "previous_close": "REAL",
    "open": "REAL",
    "high": "REAL",
    "low": "REAL",
    "volume": "INTEGER",
    "trading_value": "REAL",
    # バリュエーション
    "market_cap": "REAL",
    "issued_shares": "REAL",
    "dividend_yield": "REAL",
    "dps": "REAL",
    "per": "REAL",
    "per_avg_3": "REAL",
    "pbr": "REAL",
    "eps": "REAL",
    "bps": "REAL",
    "roe": "REAL",
    "equity_ratio": "REAL",
    # 年高安
    "min_purchase_price": "REAL",
    "round_lot": "INTEGER",
    "year_high": "REAL",
    "year_high_date": "TEXT",
    "year_low": "REAL",
    "year_low_date": "TEXT",
    # 信用残
    "margin_buy": "REAL",
    "margin_buy_change": "REAL",
    "margin_ratio": "REAL",
    "margin_sell": "REAL",
    "margin_sell_change": "REAL",
    # 決算進捗
    "progress_rate": "REAL",
    "progress_quarter": "TEXT",
    "progress_excess": "REAL",
    # 決算・優待
    "earnings_date": "TEXT",
    "has_yutai": "TEXT",
    # 成長率
    "cagr_revenue_3y": "REAL",
    "cagr_revenue_5y": "REAL",
    "cagr_profit_3y": "REAL",
    "cagr_profit_5y": "REAL",
    "consecutive_revenue_growth": "INTEGER",
    "consecutive_profit_growth": "INTEGER",
    # プレミアム
    "vwap": "REAL",
    # セクター
    "sector": "TEXT",
    "settlement_month": "INTEGER",
    # 分析結果
    "squeeze_score": "INTEGER",
}

TRACKED_STOCK_COLUMNS = {
    "code": "TEXT PRIMARY KEY",
    "name": "TEXT",
    "tracked_date": "TEXT",
    "tracked_price": "REAL",
    "latest_price": "REAL",
}

POSITION_COLUMNS = {
    "code": "TEXT PRIMARY KEY",
    "name": "TEXT",
    "purchase_price": "REAL",
    "shares": "INTEGER",
    "timestamp": "TEXT",
}

GEMINI_RECOMMENDATION_COLUMNS = {
    "date": "TEXT",
    "code": "TEXT",
    "name": "TEXT",
    "reg_price": "REAL",
    "entry_low": "REAL",
    "entry_high": "REAL",
    "target_price": "REAL",
    "stop_loss": "REAL",
    "latest_price": "REAL",
    "return_pct": "REAL",
    "status": "TEXT",
    "reason": "TEXT",
}

SECTOR_DAILY_RECORD_COLUMNS = {
    "date": "TEXT NOT NULL",
    "sector_code": "TEXT NOT NULL",
    "sector_name": "TEXT",
    "stock_count": "INTEGER",
    "price": "REAL",
    "price_change": "REAL",
    "change_pct": "REAL",
    "per": "REAL",
    "pbr": "REAL",
    "yield_val": "REAL",
    "short_ratio": "REAL",
    "timestamp": "TEXT",
}

CANDIDATE_STOCK_COLUMNS = {
    "date": "TEXT NOT NULL",
    "code": "TEXT NOT NULL",
    "screening_score": "REAL",
    "reason": "TEXT",
    "timestamp": "TEXT",
}


class Database:
    """kabutan_stock.db への接続とCRUDをまとめるクラス。

    SQLiteはスレッドごとに個別接続する前提のため、メソッド呼び出しの都度
    connect/closeする（コネクションを長期保持しない）。
    """

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.initialize_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize_db(self) -> None:
        """自己修復型マイグレーション: テーブルが無ければ作成し、カラムが不足していれば追加する"""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    UNIQUE(date, code)
                )
                """
            )
            ensure_columns(conn, "stock_records", STOCK_RECORD_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_stocks (
                    code TEXT PRIMARY KEY
                )
                """
            )
            ensure_columns(conn, "tracked_stocks", TRACKED_STOCK_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    code TEXT PRIMARY KEY
                )
                """
            )
            ensure_columns(conn, "positions", POSITION_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gemini_recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT
                )
                """
            )
            ensure_columns(conn, "gemini_recommendations", GEMINI_RECOMMENDATION_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_daily_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    sector_code TEXT NOT NULL,
                    UNIQUE(date, sector_code)
                )
                """
            )
            ensure_columns(conn, "sector_daily_records", SECTOR_DAILY_RECORD_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    UNIQUE(date, code)
                )
                """
            )
            ensure_columns(conn, "candidate_stocks", CANDIDATE_STOCK_COLUMNS)

    # -- stock_records ---------------------------------------------------

    def upsert_stock_record(self, record: dict) -> None:
        """(date, code) が既存なら上書き、なければ新規挿入"""
        if "date" not in record or "code" not in record:
            raise ValueError("record must include 'date' and 'code'")
        columns = [c for c in record if c in STOCK_RECORD_COLUMNS]
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in ("date", "code"))
        sql = f"INSERT INTO stock_records ({column_list}) VALUES ({placeholders})"
        if update_clause:
            sql += f" ON CONFLICT(date, code) DO UPDATE SET {update_clause}"
        with self._connect() as conn:
            conn.execute(sql, record)

    def get_latest_record(self, code: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM stock_records WHERE code = ? ORDER BY date DESC LIMIT 1",
                (code,),
            )
            return cur.fetchone()

    def get_stock_name(self, code: str) -> str | None:
        """銘柄名を取得する(お気に入り登録名 > 最新レコード名の優先順)"""
        with self._connect() as conn:
            row = conn.execute("SELECT name FROM tracked_stocks WHERE code = ? LIMIT 1", (code,)).fetchone()
            if row and row["name"]:
                return row["name"]
            row = conn.execute(
                "SELECT name FROM stock_records WHERE code = ? ORDER BY date DESC LIMIT 1", (code,)
            ).fetchone()
            return row["name"] if row and row["name"] else None

    def get_squeeze_score(self, code: str) -> tuple[int, str]:
        """最新レコードのスクイーズスコア(★1〜5)を (score, 星文字列) で返す。データが無ければ (0, "")"""
        row = self.get_latest_record(code)
        score = row["squeeze_score"] if row else None
        if not score:
            return 0, ""
        return score, "★" * score

    def get_records(self, code: str, start_date: str | None = None, end_date: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM stock_records WHERE code = ?"
        params: list = [code]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def get_stock_close_prices(self, code: str, limit: int = 75) -> list[sqlite3.Row]:
        """直近limit件の終値を新しい順(index 0が最新)で返す"""
        with self._connect() as conn:
            return conn.execute(
                "SELECT date, price FROM stock_records WHERE code = ? AND price IS NOT NULL "
                "ORDER BY date DESC LIMIT ?",
                (code, limit),
            ).fetchall()

    def get_distinct_stock_codes(self) -> list[str]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_records").fetchall()]

    def get_tracked_codes(self) -> set[str]:
        with self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT code FROM tracked_stocks").fetchall()}

    def get_recent_stock_history(self, code: str, limit: int = 160) -> list[sqlite3.Row]:
        """直近limit件の日次レコードを新しい順(index 0が最新)で返す(price > 0のみ)"""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM stock_records WHERE code = ? AND price > 0 ORDER BY date DESC LIMIT ?",
                (code, limit),
            ).fetchall()

    def get_recent_margin_history(self, code: str, limit: int = 28) -> list[sqlite3.Row]:
        """信用残が記録されている直近limit件を新しい順で返す(週次サンプルに相当)"""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM stock_records WHERE code = ? AND (margin_buy IS NOT NULL OR margin_sell IS NOT NULL) "
                "ORDER BY date DESC LIMIT ?",
                (code, limit),
            ).fetchall()

    def get_sector_margin_summary(self) -> list[dict]:
        """業種ごとに、直近レコードで信用買い残が前回比減少している銘柄の割合等を集計する"""
        query = """
            WITH MarginLatest AS (
                SELECT
                    code,
                    sector,
                    margin_buy,
                    margin_sell,
                    margin_ratio,
                    (margin_buy - LAG(margin_buy, 1) OVER (PARTITION BY code ORDER BY date ASC)) AS margin_buy_change,
                    (margin_sell - LAG(margin_sell, 1) OVER (PARTITION BY code ORDER BY date ASC)) AS margin_sell_change,
                    ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                FROM stock_records
                WHERE margin_buy IS NOT NULL AND sector IS NOT NULL AND sector != ''
            )
            SELECT
                sector,
                COUNT(code) AS stock_count,
                SUM(CASE WHEN margin_buy_change < 0 THEN 1 ELSE 0 END) AS buy_reduced_count,
                ROUND(CAST(SUM(CASE WHEN margin_buy_change < 0 THEN 1 ELSE 0 END) AS REAL) * 100.0 / COUNT(code), 1) AS buy_reduced_pct,
                SUM(CASE WHEN margin_sell_change > 0 THEN 1 ELSE 0 END) AS sell_increased_count,
                ROUND(AVG(margin_ratio), 2) AS avg_margin_ratio
            FROM MarginLatest
            WHERE rn = 1
            GROUP BY sector
            HAVING stock_count >= 2
            ORDER BY buy_reduced_pct DESC, avg_margin_ratio ASC
        """
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(query).fetchall()]

    def get_latest_records_for_screening(self) -> list[sqlite3.Row]:
        """全銘柄の最新レコードを取得する。信用残系カラムがNULLの場合は、
        その銘柄の直近の非NULL値で補完する(信用残は毎日更新されないため)。"""
        query = """
            SELECT r.*,
                   COALESCE(r.margin_sell, (
                       SELECT margin_sell FROM stock_records
                       WHERE code = r.code AND margin_sell IS NOT NULL
                       ORDER BY date DESC LIMIT 1
                   )) AS margin_sell,
                   COALESCE(r.margin_buy, (
                       SELECT margin_buy FROM stock_records
                       WHERE code = r.code AND margin_buy IS NOT NULL
                       ORDER BY date DESC LIMIT 1
                   )) AS margin_buy,
                   COALESCE(r.margin_ratio, (
                       SELECT margin_ratio FROM stock_records
                       WHERE code = r.code AND margin_ratio IS NOT NULL
                       ORDER BY date DESC LIMIT 1
                   )) AS margin_ratio
            FROM stock_records r
            INNER JOIN (
                SELECT code, MAX(date) AS max_date
                FROM stock_records
                GROUP BY code
            ) m ON r.code = m.code AND r.date = m.max_date
        """
        with self._connect() as conn:
            return conn.execute(query).fetchall()

    # -- tracked_stocks (お気に入り) --------------------------------------

    def add_tracked_stock(self, code: str, name: str, tracked_date: str, tracked_price: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tracked_stocks (code, name, tracked_date, tracked_price, latest_price)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name, tracked_date=excluded.tracked_date, tracked_price=excluded.tracked_price
                """,
                (code, name, tracked_date, tracked_price, tracked_price),
            )

    def remove_tracked_stock(self, code: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tracked_stocks WHERE code = ?", (code,))

    def update_tracked_stock_price(self, code: str, latest_price: float) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tracked_stocks SET latest_price = ? WHERE code = ?", (latest_price, code))

    def list_tracked_stocks(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM tracked_stocks ORDER BY tracked_date DESC").fetchall()

    # -- positions (保有ポジション) ----------------------------------------

    def add_position(self, code: str, name: str, purchase_price: float, shares: int, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO positions (code, name, purchase_price, shares, timestamp)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name=excluded.name, purchase_price=excluded.purchase_price,
                    shares=excluded.shares, timestamp=excluded.timestamp
                """,
                (code, name, purchase_price, shares, timestamp),
            )

    def remove_position(self, code: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE code = ?", (code,))

    def list_positions(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM positions ORDER BY timestamp DESC").fetchall()

    # -- gemini_recommendations -------------------------------------------

    def add_gemini_recommendation(self, record: dict) -> int:
        columns = [c for c in record if c in GEMINI_RECOMMENDATION_COLUMNS]
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        with self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO gemini_recommendations ({column_list}) VALUES ({placeholders})",
                record,
            )
            return cur.lastrowid

    def update_gemini_recommendation(self, rec_id: int, fields: dict) -> None:
        columns = [c for c in fields if c in GEMINI_RECOMMENDATION_COLUMNS]
        set_clause = ", ".join(f"{c} = :{c}" for c in columns)
        params = dict(fields)
        params["id"] = rec_id
        with self._connect() as conn:
            conn.execute(f"UPDATE gemini_recommendations SET {set_clause} WHERE id = :id", params)

    def list_gemini_recommendations(self, status: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM gemini_recommendations"
        params: list = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY date DESC"
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    # -- sector_daily_records -----------------------------------------------

    def upsert_sector_daily_record(self, record: dict) -> None:
        if "date" not in record or "sector_code" not in record:
            raise ValueError("record must include 'date' and 'sector_code'")
        columns = [c for c in record if c in SECTOR_DAILY_RECORD_COLUMNS]
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        update_clause = ", ".join(
            f"{c}=excluded.{c}" for c in columns if c not in ("date", "sector_code")
        )
        sql = f"INSERT INTO sector_daily_records ({column_list}) VALUES ({placeholders})"
        if update_clause:
            sql += f" ON CONFLICT(date, sector_code) DO UPDATE SET {update_clause}"
        with self._connect() as conn:
            conn.execute(sql, record)

    def get_sector_daily_records(self, date: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM sector_daily_records WHERE date = ? ORDER BY change_pct DESC",
                (date,),
            ).fetchall()

    def update_sector_short_ratio(self, date: str, sector_code_or_name: str, short_ratio: float) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sector_daily_records SET short_ratio = ?
                WHERE date = ? AND (sector_code = ? OR sector_name = ?)
                """,
                (short_ratio, date, sector_code_or_name, sector_code_or_name),
            )

    def batch_update_sector_short_ratios(self, date: str, ratio_map: dict) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                UPDATE sector_daily_records SET short_ratio = ?
                WHERE date = ? AND (sector_code = ? OR sector_name = ?)
                """,
                [(ratio, date, str(key), str(key)) for key, ratio in ratio_map.items()],
            )

    def get_latest_sector_date(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(date) FROM sector_daily_records").fetchone()
            return row[0] if row else None

    def get_all_sector_dates(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT date FROM sector_daily_records ORDER BY date DESC").fetchall()
            return [r[0] for r in rows]

    def get_sector_history(self, sector_code: str, limit: int = 60) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sector_daily_records WHERE sector_code = ? ORDER BY date ASC",
                (sector_code,),
            ).fetchall()
            return rows[-limit:] if len(rows) > limit else rows

    # -- candidate_stocks (Stage1スクリーニング結果) --------------------------

    def upsert_candidate_stock(self, record: dict) -> None:
        if "date" not in record or "code" not in record:
            raise ValueError("record must include 'date' and 'code'")
        columns = [c for c in record if c in CANDIDATE_STOCK_COLUMNS]
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in ("date", "code"))
        sql = f"INSERT INTO candidate_stocks ({column_list}) VALUES ({placeholders})"
        if update_clause:
            sql += f" ON CONFLICT(date, code) DO UPDATE SET {update_clause}"
        with self._connect() as conn:
            conn.execute(sql, record)

    def get_candidate_stocks(self, date: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM candidate_stocks WHERE date = ? ORDER BY screening_score DESC",
                (date,),
            ).fetchall()
