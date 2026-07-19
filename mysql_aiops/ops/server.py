"""Server-level reads: version + flavor, variables, status, databases, engines.

All read-only queries against ``information_schema`` and ``SHOW`` commands.
Values that could be large or caller-influenced (variable values) are bounded
via ``s`` before returning. The **flavor** (mysql vs mariadb) is reported so
agents and flavor-dependent reads can branch.
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops._util import human_bytes, opt, s

_DATABASES_SQL = """
SELECT t.table_schema AS name,
       COUNT(*) AS table_count,
       COALESCE(SUM(t.data_length), 0) AS data_bytes,
       COALESCE(SUM(t.index_length), 0) AS index_bytes,
       COALESCE(SUM(t.data_length + t.index_length), 0) AS total_bytes
FROM information_schema.tables t
WHERE t.table_schema NOT IN
      ('mysql', 'information_schema', 'performance_schema', 'sys')
GROUP BY t.table_schema
ORDER BY total_bytes DESC
"""


def server_version(conn: Any) -> dict:
    """[READ] Server version, flavor (mysql/mariadb), uptime and read-only state."""
    version = str(conn.scalar("SELECT version() AS version") or "")
    flavor = "mariadb" if "mariadb" in version.lower() else "mysql"
    uptime_row = conn.query_one("SHOW GLOBAL STATUS LIKE 'Uptime'") or {}
    read_only = conn.query_one("SHOW GLOBAL VARIABLES LIKE 'read_only'") or {}
    super_read_only = conn.query_one("SHOW GLOBAL VARIABLES LIKE 'super_read_only'") or {}
    datadir = conn.query_one("SHOW GLOBAL VARIABLES LIKE 'datadir'") or {}
    try:
        uptime_seconds = int(uptime_row.get("Value") or 0)
    except (TypeError, ValueError):
        uptime_seconds = 0
    return {
        "version": s(version, 120),
        "flavor": flavor,
        "uptimeSeconds": uptime_seconds,
        "uptimeDays": round(uptime_seconds / 86400.0, 1),
        "readOnly": str(read_only.get("Value", "")).upper() == "ON",
        "superReadOnly": str(super_read_only.get("Value", "")).upper() == "ON",
        "dataDirectory": opt(datadir.get("Value"), 256),
    }


def show_variables(conn: Any, pattern: str | None = None) -> list[dict]:
    """[READ] Global variables (SHOW GLOBAL VARIABLES), optional LIKE ``pattern``."""
    if pattern:
        rows = conn.query("SHOW GLOBAL VARIABLES LIKE %(p)s", {"p": f"%{pattern}%"})
    else:
        rows = conn.query("SHOW GLOBAL VARIABLES")
    return [
        {
            "name": opt(r.get("Variable_name"), 128),
            "value": opt(r.get("Value"), 512),
        }
        for r in rows
    ]


def show_status(conn: Any, pattern: str | None = None) -> list[dict]:
    """[READ] Global status counters (SHOW GLOBAL STATUS), optional LIKE ``pattern``."""
    if pattern:
        rows = conn.query("SHOW GLOBAL STATUS LIKE %(p)s", {"p": f"%{pattern}%"})
    else:
        rows = conn.query("SHOW GLOBAL STATUS")
    return [
        {
            "name": opt(r.get("Variable_name"), 128),
            "value": opt(r.get("Value"), 512),
        }
        for r in rows
    ]


def list_databases(conn: Any) -> list[dict]:
    """[READ] User schemas with table count and data/index size (largest first)."""
    rows = conn.query(_DATABASES_SQL)
    return [
        {
            "name": opt(r.get("name"), 128),
            "tableCount": r.get("table_count"),
            "dataBytes": r.get("data_bytes"),
            "indexBytes": r.get("index_bytes"),
            "totalBytes": r.get("total_bytes"),
            "totalPretty": human_bytes(r.get("total_bytes")),
        }
        for r in rows
    ]


def list_engines(conn: Any) -> list[dict]:
    """[READ] Storage engines and which is the default (SHOW ENGINES)."""
    rows = conn.query("SHOW ENGINES")
    return [
        {
            "engine": opt(r.get("Engine"), 64),
            "support": opt(r.get("Support"), 32),
            "isDefault": str(r.get("Support", "")).upper() == "DEFAULT",
            "transactions": opt(r.get("Transactions"), 8),
            "comment": opt(r.get("Comment"), 200),
        }
        for r in rows
    ]


def connection_stats(conn: Any) -> dict:
    """[READ] Connection counters vs max_connections (exhaustion early warning)."""
    max_conn_row = conn.query_one("SHOW GLOBAL VARIABLES LIKE 'max_connections'") or {}
    status: dict[str, int] = {}
    for name in ("Threads_connected", "Threads_running", "Max_used_connections",
                 "Aborted_connects", "Connections"):
        row = conn.query_one("SHOW GLOBAL STATUS LIKE %(n)s", {"n": name}) or {}
        try:
            status[name] = int(row.get("Value") or 0)
        except (TypeError, ValueError):
            status[name] = 0
    try:
        max_connections = int(max_conn_row.get("Value") or 0)
    except (TypeError, ValueError):
        max_connections = 0
    connected = status.get("Threads_connected", 0)
    used_pct = round(100.0 * connected / max_connections, 1) if max_connections else None
    return {
        "maxConnections": max_connections,
        "threadsConnected": connected,
        "threadsRunning": status.get("Threads_running", 0),
        "maxUsedConnections": status.get("Max_used_connections", 0),
        "abortedConnects": status.get("Aborted_connects", 0),
        "totalConnectionsEver": status.get("Connections", 0),
        "usedPct": used_pct,
    }
