"""Connection management for MySQL / MariaDB via PyMySQL.

A thin wrapper over a live MySQL-protocol connection with per-target session
reuse:

  * Non-secret connection details (host / port / database / user / ssl_mode)
    come from ``config.yaml``; the **password** is read from the encrypted
    secret store (``~/.mysql-aiops/secrets.enc``) at connect time, never from
    disk in plaintext.
  * Reads run parameterised SQL against ``information_schema`` /
    ``performance_schema``; the connection is opened ``autocommit=True`` so
    maintenance commands (``OPTIMIZE TABLE``, ``ANALYZE TABLE``, ``KILL``)
    work directly.
  * Rows come back as dicts (``DictCursor``), so the ops layer never has to
    index columns positionally.
  * The server **flavor** (mysql vs mariadb) is detected once per session from
    ``version()`` so flavor-dependent statements (``SHOW REPLICA STATUS`` vs
    ``SHOW SLAVE STATUS``) can branch.

All ``pymysql`` errors are translated centrally into ``MySQLError`` with a
teaching message rather than leaking a raw traceback to an agent.

The underlying connection is injectable for tests: pass ``connection=`` to
``MySQLConnection`` to substitute a fake that implements ``cursor()`` /
``close()`` — **no live database is required** to exercise the ops layer.
"""

from __future__ import annotations

from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from mysql_aiops.config import AppConfig, TargetConfig, load_config

_CONNECT_TIMEOUT = 30
_READ_TIMEOUT = 30
_WRITE_TIMEOUT = 30

FLAVOR_MYSQL = "mysql"
FLAVOR_MARIADB = "mariadb"


class MySQLError(Exception):
    """A MySQL operation failed; carries a teaching message + server errno."""

    def __init__(self, message: str, *, errno: int | None = None) -> None:
        self.errno = errno
        super().__init__(message)


class MySQLConnectionLostError(MySQLError):
    """An ESTABLISHED connection dropped while a statement was running.

    Distinct from an ordinary failure because the outcome is genuinely
    undetermined: the statement may have committed before the link died. InnoDB
    rolls back on connection loss, so usually nothing landed — but a COMMIT
    whose acknowledgement was lost did land, and from here the two are
    indistinguishable. The MCP layer maps this to ``status=unknown`` rather than
    asserting a failure it cannot vouch for.

    Note the discriminator is WHERE it was raised, not the errno: 2006 and 2013
    mean "could not connect" from ``connect()`` and "the link died mid-statement"
    from ``execute()``. pymysql gives both the same class and code, so position
    is the only reliable signal.
    """


_LOST_MID_STATEMENT = (2006, 2013)  # server has gone away / lost connection during query


def _errno(exc: pymysql.MySQLError) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def _teaching_message(exc: pymysql.MySQLError, target: TargetConfig) -> str:
    """Map a pymysql error to an actionable, teaching message."""
    code = _errno(exc)
    detail = str(exc).strip().splitlines()[0][:200] if str(exc) else ""
    if isinstance(exc, pymysql.err.OperationalError) and code in (2003, 2006, 2013, 1045):
        return (
            f"Could not connect to MySQL at {target.host}:{target.port}/"
            f"{target.database} as '{target.user}'. Check the host/port are "
            f"reachable, the account/password are correct, and the account is "
            f"allowed from this client host (ssl_mode={target.ssl_mode}). {detail}"
        )
    if code == 1146:  # ER_NO_SUCH_TABLE
        return (
            f"Table not found ({code}). A required performance_schema / "
            f"information_schema table is missing — performance_schema must be "
            f"enabled (performance_schema=ON) for query statistics. {detail}"
        )
    if code in (1044, 1142, 1227):  # access / privilege denied
        return (
            f"Insufficient privilege ({code}). This account lacks rights for the "
            f"operation; a monitoring account needs PROCESS + SELECT on "
            f"performance_schema, and maintenance commands need ALTER/INDEX. {detail}"
        )
    prefix = f" [{code}]" if code else ""
    return f"MySQL error{prefix} on {target.name}: {detail}"


class MySQLConnection:
    """A single authenticated session against one MySQL / MariaDB target."""

    def __init__(self, target: TargetConfig, connection: Any | None = None) -> None:
        self._target = target
        self._conn = connection if connection is not None else self._open(target)
        self._flavor: str | None = None

    @staticmethod
    def _open(target: TargetConfig) -> Any:
        try:
            return pymysql.connect(
                **target.conn_kwargs,
                cursorclass=DictCursor,
                autocommit=True,
                connect_timeout=_CONNECT_TIMEOUT,
                read_timeout=_READ_TIMEOUT,
                write_timeout=_WRITE_TIMEOUT,
            )
        except pymysql.MySQLError as exc:
            raise MySQLError(_teaching_message(exc, target), errno=_errno(exc)) from exc

    @property
    def target(self) -> TargetConfig:
        return self._target

    @property
    def flavor(self) -> str:
        """The server flavor: 'mariadb' if version() mentions MariaDB, else 'mysql'."""
        if self._flavor is None:
            version = str(self.scalar("SELECT version() AS version") or "")
            self._flavor = FLAVOR_MARIADB if "mariadb" in version.lower() else FLAVOR_MYSQL
        return self._flavor

    def query(self, sql: str, params: Any | None = None) -> list[dict]:
        """Run a read query and return rows as a list of dicts."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
        except pymysql.MySQLError as exc:
            raise MySQLError(
                _teaching_message(exc, self._target), errno=_errno(exc)
            ) from exc

    def query_one(self, sql: str, params: Any | None = None) -> dict | None:
        """Run a read query expected to return at most one row."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Any | None = None) -> Any:
        """Run a read query and return the first column of the first row (or None)."""
        row = self.query_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()), None)

    def execute(self, sql: str, params: Any | None = None) -> int:
        """Run a write/DDL/maintenance statement; return the affected row count."""
        try:
            with self._conn.cursor() as cur:
                affected = cur.execute(sql, params)
                return int(affected or 0)
        except pymysql.MySQLError as exc:
            code = _errno(exc)
            # Reached only on an established connection, so these codes mean the
            # link died mid-statement — not that we failed to reach the server.
            cls = MySQLConnectionLostError if code in _LOST_MID_STATEMENT else MySQLError
            raise cls(_teaching_message(exc, self._target), errno=code) from exc

    def close(self) -> None:
        try:
            self._conn.close()
        except pymysql.MySQLError:
            pass


class ConnectionManager:
    """Manages connections to multiple MySQL targets with session reuse."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._connections: dict[str, MySQLConnection] = {}

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> ConnectionManager:
        cfg = config or load_config()
        return cls(cfg)

    def connect(self, target_name: str | None = None) -> MySQLConnection:
        """Connect to a target by name, or the default target."""
        target = (
            self._config.get_target(target_name)
            if target_name
            else self._config.default_target
        )
        cached = self._connections.get(target.name)
        if cached is not None:
            return cached
        conn = MySQLConnection(target)
        self._connections[target.name] = conn
        return conn

    def disconnect(self, target_name: str) -> None:
        conn = self._connections.pop(target_name, None)
        if conn is not None:
            conn.close()

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def list_targets(self) -> list[str]:
        return [t.name for t in self._config.targets]

    def list_connected(self) -> list[str]:
        return list(self._connections.keys())
