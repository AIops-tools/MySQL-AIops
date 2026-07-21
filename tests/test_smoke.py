"""Smoke + harness tests for mysql-aiops.

Proves: every module imports, the CLI Typer app builds and --help works, the MCP
server exposes the expected tools, EVERY MCP tool carries the harness marker
``_is_governed_tool``, the write tools have the correct risk tiers and dry-run
gating, and undo is recorded through the harness from the REAL fetched
before-state. No live MySQL is needed — the connection is faked.
"""

import asyncio
import importlib

import pytest
from typer.testing import CliRunner

from tests.conftest import FakeMySQL, mutating_statements

EXPECTED_TOOLS = {
    # server
    "overview", "server_version", "show_variables", "show_status",
    "list_databases", "list_engines", "connection_stats",
    # activity
    "list_sessions", "long_running_queries", "list_transactions", "lock_waits",
    # queries
    "top_queries", "explain_query", "reset_query_stats",
    # indexes
    "unused_indexes", "redundant_indexes", "index_stats",
    # tables
    "table_sizes", "table_fragmentation", "table_status",
    # replication
    "replica_status", "binlog_status",
    # flagship
    "slow_query_rca", "lock_wait_rca", "replication_lag_rca",
    "fragmentation_analysis",
    # writes
    "kill_session", "kill_query", "optimize_table", "analyze_table",
    "create_index", "drop_index", "set_global_variable",
}

WRITE_RISK = {
    "kill_session": "high",
    "kill_query": "high",
    "drop_index": "high",
    "optimize_table": "medium",
    "analyze_table": "medium",
    "create_index": "medium",
    "set_global_variable": "medium",
    "reset_query_stats": "medium",
}

_CREATE_TABLE_DDL = (
    "CREATE TABLE `orders` (\n"
    "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
    "  `customer_id` bigint NOT NULL,\n"
    "  PRIMARY KEY (`id`),\n"
    "  KEY `idx_orders_cid` (`customer_id`)\n"
    ") ENGINE=InnoDB"
)


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "mysql_aiops", "mysql_aiops.config", "mysql_aiops.connection",
        "mysql_aiops.doctor", "mysql_aiops.secretstore",
        "mysql_aiops.ops.server", "mysql_aiops.ops.activity",
        "mysql_aiops.ops.queries", "mysql_aiops.ops.indexes",
        "mysql_aiops.ops.tables", "mysql_aiops.ops.replication",
        "mysql_aiops.ops.analysis", "mysql_aiops.ops.remediation",
        "mysql_aiops.ops.overview",
        "mysql_aiops.cli", "mysql_aiops.cli._root", "mysql_aiops.cli._common",
        "mysql_aiops.cli.init", "mysql_aiops.cli.secret",
        "mcp_server.server", "mcp_server._shared",
        "mcp_server.tools.server", "mcp_server.tools.activity",
        "mcp_server.tools.queries", "mcp_server.tools.indexes",
        "mcp_server.tools.tables", "mcp_server.tools.replication",
        "mcp_server.tools.analysis", "mcp_server.tools.remediation",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import mysql_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert mysql_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from mysql_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("server", "activity", "query", "index", "table", "repl",
                "analyze", "remediate", "secret", "init", "overview", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    from mysql_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["server", "--help"], ["activity", "--help"], ["query", "--help"],
        ["index", "--help"], ["table", "--help"], ["repl", "--help"],
        ["analyze", "--help"], ["remediate", "--help"], ["secret", "--help"],
        ["server", "version", "--help"], ["activity", "long", "--help"],
        ["activity", "lock-waits", "--help"], ["query", "top", "--help"],
        ["query", "explain", "--help"], ["index", "unused", "--help"],
        ["table", "fragmentation", "--help"], ["repl", "status", "--help"],
        ["analyze", "slow-query", "--help"], ["analyze", "lock-waits", "--help"],
        ["analyze", "replication", "--help"], ["analyze", "fragmentation", "--help"],
        ["remediate", "optimize", "--help"], ["remediate", "kill", "--help"],
        ["remediate", "drop-index", "--help"], ["remediate", "set", "--help"],
        ["overview", "--help"], ["init", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), (
            f"{name} is not wrapped with @governed_tool (harness marker missing)"
        )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import queries as q
    from mcp_server.tools import remediation as rem

    assert q.reset_query_stats._risk_level == "medium"
    for tool_name, expected in WRITE_RISK.items():
        if tool_name == "reset_query_stats":
            continue
        assert getattr(rem, tool_name)._risk_level == expected, tool_name


@pytest.mark.unit
def test_drop_index_records_undo_from_captured_definition(monkeypatch):
    """drop_index through the harness records an inverse recreate from the
    definition captured out of SHOW CREATE TABLE."""
    import mysql_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = FakeMySQL({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.drop_index(table="orders", name="idx_orders_cid")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "create_index"
    # the captured prior definition must be a replayable CREATE INDEX statement
    assert recorded["descriptor"]["params"]["definition"] == (
        "CREATE INDEX `idx_orders_cid` ON `orders` (`customer_id`)"
    )
    assert result.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_create_index_undo_drops_created_name(monkeypatch):
    """create_index through the harness records an inverse that drops the new index."""
    import mysql_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = FakeMySQL()
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-2"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.create_index(table="shop.orders", columns=["customer_id"], name="idx_new")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "drop_index"
    assert recorded["descriptor"]["params"]["name"] == "idx_new"
    assert recorded["descriptor"]["params"]["table"] == "shop.orders"


@pytest.mark.unit
def test_set_global_variable_undo_restores_prior_value(monkeypatch):
    """set_global_variable records an inverse SET GLOBAL back to the prior value."""
    import mysql_aiops.governance.undo as undo_mod
    from mcp_server.tools import remediation as rem

    conn = FakeMySQL({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "long_query_time", "Value": "10"},
    ]})
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-3"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = rem.set_global_variable(name="long_query_time", value="1")
    assert "error" not in result
    assert recorded["descriptor"]["tool"] == "set_global_variable"
    assert recorded["descriptor"]["params"] == {"name": "long_query_time", "value": "10"}


@pytest.mark.unit
def test_dry_run_gates_destructive_cli(monkeypatch):
    """remediate drop-index --dry-run previews without dropping anything.

    The preview routes through the governed twin, so it DOES open a connection
    (that is how the twin's guards get to run) — what it must never do is issue
    the DROP.
    """
    from mcp_server.tools import remediation as rem
    from mysql_aiops.cli import app

    conn = FakeMySQL()
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)
    runner = CliRunner()
    result = runner.invoke(app, ["remediate", "drop-index", "orders", "idx_x", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert mutating_statements(conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_dry_run_mcp_write_does_not_execute(monkeypatch):
    from mcp_server.tools import remediation as rem

    conn = FakeMySQL()
    monkeypatch.setattr(rem, "_get_connection", lambda target=None: conn)
    out = rem.optimize_table(table="shop.orders", dry_run=True)
    assert out.get("dryRun") is True
    assert conn.executed == [] and conn.queried == []


@pytest.mark.unit
def test_risk_level_agrees_with_read_write_docstring_tag():
    """The two write-markers must never drift apart.

    A tool's ``risk_level`` decides its audit tier and whether it gets dry-run /
    undo handling; its ``[READ]``/``[WRITE]`` docstring tag is what the docs and
    capability tables are built from. If a ``[WRITE]`` were left ``risk_level=low``
    it would be audited as a read and skip the write machinery — this test caught
    16 such mislabels line-wide once, so it is kept even though read-only mode
    (its original motivation) is gone.
    """
    from mcp_server import server

    untagged, mismatched = [], []
    for name, tool in server.mcp._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").lstrip()
        if doc.startswith("[READ]"):
            tagged_as_read = True
        elif doc.startswith("[WRITE]"):
            tagged_as_read = False
        else:
            untagged.append(name)
            continue
        if tagged_as_read != (getattr(tool.fn, "_risk_level", "low") == "low"):
            mismatched.append(name)

    assert not untagged, f"tools missing a [READ]/[WRITE] docstring tag: {untagged}"
    assert not mismatched, f"risk_level disagrees with the docstring tag: {mismatched}"
