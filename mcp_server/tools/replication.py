"""Replication MySQL MCP tools (read-only)."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import replication as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def replica_status(target: Optional[str] = None) -> dict:
    """[READ] Replica thread state and lag (SHOW REPLICA/SLAVE STATUS, flavor-branched).

    Empty on a primary/standalone server. MySQL 8.x uses SHOW REPLICA STATUS;
    MariaDB uses SHOW SLAVE STATUS — the result is normalised either way.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.replica_status(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def binlog_status(target: Optional[str] = None) -> dict:
    """[READ] Binary-log configuration, GTID mode, and connected downstream replicas.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.binlog_status(_get_connection(target))
