"""Write-path ops tests: before-state capture, identifier safety, SQL shape.

No live database — ``FakeMySQL`` records executed statements and serves canned
before-state rows, so the guarded writes are verified offline.
"""

from __future__ import annotations

import pytest

from mysql_aiops.ops import _util
from mysql_aiops.ops import remediation as ops
from tests.conftest import FakeMySQL

_CREATE_TABLE_DDL = (
    "CREATE TABLE `orders` (\n"
    "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
    "  `customer_id` bigint NOT NULL,\n"
    "  `email` varchar(255) NOT NULL,\n"
    "  PRIMARY KEY (`id`),\n"
    "  UNIQUE KEY `idx_email` (`email`),\n"
    "  KEY `idx_orders_cid` (`customer_id`,`created_at`)\n"
    ") ENGINE=InnoDB"
)

# ── identifier safety ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_qualify_backtick_quotes_each_part():
    assert _util.qualify("shop.orders") == "`shop`.`orders`"
    assert _util.qualify("orders") == "`orders`"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    ["orders; DROP TABLE users", "a.b.c", "o'rders", "1foo", "or`ders", "or ders"],
)
def test_qualify_rejects_injection(bad):
    with pytest.raises(ValueError):
        _util.qualify(bad)


@pytest.mark.unit
def test_split_qualified():
    assert _util.split_qualified("shop.orders") == ("shop", "orders")
    assert _util.split_qualified("orders") == (None, "orders")


# ── session control captures before-state ───────────────────────────────────


@pytest.mark.unit
def test_kill_session_captures_prior_and_binds_id():
    conn = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 42, "user": "app", "host": "10.0.0.9", "db": "shop",
         "command": "Query", "time": 300, "state": "Sending data",
         "query": "SELECT bad()"},
    ]})
    out = ops.kill_session(conn, 42)
    assert out["action"] == "kill_session"
    assert out["killed"] is True
    assert out["priorState"]["query"] == "SELECT bad()"
    sql, params = conn.executed[0]
    assert sql == "KILL CONNECTION %(id)s"  # id is a bound parameter
    assert params == {"id": 42}


@pytest.mark.unit
def test_kill_query_captures_prior():
    conn = FakeMySQL({"FROM information_schema.processlist": [
        {"id": 7, "query": "SELECT SLEEP(999)"},
    ]})
    out = ops.kill_query(conn, 7)
    assert out["action"] == "kill_query" and out["cancelled"] is True
    sql, params = conn.executed[0]
    assert sql == "KILL QUERY %(id)s" and params == {"id": 7}


# ── optimize / analyze ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_optimize_table_quotes_identifier_and_captures_stats():
    conn = FakeMySQL({
        "FROM information_schema.tables": [
            {"table_rows": 1000, "data_length": 4096, "index_length": 1024,
             "data_free": 2048, "update_time": None},
        ],
        "OPTIMIZE TABLE": [
            {"Table": "shop.orders", "Op": "optimize", "Msg_type": "status",
             "Msg_text": "OK"},
        ],
    })
    out = ops.optimize_table(conn, "shop.orders")
    assert out["priorState"]["freeBytes"] == 2048
    assert out["result"][0]["msgText"] == "OK"
    sql, _ = [q for q in conn.queried if q[0].startswith("OPTIMIZE")][0]
    assert sql == "OPTIMIZE TABLE `shop`.`orders`"


@pytest.mark.unit
def test_analyze_table_quotes_identifier():
    conn = FakeMySQL({"FROM information_schema.tables": [{"table_rows": 5}]})
    ops.analyze_table(conn, "orders")
    sql, _ = [q for q in conn.queried if q[0].startswith("ANALYZE")][0]
    assert sql == "ANALYZE TABLE `orders`"


@pytest.mark.unit
def test_optimize_rejects_bad_identifier():
    conn = FakeMySQL()
    with pytest.raises(ValueError):
        ops.optimize_table(conn, "orders; DROP TABLE users")
    assert conn.executed == []


# ── index create/drop ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_create_index_builds_statement_and_returns_name():
    conn = FakeMySQL()
    out = ops.create_index(conn, "shop.orders", ["customer_id", "created_at"])
    assert out["action"] == "create_index"
    assert out["table"] == "shop.orders"
    sql, _ = conn.executed[0]
    assert sql.startswith("CREATE INDEX ")
    assert "`shop`.`orders`" in sql
    assert "`customer_id`, `created_at`" in sql


@pytest.mark.unit
def test_create_index_unique_keyword():
    conn = FakeMySQL()
    ops.create_index(conn, "orders", ["email"], name="idx_email", unique=True)
    sql, _ = conn.executed[0]
    assert sql == "CREATE UNIQUE INDEX `idx_email` ON `orders` (`email`)"


@pytest.mark.unit
def test_create_index_rejects_bad_column():
    conn = FakeMySQL()
    with pytest.raises(ValueError):
        ops.create_index(conn, "orders", ["id); DROP TABLE t; --"])
    assert conn.executed == []


@pytest.mark.unit
def test_drop_index_captures_definition_before_dropping():
    conn = FakeMySQL({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    out = ops.drop_index(conn, "orders", "idx_orders_cid")
    assert out["priorState"]["definition"] == (
        "CREATE INDEX `idx_orders_cid` ON `orders` (`customer_id`,`created_at`)"
    )
    sql, _ = conn.executed[0]
    assert sql == "DROP INDEX `idx_orders_cid` ON `orders`"


@pytest.mark.unit
def test_drop_index_captures_unique_definition():
    conn = FakeMySQL({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    out = ops.drop_index(conn, "orders", "idx_email")
    assert out["priorState"]["definition"] == (
        "CREATE UNIQUE INDEX `idx_email` ON `orders` (`email`)"
    )


@pytest.mark.unit
def test_drop_index_raises_when_not_found():
    conn = FakeMySQL({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    with pytest.raises(ValueError, match="not found"):
        ops.drop_index(conn, "orders", "nope")
    assert conn.executed == []


# ── global variables ────────────────────────────────────────────────────────


@pytest.mark.unit
def test_set_global_variable_captures_prior_and_binds_value():
    conn = FakeMySQL({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "long_query_time", "Value": "10"},
    ]})
    out = ops.set_global_variable(conn, "long_query_time", "1")
    assert out["priorState"]["value"] == "10"
    sql, params = conn.executed[0]
    assert sql == "SET GLOBAL long_query_time = %(v)s"  # value is bound
    assert params == {"v": "1"}


@pytest.mark.unit
def test_set_global_variable_rejects_bad_name():
    conn = FakeMySQL()
    with pytest.raises(ValueError, match="variable name"):
        ops.set_global_variable(conn, "max_connections; DROP", "1")
    assert conn.executed == []


@pytest.mark.unit
def test_set_global_variable_rejects_unknown_variable():
    conn = FakeMySQL({"SHOW GLOBAL VARIABLES": []})
    with pytest.raises(ValueError, match="Unknown global variable"):
        ops.set_global_variable(conn, "not_a_variable", "1")
    assert conn.executed == []
