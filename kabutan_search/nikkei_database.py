"""nikkei_data.db の管理: マクロ指標・JPX・EDINETデータ

SPECIFICATION.md 3.2 節のテーブル定義に対応する。
"""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .db_common import ensure_columns

DEFAULT_DB_PATH = Path("nikkei_data.db")

NIKKEI_PER_RECORD_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "price": "REAL",
    "per": "REAL",
    "pbr": "REAL",
    "eps": "REAL",
    "bps": "REAL",
    "earnings_yield": "REAL",
    "dividend_yield": "REAL",
    "jgb_yield": "REAL",
    "timestamp": "TEXT",
}

NIKKEI_TOURAKU_RECORD_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "price": "REAL",
    "price_change": "REAL",
    "prime_volume": "REAL",
    "advancing_count": "REAL",
    "declining_count": "REAL",
    "touraku_6d": "REAL",
    "touraku_10d": "REAL",
    "touraku_10_12d": "REAL",
    "touraku_15d": "REAL",
    "touraku_25d": "REAL",
    "timestamp": "TEXT",
}

NIKKEI_MARGIN_RECORD_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "price": "REAL",
    "margin_buy": "REAL",
    "margin_sell": "REAL",
    "margin_buy_shares": "REAL",
    "margin_sell_shares": "REAL",
    "margin_buy_change_pct": "REAL",
    "margin_sell_change_pct": "REAL",
    "margin_ratio": "REAL",
    "profit_loss_ratio": "REAL",
    "timestamp": "TEXT",
}

JPX_SHORT_SELLING_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "short_selling_ratio": "REAL",
    "short_selling_value": "REAL",
    "total_value": "REAL",
    "regulated_ratio": "REAL",
    "non_regulated_ratio": "REAL",
    "timestamp": "TEXT",
}

JPX_INVESTOR_TRENDS_COLUMNS = {
    "date": "TEXT PRIMARY KEY",
    "foreign_net": "REAL",
    "individual_net": "REAL",
    "trust_bank_net": "REAL",
    "investment_trust_net": "REAL",
    "business_corp_net": "REAL",
    "other_net": "REAL",
    "timestamp": "TEXT",
}

NIKKEI225JP_INVESTOR_TRENDS_COLUMNS = {
    # nikkei225jp.com/data/shutai.php(投資主体別売買状況、週次、単位: 億円換算)
    # JPX公式のjpx_investor_trendsを補完・裏取りするための別データ源。
    "date": "TEXT PRIMARY KEY",
    "price": "REAL",
    "price_change_pct": "REAL",
    "foreign_net": "REAL",
    "dealer_net": "REAL",
    "individual_net": "REAL",
    "individual_cash_net": "REAL",
    "individual_margin_net": "REAL",
    "investment_trust_net": "REAL",
    "business_corp_net": "REAL",
    "other_corp_net": "REAL",
    "trust_bank_net": "REAL",
    "insurance_net": "REAL",
    "bank_net": "REAL",
    "timestamp": "TEXT",
}

NIKKEI225JP_SHORT_SELLING_COLUMNS = {
    # nikkei225jp.com/data/karauri.php(空売り比率90営業日日本市況)
    # JPX公式のjpx_short_sellingを補完・裏取りするための別データ源。
    "date": "TEXT PRIMARY KEY",
    "price": "REAL",
    "price_change": "REAL",
    "prime_trading_value": "REAL",
    "prime_volume": "REAL",
    "short_ratio_total": "REAL",
    "short_ratio_regulated": "REAL",
    "short_ratio_non_regulated": "REAL",
    "timestamp": "TEXT",
}

JPX_MARGIN_POSITION_COLUMNS = {
    "date": "TEXT NOT NULL",
    "code": "TEXT NOT NULL",
    "name": "TEXT",
    "margin_buy": "REAL",
    "margin_sell": "REAL",
    "margin_ratio": "REAL",
    "margin_buy_change": "REAL",
    "margin_sell_change": "REAL",
    "timestamp": "TEXT",
}

JPX_SHORT_POSITION_COLUMNS = {
    "date": "TEXT NOT NULL",
    "code": "TEXT NOT NULL",
    "holder_name": "TEXT NOT NULL",
    "short_position_ratio": "REAL",
    "short_position_shares": "REAL",
    "disclosure_date": "TEXT",
    "timestamp": "TEXT",
}

EDINET_LARGE_HOLDING_COLUMNS = {
    "doc_id": "TEXT PRIMARY KEY",
    "date": "TEXT",
    "code": "TEXT",
    "issuer_name": "TEXT",
    "holder_name": "TEXT",
    "holding_ratio": "REAL",
    "report_type": "TEXT",
    "purpose": "TEXT",
    "submission_date": "TEXT",
    "timestamp": "TEXT",
}


class NikkeiDatabase:
    """nikkei_data.db への接続とCRUDをまとめるクラス"""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.initialize_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS nikkei_per_records (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "nikkei_per_records", NIKKEI_PER_RECORD_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS nikkei_touraku_records (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "nikkei_touraku_records", NIKKEI_TOURAKU_RECORD_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS nikkei_margin_records (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "nikkei_margin_records", NIKKEI_MARGIN_RECORD_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS jpx_short_selling (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "jpx_short_selling", JPX_SHORT_SELLING_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS jpx_investor_trends (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "jpx_investor_trends", JPX_INVESTOR_TRENDS_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS nikkei225jp_investor_trends (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "nikkei225jp_investor_trends", NIKKEI225JP_INVESTOR_TRENDS_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS nikkei225jp_short_selling (date TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "nikkei225jp_short_selling", NIKKEI225JP_SHORT_SELLING_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jpx_margin_positions (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    PRIMARY KEY (date, code)
                )
                """
            )
            ensure_columns(conn, "jpx_margin_positions", JPX_MARGIN_POSITION_COLUMNS)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jpx_short_positions (
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    holder_name TEXT NOT NULL,
                    PRIMARY KEY (date, code, holder_name)
                )
                """
            )
            ensure_columns(conn, "jpx_short_positions", JPX_SHORT_POSITION_COLUMNS)

            conn.execute(
                "CREATE TABLE IF NOT EXISTS edinet_large_holdings (doc_id TEXT PRIMARY KEY)"
            )
            ensure_columns(conn, "edinet_large_holdings", EDINET_LARGE_HOLDING_COLUMNS)

    def _upsert(self, table: str, columns_def: dict, key_columns: tuple, record: dict) -> None:
        for key in key_columns:
            if key not in record:
                raise ValueError(f"record must include '{key}'")
        columns = [c for c in record if c in columns_def]
        column_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        update_clause = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in key_columns)
        key_list = ", ".join(key_columns)
        sql = f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})"
        if update_clause:
            sql += f" ON CONFLICT({key_list}) DO UPDATE SET {update_clause}"
        with self._connect() as conn:
            conn.execute(sql, record)

    # -- 日経マクロ指標 -----------------------------------------------------

    def upsert_nikkei_per_record(self, record: dict) -> None:
        self._upsert("nikkei_per_records", NIKKEI_PER_RECORD_COLUMNS, ("date",), record)

    def upsert_nikkei_touraku_record(self, record: dict) -> None:
        self._upsert("nikkei_touraku_records", NIKKEI_TOURAKU_RECORD_COLUMNS, ("date",), record)

    def upsert_nikkei_margin_record(self, record: dict) -> None:
        self._upsert("nikkei_margin_records", NIKKEI_MARGIN_RECORD_COLUMNS, ("date",), record)

    def upsert_nikkei225jp_investor_trends(self, record: dict) -> None:
        self._upsert(
            "nikkei225jp_investor_trends", NIKKEI225JP_INVESTOR_TRENDS_COLUMNS, ("date",), record
        )

    def get_latest_nikkei225jp_investor_trends(self) -> sqlite3.Row | None:
        return self._get_latest("nikkei225jp_investor_trends")

    def get_nikkei225jp_investor_trends(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[sqlite3.Row]:
        return self._get_range("nikkei225jp_investor_trends", start_date, end_date)

    def upsert_nikkei225jp_short_selling(self, record: dict) -> None:
        self._upsert(
            "nikkei225jp_short_selling", NIKKEI225JP_SHORT_SELLING_COLUMNS, ("date",), record
        )

    def get_latest_nikkei225jp_short_selling(self) -> sqlite3.Row | None:
        return self._get_latest("nikkei225jp_short_selling")

    def get_nikkei225jp_short_selling(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[sqlite3.Row]:
        return self._get_range("nikkei225jp_short_selling", start_date, end_date)

    def get_nikkei_per_records(self, start_date: str | None = None, end_date: str | None = None) -> list[sqlite3.Row]:
        return self._get_range("nikkei_per_records", start_date, end_date)

    def get_nikkei_touraku_records(self, start_date: str | None = None, end_date: str | None = None) -> list[sqlite3.Row]:
        return self._get_range("nikkei_touraku_records", start_date, end_date)

    def get_latest_nikkei_per_record(self) -> sqlite3.Row | None:
        return self._get_latest("nikkei_per_records")

    def get_latest_nikkei_touraku_record(self) -> sqlite3.Row | None:
        return self._get_latest("nikkei_touraku_records")

    def get_latest_nikkei_margin_record(self) -> sqlite3.Row | None:
        return self._get_latest("nikkei_margin_records")

    def _get_latest(self, table: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(f"SELECT * FROM {table} ORDER BY date DESC LIMIT 1").fetchone()

    def get_nikkei_margin_records(self, start_date: str | None = None, end_date: str | None = None) -> list[sqlite3.Row]:
        return self._get_range("nikkei_margin_records", start_date, end_date)

    def _get_range(self, table: str, start_date: str | None, end_date: str | None) -> list[sqlite3.Row]:
        query = f"SELECT * FROM {table} WHERE 1=1"
        params: list = []
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    # -- JPX ----------------------------------------------------------------

    def upsert_jpx_short_selling(self, record: dict) -> None:
        self._upsert("jpx_short_selling", JPX_SHORT_SELLING_COLUMNS, ("date",), record)

    def upsert_jpx_investor_trends(self, record: dict) -> None:
        self._upsert("jpx_investor_trends", JPX_INVESTOR_TRENDS_COLUMNS, ("date",), record)

    def get_latest_jpx_short_selling(self) -> sqlite3.Row | None:
        return self._get_latest("jpx_short_selling")

    def get_latest_jpx_investor_trends(self) -> sqlite3.Row | None:
        return self._get_latest("jpx_investor_trends")

    def upsert_jpx_margin_position(self, record: dict) -> None:
        self._upsert("jpx_margin_positions", JPX_MARGIN_POSITION_COLUMNS, ("date", "code"), record)

    def get_latest_margin_positions_date(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(date) FROM jpx_margin_positions").fetchone()
            return row[0] if row else None

    def get_low_margin_ratio_positions(self, max_ratio: float = 1.5, date: str | None = None) -> list[sqlite3.Row]:
        """信用倍率がmax_ratio以下(売り長=踏み上げ期待)の銘柄を、指定日(省略時は最新日)で返す"""
        if date is None:
            date = self.get_latest_margin_positions_date()
        if date is None:
            return []
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM jpx_margin_positions WHERE date = ? AND margin_ratio IS NOT NULL "
                "AND margin_ratio <= ? ORDER BY margin_ratio ASC",
                (date, max_ratio),
            ).fetchall()

    def get_jpx_margin_positions(self, code: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM jpx_margin_positions WHERE code = ? ORDER BY date ASC",
                (code,),
            ).fetchall()

    def upsert_jpx_short_position(self, record: dict) -> None:
        self._upsert(
            "jpx_short_positions", JPX_SHORT_POSITION_COLUMNS, ("date", "code", "holder_name"), record
        )

    def get_jpx_short_positions(self, code: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM jpx_short_positions WHERE code = ? ORDER BY date ASC",
                (code,),
            ).fetchall()

    # -- EDINET ---------------------------------------------------------------

    def upsert_edinet_large_holding(self, record: dict) -> None:
        self._upsert("edinet_large_holdings", EDINET_LARGE_HOLDING_COLUMNS, ("doc_id",), record)

    def get_edinet_large_holdings(self, code: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM edinet_large_holdings WHERE code = ? ORDER BY submission_date DESC",
                (code,),
            ).fetchall()

    def get_latest_edinet_date(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(date) FROM edinet_large_holdings").fetchone()
            return row[0] if row else None

    def get_recent_large_holdings(self, days: int = 30) -> list[sqlite3.Row]:
        """直近days日以内に提出された大量保有報告書(EDINET)を返す"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM edinet_large_holdings WHERE date >= ? ORDER BY date DESC",
                (cutoff,),
            ).fetchall()

    # -- 空売り残高(機関投資家の個別ポジション) --------------------------------

    def get_latest_short_positions_date(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(date) FROM jpx_short_positions").fetchone()
            return row[0] if row else None

    def get_short_positions_summary(self, date: str | None = None) -> list[sqlite3.Row]:
        """コード別の機関投資家空売りポジション集計(合計比率・保有機関数)を、
        指定日(省略時は最新日)で返す"""
        if date is None:
            date = self.get_latest_short_positions_date()
        if date is None:
            return []
        with self._connect() as conn:
            return conn.execute(
                "SELECT code, SUM(short_position_ratio) AS total_ratio, COUNT(*) AS holder_count "
                "FROM jpx_short_positions WHERE date = ? GROUP BY code ORDER BY total_ratio DESC",
                (date,),
            ).fetchall()
