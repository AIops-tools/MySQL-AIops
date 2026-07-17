"""One-shot server health snapshot (read-only, resilient).

Folds a handful of cheap reads into a single summary a DBA/agent can call
first: version + flavor + uptime, connection headroom, sessions by command, the
longest-running query, the most fragmented table, and the replica role. Each
section is captured defensively — one failing probe becomes an ``error`` field,
never a raised traceback (a health probe must survive the thing it probes being
unhealthy).
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops import activity, replication, server, tables


def _safe(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 — report as partial
        return {"error": str(exc)[:200]}


def snapshot(conn: Any) -> dict:
    """[READ] One-shot server health snapshot across several subsystems."""
    version = _safe(server.server_version, conn)
    conns = _safe(server.connection_stats, conn)
    sessions = _safe(activity.list_sessions, conn)
    long_running = _safe(activity.long_running_queries, conn, 60)
    frag = _safe(tables.table_fragmentation, conn, 5)
    repl = _safe(replication.replica_status, conn)

    longest = None
    if isinstance(long_running, dict) and long_running.get("queries"):
        longest = long_running["queries"][0]
    worst_frag = None
    if isinstance(frag, dict) and frag.get("tables"):
        worst_frag = frag["tables"][0]

    is_replica = repl.get("isReplica") if isinstance(repl, dict) else None
    return {
        "version": version.get("version") if isinstance(version, dict) else None,
        "flavor": version.get("flavor") if isinstance(version, dict) else None,
        "uptimeDays": version.get("uptimeDays") if isinstance(version, dict) else None,
        "readOnly": version.get("readOnly") if isinstance(version, dict) else None,
        "role": ("replica" if is_replica else "primary/standalone")
                if is_replica is not None else None,
        "connections": conns if isinstance(conns, dict) else {"error": str(conns)[:200]},
        "sessionsByCommand": sessions.get("byCommand") if isinstance(sessions, dict) else sessions,
        "totalSessions": sessions.get("total") if isinstance(sessions, dict) else None,
        "longRunningCount": long_running.get("count") if isinstance(long_running, dict) else None,
        "longestQuery": longest,
        "mostFragmentedTable": worst_frag,
        "secondsBehindSource": (
            (repl.get("replicas") or [{}])[0].get("secondsBehindSource")
            if isinstance(repl, dict) and repl.get("replicas") else None
        ),
    }
