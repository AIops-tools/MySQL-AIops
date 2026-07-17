"""Environment and connectivity diagnostics for MySQL AIops."""

from __future__ import annotations

from rich.console import Console

from mysql_aiops.config import CONFIG_FILE, ENV_FILE, load_config
from mysql_aiops.secretstore import SECRETS_FILE, check_permissions, has_store

_console = Console()


def _check_target_live(conn) -> list[str]:
    """Version/flavor + performance_schema + replica-role probes for one target.

    Returns a list of warning strings (never raises — callers already guard).
    """
    warnings: list[str] = []
    version = str(conn.scalar("SELECT version() AS version") or "?")[:80]
    flavor = "mariadb" if "mariadb" in version.lower() else "mysql"
    _console.print(f"  [green]✓ version {version} (flavor: {flavor})[/]")

    ps_row = conn.query_one("SHOW GLOBAL VARIABLES LIKE 'performance_schema'") or {}
    if str(ps_row.get("Value", "")).upper() == "ON":
        _console.print("  [green]✓ performance_schema is ON (query stats available)[/]")
    else:
        warnings.append(
            "performance_schema is OFF — top_queries / slow_query_rca will be "
            "empty. Enable performance_schema=ON in the server config."
        )

    stmt = "SHOW SLAVE STATUS" if flavor == "mariadb" else "SHOW REPLICA STATUS"
    try:
        replica_rows = conn.query(stmt)
    except Exception:  # noqa: BLE001 — role detection is best-effort (needs REPLICATION CLIENT)
        replica_rows = []
        warnings.append(
            "Could not read replica status (needs REPLICATION CLIENT privilege) "
            "— role detection skipped."
        )
    if replica_rows:
        behind = replica_rows[0].get("Seconds_Behind_Source",
                                     replica_rows[0].get("Seconds_Behind_Master"))
        _console.print(f"  [green]✓ role: replica (seconds behind source: {behind})[/]")
    else:
        _console.print("  [green]✓ role: primary/standalone (no replication channel)[/]")
    return warnings


def run_doctor(skip_auth: bool = False) -> int:
    """Check config, secrets, and (optionally) connectivity + server capability.

    Returns a process exit code: 0 healthy, 1 problems found. Connectivity
    failures are reported as status, never raised as tracebacks (a doctor must
    survive the thing it diagnoses being unhealthy).
    """
    problems = 0

    if not CONFIG_FILE.exists():
        _console.print(f"[red]✗ Config file missing: {CONFIG_FILE}[/]")
        _console.print("[yellow]  Run 'mysql-aiops init' to set up your first target.[/]")
        return 1
    _console.print(f"[green]✓ Config file present: {CONFIG_FILE}[/]")

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 — report, do not crash
        _console.print(f"[red]✗ Config load failed: {exc}[/]")
        return 1

    if not config.targets:
        _console.print("[red]✗ No targets configured[/]")
        return 1
    _console.print(f"[green]✓ {len(config.targets)} target(s) configured[/]")

    if has_store():
        _console.print(f"[green]✓ Encrypted secret store present: {SECRETS_FILE}[/]")
        perm_warning = check_permissions()
        if perm_warning:
            _console.print(f"[yellow]! {perm_warning}[/]")
    elif ENV_FILE.exists():
        _console.print(
            f"[yellow]! Using legacy plaintext .env ({ENV_FILE}). Migrate with "
            f"'mysql-aiops secret migrate'.[/]"
        )
    else:
        _console.print(
            "[yellow]! No secret store yet. Run 'mysql-aiops init' to set up "
            "credentials (stored encrypted).[/]"
        )
        problems += 1

    for target in config.targets:
        try:
            _ = target.password
            _console.print(f"[green]✓ password present for '{target.name}'[/]")
        except OSError as exc:
            _console.print(f"[red]✗ {exc}[/]")
            problems += 1

    if skip_auth:
        _console.print("[dim]Skipping connectivity check (--skip-auth).[/]")
        return 1 if problems else 0

    from mysql_aiops.connection import ConnectionManager

    mgr = ConnectionManager(config)
    for target in config.targets:
        try:
            conn = mgr.connect(target.name)
            _console.print(
                f"[green]✓ Connected to '{target.name}' ({target.host}:{target.port})[/]"
            )
            for warning in _check_target_live(conn):
                _console.print(f"  [yellow]! {warning}[/]")
        except Exception as exc:  # noqa: BLE001 — connectivity is a status, not a crash
            _console.print(f"[red]✗ Connect to '{target.name}' failed: {exc}[/]")
            problems += 1

    return 1 if problems else 0
