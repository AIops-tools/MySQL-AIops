"""CLI write path — preview and confirmed write, both through governance.

The CLI write commands delegate BOTH the preview and the real execution to the
``@governed_tool`` functions in ``mcp_server.tools``. These tests drive a write
command past the double-confirm prompts and assert the call really went through
the governed path (audit row on disk) — the regression test for the "CLI writes
were unaudited" line-wide fix — and hold the ``--dry-run`` branch to the rule
that survives: **a dry_run MAY read; it must never write**, and it is audited
just like the MCP previews always have been.
"""

from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

import mysql_aiops.governance.audit as audit_mod
import mysql_aiops.governance.policy as policy_mod
import mysql_aiops.governance.undo as undo_mod
from tests.conftest import mutating_statements


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


def _audit_tools(db_path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT tool FROM audit_log ORDER BY id")]
    finally:
        conn.close()


@pytest.mark.unit
def test_cli_query_reset_dry_run_writes_nothing_but_is_audited(gov_home, monkeypatch, fake_mysql):
    """A dry_run MAY read; it must never write — and it IS audited.

    The old version of this test asserted the preview made no call at all and
    left no audit row. Both halves were wrong: the MCP path has always audited
    previews (``@governed_tool`` wraps the function regardless of the ``dry_run``
    argument), so the CLI's silence was the outlier, and forbidding reads would
    forbid routing through the governed twin — which is what runs the guards.
    The surviving rule is the one that matters: no mutating statement.
    """
    from mysql_aiops.cli import app

    fake = fake_mysql()
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset", "--dry-run"])
    assert result.exit_code == 0
    assert "DRY-RUN" in result.output
    assert mutating_statements(fake) == [], "a dry-run must never write"
    assert not any("TRUNCATE" in sql.upper() for sql, _ in fake.executed)
    assert _audit_tools(gov_home / "audit.db") == ["reset_query_stats"]


@pytest.mark.unit
def test_cli_remediate_drop_index_dry_run_writes_nothing_but_is_audited(
    gov_home, monkeypatch, fake_mysql
):
    """Same invariant for a high-risk preview: audited, and nothing dropped."""
    from mysql_aiops.cli import app

    fake = fake_mysql()
    import mcp_server.tools.remediation as gov_rem

    monkeypatch.setattr(gov_rem, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["remediate", "drop-index", "orders", "idx_email",
                                      "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    assert mutating_statements(fake) == [], "a dry-run must never write"
    assert _audit_tools(gov_home / "audit.db") == ["drop_index"]


@pytest.mark.unit
def test_cli_remediate_kill_refused_dry_run_exits_nonzero_and_is_audited(gov_home, monkeypatch):
    """A refused preview: non-zero exit, no success banner, and an audit row.

    The refusal is the whole point of routing the preview through the twin. A
    green banner here would teach a weak model that the write is available, and
    the refusal it then hits reads as transient — so it retries.
    """
    from mysql_aiops.cli import app
    from tests.conftest import FakeMySQL

    fake = FakeMySQL(scalars={"CONNECTION_ID()": 7})
    import mcp_server.tools.remediation as gov_rem

    monkeypatch.setattr(gov_rem, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["remediate", "kill", "7", "--dry-run"])
    assert result.exit_code == 1, result.output
    assert "DRY-RUN" not in result.output
    assert "calling through" in result.output
    assert mutating_statements(fake) == [], "a refused dry-run must never write"
    assert _audit_tools(gov_home / "audit.db") == ["kill_session"]


@pytest.mark.unit
def test_cli_query_reset_confirmed_goes_through_governance(gov_home, monkeypatch, fake_mysql):
    """Confirmed CLI write must execute via the governed twin: the SQL runs
    AND an audit row lands in audit.db (this is what the reroute fix bought)."""
    from mysql_aiops.cli import app

    fake = fake_mysql()
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert any("TRUNCATE TABLE" in sql for sql, _ in fake.executed)
    assert _audit_tools(gov_home / "audit.db") == ["reset_query_stats"]


@pytest.mark.unit
def test_cli_query_reset_aborts_without_double_confirm(gov_home, monkeypatch, fake_mysql):
    from mysql_aiops.cli import app

    fake = fake_mysql()
    import mcp_server.tools.queries as gov_queries

    monkeypatch.setattr(gov_queries, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["query", "reset"], input="y\nn\n")
    assert result.exit_code != 0
    assert fake.executed == [] and fake.queried == []
    assert not (gov_home / "audit.db").exists()


@pytest.mark.unit
def test_cli_remediate_optimize_confirmed_audits(gov_home, monkeypatch, fake_mysql):
    """A remediate write past double-confirm lands an audit row on disk."""
    from mysql_aiops.cli import app

    fake = fake_mysql({
        "FROM information_schema.tables": [{"table_rows": 10, "data_free": 0}],
        "OPTIMIZE TABLE": [{"Table": "t", "Op": "optimize", "Msg_type": "status",
                            "Msg_text": "OK"}],
    })
    import mcp_server.tools.remediation as gov_rem

    monkeypatch.setattr(gov_rem, "_get_connection", lambda target=None: fake)
    result = CliRunner().invoke(app, ["remediate", "optimize", "orders"], input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert _audit_tools(gov_home / "audit.db") == ["optimize_table"]


# ── refusals must teach, not traceback ────────────────────────────────────────
#
# ``PolicyDenied``/``BudgetExceeded`` are raised by ``@governed_tool`` OUTSIDE the
# tool body, so ``tool_errors`` never flattens them into ``{"error": ...}`` and
# ``dry_run_preview``'s dict check cannot see them. Before they were listed in
# ``_cli_error_types`` a refused preview reached the operator as a raw traceback:
# the teaching text was in there, buried under a stack dump. A weak model reads
# that as a crash and retries — the very loop the preview reroute exists to stop.


def test_cli_error_types_covers_governance_refusals() -> None:
    """A governance refusal must be translated, not dumped as a traceback."""
    from mysql_aiops.cli._common import _cli_error_types
    from mysql_aiops.governance import BudgetExceeded, PolicyDenied

    types = _cli_error_types()
    assert PolicyDenied in types
    assert BudgetExceeded in types


