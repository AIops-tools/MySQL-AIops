"""Flagship MySQL analysis MCP tools (read-only)."""

from typing import Any, Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import activity as activity_ops
from mysql_aiops.ops import analysis as ops
from mysql_aiops.ops import queries as query_ops
from mysql_aiops.ops import replication as replication_ops
from mysql_aiops.ops import tables as table_ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def slow_query_rca(
    statements: Optional[list[dict[str, Any]]] = None,
    explain_sql: Optional[str] = None,
    limit: int = 20,
    target: Optional[str] = None,
) -> dict:
    """[READ] RCA for the worst statement digest, with cause + action.

    Picks the digest with the greatest total time and maps its numbers
    (no-index share, lock-time share, examined/sent ratio, tmp-disk spill,
    calls) — plus an optional EXPLAIN plan — to cited causes and concrete
    actions. Pass 'statements' for pure/offline analysis, or omit to pull the
    top digests live from performance_schema.

    Args:
        statements: Injected digest rows (as from top_queries); if omitted,
            the worst statements are pulled live.
        explain_sql: Optional SQL to EXPLAIN so plan access types feed the RCA.
        limit: How many statements to pull when not injected (default 20).
        target: Target name from config; omit for the default.
    """
    conn = None
    if statements is None:
        conn = _get_connection(target)
        statements = query_ops.top_queries(conn, order_by="total_time", limit=limit)["statements"]
    explain = None
    if explain_sql:
        conn = conn or _get_connection(target)
        explain = query_ops.explain_query(conn, explain_sql)
    return ops.slow_query_rca(statements, explain=explain)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def lock_wait_rca(
    pairs: Optional[list[dict[str, Any]]] = None,
    include_deadlock: bool = True,
    target: Optional[str] = None,
) -> dict:
    """[READ] Build the InnoDB wait-for tree, name the root blocker, and parse
    the last deadlock out of SHOW ENGINE INNODB STATUS.

    Pass 'pairs' (as from lock_waits) for pure/offline analysis, or omit to
    pull the current lock-wait graph live.

    Args:
        pairs: Injected lock-wait pairs {blockedId, blockingId, ...}; if
            omitted, pulled live (flavor-branched).
        include_deadlock: Also parse the LATEST DETECTED DEADLOCK section from
            SHOW ENGINE INNODB STATUS (default True).
        target: Target name from config; omit for the default.
    """
    conn = None
    if pairs is None:
        conn = _get_connection(target)
        pairs = activity_ops.lock_wait_pairs(conn)
    innodb_status = None
    if include_deadlock:
        conn = conn or _get_connection(target)
        row = conn.query_one("SHOW ENGINE INNODB STATUS") or {}
        innodb_status = str(row.get("Status") or "")
    return ops.lock_wait_rca(pairs, innodb_status=innodb_status)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def replication_lag_rca(
    status: Optional[dict[str, Any]] = None,
    target: Optional[str] = None,
) -> dict:
    """[READ] Map replica thread state / lag / error fields to cause + action.

    Pass 'status' (as from replica_status) for pure/offline analysis, or omit
    to pull the live replica status (flavor-branched).

    Args:
        status: Injected replica status record; if omitted, pulled live.
        target: Target name from config; omit for the default.
    """
    if status is None:
        status = replication_ops.replica_status(_get_connection(target))
    return ops.replication_lag_rca(status)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def fragmentation_analysis(
    tables: Optional[list[dict[str, Any]]] = None,
    limit: int = 50,
    target: Optional[str] = None,
) -> dict:
    """[READ] Rank tables by reclaimable data_free into OPTIMIZE TABLE candidates.

    Pass 'tables' (as from table_fragmentation) for pure/offline analysis, or
    omit to pull the worst-fragmented tables live. Each recommendation cites
    its numbers.

    Args:
        tables: Injected fragmentation rows; if omitted, pulled live.
        limit: How many tables to pull when not injected (default 50).
        target: Target name from config; omit for the default.
    """
    if tables is None:
        tables = table_ops.table_fragmentation(_get_connection(target), limit=limit)["tables"]
    return ops.fragmentation_analysis(tables)
