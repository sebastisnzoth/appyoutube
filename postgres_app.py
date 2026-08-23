import os
import re
import sqlite3

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row

    class CursorProxy:
        def __init__(self, cursor, lastrowid=None):
            self._cursor = cursor
            self.lastrowid = lastrowid

        def fetchone(self):
            return self._cursor.fetchone()

        def fetchall(self):
            return self._cursor.fetchall()

        def __iter__(self):
            return iter(self._cursor)

    class ConnectionProxy:
        def __init__(self):
            self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
            self.row_factory = None

        def execute(self, sql, params=()):
            stripped = sql.strip()

            pragma = re.fullmatch(r"PRAGMA\s+table_info\(([^)]+)\)", stripped, flags=re.IGNORECASE)
            if pragma:
                table = pragma.group(1).strip().strip('"\'')
                cur = self._conn.cursor(row_factory=dict_row)
                cur.execute(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
                return CursorProxy(cur)

            sql = re.sub(
                r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                "BIGSERIAL PRIMARY KEY",
                sql,
                flags=re.IGNORECASE,
            )

            if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+radar_run_channels", sql, flags=re.IGNORECASE):
                sql = re.sub(
                    r"INSERT\s+OR\s+REPLACE\s+INTO",
                    "INSERT INTO",
                    sql,
                    count=1,
                    flags=re.IGNORECASE,
                )
                sql += (
                    " ON CONFLICT (run_id, channel_id) DO UPDATE SET "
                    "position=EXCLUDED.position, channel_score=EXCLUDED.channel_score, "
                    "momentum=EXCLUDED.momentum, outliers=EXCLUDED.outliers, "
                    "audience_efficiency=EXCLUDED.audience_efficiency, freshness=EXCLUDED.freshness, "
                    "consistency=EXCLUDED.consistency, observed_growth_per_day=EXCLUDED.observed_growth_per_day, "
                    "confidence_score=EXCLUDED.confidence_score, confidence_label=EXCLUDED.confidence_label, "
                    "created_at=EXCLUDED.created_at"
                )

            sql = sql.replace("?", "%s")
            wants_lastrowid = bool(re.match(r"\s*INSERT\s+INTO\s+radar_runs\b", sql, flags=re.IGNORECASE))
            if wants_lastrowid and "RETURNING" not in sql.upper():
                sql += " RETURNING id"

            cur = self._conn.cursor(row_factory=dict_row)
            cur.execute(sql, params)
            lastrowid = None
            if wants_lastrowid:
                row = cur.fetchone()
                if row:
                    lastrowid = row["id"]
            return CursorProxy(cur, lastrowid=lastrowid)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            self._conn.close()

    def _postgres_connect(*args, **kwargs):
        return ConnectionProxy()

    sqlite3.connect = _postgres_connect

from entry import app
