"""Query-statistics MySQL MCP tools: top-N, EXPLAIN (read) + stats reset (write)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import queries as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def top_queries(
    order_by: str = "total_time",
    limit: int = 20,
    target: Optional[str] = None,
) -> dict:
    """[READ] Top statement digests from performance_schema by a whitelisted metric.

    Args:
        order_by: One of total_time, mean_time, calls, rows_examined,
            lock_time, no_index.
        limit: Number of statements to return (1..200, default 20).
        target: Target name from config; omit for the default.

    Returns an envelope: {"statements": [...], "returned": N, "limit": L,
    "truncated": bool}. When "truncated" is true there is more beyond what
    was returned — re-run with a higher limit rather than treating this as
    the complete picture.
    """
    return ops.top_queries(_get_connection(target), order_by=order_by, limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def explain_query(sql: str, target: Optional[str] = None) -> dict:
    """[READ] Return the JSON execution plan for ``sql`` (EXPLAIN FORMAT=JSON).

    The statement is planned, not executed.

    Args:
        sql: A single SQL statement to EXPLAIN.
        target: Target name from config; omit for the default.
    """
    return ops.explain_query(_get_connection(target), sql)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def reset_query_stats(dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=medium] Reset statement-digest accumulators (irreversible).

    Truncates performance_schema.events_statements_summary_by_digest; the
    counters cannot be restored, so no undo is recorded. Pass dry_run=True
    to preview.

    Args:
        dry_run: If True, preview without resetting.
        target: Target name from config; omit for the default.
    """
    if dry_run:
        return {"dryRun": True, "wouldReset": "events_statements_summary_by_digest"}
    return ops.reset_query_stats(_get_connection(target))
