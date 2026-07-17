"""Governance harness for mysql-aiops — audit, policy, budget, undo, sanitize.

A self-contained, vendored governance layer. mysql-aiops has NO dependency on
any external skill family — this package is its own copy of the harness:

  - ``@governed_tool`` — mandatory decorator on every MCP tool: policy pre-check,
    token/runaway budget guard, graduated-autonomy risk-tier gate, audit logging,
    and undo-token recording.
  - unified SQLite audit log under ``~/.mysql-aiops/`` (override with
    ``MYSQL_AIOPS_HOME``).
  - ``sanitize`` — control-character stripping + truncation for API-returned text.

State lives under ``ops_home()`` (default ``~/.mysql-aiops``).
"""

from mysql_aiops.governance.audit import AuditEngine, get_engine
from mysql_aiops.governance.budget import BudgetExceeded, BudgetTracker, get_budget
from mysql_aiops.governance.decorators import PolicyDenied, governed_tool
from mysql_aiops.governance.patterns import Pattern, PatternMatch, get_pattern_engine
from mysql_aiops.governance.policy import TierDecision, get_policy_engine
from mysql_aiops.governance.sanitize import sanitize
from mysql_aiops.governance.undo import UndoStore, get_undo_store

__all__ = [
    "governed_tool",
    "sanitize",
    "PolicyDenied",
    "get_engine",
    "AuditEngine",
    "get_policy_engine",
    "TierDecision",
    "get_budget",
    "BudgetTracker",
    "BudgetExceeded",
    "get_undo_store",
    "UndoStore",
    "Pattern",
    "PatternMatch",
    "get_pattern_engine",
]
