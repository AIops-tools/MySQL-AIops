"""CLI read-command bodies (server / activity / index / table / query / repl /
overview / analyze) driven through Typer's ``CliRunner``.

These exercise the CLI command bodies themselves — argument wiring, the
``get_connection`` call, JSON emission — against the ``FakeMySQL`` double, so no
live database is touched. Each command's ``get_connection`` is bound into its
own module namespace at import, so it is patched per-module.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from mysql_aiops.cli import app
from tests.conftest import FakeMySQL

runner = CliRunner()


def _patch_conn(monkeypatch, module_path: str, fake: FakeMySQL) -> None:
    monkeypatch.setattr(module_path, lambda target=None, config_path=None: (fake, None))


# ── server ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_server_version_emits_flavor(monkeypatch):
    fake = FakeMySQL(
        {"SHOW GLOBAL STATUS": [{"Variable_name": "Uptime", "Value": "86400"}]},
        {"version()": "8.4.2"},
    )
    _patch_conn(monkeypatch, "mysql_aiops.cli.server.get_connection", fake)
    result = runner.invoke(app, ["server", "version"])
    assert result.exit_code == 0, result.output
    assert '"flavor": "mysql"' in result.output


@pytest.mark.unit
def test_cli_server_variables_binds_like_pattern(monkeypatch):
    fake = FakeMySQL({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "max_connections", "Value": "151"},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.server.get_connection", fake)
    result = runner.invoke(app, ["server", "variables", "max_conn"])
    assert result.exit_code == 0, result.output
    assert "max_connections" in result.output
    sql, params = fake.queried[0]
    assert "LIKE %(p)s" in sql and params == {"p": "%max_conn%"}


@pytest.mark.unit
def test_cli_server_status_no_pattern_runs_plain_query(monkeypatch):
    fake = FakeMySQL({"SHOW GLOBAL STATUS": [
        {"Variable_name": "Threads_connected", "Value": "9"},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.server.get_connection", fake)
    result = runner.invoke(app, ["server", "status"])
    assert result.exit_code == 0, result.output
    sql, params = fake.queried[0]
    assert sql == "SHOW GLOBAL STATUS" and params is None


@pytest.mark.unit
def test_cli_server_databases_and_engines(monkeypatch):
    fake = FakeMySQL({
        "FROM information_schema.tables": [
            {"name": "shop", "table_count": 3, "data_bytes": 1024,
             "index_bytes": 0, "total_bytes": 1024},
        ],
        "SHOW ENGINES": [
            {"Engine": "InnoDB", "Support": "DEFAULT", "Transactions": "YES",
             "Comment": "row locking"},
        ],
    })
    _patch_conn(monkeypatch, "mysql_aiops.cli.server.get_connection", fake)
    assert runner.invoke(app, ["server", "databases"]).exit_code == 0
    res = runner.invoke(app, ["server", "engines"])
    assert res.exit_code == 0
    assert '"isDefault": true' in res.output


@pytest.mark.unit
def test_cli_server_connections(monkeypatch):
    fake = FakeMySQL({
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "max_connections", "Value": "100"}],
        "SHOW GLOBAL STATUS": [{"Variable_name": "x", "Value": "40"}],
    })
    _patch_conn(monkeypatch, "mysql_aiops.cli.server.get_connection", fake)
    result = runner.invoke(app, ["server", "connections"])
    assert result.exit_code == 0
    assert '"maxConnections": 100' in result.output


# ── activity ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_activity_sessions_no_sleeping_flag(monkeypatch):
    fake = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 1, "command": "Query", "query": "SELECT 1"},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.activity.get_connection", fake)
    result = runner.invoke(app, ["activity", "sessions", "--no-sleeping"])
    assert result.exit_code == 0, result.output
    # --no-sleeping → include_sleeping False → COMMAND != 'Sleep' filter in SQL
    sql = fake.queried[0][0]
    assert "processlist" in sql


@pytest.mark.unit
def test_cli_activity_long_binds_min_seconds(monkeypatch):
    fake = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 5, "duration_seconds": 200, "query": "SELECT SLEEP(300)"},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.activity.get_connection", fake)
    result = runner.invoke(app, ["activity", "long", "--min-seconds", "120"])
    assert result.exit_code == 0, result.output
    assert '"thresholdSeconds": 120' in result.output
    assert fake.queried[0][1] == {"min_seconds": 120}


@pytest.mark.unit
def test_cli_activity_transactions_and_lock_waits(monkeypatch):
    fake = FakeMySQL({
        "FROM information_schema.innodb_trx": [
            {"trx_id": "1", "thread_id": 1, "trx_state": "LOCK WAIT", "query": "x"},
        ],
        "performance_schema.data_lock_waits": [
            {"blocked_id": 2, "blocking_id": 1},
        ],
    })
    _patch_conn(monkeypatch, "mysql_aiops.cli.activity.get_connection", fake)
    assert runner.invoke(app, ["activity", "transactions"]).exit_code == 0
    assert runner.invoke(app, ["activity", "lock-waits"]).exit_code == 0


# ── index ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_index_unused_redundant_stats(monkeypatch):
    fake = FakeMySQL({
        "table_io_waits_summary_by_index_usage": [
            {"schema": "shop", "table": "t", "index": "idx_a", "io_count": 0},
        ],
        "FROM information_schema.statistics": [
            {"schema": "shop", "table": "t", "index": "idx_a", "non_unique": 1,
             "seq_in_index": 1, "column_name": "a", "cardinality": 10},
        ],
    })
    _patch_conn(monkeypatch, "mysql_aiops.cli.index.get_connection", fake)
    assert '"idx_a"' in runner.invoke(app, ["index", "unused"]).output
    assert runner.invoke(app, ["index", "redundant"]).exit_code == 0
    stats = runner.invoke(app, ["index", "stats"])
    assert stats.exit_code == 0 and '"cardinality": 10' in stats.output


# ── table ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_table_sizes_fragmentation_status(monkeypatch):
    fake = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "orders", "engine": "InnoDB", "est_rows": 100,
         "data_bytes": 6000, "index_bytes": 2000, "total_bytes": 8000,
         "free_bytes": 2000, "free_pct": 20.0, "row_format": "Dynamic"},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.table.get_connection", fake)
    assert runner.invoke(app, ["table", "sizes"]).exit_code == 0
    frag = runner.invoke(app, ["table", "fragmentation"])
    assert frag.exit_code == 0 and '"freePct": 20.0' in frag.output
    assert runner.invoke(app, ["table", "status"]).exit_code == 0


# ── query ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_query_top_orders_and_limits(monkeypatch):
    fake = FakeMySQL({"events_statements_summary_by_digest": [
        {"schema_name": "shop", "digest": "abc", "digest_text": "SELECT ...",
         "calls": 10, "total_time_ms": 1000.0, "mean_time_ms": 100.0,
         "lock_time_ms": 100.0, "rows_examined": 500, "rows_sent": 10,
         "no_index_used": 0, "tmp_disk_tables": 0},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.query.get_connection", fake)
    result = runner.invoke(app, ["query", "top", "--order-by", "calls", "--limit", "5"])
    assert result.exit_code == 0, result.output
    sql, params = fake.queried[0]
    assert "COUNT_STAR DESC" in sql and params == {"limit": 5}


@pytest.mark.unit
def test_cli_query_explain(monkeypatch):
    fake = FakeMySQL({"EXPLAIN": [
        {"EXPLAIN": json.dumps({"query_block": {"table": {"access_type": "ALL"}}})},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.query.get_connection", fake)
    result = runner.invoke(app, ["query", "explain", "SELECT * FROM t"])
    assert result.exit_code == 0, result.output
    assert '"access_type": "ALL"' in result.output


@pytest.mark.unit
def test_cli_query_top_bad_order_by_is_teaching_error(monkeypatch):
    fake = FakeMySQL()
    _patch_conn(monkeypatch, "mysql_aiops.cli.query.get_connection", fake)
    result = runner.invoke(app, ["query", "top", "--order-by", "; DROP"])
    assert result.exit_code == 1
    assert "Error:" in result.output and "order_by" in result.output


# ── replication ─────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_repl_status_and_binlog(monkeypatch):
    fake = FakeMySQL({
        "SHOW REPLICA STATUS": [
            {"Source_Host": "db1", "Replica_IO_Running": "Yes",
             "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 0},
        ],
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "log_bin", "Value": "ON"}],
        "SHOW BINARY LOGS": [{"Log_name": "binlog.1", "File_size": 100}],
        "SHOW PROCESSLIST": [],
    }, flavor="mysql")
    _patch_conn(monkeypatch, "mysql_aiops.cli.replication.get_connection", fake)
    status = runner.invoke(app, ["repl", "status"])
    assert status.exit_code == 0 and '"isReplica": true' in status.output
    binlog = runner.invoke(app, ["repl", "binlog"])
    assert binlog.exit_code == 0 and '"logBin": true' in binlog.output


# ── overview ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_cli_overview_snapshot(monkeypatch):
    fake = FakeMySQL(
        {"FROM information_schema.processlist": [
            {"id": 1, "command": "Query", "query": "x"},
        ]},
        {"version()": "8.4.2"},
    )
    _patch_conn(monkeypatch, "mysql_aiops.cli.overview.get_connection", fake)
    result = runner.invoke(app, ["overview"])
    assert result.exit_code == 0, result.output
    assert '"totalSessions": 1' in result.output


# ── analyze (flagship, live-pull) ───────────────────────────────────────────


@pytest.mark.unit
def test_cli_analyze_slow_query(monkeypatch):
    fake = FakeMySQL({"events_statements_summary_by_digest": [
        {"schema_name": "shop", "digest": "b", "digest_text": "SELECT big",
         "calls": 30, "total_time_ms": 9000.0, "mean_time_ms": 300.0,
         "lock_time_ms": 6300.0, "rows_examined": 5000, "rows_sent": 10,
         "no_index_used": 27, "tmp_disk_tables": 12},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.analyze.get_connection", fake)
    result = runner.invoke(app, ["analyze", "slow-query"])
    assert result.exit_code == 0, result.output
    assert '"worst"' in result.output


@pytest.mark.unit
def test_cli_analyze_slow_query_with_explain(monkeypatch):
    fake = FakeMySQL({
        "events_statements_summary_by_digest": [
            {"schema_name": "shop", "digest": "b", "digest_text": "SELECT x",
             "calls": 5, "total_time_ms": 500.0, "mean_time_ms": 100.0,
             "lock_time_ms": 0.0, "rows_examined": 10, "rows_sent": 10,
             "no_index_used": 0, "tmp_disk_tables": 0},
        ],
        "EXPLAIN": [
            {"EXPLAIN": json.dumps({"query_block": {"table": {"access_type": "ALL"}}})},
        ],
    })
    _patch_conn(monkeypatch, "mysql_aiops.cli.analyze.get_connection", fake)
    result = runner.invoke(app, ["analyze", "slow-query", "--explain", "SELECT * FROM t"])
    assert result.exit_code == 0, result.output
    assert "ALL" in result.output


@pytest.mark.unit
def test_cli_analyze_lock_waits(monkeypatch):
    fake = FakeMySQL({"performance_schema.data_lock_waits": [
        {"blocked_id": 200, "blocking_id": 100, "blocking_query": "UPDATE a",
         "wait_seconds": 12},
    ]}, flavor="mysql")
    _patch_conn(monkeypatch, "mysql_aiops.cli.analyze.get_connection", fake)
    result = runner.invoke(app, ["analyze", "lock-waits"])
    assert result.exit_code == 0, result.output
    assert '"worstRootId"' in result.output


@pytest.mark.unit
def test_cli_analyze_replication(monkeypatch):
    fake = FakeMySQL({"SHOW REPLICA STATUS": [
        {"Source_Host": "db1", "Replica_IO_Running": "Yes",
         "Replica_SQL_Running": "Yes", "Seconds_Behind_Source": 0},
    ]}, flavor="mysql")
    _patch_conn(monkeypatch, "mysql_aiops.cli.analyze.get_connection", fake)
    result = runner.invoke(app, ["analyze", "replication"])
    assert result.exit_code == 0, result.output
    assert '"signal": "replication healthy"' in result.output


@pytest.mark.unit
def test_cli_analyze_fragmentation(monkeypatch):
    fake = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "hot", "engine": "InnoDB", "est_rows": 100,
         "data_bytes": 1000, "index_bytes": 0, "free_bytes": 500 * 1024 * 1024,
         "free_pct": 40.0},
    ]})
    _patch_conn(monkeypatch, "mysql_aiops.cli.analyze.get_connection", fake)
    result = runner.invoke(app, ["analyze", "fragmentation"])
    assert result.exit_code == 0, result.output
    assert '"needsAttentionCount": 1' in result.output
