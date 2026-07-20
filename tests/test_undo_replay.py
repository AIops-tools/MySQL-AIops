"""drop_index → create_index(definition=…) undo REPLAY — the descriptor recorded
by _drop_index_undo must be executable by the tool it names (the line-wide
undo-replayability requirement: params must match the target tool's signature)."""

from __future__ import annotations

import pytest

from mcp_server.tools import remediation as gov
from mysql_aiops.ops import remediation as ops

_CREATE_TABLE_DDL = (
    "CREATE TABLE `orders` (\n"
    "  `id` bigint NOT NULL AUTO_INCREMENT,\n"
    "  `ts` datetime NOT NULL,\n"
    "  PRIMARY KEY (`id`),\n"
    "  UNIQUE KEY `idx_orders_ts` (`ts`)\n"
    ") ENGINE=InnoDB"
)

INDEXDEF = "CREATE UNIQUE INDEX `idx_orders_ts` ON `orders` (`ts`)"


@pytest.mark.unit
def test_drop_index_descriptor_replays_through_create_index(fake_mysql, monkeypatch):
    fake = fake_mysql({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)

    dropped = gov.drop_index(table="orders", name="idx_orders_ts")
    assert dropped["priorState"]["definition"] == INDEXDEF

    descriptor = gov._drop_index_undo({"table": "orders", "name": "idx_orders_ts"}, dropped)
    assert descriptor["tool"] == "create_index"

    replay = gov.create_index(**descriptor["params"])
    assert replay["index"] == "idx_orders_ts"
    assert replay["fromDefinition"] is True
    assert (INDEXDEF, None) in fake.executed


@pytest.mark.unit
def test_create_index_descriptor_replays_through_drop_index(fake_mysql, monkeypatch):
    """The inverse pair the other way: create_index's undo params must be
    accepted verbatim by drop_index."""
    fake = fake_mysql({"SHOW CREATE TABLE": [
        {"Table": "orders", "Create Table": _CREATE_TABLE_DDL},
    ]})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)

    created = gov.create_index(table="orders", columns=["ts"], name="idx_orders_ts", unique=True)
    descriptor = gov._create_index_undo({"table": "orders"}, created)
    assert descriptor["tool"] == "drop_index"

    replay = gov.drop_index(**descriptor["params"])
    assert replay["action"] == "drop_index"
    assert ("DROP INDEX `idx_orders_ts` ON `orders`", None) in fake.executed


@pytest.mark.unit
def test_set_global_variable_descriptor_replays(fake_mysql, monkeypatch):
    fake = fake_mysql({"SHOW GLOBAL VARIABLES": [
        {"Variable_name": "long_query_time", "Value": "10"},
    ]})
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)

    result = gov.set_global_variable(name="long_query_time", value="1")
    descriptor = gov._set_global_variable_undo({"name": "long_query_time"}, result)
    assert descriptor["tool"] == "set_global_variable"

    replay = gov.set_global_variable(**descriptor["params"])
    assert replay["newValue"] == "10"
    assert ("SET GLOBAL long_query_time = %(v)s", {"v": "10"}) in fake.executed


@pytest.mark.unit
def test_create_index_definition_and_columns_are_mutually_exclusive(fake_mysql, monkeypatch):
    fake = fake_mysql()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    result = gov.create_index(table="t", columns=["a"], definition=INDEXDEF)
    assert "not both" in result["error"]
    result = gov.create_index()
    assert "requires table+columns" in result["error"]
    assert fake.executed == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "DROP TABLE users",
        "CREATE INDEX a ON t (c); DROP TABLE users",
        "SELECT 1",
        "",
    ],
)
def test_create_index_from_definition_rejects_non_indexdef_shapes(fake_mysql, bad):
    fake = fake_mysql()
    with pytest.raises(ValueError):
        ops.create_index_from_definition(fake, bad)
    assert fake.executed == []


@pytest.mark.unit
def test_create_index_definition_dry_run_executes_nothing(fake_mysql, monkeypatch):
    fake = fake_mysql()
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: fake)
    result = gov.create_index(definition=INDEXDEF, dry_run=True)
    assert result["dryRun"] is True and result["wouldExecute"] == INDEXDEF
    assert fake.executed == []
