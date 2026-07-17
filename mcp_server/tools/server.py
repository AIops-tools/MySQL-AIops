"""Server-level MySQL MCP tools (read-only): overview + catalog reads."""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from mysql_aiops.governance import governed_tool
from mysql_aiops.ops import overview as overview_ops
from mysql_aiops.ops import server as ops


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def overview(target: Optional[str] = None) -> dict:
    """[READ] One-shot server health snapshot.

    Version + flavor (mysql/mariadb) + uptime, connection headroom vs
    max_connections, sessions by command, the longest-running query, the most
    fragmented table, and the replica role — each section captured defensively
    so one failing probe does not sink the rest.

    Args:
        target: Target name from config; omit for the default.
    """
    return overview_ops.snapshot(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def server_version(target: Optional[str] = None) -> dict:
    """[READ] Server version, flavor (mysql/mariadb), uptime and read-only state.

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.server_version(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def show_variables(pattern: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Global variables (SHOW GLOBAL VARIABLES).

    Args:
        pattern: Optional substring to filter variable names
            (e.g. 'innodb_buffer', 'max_connections').
        target: Target name from config; omit for the default.
    """
    return ops.show_variables(_get_connection(target), pattern)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def show_status(pattern: Optional[str] = None, target: Optional[str] = None) -> list:
    """[READ] Global status counters (SHOW GLOBAL STATUS).

    Args:
        pattern: Optional substring to filter counter names
            (e.g. 'Threads', 'Innodb_buffer_pool').
        target: Target name from config; omit for the default.
    """
    return ops.show_status(_get_connection(target), pattern)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def list_databases(target: Optional[str] = None) -> list:
    """[READ] User schemas with table count and data/index size (largest first).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_databases(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("list")
def list_engines(target: Optional[str] = None) -> list:
    """[READ] Storage engines and which is the default (SHOW ENGINES).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.list_engines(_get_connection(target))


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def connection_stats(target: Optional[str] = None) -> dict:
    """[READ] Connection counters vs max_connections (exhaustion early warning).

    Args:
        target: Target name from config; omit for the default.
    """
    return ops.connection_stats(_get_connection(target))
