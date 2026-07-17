"""Index-health MySQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import indexes as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def unused_indexes(target: Optional[str] = None) -> dict:
    """[READ] Secondary indexes with zero I/O events since restart (drop candidates).

    From performance_schema.table_io_waits_summary_by_index_usage — counters
    reset on server restart, so confirm over a full business cycle.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.unused_indexes(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def redundant_indexes(target: Optional[str] = None) -> dict:
    """[READ] Indexes whose columns are a leading prefix of another index (dupes).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.redundant_indexes(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def index_stats(target: Optional[str] = None) -> dict:
    """[READ] Per-index column lists and cardinality (selectivity screening).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.index_stats(_get_connection(target))
