"""Replication reads: replica status (flavor-branched) and binary-log state.

MySQL 8.x uses ``SHOW REPLICA STATUS`` (the pre-8.0 statement is removed in
8.4); MariaDB still uses ``SHOW SLAVE STATUS``. Both result shapes carry the
same information under two naming families (``Source_*``/``Replica_*`` vs
``Master_*``/``Slave_*``) — ``_pick`` normalises them into one record, which is
also the input to the flagship ``replication_lag_rca``.
"""

from __future__ import annotations

from typing import Any

from mysql_aiops.ops._util import opt


def _pick(row: dict, *names: str) -> Any:
    """Return the first present key from the MySQL/MariaDB naming families."""
    for n in names:
        if n in row:
            return row[n]
    return None


def _normalize_replica_row(row: dict) -> dict:
    """Normalise a SHOW REPLICA/SLAVE STATUS row across flavors."""
    seconds_behind = _pick(row, "Seconds_Behind_Source", "Seconds_Behind_Master")
    return {
        "sourceHost": opt(_pick(row, "Source_Host", "Master_Host"), 128),
        "sourcePort": _pick(row, "Source_Port", "Master_Port"),
        "ioThreadRunning": opt(_pick(row, "Replica_IO_Running", "Slave_IO_Running"), 32),
        "sqlThreadRunning": opt(_pick(row, "Replica_SQL_Running", "Slave_SQL_Running"), 32),
        "secondsBehindSource": seconds_behind,
        "lastIoError": opt(_pick(row, "Last_IO_Error"), 300),
        "lastSqlError": opt(_pick(row, "Last_SQL_Error"), 300),
        "lastIoErrno": _pick(row, "Last_IO_Errno"),
        "lastSqlErrno": _pick(row, "Last_SQL_Errno"),
        "retrievedGtidSet": opt(_pick(row, "Retrieved_Gtid_Set"), 200),
        "executedGtidSet": opt(_pick(row, "Executed_Gtid_Set"), 200),
        "relayLogSpace": _pick(row, "Relay_Log_Space"),
        "sqlDelay": _pick(row, "SQL_Delay"),
        "channelName": opt(_pick(row, "Channel_Name"), 128),
    }


def replica_status(conn: Any) -> dict:
    """[READ] Replica thread state and lag (SHOW REPLICA/SLAVE STATUS, flavor-branched).

    Empty ``replicas`` means this server is not a replica (primary/standalone).
    """
    flavor = getattr(conn, "flavor", "mysql")
    stmt = "SHOW SLAVE STATUS" if flavor == "mariadb" else "SHOW REPLICA STATUS"
    rows = conn.query(stmt)
    replicas = [_normalize_replica_row(r) for r in rows]
    return {
        "flavor": flavor,
        "isReplica": bool(replicas),
        "count": len(replicas),
        "replicas": replicas,
        "note": (
            "Empty on a primary/standalone server. secondsBehindSource is NULL "
            "while the SQL thread is stopped or the IO thread is reconnecting."
        ),
    }


def binlog_status(conn: Any) -> dict:
    """[READ] Binary-log configuration, GTID mode, and connected downstream replicas."""
    def _var(name: str) -> str | None:
        """The variable's value, or None when this server has no such variable.

        MariaDB has no ``gtid_mode`` at all; MySQL always does. "The variable
        does not exist here" and "the variable is set to the empty string" are
        different facts, and only the second should read as an empty value.
        """
        row = conn.query_one("SHOW GLOBAL VARIABLES LIKE %(n)s", {"n": name}) or {}
        return opt(row.get("Value"), 200)

    log_bin = _var("log_bin")
    gtid_mode = _var("gtid_mode")  # empty on MariaDB (uses gtid_current_pos)
    server_id = _var("server_id")
    binlog_format = _var("binlog_format")
    expire = _var("binlog_expire_logs_seconds") or _var("expire_logs_days")

    log_bin_on = (log_bin or "").upper() == "ON"
    binlogs = conn.query("SHOW BINARY LOGS") if log_bin_on else []
    total_bytes = 0
    for b in binlogs:
        try:
            total_bytes += int(b.get("File_size") or 0)
        except (TypeError, ValueError):
            pass

    downstream = conn.query("SHOW PROCESSLIST")
    replica_threads = [
        {"id": r.get("Id"), "host": opt(r.get("Host"), 128)}
        for r in downstream
        if "binlog dump" in str(r.get("Command", "")).lower()
    ]

    return {
        "logBin": log_bin_on,
        "serverId": server_id,
        "binlogFormat": opt(binlog_format, 32),
        "gtidMode": opt(gtid_mode, 32),
        "binlogRetention": opt(expire, 32),
        "binlogCount": len(binlogs),
        "binlogTotalBytes": total_bytes,
        "downstreamReplicaCount": len(replica_threads),
        "downstreamReplicas": replica_threads,
    }
