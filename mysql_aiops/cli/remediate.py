"""``mysql-aiops remediate`` — guarded maintenance writes (dry-run + confirm)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from mysql_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_print,
)

remediate_app = typer.Typer(
    name="remediate",
    help="Guarded writes: kill session/query, optimize/analyze, index ops, SET GLOBAL.",
    no_args_is_help=True,
)


@remediate_app.command("kill")
@cli_errors
def remediate_kill(
    session_id: Annotated[int, typer.Argument(help="Session id to terminate")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Terminate a session (no undo; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="kill_session",
                      api_call="KILL CONNECTION <id>", parameters={"sessionId": session_id})
        return
    double_confirm("kill session", str(session_id))
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(
        gov.kill_session(session_id=session_id, target=target), default=str))


@remediate_app.command("kill-query")
@cli_errors
def remediate_kill_query(
    session_id: Annotated[int, typer.Argument(help="Session id whose statement to cancel")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Cancel a session's running statement (no undo; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="kill_query",
                      api_call="KILL QUERY <id>", parameters={"sessionId": session_id})
        return
    double_confirm("kill query on session", str(session_id))
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(
        gov.kill_query(session_id=session_id, target=target), default=str))


@remediate_app.command("optimize")
@cli_errors
def remediate_optimize(
    table: Annotated[str, typer.Argument(help="Table (optionally schema-qualified)")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """OPTIMIZE TABLE (rebuild, reclaim data_free; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="optimize_table", api_call=f"OPTIMIZE TABLE {table}")
        return
    double_confirm("OPTIMIZE TABLE", table)
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(gov.optimize_table(table=table, target=target), default=str))


@remediate_app.command("analyze-table")
@cli_errors
def remediate_analyze(
    table: Annotated[str, typer.Argument(help="Table (optionally schema-qualified)")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """ANALYZE TABLE (refresh index statistics; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="analyze_table", api_call=f"ANALYZE TABLE {table}")
        return
    double_confirm("ANALYZE TABLE", table)
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(gov.analyze_table(table=table, target=target), default=str))


@remediate_app.command("create-index")
@cli_errors
def remediate_create_index(
    table: Annotated[str, typer.Argument(help="Table to index")],
    columns: Annotated[list[str], typer.Argument(help="Column(s) to index")],
    name: Annotated[str | None, typer.Option("--name", help="Index name")] = None,
    unique: Annotated[bool, typer.Option("--unique", help="UNIQUE index")] = False,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Create an index (reversible; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="create_index", api_call=f"CREATE INDEX ON {table}",
                      parameters={"columns": columns, "name": name, "unique": unique})
        return
    double_confirm("create index on", table)
    from mcp_server.tools import remediation as gov

    result = gov.create_index(table=table, columns=columns, name=name, unique=unique,
                              target=target)
    console.print_json(json.dumps(result, default=str))


@remediate_app.command("drop-index")
@cli_errors
def remediate_drop_index(
    table: Annotated[str, typer.Argument(help="Table the index belongs to")],
    name: Annotated[str, typer.Argument(help="Index name to drop")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Drop an index (reversible; captures the definition first; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="drop_index", api_call=f"DROP INDEX {name} ON {table}")
        return
    double_confirm("drop index", f"{name} on {table}")
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(
        gov.drop_index(table=table, name=name, target=target), default=str))


@remediate_app.command("set")
@cli_errors
def remediate_set(
    name: Annotated[str, typer.Argument(help="Global variable name (e.g. max_connections)")],
    value: Annotated[str, typer.Argument(help="New value")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """SET GLOBAL a server variable (reversible; dry-run + confirm)."""
    if dry_run:
        dry_run_print(operation="set_global_variable",
                      api_call=f"SET GLOBAL {name} = ...", parameters={"value": value})
        return
    double_confirm(f"SET GLOBAL {name} =", value)
    from mcp_server.tools import remediation as gov

    console.print_json(json.dumps(
        gov.set_global_variable(name=name, value=value, target=target), default=str))
