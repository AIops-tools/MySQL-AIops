"""Table-health MySQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import tables as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def table_sizes(limit: int = 20, target: Optional[str] = None) -> dict:
    """[READ] Largest tables by data + index size.

    Args:
        limit: Number of tables to return, largest first (default 20).
        target: Target name from config; omit for the default.

    Returns an envelope: {"tables": [...], "returned": N, "limit": L,
    "truncated": bool}. When "truncated" is true there is more beyond what
    was returned — re-run with a higher limit rather than treating this as
    the complete picture.
    """
    return ops.table_sizes(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def table_fragmentation(limit: int = 50, target: Optional[str] = None) -> dict:
    """[READ] data_free per table (space OPTIMIZE TABLE could reclaim), worst first.

    Args:
        limit: Number of tables to inspect (default 50).
        target: Target name from config; omit for the default.

    Returns an envelope: {"tables": [...], "returned": N, "limit": L,
    "truncated": bool}. When "truncated" is true there is more beyond what
    was returned — re-run with a higher limit rather than treating this as
    the complete picture.
    """
    return ops.table_fragmentation(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def table_status(limit: int = 50, target: Optional[str] = None) -> dict:
    """[READ] Per-table engine, row format, row estimate and last update time.

    Flags non-InnoDB tables (no row-level locking / crash recovery).

    Args:
        limit: Number of tables to inspect (default 50).
        target: Target name from config; omit for the default.

    Returns an envelope: {"tables": [...], "returned": N, "limit": L,
    "truncated": bool}. When "truncated" is true there is more beyond what
    was returned — re-run with a higher limit rather than treating this as
    the complete picture.
    """
    return ops.table_status(_get_connection(target), limit=limit)
