"""Activity MySQL MCP tools (read-only): sessions, long queries, transactions, lock waits."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import activity as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_sessions(include_sleeping: bool = True, target: Optional[str] = None) -> dict:
    """[READ] Current sessions (processlist) with per-command counts.

    Flags sleeping sessions (connection-pool leak suspects).

    Args:
        include_sleeping: Include sessions in command=Sleep (default True).
        target: Target name from config; omit for the default.
    """
    return ops.list_sessions(_get_connection(target), include_sleeping=include_sleeping)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def long_running_queries(min_seconds: int = 60, target: Optional[str] = None) -> dict:
    """[READ] Active statements running at least ``min_seconds``, oldest first.

    Args:
        min_seconds: Minimum age in seconds (default 60).
        target: Target name from config; omit for the default.
    """
    return ops.long_running_queries(_get_connection(target), min_seconds=min_seconds)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def list_transactions(target: Optional[str] = None) -> dict:
    """[READ] Open InnoDB transactions, oldest first (stuck-transaction hunting).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_transactions(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def lock_waits(target: Optional[str] = None) -> list:
    """[READ] InnoDB wait-for edges (blocked session -> blocking session).

    Reads performance_schema.data_lock_waits on MySQL 8.x, or
    information_schema.innodb_lock_waits on MariaDB (flavor-branched).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.lock_wait_pairs(_get_connection(target))
