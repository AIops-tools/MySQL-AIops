"""MCP tool wrappers (``mcp_server/tools/*``) exercised offline.

Each tool module binds ``_get_connection`` into its own namespace, so the fake
connection is patched per-module. The governance harness audit/undo state is
redirected to a tmp ``MYSQL_AIOPS_HOME`` and its engines reset around each test
so nothing touches the real ``~/.mysql-aiops``. These assert the live-pull
branches, dry-run shapes, flavor branching, and undo prior-state capture that
the CLI-facing tests reach only indirectly.
"""

from __future__ import annotations

import sqlite3

import pytest

import mysql_aiops.governance.audit as audit_mod
import mysql_aiops.governance.policy as policy_mod
import mysql_aiops.governance.undo as undo_mod
from mcp_server.tools import activity as t_activity
from mcp_server.tools import analysis as t_analysis
from mcp_server.tools import indexes as t_indexes
from mcp_server.tools import queries as t_queries
from mcp_server.tools import remediation as t_remediation
from mcp_server.tools import replication as t_replication
from mcp_server.tools import server as t_server
from mcp_server.tools import tables as t_tables
from tests.conftest import FakeMySQL

_CREATE_TABLE_DDL = (
    "CREATE TABLE `orders` (\n"
    "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
    "  `customer_id` bigint NOT NULL,\n"
    "  PRIMARY KEY (`id`),\n"
    "  KEY `idx_orders_cid` (`customer_id`)\n"
    ") ENGINE=InnoDB"
)


@pytest.fixture
def gov_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MYSQL_AIOPS_HOME", str(tmp_path))
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()
    yield tmp_path
    audit_mod.reset_engine()
    policy_mod.reset_policy_engine()
    undo_mod.reset_undo_store()


def _bind(monkeypatch, module, fake: FakeMySQL) -> None:
    monkeypatch.setattr(module, "_get_connection", lambda target=None: fake)


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


# ── server read tools ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_mcp_server_version_and_overview(gov_home, monkeypatch):
    fake = FakeMySQL(
        {"FROM information_schema.processlist": [
            {"id": 1, "command": "Query", "query": "x"},
        ]},
        {"version()": "8.4.2"},
    )
    _bind(monkeypatch, t_server, fake)
    assert t_server.server_version()["flavor"] == "mysql"
    assert isinstance(t_server.overview(), dict)


@pytest.mark.unit
def test_mcp_show_variables_pattern_binds_and_returns_list(gov_home, monkeypatch):
    fake = FakeMySQL({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "max_connections", "Value": "151"},
    ]})
    _bind(monkeypatch, t_server, fake)
    out = t_server.show_variables(pattern="max_conn")
    assert out[0]["name"] == "max_connections"
    sql, params = fake.queried[0]
    assert "LIKE %(p)s" in sql and params == {"p": "%max_conn%"}


@pytest.mark.unit
def test_mcp_show_status_databases_engines_connections(gov_home, monkeypatch):
    fake = FakeMySQL({
        "SHOW GLOBAL STATUS": [{"Variable_name": "Threads_connected", "Value": "9"}],
        "FROM information_schema.tables": [
            {"name": "shop", "table_count": 2, "data_bytes": 1024,
             "index_bytes": 0, "total_bytes": 1024},
        ],
        "SHOW ENGINES": [{"Engine": "InnoDB", "Support": "DEFAULT"}],
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "max_connections", "Value": "100"}],
    })
    _bind(monkeypatch, t_server, fake)
    assert isinstance(t_server.show_status(), list)
    assert t_server.list_databases()[0]["name"] == "shop"
    assert t_server.list_engines()[0]["isDefault"] is True
    assert t_server.connection_stats()["maxConnections"] == 100


# ── activity / queries / tables / indexes / replication read tools ──────────


@pytest.mark.unit
def test_mcp_activity_tools(gov_home, monkeypatch):
    fake = FakeMySQL({
        "FROM information_schema.processlist": [
            {"id": 1, "command": "Query", "duration_seconds": 200, "query": "x"},
        ],
        "FROM information_schema.innodb_trx": [
            {"trx_id": "1", "thread_id": 1, "trx_state": "RUNNING", "query": "x"},
        ],
        "performance_schema.data_lock_waits": [{"blocked_id": 2, "blocking_id": 1}],
    }, flavor="mysql")
    _bind(monkeypatch, t_activity, fake)
    assert t_activity.list_sessions()["total"] == 1
    assert t_activity.long_running_queries(min_seconds=100)["thresholdSeconds"] == 100
    assert t_activity.list_transactions()["count"] == 1
    assert t_activity.lock_waits()[0]["blockedId"] == 2


@pytest.mark.unit
def test_mcp_query_tools_and_reset_dry_run(gov_home, monkeypatch):
    fake = FakeMySQL({
        "events_statements_summary_by_digest": [
            {"schema_name": "shop", "digest": "a", "digest_text": "SELECT 1",
             "calls": 3, "total_time_ms": 30.0, "mean_time_ms": 10.0,
             "lock_time_ms": 0.0, "rows_examined": 3, "rows_sent": 3,
             "no_index_used": 0, "tmp_disk_tables": 0},
        ],
        "EXPLAIN": [{"EXPLAIN": '{"query_block": {"table": {"access_type": "ref"}}}'}],
    })
    _bind(monkeypatch, t_queries, fake)
    assert t_queries.top_queries(order_by="calls")["statements"][0]["calls"] == 3
    plan = t_queries.explain_query("SELECT 1")["plan"]
    assert plan["query_block"]["table"]["access_type"] == "ref"
    # dry-run returns preview before touching the connection
    assert t_queries.reset_query_stats(dry_run=True)["dryRun"] is True


@pytest.mark.unit
def test_mcp_table_tools(gov_home, monkeypatch):
    fake = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "t", "engine": "MyISAM", "est_rows": 5,
         "data_bytes": 100, "index_bytes": 0, "total_bytes": 100,
         "free_bytes": 50, "free_pct": 10.0, "row_format": "Fixed"},
    ]})
    _bind(monkeypatch, t_tables, fake)
    assert t_tables.table_sizes()["returned"] == 1
    assert t_tables.table_fragmentation()["tables"][0]["freePct"] == 10.0
    assert t_tables.table_status()["nonInnodbTables"] == ["t"]


@pytest.mark.unit
def test_mcp_index_tools(gov_home, monkeypatch):
    fake = FakeMySQL({
        "table_io_waits_summary_by_index_usage": [
            {"schema": "shop", "table": "t", "index": "idx_a", "io_count": 0},
        ],
        "FROM information_schema.statistics": [
            {"schema": "shop", "table": "t", "index": "idx_a", "non_unique": 1,
             "seq_in_index": 1, "column_name": "a", "cardinality": 10},
        ],
    })
    _bind(monkeypatch, t_indexes, fake)
    assert t_indexes.unused_indexes()["count"] == 1
    assert isinstance(t_indexes.redundant_indexes()["redundant"], list)
    assert t_indexes.index_stats()["indexes"][0]["cardinality"] == 10


@pytest.mark.unit
def test_mcp_replication_tools_flavor_branch(gov_home, monkeypatch):
    fake = FakeMySQL({
        "SHOW SLAVE STATUS": [
            {"Master_Host": "db1", "Slave_IO_Running": "Yes",
             "Slave_SQL_Running": "No", "Seconds_Behind_Master": None},
        ],
        "SHOW GLOBAL VARIABLES": [{"Variable_name": "log_bin", "Value": "OFF"}],
        "SHOW BINARY LOGS": [],
        "SHOW PROCESSLIST": [],
    }, flavor="mariadb")
    _bind(monkeypatch, t_replication, fake)
    status = t_replication.replica_status()
    assert status["flavor"] == "mariadb"
    assert status["replicas"][0]["sqlThreadRunning"] == "No"
    assert t_replication.binlog_status()["logBin"] is False


# ── analysis tools: injected + live-pull branches ───────────────────────────


@pytest.mark.unit
def test_mcp_slow_query_rca_injected_vs_live(gov_home, monkeypatch):
    statements = [
        {"digest": "b", "totalTimeMs": 9000, "meanTimeMs": 300, "calls": 30,
         "noIndexUsedPct": 90.0, "lockTimePct": 70.0, "rowsExaminedPerSent": 500.0,
         "tmpDiskTables": 12},
    ]
    # injected path — no connection required
    out = t_analysis.slow_query_rca(statements=statements)
    assert out["worst"]["digest"] == "b"

    # live path — pulls the top digests through the fake connection
    fake = FakeMySQL({"events_statements_summary_by_digest": [
        {"schema_name": "shop", "digest": "b", "digest_text": "SELECT big",
         "calls": 30, "total_time_ms": 9000.0, "mean_time_ms": 300.0,
         "lock_time_ms": 6300.0, "rows_examined": 5000, "rows_sent": 10,
         "no_index_used": 27, "tmp_disk_tables": 12},
    ]})
    _bind(monkeypatch, t_analysis, fake)
    live = t_analysis.slow_query_rca()
    assert live["worst"] is not None


@pytest.mark.unit
def test_mcp_lock_wait_rca_live_with_deadlock(gov_home, monkeypatch):
    fake = FakeMySQL({"performance_schema.data_lock_waits": [
        {"blocked_id": 200, "blocking_id": 100, "blocking_query": "UPDATE a",
         "wait_seconds": 12},
    ]}, flavor="mysql")
    _bind(monkeypatch, t_analysis, fake)
    out = t_analysis.lock_wait_rca()
    assert out["worstRootId"] == 100


@pytest.mark.unit
def test_mcp_replication_lag_rca_injected(gov_home):
    out = t_analysis.replication_lag_rca(status={"isReplica": False, "replicas": []})
    assert out["isReplica"] is False


@pytest.mark.unit
def test_mcp_fragmentation_analysis_live(gov_home, monkeypatch):
    fake = FakeMySQL({"FROM information_schema.tables": [
        {"schema": "shop", "table": "hot", "engine": "InnoDB", "est_rows": 100,
         "data_bytes": 1000, "index_bytes": 0, "free_bytes": 500 * 1024 * 1024,
         "free_pct": 40.0},
    ]})
    _bind(monkeypatch, t_analysis, fake)
    out = t_analysis.fragmentation_analysis()
    assert out["needsAttentionCount"] == 1


# ── remediation tools: dry-run, validation, confirmed writes + undo capture ──


@pytest.mark.unit
def test_mcp_remediation_dry_runs(gov_home, monkeypatch):
    fake = FakeMySQL()
    _bind(monkeypatch, t_remediation, fake)
    assert t_remediation.kill_session(session_id=1, dry_run=True)["dryRun"] is True
    assert t_remediation.kill_query(session_id=1, dry_run=True)["dryRun"] is True
    assert t_remediation.optimize_table(table="orders", dry_run=True)["dryRun"] is True
    assert t_remediation.analyze_table(table="orders", dry_run=True)["dryRun"] is True
    assert t_remediation.set_global_variable(
        name="max_connections", value="5", dry_run=True)["dryRun"] is True
    ci = t_remediation.create_index(table="orders", columns=["a"], dry_run=True)
    assert ci["wouldCreate"]["table"] == "orders"
    di = t_remediation.drop_index(table="orders", name="idx_a", dry_run=True)
    assert di["wouldDrop"]["name"] == "idx_a"
    # nothing executed on the fake
    assert fake.executed == []


@pytest.mark.unit
def test_mcp_create_index_validation_errors_are_sanitised(gov_home, monkeypatch):
    fake = FakeMySQL()
    _bind(monkeypatch, t_remediation, fake)
    # both definition and table+columns → ValueError → sanitised error dict
    both = t_remediation.create_index(table="orders", columns=["a"], definition="CREATE INDEX x")
    assert "error" in both and "not both" in both["error"]
    # neither → ValueError
    neither = t_remediation.create_index()
    assert "error" in neither


@pytest.mark.unit
def test_mcp_drop_index_confirmed_captures_definition_and_audits(gov_home, monkeypatch):
    fake = FakeMySQL({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    _bind(monkeypatch, t_remediation, fake)
    out = t_remediation.drop_index(table="orders", name="idx_orders_cid")
    assert out["priorState"]["definition"] == (
        "CREATE INDEX `idx_orders_cid` ON `orders` (`customer_id`)"
    )
    assert _audit_tools(gov_home / "audit.db") == ["drop_index"]


@pytest.mark.unit
def test_mcp_set_global_variable_confirmed_records_prior(gov_home, monkeypatch):
    fake = FakeMySQL({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "max_connections", "Value": "151"},
    ]})
    _bind(monkeypatch, t_remediation, fake)
    out = t_remediation.set_global_variable(name="max_connections", value="500")
    assert out["priorState"]["value"] == "151"
    sql, params = fake.executed[0]
    assert sql == "SET GLOBAL max_connections = %(v)s" and params == {"v": "500"}
    assert _audit_tools(gov_home / "audit.db") == ["set_global_variable"]
