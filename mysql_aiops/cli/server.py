"""``mysql-aiops server`` — server-level reads."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from mysql_aiops.cli._common import TargetOption, cli_errors, console, get_connection

server_app = typer.Typer(
    name="server",
    help="Server reads: version+flavor, variables, status, databases, engines.",
    no_args_is_help=True,
)


@server_app.command("version")
@cli_errors
def server_version(target: TargetOption = None) -> None:
    """Server version, flavor (mysql/mariadb), uptime and read-only state."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.server_version(conn)))


@server_app.command("variables")
@cli_errors
def server_variables(
    pattern: Annotated[str | None, typer.Argument(help="Name substring filter")] = None,
    target: TargetOption = None,
) -> None:
    """Global variables (SHOW GLOBAL VARIABLES), optionally filtered by name."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.show_variables(conn, pattern)))


@server_app.command("status")
@cli_errors
def server_status(
    pattern: Annotated[str | None, typer.Argument(help="Name substring filter")] = None,
    target: TargetOption = None,
) -> None:
    """Global status counters (SHOW GLOBAL STATUS), optionally filtered by name."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.show_status(conn, pattern)))


@server_app.command("databases")
@cli_errors
def server_databases(target: TargetOption = None) -> None:
    """User schemas with table count and data/index size."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_databases(conn)))


@server_app.command("engines")
@cli_errors
def server_engines(target: TargetOption = None) -> None:
    """Storage engines and which is the default."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_engines(conn)))


@server_app.command("connections")
@cli_errors
def server_connections(target: TargetOption = None) -> None:
    """Connection counters vs max_connections."""
    from mysql_aiops.ops import server as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.connection_stats(conn)))
