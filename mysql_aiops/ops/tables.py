"""Table reads: sizes, fragmentation (data_free), and table status.

Fragmentation here is an honest ``data_free`` proxy from
``information_schema.tables`` (free space inside the tablespace that OPTIMIZE
TABLE can reclaim) — it needs no plugin and directly drives the flagship
``fragmentation_analysis``.
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops._util import human_bytes, opt

_SYSTEM_SCHEMAS = "('mysql', 'information_schema', 'performance_schema', 'sys')"

_SIZES_SQL = f"""
SELECT table_schema AS `schema`,
       table_name AS `table`,
       engine,
       table_rows AS est_rows,
       data_length AS data_bytes,
       index_length AS index_bytes,
       (data_length + index_length) AS total_bytes
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema NOT IN {_SYSTEM_SCHEMAS}
ORDER BY (data_length + index_length) DESC
LIMIT %(limit)s
"""  # nosec B608 — schema list is a static constant

_FRAGMENTATION_SQL = f"""
SELECT table_schema AS `schema`,
       table_name AS `table`,
       engine,
       table_rows AS est_rows,
       data_length AS data_bytes,
       index_length AS index_bytes,
       data_free AS free_bytes,
       CASE WHEN (data_length + index_length) > 0
            THEN ROUND(100.0 * data_free / (data_length + index_length + data_free), 2)
            ELSE 0 END AS free_pct
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema NOT IN {_SYSTEM_SCHEMAS}
ORDER BY data_free DESC
LIMIT %(limit)s
"""  # nosec B608 — schema list is a static constant

_STATUS_SQL = f"""
SELECT table_schema AS `schema`,
       table_name AS `table`,
       engine,
       row_format,
       table_rows AS est_rows,
       avg_row_length,
       auto_increment,
       create_time,
       update_time,
       table_collation
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
  AND table_schema NOT IN {_SYSTEM_SCHEMAS}
ORDER BY (data_length + index_length) DESC
LIMIT %(limit)s
"""  # nosec B608 — schema list is a static constant


def table_sizes(conn: Any, limit: int = 20) -> dict:
    """[READ] Largest tables by data + index size."""
    want = max(1, min(int(limit), 500))
    rows = conn.query(_SIZES_SQL, {"limit": want + 1})  # +1 measures truncation
    truncated = len(rows) > want
    rows = rows[:want]
    tables = [
        {
            "schema": opt(r.get("schema"), 128),
            "table": opt(r.get("table"), 128),
            "engine": opt(r.get("engine"), 32),
            "estRows": r.get("est_rows"),
            "dataBytes": r.get("data_bytes"),
            "indexBytes": r.get("index_bytes"),
            "totalBytes": r.get("total_bytes"),
            "totalPretty": human_bytes(r.get("total_bytes")),
        }
        for r in rows
    ]
    return {
        "tables": tables,
        "returned": len(tables),
        "limit": want,
        "truncated": truncated,
    }


def _fragmentation_row(r: dict) -> dict:
    return {
        "schema": opt(r.get("schema"), 128),
        "table": opt(r.get("table"), 128),
        "engine": opt(r.get("engine"), 32),
        "estRows": r.get("est_rows"),
        "dataBytes": r.get("data_bytes"),
        "indexBytes": r.get("index_bytes"),
        "freeBytes": r.get("free_bytes"),
        "freePct": float(r.get("free_pct") or 0),
        "freePretty": human_bytes(r.get("free_bytes")),
    }


def table_fragmentation(conn: Any, limit: int = 50) -> dict:
    """[READ] data_free per table (space OPTIMIZE TABLE could reclaim), worst first."""
    want = max(1, min(int(limit), 500))
    rows = conn.query(_FRAGMENTATION_SQL, {"limit": want + 1})  # +1 measures truncation
    truncated = len(rows) > want
    tables = [_fragmentation_row(r) for r in rows[:want]]
    return {
        "tables": tables,
        "returned": len(tables),
        "limit": want,
        "truncated": truncated,
        "note": (
            "freePct = data_free / (data + index + free) from "
            "information_schema.tables — a fragmentation proxy. OPTIMIZE TABLE "
            "rebuilds the table and reclaims the free space (locks briefly; "
            "InnoDB uses online DDL where possible)."
        ),
    }


def table_status(conn: Any, limit: int = 50) -> dict:
    """[READ] Per-table engine, row format, row estimate and last update time."""
    want = max(1, min(int(limit), 500))
    rows = conn.query(_STATUS_SQL, {"limit": want + 1})  # +1 measures truncation
    truncated = len(rows) > want
    rows = rows[:want]
    tables = [
        {
            "schema": opt(r.get("schema"), 128),
            "table": opt(r.get("table"), 128),
            "engine": opt(r.get("engine"), 32),
            "rowFormat": opt(r.get("row_format"), 32),
            "estRows": r.get("est_rows"),
            "avgRowLength": r.get("avg_row_length"),
            "autoIncrement": r.get("auto_increment"),
            "createTime": opt(r.get("create_time"), 64),
            "updateTime": opt(r.get("update_time"), 64),
            "collation": opt(r.get("table_collation"), 64),
        }
        for r in rows
    ]
    non_innodb = [t["table"] for t in tables
                  if t["engine"] and t["engine"].lower() != "innodb"]
    return {
        "nonInnodbTables": non_innodb,
        "tables": tables,
        "returned": len(tables),
        "limit": want,
        "truncated": truncated,
    }
