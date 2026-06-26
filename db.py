import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "monintion.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS disk_snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                device    TEXT NOT NULL,
                mountpoint TEXT NOT NULL,
                total_gb  REAL,
                used_gb   REAL,
                free_gb   REAL,
                percent   REAL
            );

            CREATE TABLE IF NOT EXISTS ram_snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                total_gb  REAL,
                used_gb   REAL,
                free_gb   REAL,
                percent   REAL
            );

            CREATE TABLE IF NOT EXISTS cpu_snapshots (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
                percent   REAL
            );

        """)


if __name__ == "__main__":
    init_db()
    print(f"DB inicializado em: {DB_PATH}")
