"""Read-only Oracle access. Every query runs with CLIENT_IDENTIFIER = the JDE user,
which the JDEAGENT_OWN views use for row scoping (fail closed when unset)."""
from __future__ import annotations

import oracledb

from .config import Settings

_pool: oracledb.ConnectionPool | None = None


def pool(s: Settings) -> oracledb.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = oracledb.create_pool(user=s.db_user, password=s.db_password, dsn=s.dsn,
                                     min=1, max=4, increment=1)
    return _pool


def query(s: Settings, jde_user: str, sql: str, params: dict | None = None,
          max_rows: int = 500) -> list[dict]:
    with pool(s).acquire() as conn:
        conn.client_identifier = jde_user
        conn.module, conn.action = "jde-planner-agent", "read"
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [d[0].lower() for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchmany(max_rows)]
