"""CLI ``remediate`` command bodies — dry-run previews and abort paths.

EVERY remediate dry-run routes through its ``@governed_tool`` twin with
``dry_run=True``. That is what makes the preview trustworthy: the twin's guards
run against the real target, so a preview of a call that will be refused reports
the refusal instead of a green banner, and the preview lands an audit row like
any other governed call. The banner itself is unchanged — routing through the
governed call buys the guard and the audit row, not a new serialization.

The invariant these tests hold the previews to is **a dry_run MAY read; it must
never write**. Connecting and fetching before-state is allowed; issuing a
mutating statement is not, and ``mutating_statements`` checks both the
``execute`` and the ``query`` paths because OPTIMIZE/ANALYZE travel by ``query``.
A couple of abort-on-confirm cases exercise the ``double_confirm`` gate.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mysql_aiops.cli import app
from tests.conftest import FakeMySQL, mutating_statements

runner = CliRunner()


@pytest.fixture
def guarded_conn(monkeypatch):
    """Wire the governed remediation tools to a fake whose own session id is 1.

    Required by every remediate preview now, not just the self-lockout-guarded
    ones: routing a preview through the governed twin means the twin opens a
    connection, so an unwired test would try to reach a real server.
    """
    import mcp_server.tools.remediation as gov

    fake = FakeMySQL(
        {"FROM information_schema.processlist": [{"id": 42, "user": "app"}],
         "SHOW GLOBAL VARIABLES": [{"Variable_name": "long_query_time", "Value": "10"}]},
        scalars={"CONNECTION_ID()": 1},
    )
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    return fake


@pytest.mark.unit
def test_remediate_kill_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "kill", "42", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output and "kill_session" in result.output
    assert "sessionId = 42" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_kill_dry_run_reports_a_self_targeted_refusal(guarded_conn):
    """The preview must not show a green banner for a call that will be refused."""
    result = runner.invoke(app, ["remediate", "kill", "1", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "calling through" in result.output


@pytest.mark.unit
def test_remediate_kill_query_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "kill-query", "7", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "kill_query" in result.output and "sessionId = 7" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_optimize_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "optimize", "shop.orders", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "optimize_table" in result.output
    assert "OPTIMIZE TABLE shop.orders" in result.output
    # OPTIMIZE travels by query(), not execute() — check both.
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_analyze_table_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "analyze-table", "orders", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "analyze_table" in result.output
    assert "ANALYZE TABLE orders" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_create_index_dry_run_lists_params(guarded_conn):
    result = runner.invoke(app, [
        "remediate", "create-index", "orders", "email",
        "--name", "idx_email", "--unique", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert "create_index" in result.output
    assert "idx_email" in result.output
    assert "unique = True" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_create_index_dry_run_banner_reports_the_governed_answer(guarded_conn):
    """The banner's columns/name come from the twin's ``wouldCreate``, not from
    the CLI re-stating its own arguments — so it cannot drift from the tool."""
    import mcp_server.tools.remediation as gov

    result = runner.invoke(app, [
        "remediate", "create-index", "orders", "email", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    would = gov.create_index(table="orders", columns=["email"], dry_run=True)["wouldCreate"]
    assert f"columns = {would['columns']}" in result.output


@pytest.mark.unit
def test_remediate_drop_index_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "drop-index", "orders", "idx_email", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "drop_index" in result.output
    assert "DROP INDEX idx_email ON orders" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_drop_index_dry_run_reports_an_unreachable_target(monkeypatch):
    """A preview that cannot reach the server must not claim it would succeed.

    Before the reroute this printed a green DRY-RUN banner composed entirely
    from the CLI's own arguments — the connection was never attempted, so a
    misconfigured target looked identical to a healthy one until the real run.

    The message is ``tool_errors``' sanitized one, not the raw OSError text:
    what matters here is that the failure surfaces as a failure at all.
    """
    import mcp_server.tools.remediation as gov

    def _boom(target=None):
        raise OSError("connection refused")

    monkeypatch.setattr(gov, "_get_connection", _boom)
    result = runner.invoke(app, ["remediate", "drop-index", "orders", "idx_email", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "Error:" in result.output and "OSError" in result.output


@pytest.mark.unit
def test_remediate_set_dry_run(guarded_conn):
    result = runner.invoke(app, ["remediate", "set", "long_query_time", "1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "set_global_variable" in result.output
    assert "value = 1" in result.output
    assert mutating_statements(guarded_conn) == [], "a dry-run must never write"


@pytest.mark.unit
def test_remediate_set_dry_run_reports_a_denylisted_global(guarded_conn):
    """max_connections=1 locks out every later connection, undo included."""
    result = runner.invoke(app, ["remediate", "set", "max_connections", "1", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "my.cnf" in result.output


@pytest.mark.unit
def test_remediate_optimize_aborts_on_first_no():
    # No --dry-run → double_confirm; answering 'n' to confirm 1/2 aborts and
    # never reaches the governed import/execution.
    result = runner.invoke(app, ["remediate", "optimize", "orders"], input="n\n")
    assert result.exit_code != 0
    assert "Aborted" in result.output or result.exit_code == 1


@pytest.mark.unit
def test_remediate_set_aborts_on_second_no():
    result = runner.invoke(app, ["remediate", "set", "max_connections", "500"], input="y\nn\n")
    assert result.exit_code != 0
