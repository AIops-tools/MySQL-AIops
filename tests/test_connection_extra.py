"""Connection layer: teaching-error branches, close(), ConnectionManager.

Complements ``test_connection.py`` — covers the privilege / generic teaching
messages, ``execute`` error translation, ``close`` swallowing driver errors, the
``_open`` failure path, and the ``ConnectionManager`` session-reuse surface, all
against fakes (no live database).
"""

from __future__ import annotations

import pymysql
import pytest

import mysql_aiops.connection as conn_mod
from mysql_aiops.config import AppConfig, TargetConfig
from mysql_aiops.connection import (
    ConnectionManager,
    MySQLConnection,
    MySQLError,
)


class _RaisingCursor:
    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        raise self._exc

    def fetchall(self):
        return []


class _RaisingConn:
    def __init__(self, exc, *, close_exc=None):
        self._exc = exc
        self._close_exc = close_exc

    def cursor(self):
        return _RaisingCursor(self._exc)

    def close(self):
        if self._close_exc is not None:
            raise self._close_exc


def _target(**overrides):
    kwargs = {"name": "primary", "host": "db.local", "port": 3306, "user": "root"}
    kwargs.update(overrides)
    return TargetConfig(**kwargs)


@pytest.mark.unit
def test_privilege_error_gets_teaching_message():
    boom = pymysql.err.OperationalError(1044, "Access denied for user")
    conn = MySQLConnection(_target(), connection=_RaisingConn(boom))
    with pytest.raises(MySQLError, match="privilege") as ei:
        conn.query("SELECT 1")
    assert ei.value.errno == 1044
    assert "PROCESS" in str(ei.value)


@pytest.mark.unit
def test_generic_error_message_includes_target_name():
    boom = pymysql.err.InternalError(1105, "Unknown error")
    conn = MySQLConnection(_target(), connection=_RaisingConn(boom))
    with pytest.raises(MySQLError, match="primary") as ei:
        conn.query("SELECT 1")
    assert ei.value.errno == 1105


@pytest.mark.unit
def test_execute_translates_error():
    boom = pymysql.err.OperationalError(2006, "MySQL server has gone away")
    conn = MySQLConnection(_target(), connection=_RaisingConn(boom))
    with pytest.raises(MySQLError):
        conn.execute("ANALYZE TABLE t")


@pytest.mark.unit
def test_close_swallows_driver_error():
    boom = pymysql.err.OperationalError(2013, "Lost connection")
    conn = MySQLConnection(_target(), connection=_RaisingConn(RuntimeError("x"), close_exc=boom))
    conn.close()  # must not raise


@pytest.mark.unit
def test_open_failure_is_translated(monkeypatch):
    def _boom(**kwargs):
        raise pymysql.err.OperationalError(2003, "Can't connect")

    monkeypatch.setattr(conn_mod.pymysql, "connect", _boom)
    monkeypatch.setattr("mysql_aiops.config.has_store", lambda: False)
    monkeypatch.setenv("MYSQL_PRIMARY_PASSWORD", "x")
    with pytest.raises(MySQLError) as ei:
        MySQLConnection(_target())
    assert ei.value.errno == 2003


@pytest.mark.unit
def test_connection_manager_reuses_and_disconnects(monkeypatch):
    opened: list[str] = []

    class _FakeSession:
        def __init__(self, target, connection=None):
            self.target = target
            self.closed = False
            opened.append(target.name)

        def close(self):
            self.closed = True

    monkeypatch.setattr(conn_mod, "MySQLConnection", _FakeSession)
    cfg = AppConfig(targets=(_target(name="a"), _target(name="b")))
    mgr = ConnectionManager(cfg)

    first = mgr.connect()          # default target 'a' (first configured)
    second = mgr.connect("a")      # cache hit — no new session opened
    assert first is second
    assert opened == ["a"]
    assert mgr.list_targets() == ["a", "b"]
    assert mgr.list_connected() == ["a"]

    mgr.connect("b")
    mgr.disconnect_all()
    assert first.closed is True
    assert mgr.list_connected() == []


@pytest.mark.unit
def test_connection_manager_from_config_uses_loader(monkeypatch):
    cfg = AppConfig(targets=(_target(name="only"),))
    monkeypatch.setattr(conn_mod, "load_config", lambda: cfg)
    mgr = ConnectionManager.from_config()
    assert mgr.list_targets() == ["only"]
