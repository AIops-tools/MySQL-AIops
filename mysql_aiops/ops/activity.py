"""Activity reads: sessions (processlist), long-running queries, InnoDB lock waits.

Every query surfaced to an agent is truncated in SQL (``LEFT(info, N)``) and
sanitised again via ``s`` — a running statement can contain arbitrary text.
Thresholds (seconds) are passed as bound parameters, never string-formatted.

The lock-wait read branches by flavor: MySQL 8.x exposes the wait-for graph in
``performance_schema.data_lock_waits``; MariaDB keeps the older
``information_schema.innodb_lock_waits``.
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops._util import opt

_PROCESSLIST_SQL = """
SELECT id,
       user,
       host,
       db,
       command,
       time AS age_seconds,
       state,
       LEFT(info, 500) AS query
FROM information_schema.processlist
WHERE id <> CONNECTION_ID()
ORDER BY time DESC
"""

_LONG_RUNNING_SQL = """
SELECT id,
       user,
       host,
       db,
       command,
       time AS duration_seconds,
       state,
       LEFT(info, 500) AS query
FROM information_schema.processlist
WHERE id <> CONNECTION_ID()
  AND command NOT IN ('Sleep', 'Binlog Dump', 'Binlog Dump GTID', 'Daemon')
  AND info IS NOT NULL
  AND time >= %(min_seconds)s
ORDER BY time DESC
"""

# MySQL 8.x wait-for edges: waiting trx -> blocking trx, joined to the running
# statements. One row per (blocked session -> a session blocking it).
_LOCK_WAITS_MYSQL_SQL = """
SELECT wt.trx_mysql_thread_id AS blocked_id,
       wt.trx_id AS blocked_trx,
       LEFT(wt.trx_query, 300) AS blocked_query,
       wt.trx_wait_started AS wait_started,
       TIMESTAMPDIFF(SECOND, wt.trx_wait_started, NOW()) AS wait_seconds,
       bt.trx_mysql_thread_id AS blocking_id,
       bt.trx_id AS blocking_trx,
       LEFT(bt.trx_query, 300) AS blocking_query,
       bt.trx_state AS blocking_state,
       dlw.OBJECT_SCHEMA AS object_schema,
       dlw.OBJECT_NAME AS object_name
FROM performance_schema.data_lock_waits w
JOIN performance_schema.data_locks dlw
  ON dlw.ENGINE_LOCK_ID = w.REQUESTING_ENGINE_LOCK_ID
JOIN information_schema.innodb_trx wt
  ON wt.trx_id = w.REQUESTING_ENGINE_TRANSACTION_ID
JOIN information_schema.innodb_trx bt
  ON bt.trx_id = w.BLOCKING_ENGINE_TRANSACTION_ID
"""

# MariaDB keeps the pre-8.0 lock-wait view in information_schema.
_LOCK_WAITS_MARIADB_SQL = """
SELECT wt.trx_mysql_thread_id AS blocked_id,
       wt.trx_id AS blocked_trx,
       LEFT(wt.trx_query, 300) AS blocked_query,
       wt.trx_wait_started AS wait_started,
       TIMESTAMPDIFF(SECOND, wt.trx_wait_started, NOW()) AS wait_seconds,
       bt.trx_mysql_thread_id AS blocking_id,
       bt.trx_id AS blocking_trx,
       LEFT(bt.trx_query, 300) AS blocking_query,
       bt.trx_state AS blocking_state,
       NULL AS object_schema,
       NULL AS object_name
FROM information_schema.innodb_lock_waits w
JOIN information_schema.innodb_trx wt ON wt.trx_id = w.requesting_trx_id
JOIN information_schema.innodb_trx bt ON bt.trx_id = w.blocking_trx_id
"""

_TRX_SQL = """
SELECT trx_id,
       trx_mysql_thread_id AS thread_id,
       trx_state,
       trx_started,
       TIMESTAMPDIFF(SECOND, trx_started, NOW()) AS age_seconds,
       trx_rows_locked,
       trx_rows_modified,
       LEFT(trx_query, 300) AS query
FROM information_schema.innodb_trx
ORDER BY trx_started ASC
"""


def _session_row(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "user": opt(r.get("user"), 128),
        "host": opt(r.get("host"), 128),
        "database": opt(r.get("db"), 128),
        "command": opt(r.get("command"), 64),
        "ageSeconds": r.get("age_seconds"),
        "state": opt(r.get("state"), 128),
        "query": opt(r.get("query"), 500),
    }


def list_sessions(conn: Any, include_sleeping: bool = True) -> dict:
    """[READ] Current sessions from the processlist, with per-command counts.

    Flags long-idle sessions in ``Sleep`` (connection-pool leak suspects).
    """
    rows = conn.query(_PROCESSLIST_SQL)
    sessions = [_session_row(r) for r in rows]
    if not include_sleeping:
        sessions = [r for r in sessions if r["command"] != "Sleep"]
    by_command: dict[str, int] = {}
    for r in sessions:
        key = r["command"] or "unknown"
        by_command[key] = by_command.get(key, 0) + 1
    sleeping = [r for r in sessions if r["command"] == "Sleep"]
    return {
        "total": len(sessions),
        "byCommand": dict(sorted(by_command.items(), key=lambda kv: kv[1], reverse=True)),
        "sleepingCount": len(sleeping),
        "sessions": sessions,
    }


def long_running_queries(conn: Any, min_seconds: int = 60) -> dict:
    """[READ] Active statements running at least ``min_seconds``, oldest first."""
    rows = conn.query(_LONG_RUNNING_SQL, {"min_seconds": int(min_seconds)})
    queries = [
        {
            "id": r.get("id"),
            "user": opt(r.get("user"), 128),
            "database": opt(r.get("db"), 128),
            "command": opt(r.get("command"), 64),
            "durationSeconds": r.get("duration_seconds"),
            "state": opt(r.get("state"), 128),
            "query": opt(r.get("query"), 500),
        }
        for r in rows
    ]
    return {
        "thresholdSeconds": int(min_seconds),
        "count": len(queries),
        "queries": queries,
    }


def list_transactions(conn: Any) -> dict:
    """[READ] Open InnoDB transactions, oldest first (stuck-transaction hunting)."""
    rows = conn.query(_TRX_SQL)
    transactions = [
        {
            "trxId": opt(r.get("trx_id"), 32),
            "threadId": r.get("thread_id"),
            "state": opt(r.get("trx_state"), 32),
            "started": opt(r.get("trx_started"), 64),
            "ageSeconds": r.get("age_seconds"),
            "rowsLocked": r.get("trx_rows_locked"),
            "rowsModified": r.get("trx_rows_modified"),
            "query": opt(r.get("query"), 300),
        }
        for r in rows
    ]
    lock_wait = [t for t in transactions if t["state"] == "LOCK WAIT"]
    return {
        "count": len(transactions),
        "lockWaitCount": len(lock_wait),
        "transactions": transactions,
    }


def lock_wait_pairs(conn: Any) -> list[dict]:
    """[READ] Wait-for edges (blocked session -> blocking session), flavor-branched.

    MySQL 8.x reads ``performance_schema.data_lock_waits``; MariaDB reads
    ``information_schema.innodb_lock_waits``. Both shapes normalise to the same
    pair records the flagship lock-wait RCA walks.
    """
    flavor = getattr(conn, "flavor", "mysql")
    sql = _LOCK_WAITS_MARIADB_SQL if flavor == "mariadb" else _LOCK_WAITS_MYSQL_SQL
    rows = conn.query(sql)
    return [
        {
            "blockedId": r.get("blocked_id"),
            "blockedTrx": opt(r.get("blocked_trx"), 32),
            "blockedQuery": opt(r.get("blocked_query"), 300),
            "waitSeconds": r.get("wait_seconds"),
            "blockingId": r.get("blocking_id"),
            "blockingTrx": opt(r.get("blocking_trx"), 32),
            "blockingQuery": opt(r.get("blocking_query"), 300),
            "blockingState": opt(r.get("blocking_state"), 32),
            "objectSchema": opt(r.get("object_schema"), 128),
            "objectName": opt(r.get("object_name"), 128),
        }
        for r in rows
    ]
