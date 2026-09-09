"""SQLiteテーブルの自己修復マイグレーション共通処理"""
import sqlite3


def ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    """テーブルに存在しないカラムを ALTER TABLE で追加する（新カラムは常にNULL許容）"""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
