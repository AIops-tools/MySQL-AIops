"""Refuse writes that destroy their own reversibility.

Two shapes of the same bug, both reachable from an ordinary agent turn:

1. ``kill_session`` / ``kill_query`` on the tool's OWN session id. The read path
   has always hidden it (``activity.py`` filters ``WHERE id <> CONNECTION_ID()``)
   — the detection primitive existed, it simply never crossed to the writes. A
   kill has no undo, so aiming it at the calling connection aborts the statement
   issuing it and drops the session the audit row is written from.

2. ``set_global_variable`` on a self-affecting global. ``_validate_variable_name``
   is a shape check only, so any dynamic global was settable — and unlike
   Postgres's ``ALTER SYSTEM``, ``SET GLOBAL`` takes effect IMMEDIATELY.
   ``init_connect = 'SELECT 1/0'`` kills every new non-SUPER connection at
   handshake; ``max_connections = 1`` does the same. The cached connection
   survives inside the current process, but every CLI invocation and every
   restarted MCP server opens a new one — which is exactly what the undo needs.

The session guard has a fail-open case (the id probe can fail) and MUST fail
open: unknown identity may never read as "it is me". The variable denylist is
static, so it has no fail-open case — but both must be EXACT, or ordinary
remediation stops working.
"""

from __future__ import annotations

import pytest

from mysql_aiops.ops import remediation as ops
from mysql_aiops.ops.remediation import SelfLockout
from tests.conftest import FakeMySQL

_OWN_ID = 42
_PROCESSLIST = "FROM information_schema.processlist"


def _conn(own_id: int | None = _OWN_ID, **kwargs):
    """A fake whose CONNECTION_ID() answers ``own_id`` (None = probe returns nothing)."""
    return FakeMySQL(
        {_PROCESSLIST: [{"id": 7, "user": "app", "query": "SELECT 1"}]},
        scalars={"CONNECTION_ID()": own_id},
        **kwargs,
    )


# ── 1. killing your own session ─────────────────────────────────────────────


@pytest.mark.unit
def test_kill_session_refuses_this_connections_own_id():
    with pytest.raises(SelfLockout, match="calling through"):
        ops.kill_session(_conn(), _OWN_ID)


@pytest.mark.unit
def test_kill_query_refuses_this_connections_own_id():
    with pytest.raises(SelfLockout, match="calling through"):
        ops.kill_query(_conn(), _OWN_ID)


@pytest.mark.unit
def test_the_refusal_names_the_action_and_the_way_out():
    with pytest.raises(SelfLockout) as ei:
        ops.kill_session(_conn(), _OWN_ID)
    msg = str(ei.value)
    assert "kill_session" in msg, "must name the operation being refused"
    assert "list_sessions" in msg, "must offer the route that does work"


@pytest.mark.unit
def test_nothing_reaches_the_wire_when_the_kill_is_refused():
    """A refusal must not KILL, and must not capture priorState either."""
    conn = _conn()
    with pytest.raises(SelfLockout):
        ops.kill_session(conn, _OWN_ID)
    assert conn.executed == [], "no KILL may be issued for a self-targeted call"


# ── exactness: a different session is still killable ────────────────────────


@pytest.mark.unit
def test_a_different_session_is_still_killed():
    conn = _conn()
    out = ops.kill_session(conn, 7)
    assert out["action"] == "kill_session" and out["killed"] is True
    assert conn.executed[0] == ("KILL CONNECTION %(id)s", {"id": 7})


@pytest.mark.unit
def test_a_different_session_can_still_have_its_query_cancelled():
    conn = _conn()
    out = ops.kill_query(conn, 7)
    assert out["cancelled"] is True
    assert conn.executed[0] == ("KILL QUERY %(id)s", {"id": 7})


# ── fail open: unknown identity is never read as "it is me" ─────────────────


@pytest.mark.unit
def test_kill_proceeds_when_the_id_probe_returns_nothing():
    """Unknown identity must not block a legitimate kill."""
    conn = _conn(own_id=None)
    out = ops.kill_session(conn, _OWN_ID)
    assert out["killed"] is True, "an undeterminable id must fail OPEN, not closed"


@pytest.mark.unit
def test_kill_proceeds_when_the_id_probe_raises():
    class Exploding(FakeMySQL):
        def scalar(self, sql, params=None):
            raise RuntimeError("CONNECTION_ID() unavailable")

    conn = Exploding({_PROCESSLIST: [{"id": _OWN_ID}]})
    out = ops.kill_session(conn, _OWN_ID)
    assert out["killed"] is True, "a failed probe must fail OPEN"


@pytest.mark.unit
def test_the_guard_is_reachable_without_performing_the_kill():
    """The MCP wrapper calls this ahead of its dry_run return."""
    conn = _conn()
    ops.guard_kill_session(conn, 7)  # a non-self target is silently allowed
    with pytest.raises(SelfLockout):
        ops.guard_kill_session(conn, _OWN_ID)
    assert conn.executed == [], "the guard must not write on either path"


# ── 2. set_global_variable denylist ─────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "variable",
    ["init_connect", "max_connections", "max_user_connections", "read_only",
     "super_read_only", "skip_networking", "require_secure_transport"],
)
def test_every_self_affecting_global_is_refused(variable):
    with pytest.raises(SelfLockout):
        ops.set_global_variable(_conn(), variable, "1")


@pytest.mark.unit
def test_init_connect_the_live_footgun_is_refused():
    """SET GLOBAL init_connect='SELECT 1/0' fails every new non-SUPER login."""
    conn = _conn()
    with pytest.raises(SelfLockout, match="handshake"):
        ops.set_global_variable(conn, "init_connect", "SELECT 1/0")
    assert conn.executed == [], "no SET GLOBAL may reach the wire"


@pytest.mark.unit
def test_the_variable_refusal_explains_immediacy_and_the_my_cnf_route():
    with pytest.raises(SelfLockout) as ei:
        ops.set_global_variable(_conn(), "max_connections", "1")
    msg = str(ei.value)
    assert "immediately" in msg, "must say why SET GLOBAL differs from ALTER SYSTEM"
    assert "reversibility" in msg, "must name the concrete failure"
    assert "my.cnf" in msg, "must offer the route that does work"


@pytest.mark.unit
@pytest.mark.parametrize("variable", ["wait_timeout", "interactive_timeout"])
def test_timeouts_are_refused_only_below_the_floor(variable):
    with pytest.raises(SelfLockout, match="torn down"):
        ops.set_global_variable(_conn(), variable, "5")


@pytest.mark.unit
@pytest.mark.parametrize("variable", ["wait_timeout", "interactive_timeout"])
def test_timeouts_above_the_floor_are_ordinary_tuning(variable):
    conn = FakeMySQL(
        {"SHOW GLOBAL VARIABLES": [{"Variable_name": variable, "Value": "28800"}]},
        scalars={"CONNECTION_ID()": _OWN_ID},
    )
    out = ops.set_global_variable(conn, variable, "600")
    assert out["action"] == "set_global_variable"
    assert conn.executed[0][0] == f"SET GLOBAL {variable} = %(v)s"


@pytest.mark.unit
def test_the_denylist_cannot_be_side_stepped_by_case_or_padding():
    for spelling in ("MAX_CONNECTIONS", "  max_connections  ", "Max_Connections"):
        with pytest.raises(SelfLockout):
            ops.set_global_variable(_conn(), spelling, "1")


@pytest.mark.unit
def test_an_ordinary_global_is_still_settable():
    """Exactness: the denylist must not swallow real remediation."""
    conn = FakeMySQL(
        {"SHOW GLOBAL VARIABLES": [
            {"Variable_name": "long_query_time", "Value": "10.000000"},
        ]},
        scalars={"CONNECTION_ID()": _OWN_ID},
    )
    out = ops.set_global_variable(conn, "long_query_time", "1")
    assert out["priorState"]["value"] == "10.000000"
    assert conn.executed[0] == ("SET GLOBAL long_query_time = %(v)s", {"v": "1"})


@pytest.mark.unit
def test_the_variable_guard_is_reachable_without_any_io():
    """The MCP wrapper calls this ahead of its dry_run return; it takes no conn."""
    ops.guard_set_global_variable("long_query_time", "1")
    with pytest.raises(SelfLockout):
        ops.guard_set_global_variable("max_connections", "1")


@pytest.mark.unit
def test_self_lockout_is_a_valueerror():
    """CLI/MCP error handling keys off ValueError; keep it in that family."""
    assert issubclass(SelfLockout, ValueError)
