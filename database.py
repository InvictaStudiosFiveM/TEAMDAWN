import sqlite3
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS members (
                user_id      INTEGER PRIMARY KEY,
                display_name TEXT,
                city_name    TEXT,
                city_id      TEXT,
                channel_id   INTEGER,
                active       INTEGER NOT NULL DEFAULT 1,
                joined_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS sales (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                client_id  TEXT,
                item       TEXT,
                amount     REAL NOT NULL,
                message_id INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.conn.commit()

    # ---------- members
    def recruit_member(self, user_id, display_name, city_name, city_id, channel_id):
        self.conn.execute(
            """
            INSERT INTO members (user_id, display_name, city_name, city_id, channel_id, active, joined_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name,
                city_name    = excluded.city_name,
                city_id      = excluded.city_id,
                channel_id   = excluded.channel_id,
                active       = 1
            """,
            (user_id, display_name, city_name, city_id, channel_id, _now()),
        )
        self.conn.commit()

    def ensure_member(self, user_id, display_name):
        """Make sure someone logging a sale shows on the leaderboard."""
        self.conn.execute(
            """
            INSERT INTO members (user_id, display_name, active, joined_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET display_name = excluded.display_name, active = 1
            """,
            (user_id, display_name, _now()),
        )
        self.conn.commit()

    def get_member(self, user_id):
        return self.conn.execute("SELECT * FROM members WHERE user_id = ?", (user_id,)).fetchone()

    def deactivate_member(self, user_id):
        self.conn.execute("UPDATE members SET active = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    # ---------- sales
    def add_sale(self, user_id, client_id, item, amount, message_id):
        self.conn.execute(
            "INSERT INTO sales (user_id, client_id, item, amount, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, client_id, item, amount, message_id, _now()),
        )
        self.conn.commit()

    def sales_summary(self, user_id):
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM sales WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["n"], row["total"]

    def clear_member(self, user_id):
        """Delete all of someone's sales and take them off the leaderboard."""
        self.conn.execute("DELETE FROM sales WHERE user_id = ?", (user_id,))
        self.conn.execute("UPDATE members SET active = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def leaderboard(self, limit):
        return self.conn.execute(
            """
            SELECT m.user_id, m.display_name, m.city_name, m.city_id,
                   COALESCE(SUM(s.amount), 0) AS total
            FROM members m
            LEFT JOIN sales s ON s.user_id = m.user_id
            WHERE m.active = 1
            GROUP BY m.user_id
            ORDER BY total DESC, m.joined_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    # ---------- settings
    def get_setting(self, key):
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self.conn.commit()
