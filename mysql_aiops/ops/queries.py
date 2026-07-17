"""Query-statistics reads: statement-digest top-N, EXPLAIN, and stats reset.

``top_queries`` reads ``performance_schema.events_statements_summary_by_digest``
and orders by a whitelisted column only (never raw caller text). Timer columns
are picoseconds; they are converted to milliseconds in SQL for transparency.

``explain_query`` must interpolate the statement to EXPLAIN — it cannot be a
bound parameter — so the statement is validated (single statement, no injected
terminator) and the one interpolation site is a single-line f-string.
"""

from __future__ import annotations

import json
from typing import Any

from mysql_aiops.ops._util import order_column, s

_TOP_SQL = """
SELECT SCHEMA_NAME AS schema_name,
       DIGEST AS digest,
       LEFT(DIGEST_TEXT, 400) AS digest_text,
       COUNT_STAR AS calls,
       ROUND(SUM_TIMER_WAIT / 1000000000, 2) AS total_time_ms,
       ROUND(AVG_TIMER_WAIT / 1000000000, 2) AS mean_time_ms,
       ROUND(SUM_LOCK_TIME / 1000000000, 2) AS lock_time_ms,
       SUM_ROWS_EXAMINED AS rows_examined,
       SUM_ROWS_SENT AS rows_sent,
       SUM_ROWS_AFFECTED AS rows_affected,
       SUM_NO_INDEX_USED AS no_index_used,
       SUM_NO_GOOD_INDEX_USED AS no_good_index_used,
       SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
       SUM_SORT_MERGE_PASSES AS sort_merge_passes,
       FIRST_SEEN AS first_seen,
       LAST_SEEN AS last_seen
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME IS NOT NULL
ORDER BY {col} DESC
LIMIT %(limit)s
"""

_MAX_STATEMENT_LEN = 100_000


def _statement_row(r: dict) -> dict:
    calls = int(r.get("calls") or 0)
    no_index = int(r.get("no_index_used") or 0)
    examined = int(r.get("rows_examined") or 0)
    sent = int(r.get("rows_sent") or 0)
    total_ms = float(r.get("total_time_ms") or 0)
    lock_ms = float(r.get("lock_time_ms") or 0)
    return {
        "schema": s(r.get("schema_name"), 128),
        "digest": s(r.get("digest"), 64),
        "query": s(r.get("digest_text"), 400),
        "calls": calls,
        "totalTimeMs": total_ms,
        "meanTimeMs": float(r.get("mean_time_ms") or 0),
        "lockTimeMs": lock_ms,
        "lockTimePct": round(100.0 * lock_ms / total_ms, 1) if total_ms else 0.0,
        "rowsExamined": examined,
        "rowsSent": sent,
        "rowsExaminedPerSent": round(examined / sent, 1) if sent else None,
        "noIndexUsedCount": no_index,
        "noIndexUsedPct": round(100.0 * no_index / calls, 1) if calls else 0.0,
        "tmpDiskTables": r.get("tmp_disk_tables"),
        "sortMergePasses": r.get("sort_merge_passes"),
        "firstSeen": s(r.get("first_seen"), 64),
        "lastSeen": s(r.get("last_seen"), 64),
    }


def top_queries(conn: Any, order_by: str = "total_time", limit: int = 20) -> dict:
    """[READ] Top statement digests from performance_schema by a whitelisted metric.

    ``order_by`` is one of total_time, mean_time, calls, rows_examined,
    lock_time, no_index — mapped to a real column through a whitelist, so no
    caller text ever reaches the ORDER BY.
    """
    col = order_column(order_by)  # validated → safe to interpolate below
    sql = _TOP_SQL.format(col=col)  # nosec B608 — col is whitelisted, not user text
    rows = conn.query(sql, {"limit": max(1, min(int(limit), 200))})
    return {
        "orderBy": order_by,
        "count": len(rows),
        "statements": [_statement_row(r) for r in rows],
        "note": (
            "Requires performance_schema=ON. Times are milliseconds (converted "
            "from picosecond timers); noIndexUsedPct is the share of executions "
            "that used no index."
        ),
    }


def _validate_statement(sql: str) -> str:
    """Reject empty/multi-statement input so EXPLAIN interpolation is bounded."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise ValueError("No SQL statement supplied to EXPLAIN.")
    if len(text) > _MAX_STATEMENT_LEN:
        raise ValueError("Statement too long to EXPLAIN.")
    if ";" in text:
        raise ValueError(
            "Only a single statement may be EXPLAINed (embedded ';' rejected)."
        )
    return text


def explain_query(conn: Any, sql: str) -> dict:
    """[READ] Return the JSON execution plan for ``sql`` (EXPLAIN FORMAT=JSON).

    EXPLAIN plans without executing the statement. The statement is validated to
    be a single statement before it is placed into the EXPLAIN command.
    """
    statement = _validate_statement(sql)
    command = f"EXPLAIN FORMAT=JSON {statement}"  # nosec B608 — validated single statement
    row = conn.query_one(command) or {}
    plan_raw = next(iter(row.values()), None) if row else None
    plan = plan_raw
    if isinstance(plan_raw, str):
        try:
            plan = json.loads(plan_raw)
        except ValueError:
            plan = {"raw": s(plan_raw, 4000)}
    return {
        "plan": plan,
        "note": "EXPLAIN FORMAT=JSON plan; the statement is planned, not executed.",
    }


def reset_query_stats(conn: Any) -> dict:
    """[WRITE] Reset statement-digest accumulators (TRUNCATE the digest table).

    Irreversible — the counters cannot be restored — so no undo is recorded.
    """
    conn.execute("TRUNCATE TABLE performance_schema.events_statements_summary_by_digest")
    return {"action": "reset_query_stats", "reset": True}
