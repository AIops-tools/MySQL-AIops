"""Read-path ops tests (server / activity / queries / indexes / tables / replication).

Uses the ``FakeMySQL`` double (substring-matched canned rows) so normalisation,
rollups, flavor branching and summaries are exercised without a live server.
"""

from __future__ import annotations

import pytest

from mysql_aiops.ops import (
    activity,
    indexes,
    overview,
    queries,
    replication,
    server,
    tables,
)
from tests.conftest import FakeMySQL


@pytest.mark.unit
def test_server_version_detects_mysql_flavor():
    conn = FakeMySQL(
        {
            "SHOW GLOBAL STATUS": [{"Variable_name": "Uptime", "Value": "172800"}],
            "SHOW GLOBAL VARIABLES": [{"Variable_name": "read_only", "Value": "OFF"}],
        },
        {"version()": "8.4.2"},
    )
    out = server.server_version(conn)
    assert out["flavor"] == "mysql"
    assert out["uptimeDays"] == 2.0
    assert out["readOnly"] is False


@pytest.mark.unit
def test_server_version_detects_mariadb_flavor():
    conn = FakeMySQL(scalars={"version()": "11.4.2-MariaDB-log"})
    out = server.server_version(conn)
    assert out["flavor"] == "mariadb"


@pytest.mark.unit
def test_list_databases_sorted_with_pretty_size():
    conn = FakeMySQL({"FROM information_schema.tables": [
        {"name": "shop", "table_count": 12, "data_bytes": 524288,
         "index_bytes": 524288, "total_bytes": 1048576},
    ]})
    out = server.list_databases(conn)
    assert out[0]["totalPretty"] == "1.0 MB"


@pytest.mark.unit
def test_connection_stats_computes_used_pct():
    conn = FakeMySQL({
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "max_connections", "Value": "200"}],
        "SHOW GLOBAL STATUS": [{"Variable_name": "x", "Value": "50"}],
    })
    out = server.connection_stats(conn)
    assert out["maxConnections"] == 200
    assert out["usedPct"] == 25.0


@pytest.mark.unit
def test_list_sessions_counts_commands_and_sleeping():
    conn = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 1, "command": "Query", "query": "SELECT 1"},
        {"id": 2, "command": "Sleep", "query": None},
        {"id": 3, "command": "Query", "query": "SELECT 2"},
    ]})
    out = activity.list_sessions(conn)
    assert out["total"] == 3
    assert out["byCommand"]["Query"] == 2
    assert out["sleepingCount"] == 1


@pytest.mark.unit
def test_long_running_passes_threshold_param():
    conn = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 9, "duration_seconds": 120, "query": "SELECT SLEEP(200)"},
    ]})
    out = activity.long_running_queries(conn, min_seconds=90)
    assert out["thresholdSeconds"] == 90 and out["count"] == 1
    # threshold is bound as a parameter, never string-formatted into the SQL
    _, params = conn.queried[0]
    assert params == {"min_seconds": 90}


@pytest.mark.unit
def test_list_transactions_flags_lock_wait():
    conn = FakeMySQL({"FROM information_schema.innodb_trx": [
        {"trx_id": "100", "thread_id": 1, "trx_state": "RUNNING", "query": "UPDATE a"},
        {"trx_id": "101", "thread_id": 2, "trx_state": "LOCK WAIT", "query": "UPDATE b"},
    ]})
    out = activity.list_transactions(conn)
    assert out["count"] == 2 and out["lockWaitCount"] == 1


@pytest.mark.unit
def test_lock_wait_pairs_uses_performance_schema_on_mysql():
    conn = FakeMySQL({"performance_schema.data_lock_waits": [
        {"blocked_id": 2, "blocking_id": 1, "blocked_query": "UPDATE x",
         "blocking_query": "UPDATE y", "wait_seconds": 5},
    ]}, flavor="mysql")
    pairs = activity.lock_wait_pairs(conn)
    assert pairs[0]["blockedId"] == 2 and pairs[0]["blockingId"] == 1
    assert "performance_schema.data_lock_waits" in conn.queried[0][0]


@pytest.mark.unit
def test_lock_wait_pairs_uses_information_schema_on_mariadb():
    conn = FakeMySQL({"information_schema.innodb_lock_waits": [
        {"blocked_id": 4, "blocking_id": 3},
    ]}, flavor="mariadb")
    pairs = activity.lock_wait_pairs(conn)
    assert pairs[0]["blockedId"] == 4
    assert "information_schema.innodb_lock_waits" in conn.queried[0][0]


@pytest.mark.unit
def test_top_queries_orders_by_whitelist_column_and_computes_ratios():
    conn = FakeMySQL({"events_statements_summary_by_digest": [
        {"schema_name": "shop", "digest": "abc", "digest_text": "SELECT ...",
         "calls": 10, "total_time_ms": 1000.0, "mean_time_ms": 100.0,
         "lock_time_ms": 600.0, "rows_examined": 5000, "rows_sent": 10,
         "no_index_used": 8, "tmp_disk_tables": 0},
    ]})
    out = queries.top_queries(conn, order_by="no_index", limit=5)
    row = out["statements"][0]
    assert row["lockTimePct"] == 60.0
    assert row["noIndexUsedPct"] == 80.0
    assert row["rowsExaminedPerSent"] == 500.0
    # the whitelisted column must appear in the emitted SQL, not the raw choice
    sql, params = conn.queried[0]
    assert "SUM_NO_INDEX_USED DESC" in sql
    assert params == {"limit": 5}


@pytest.mark.unit
def test_top_queries_rejects_unknown_order_by():
    conn = FakeMySQL()
    with pytest.raises(ValueError, match="order_by"):
        queries.top_queries(conn, order_by="; DROP TABLE users")


@pytest.mark.unit
def test_explain_rejects_multi_statement():
    conn = FakeMySQL()
    with pytest.raises(ValueError, match="single statement"):
        queries.explain_query(conn, "SELECT 1; DROP TABLE t")


@pytest.mark.unit
def test_explain_wraps_statement_and_parses_json():
    conn = FakeMySQL({"EXPLAIN": [
        {"EXPLAIN": '{"query_block": {"table": {"access_type": "ALL"}}}'},
    ]})
    out = queries.explain_query(conn, "SELECT * FROM t")
    assert out["plan"]["query_block"]["table"]["access_type"] == "ALL"
    sql, _ = conn.queried[0]
    assert sql.startswith("EXPLAIN FORMAT=JSON ")


@pytest.mark.unit
def test_unused_indexes_lists_zero_io():
    conn = FakeMySQL({"table_io_waits_summary_by_index_usage": [
        {"schema": "shop", "table": "t", "index": "idx_a", "io_count": 0},
    ]})
    out = indexes.unused_indexes(conn)
    assert out["count"] == 1 and out["indexes"][0]["index"] == "idx_a"


@pytest.mark.unit
def test_redundant_indexes_flags_prefix_and_duplicates():
    conn = FakeMySQL({"FROM information_schema.statistics": [
        {"schema": "shop", "table": "t", "index": "idx_a", "non_unique": 1,
         "seq_in_index": 1, "column_name": "a", "cardinality": 10},
        {"schema": "shop", "table": "t", "index": "idx_ab", "non_unique": 1,
         "seq_in_index": 1, "column_name": "a", "cardinality": 10},
        {"schema": "shop", "table": "t", "index": "idx_ab", "non_unique": 1,
         "seq_in_index": 2, "column_name": "b", "cardinality": 100},
    ]})
    out = indexes.redundant_indexes(conn)
    assert out["count"] == 1
    hit = out["redundant"][0]
    assert hit["index"] == "idx_a" and hit["coveredBy"] == "idx_ab"
    assert hit["exactDuplicate"] is False


@pytest.mark.unit
def test_table_fragmentation_free_pct():
    conn = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "t", "engine": "InnoDB", "est_rows": 100,
         "data_bytes": 6000, "index_bytes": 2000, "free_bytes": 2000,
         "free_pct": 20.0},
    ]})
    out = tables.table_fragmentation(conn)
    assert out["tables"][0]["freePct"] == 20.0


@pytest.mark.unit
def test_table_status_flags_non_innodb():
    conn = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "legacy", "engine": "MyISAM"},
        {"schema": "shop", "table": "orders", "engine": "InnoDB"},
    ]})
    out = tables.table_status(conn)
    assert out["nonInnodbTables"] == ["legacy"]


@pytest.mark.unit
def test_replica_status_normalises_mysql_naming():
    conn = FakeMySQL({"SHOW REPLICA STATUS": [
        {"Source_Host": "db1", "Replica_IO_Running": "Yes",
         "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 3},
    ]}, flavor="mysql")
    out = replication.replica_status(conn)
    assert out["isReplica"] is True
    assert out["replicas"][0]["secondsBehindSource"] == 3
    assert out["replicas"][0]["ioThreadRunning"] == "Yes"


@pytest.mark.unit
def test_replica_status_normalises_mariadb_naming():
    conn = FakeMySQL({"SHOW SLAVE STATUS": [
        {"Master_Host": "db1", "Slave_IO_Running": "Yes",
         "Slave_SQL_Running": "No", "Seconds_Behind_Master": None,
         "Last_SQL_Error": "Duplicate entry"},
    ]}, flavor="mariadb")
    out = replication.replica_status(conn)
    assert out["flavor"] == "mariadb"
    assert out["replicas"][0]["sqlThreadRunning"] == "No"
    assert out["replicas"][0]["lastSqlError"] == "Duplicate entry"
    assert "SHOW SLAVE STATUS" in conn.queried[0][0]


@pytest.mark.unit
def test_binlog_status_counts_downstream_replicas():
    conn = FakeMySQL({
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "log_bin", "Value": "ON"}],
        "SHOW BINARY LOGS": [
            {"Log_name": "binlog.000001", "File_size": 1024},
            {"Log_name": "binlog.000002", "File_size": 2048},
        ],
        "SHOW PROCESSLIST": [
            {"Id": 5, "Host": "replica1:3306", "Command": "Binlog Dump GTID"},
            {"Id": 6, "Host": "app:1234", "Command": "Sleep"},
        ],
    })
    out = replication.binlog_status(conn)
    assert out["logBin"] is True
    assert out["binlogCount"] == 2 and out["binlogTotalBytes"] == 3072
    assert out["downstreamReplicaCount"] == 1


@pytest.mark.unit
def test_overview_resilient_to_partial_failure():
    # No canned rows for most queries → sections come back empty/None but
    # snapshot must still assemble a dict, never raise.
    conn = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 1, "command": "Query", "query": "x"},
    ]})
    out = overview.snapshot(conn)
    assert isinstance(out, dict)
    assert out["totalSessions"] == 1
