"""``mysql-aiops query`` — statement-digest top-N, EXPLAIN, stats reset."""

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
    get_connection,
)

query_app = typer.Typer(
    name="query",
    help="Query stats: top-N digests, EXPLAIN, reset.",
    no_args_is_help=True,
)


@query_app.command("top")
@cli_errors
def query_top(
    order_by: Annotated[
        str,
        typer.Option(
            "--order-by",
            help="total_time|mean_time|calls|rows_examined|lock_time|no_index",
        ),
    ] = "total_time",
    limit: Annotated[int, typer.Option("--limit", help="Rows to return")] = 20,
    target: TargetOption = None,
) -> None:
    """Top statement digests from performance_schema."""
    from mysql_aiops.ops import queries as ops

    conn, _ = get_connection(target)
    result = ops.top_queries(conn, order_by=order_by, limit=limit)
    console.print_json(json.dumps(result, default=str))
    if result.get("truncated"):
        console.print(
            f"[yellow]… truncated at {result.get('limit')} rows — "
            f"re-run with a higher --limit to see the rest.[/yellow]"
        )


@query_app.command("explain")
@cli_errors
def query_explain(
    sql: Annotated[str, typer.Argument(help="A single SQL statement to EXPLAIN")],
    target: TargetOption = None,
) -> None:
    """Return the JSON execution plan for a statement (planned, not executed)."""
    from mysql_aiops.ops import queries as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.explain_query(conn, sql), default=str))


@query_app.command("reset")
@cli_errors
def query_reset(target: TargetOption = None, dry_run: DryRunOption = False) -> None:
    """Reset statement-digest accumulators (irreversible; dry-run + confirm).

    Real execution is delegated to the ``@governed_tool``-wrapped MCP function
    so the reset is audited on the same governance path as MCP calls.
    """
    from mcp_server.tools import queries as gov

    if dry_run:
        dry_run_print(
            operation="reset_query_stats",
            api_call="TRUNCATE TABLE performance_schema.events_statements_summary_by_digest",
        )
        return
    double_confirm("reset statement digest stats on", "this target")
    console.print_json(json.dumps(gov.reset_query_stats(target=target), default=str))
