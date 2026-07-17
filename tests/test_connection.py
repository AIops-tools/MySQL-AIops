"""Tests for the PyMySQL-backed connection layer (no live database).

A fake connection/cursor stands in for a real pymysql connection so the row
mapping, scalar/execute helpers, flavor detection, error translation and the
ssl_mode → PyMySQL kwargs mapping are exercised offline.
"""

from __future__ import annotations

import pymysql
import pytest

from mysql_aiops.config import TargetConfig
from mysql_aiops.connection import MySQLConnection, MySQLError


class FakeCursor:
    def __init__(self, rows, *, raise_exc=None):
        self._rows = rows
        self._raise = raise_exc
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed = (sql, params)
        if self._raise is not None:
            raise self._raise
        return len(self._rows)

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, rows=None, *, raise_exc=None):
        self._rows = rows or []
        self._raise = raise_exc
        self.closed = False

    def cursor(self):
        return FakeCursor(self._rows, raise_exc=self._raise)

    def close(self):
        self.closed = True


def _target(**overrides):
    kwargs = {"name": "primary", "host": "db.local", "port": 3306, "user": "root"}
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


@pytest.mark.unit
def test_query_maps_rows_to_dicts():
    conn = MySQLConnection(_target(), connection=FakeConn([{"a": 1, "b": 2}]))
    rows = conn.query("SELECT a, b FROM t")
    assert rows == [{"a": 1, "b": 2}]


@pytest.mark.unit
def test_query_one_and_scalar():
    conn = MySQLConnection(_target(), connection=FakeConn([{"version": "8.4.2"}]))
    assert conn.query_one("SELECT version() AS version")["version"] == "8.4.2"
    assert conn.scalar("SELECT version() AS version") == "8.4.2"


@pytest.mark.unit
def test_scalar_none_when_empty():
    conn = MySQLConnection(_target(), connection=FakeConn([]))
    assert conn.scalar("SELECT 1") is None


@pytest.mark.unit
def test_execute_returns_affected_rows():
    conn = MySQLConnection(_target(), connection=FakeConn([{"x": 1}]))
    assert conn.execute("ANALYZE TABLE t") == 1


@pytest.mark.unit
def test_flavor_detected_from_version_and_cached():
    conn = MySQLConnection(
        _target(), connection=FakeConn([{"version": "11.4.2-MariaDB-log"}])
    )
    assert conn.flavor == "mariadb"
    conn_mysql = MySQLConnection(
        _target(), connection=FakeConn([{"version": "8.0.39"}])
    )
    assert conn_mysql.flavor == "mysql"


@pytest.mark.unit
def test_query_translates_operational_error_to_mysqlerror():
    boom = pymysql.err.OperationalError(2003, "Can't connect to MySQL server")
    conn = MySQLConnection(_target(), connection=FakeConn(raise_exc=boom))
    with pytest.raises(MySQLError) as ei:
        conn.query("SELECT 1")
    assert "Could not connect" in str(ei.value) or "db.local" in str(ei.value)
    assert ei.value.errno == 2003


@pytest.mark.unit
def test_missing_perf_schema_table_gets_teaching_message():
    boom = pymysql.err.ProgrammingError(1146, "Table doesn't exist")
    conn = MySQLConnection(_target(), connection=FakeConn(raise_exc=boom))
    with pytest.raises(MySQLError, match="performance_schema"):
        conn.query("SELECT * FROM performance_schema.events_statements_summary_by_digest")


@pytest.mark.unit
def test_conn_kwargs_include_password_from_legacy_env(monkeypatch):
    import mysql_aiops.config as cfg

    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.setenv("MYSQL_PRIMARY_PASSWORD", "s3cr3t")
    kwargs = _target().conn_kwargs
    assert kwargs["password"] == "s3cr3t"
    assert kwargs["host"] == "db.local"
    assert kwargs["program_name"] == "mysql-aiops"


@pytest.mark.unit
def test_ssl_mode_mapping(monkeypatch):
    import mysql_aiops.config as cfg

    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.setenv("MYSQL_PRIMARY_PASSWORD", "x")

    assert _target(ssl_mode="disabled").conn_kwargs["ssl_disabled"] is True
    preferred = _target(ssl_mode="preferred").conn_kwargs
    assert "ssl" not in preferred and "ssl_disabled" not in preferred
    assert _target(ssl_mode="required").conn_kwargs["ssl"] == {}
    verify = _target(ssl_mode="verify_ca", ssl_ca="/etc/ssl/ca.pem").conn_kwargs
    assert verify["ssl_ca"] == "/etc/ssl/ca.pem" and verify["ssl_verify_cert"] is True
    identity = _target(ssl_mode="verify_identity", ssl_ca="/etc/ssl/ca.pem").conn_kwargs
    assert identity["ssl_verify_identity"] is True


@pytest.mark.unit
def test_ssl_mode_verify_requires_ca(monkeypatch):
    import mysql_aiops.config as cfg

    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.setenv("MYSQL_PRIMARY_PASSWORD", "x")
    with pytest.raises(ValueError, match="ssl_ca"):
        _ = _target(ssl_mode="verify_ca").conn_kwargs


@pytest.mark.unit
def test_unknown_ssl_mode_rejected(monkeypatch):
    import mysql_aiops.config as cfg

    monkeypatch.setattr(cfg, "has_store", lambda: False)
    monkeypatch.setenv("MYSQL_PRIMARY_PASSWORD", "x")
    with pytest.raises(ValueError, match="ssl_mode"):
        _ = _target(ssl_mode="sometimes").conn_kwargs


@pytest.mark.unit
def test_dsn_redacted_hides_password():
    assert "***" in _target().dsn_redacted
    assert "s3cr3t" not in _target().dsn_redacted
