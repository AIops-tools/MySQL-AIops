"""CLI ``remediate`` command bodies — dry-run previews and abort paths.

The dry-run branch of every remediate command returns *before* importing the
governed twin, so these exercise the CLI argument wiring and the
``dry_run_print`` preview with no database or governance side effects. A couple
of abort-on-confirm cases exercise the ``double_confirm`` gate.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from mysql_aiops.cli import app

runner = CliRunner()


@pytest.mark.unit
def test_remediate_kill_dry_run():
    result = runner.invoke(app, ["remediate", "kill", "42", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output and "kill_session" in result.output
    assert "sessionId = 42" in result.output


@pytest.mark.unit
def test_remediate_kill_query_dry_run():
    result = runner.invoke(app, ["remediate", "kill-query", "7", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "kill_query" in result.output and "sessionId = 7" in result.output


@pytest.mark.unit
def test_remediate_optimize_dry_run():
    result = runner.invoke(app, ["remediate", "optimize", "shop.orders", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "optimize_table" in result.output
    assert "OPTIMIZE TABLE shop.orders" in result.output


@pytest.mark.unit
def test_remediate_analyze_table_dry_run():
    result = runner.invoke(app, ["remediate", "analyze-table", "orders", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "analyze_table" in result.output
    assert "ANALYZE TABLE orders" in result.output


@pytest.mark.unit
def test_remediate_create_index_dry_run_lists_params():
    result = runner.invoke(app, [
        "remediate", "create-index", "orders", "email",
        "--name", "idx_email", "--unique", "--dry-run",
    ])
    assert result.exit_code == 0, result.output
    assert "create_index" in result.output
    assert "idx_email" in result.output
    assert "unique = True" in result.output


@pytest.mark.unit
def test_remediate_drop_index_dry_run():
    result = runner.invoke(app, ["remediate", "drop-index", "orders", "idx_email", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "drop_index" in result.output
    assert "DROP INDEX idx_email ON orders" in result.output


@pytest.mark.unit
def test_remediate_set_dry_run():
    result = runner.invoke(app, ["remediate", "set", "max_connections", "500", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "set_global_variable" in result.output
    assert "value = 500" in result.output


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
