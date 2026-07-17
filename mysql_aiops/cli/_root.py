"""Top-level Typer app: assembles sub-apps and top-level commands."""

from __future__ import annotations

import typer

from mysql_aiops.cli._common import cli_errors
from mysql_aiops.cli.activity import activity_app
from mysql_aiops.cli.analyze import analyze_app
from mysql_aiops.cli.doctor import doctor_cmd
from mysql_aiops.cli.index import index_app
from mysql_aiops.cli.init import init_cmd
from mysql_aiops.cli.overview import overview_cmd
from mysql_aiops.cli.query import query_app
from mysql_aiops.cli.remediate import remediate_app
from mysql_aiops.cli.replication import repl_app
from mysql_aiops.cli.secret import secret_app
from mysql_aiops.cli.server import server_app
from mysql_aiops.cli.table import table_app
from mysql_aiops.cli.undo import undo_app

app = typer.Typer(
    name="mysql-aiops",
    help="Governed AI-ops for MySQL / MariaDB DBA operations.",
    no_args_is_help=True,
)

app.add_typer(server_app, name="server")
app.add_typer(activity_app, name="activity")
app.add_typer(query_app, name="query")
app.add_typer(index_app, name="index")
app.add_typer(table_app, name="table")
app.add_typer(repl_app, name="repl")
app.add_typer(analyze_app, name="analyze")
app.add_typer(remediate_app, name="remediate")
app.add_typer(secret_app, name="secret")
app.add_typer(undo_app, name="undo")
app.command("init")(init_cmd)
app.command("overview")(overview_cmd)
app.command("doctor")(doctor_cmd)


@app.command("mcp")
@cli_errors
def mcp_cmd() -> None:
    """Start the MCP server (stdio transport).

    Single-command entry point for MCP clients (does not go through uvx/PyPI
    resolution at launch):
        mysql-aiops mcp
    """
    import sys

    if sys.version_info < (3, 11):
        typer.echo(
            f"ERROR: mysql-aiops requires Python >= 3.11 "
            f"(got {sys.version_info.major}.{sys.version_info.minor}).\n"
            f"Fix: uv python install 3.12 && "
            f"uv tool install --python 3.12 --force mysql-aiops",
            err=True,
        )
        raise typer.Exit(2)

    from mcp_server.server import main as _mcp_main

    _mcp_main()


if __name__ == "__main__":
    app()
