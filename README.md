<!-- mcp-name: io.github.AIops-tools/mysql-aiops -->

# MySQL AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Oracle Corporation or the MariaDB Foundation.** "MySQL" is a trademark of Oracle Corporation; "MariaDB" is a trademark of MariaDB plc; all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **MySQL 8.x and MariaDB 10.6+ DBA operations** — connecting
to a server with **PyMySQL** and reading `information_schema` /
`performance_schema` — with a **built-in governance harness**: unified audit
log, token/runaway budget guard, undo-token recording, and descriptive
risk-tier labels. The server **flavor** (mysql vs mariadb) is
detected from `version()` and flavor-dependent statements branch automatically
(`SHOW REPLICA STATUS` vs `SHOW SLAVE STATUS`).

## What it does

Four flagship signature analyses, plus the guarded reads and writes around them:

- **Slow-query RCA** — take the worst `events_statements_summary_by_digest`
  entry (plus an optional `EXPLAIN FORMAT=JSON` plan) and map its numbers —
  no-index share (`SUM_NO_INDEX_USED`), lock-time share, rows-examined/sent
  ratio, tmp-disk spill, call count, plan access types — to a cited cause and a
  concrete action. Every finding carries its measured number, not a black-box
  verdict.
- **InnoDB lock-wait & deadlock chain RCA** — build the wait-for tree from
  `performance_schema.data_lock_waits` (MariaDB:
  `information_schema.innodb_lock_waits`), name the **root blocker** (blocks
  others, waits on none), and parse the **last deadlock** out of
  `SHOW ENGINE INNODB STATUS`.
- **Replication lag RCA** — map the replica's IO/SQL thread state,
  `Seconds_Behind_Source` and error fields to a cited cause + action
  (stopped IO thread, failed applier statement, lagging applier, intentional
  `SQL_Delay`).
- **Table fragmentation analysis** — rank tables by reclaimable `data_free`
  from `information_schema.tables` into cited `OPTIMIZE TABLE` candidates.

## What works

- **CLI** (`mysql-aiops ...`): `init`, `overview`, `server`, `activity`, `query`, `index`, `table`, `repl`, `analyze`, `remediate`, `secret`, `doctor`, `mcp`.
- **MCP server** (`mysql-aiops mcp` or `mysql-aiops-mcp`): **35 tools** (26 read, 9 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the account password lives in an encrypted store `~/.mysql-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `MYSQL_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse — `create_index`↔`drop_index`; `drop_index` captures the index definition out of `SHOW CREATE TABLE` so undo recreates it exactly; `set_global_variable` captures the prior value from `SHOW GLOBAL VARIABLES` so undo sets it back. Irreversible ops (`kill_session`, `kill_query`, `optimize_table`, `analyze_table`, `reset_query_stats`) record prior state for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview. All identifiers that cannot be parameterised (schema/table/index/column/variable names) are validated against a strict charset and backtick-quoted; all values are bound query parameters.

## What this tool does, and does not, decide

It delivers MySQL / MariaDB DBA operations — reads and writes — accurately and
efficiently, and records every one of them. It does **not** decide whether a write is
allowed to happen. That is the agent's judgement, or the permission of the account you
connect it with: point it at a MySQL/MariaDB account granted only SELECT / PROCESS /
REPLICATION CLIENT and no write privileges (no INSERT/UPDATE/DELETE/DDL), and the
writes fail at the server — the place that actually owns the permission.

So there is no read-only switch, no policy file, no approval gate to configure. The one
thing the tool guarantees is that nothing is silent: **every call, over MCP and over the
CLI alike, lands an audit row** in `~/.mysql-aiops/audit.db`, and destructive writes still
capture their before-state and record an inverse where one exists.

> Each tool declares a `risk_level`, carried into the audit row as a descriptive tier
> (none/confirm/review) — so a reviewer can see at a glance that a row was a high-risk delete. It
> is a label, not a gate.

Running a smaller / local model? See
[agent-guardrails.md](skills/mysql-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

## Capability matrix (35 MCP tools)

| Domain | Tools | Count | R/W |
|--------|-------|:-----:|:---:|
| **Overview** | `overview` | 1 | read |
| **Server** | `server_version`, `show_variables`, `show_status`, `list_databases`, `list_engines`, `connection_stats` | 6 | read |
| **Activity** | `list_sessions`, `long_running_queries`, `list_transactions`, `lock_waits` | 4 | read |
| **Queries** | `top_queries`, `explain_query` | 2 | read |
| **Indexes** | `unused_indexes`, `redundant_indexes`, `index_stats` | 3 | read |
| **Tables** | `table_sizes`, `table_fragmentation`, `table_status` | 3 | read |
| **Replication** | `replica_status`, `binlog_status` | 2 | read |
| **Analysis (flagship)** | `slow_query_rca`, `lock_wait_rca`, `replication_lag_rca`, `fragmentation_analysis` | 4 | read |
| **Writes** | `kill_session`, `kill_query`, `drop_index` | 3 | write (high) |
| | `optimize_table`, `analyze_table`, `create_index`, `set_global_variable`, `reset_query_stats` | 5 | write (medium) |
| **Undo** | `undo_list`, `undo_apply` | 2 | read / write |

The flagship analyses accept injected records for pure/offline analysis, or pull
live from a configured target. `top_queries`/`slow_query_rca` require
`performance_schema=ON`; the read account should have `PROCESS`,
`REPLICATION CLIENT` and `SELECT` on `performance_schema`.

## Support scope

| Platform | Status |
|----------|--------|
| MySQL 8.0 / 8.4 | targeted (`SHOW REPLICA STATUS`, `performance_schema.data_lock_waits`) |
| MariaDB 10.6+ / 11.x | targeted (`SHOW SLAVE STATUS`, `information_schema.innodb_lock_waits`) |
| MySQL 5.7 and older | not targeted (EOL; pre-8.0 digest/lock views untested) |
| Cloud-managed MySQL (RDS/Aurora/Cloud SQL flavors) | wire-compatible reads should work; managed restrictions (KILL, SET GLOBAL) apply — untested |

## Quick start

```bash
uv tool install mysql-aiops             # or: pipx install mysql-aiops
mysql-aiops init                        # wizard: add a target + store the password (encrypted)
mysql-aiops doctor                      # verify config, secrets, connectivity, flavor, perf-schema
mysql-aiops overview                    # one-shot server health snapshot
mysql-aiops analyze slow-query          # RCA the worst statement digest
mysql-aiops analyze fragmentation       # OPTIMIZE TABLE candidates
```

Run as an MCP server (stdio):

```bash
export MYSQL_AIOPS_MASTER_PASSWORD=...  # unlock secrets non-interactively
mysql-aiops-mcp
```

Claude Desktop / MCP client config:

```json
{
  "mcpServers": {
    "mysql-aiops": {
      "command": "uvx",
      "args": ["--from", "mysql-aiops", "mysql-aiops-mcp"],
      "env": { "MYSQL_AIOPS_MASTER_PASSWORD": "your-master-password" }
    }
  }
}
```

> **Env-block caveat**: the `env` block above is the only environment the MCP
> server sees — GUI-launched clients do **not** inherit your shell profile. Put
> `MYSQL_AIOPS_MASTER_PASSWORD` (and `MYSQL_AIOPS_HOME` / `MYSQL_AUDIT_APPROVED_BY`
> if you use them) there, or the server cannot unlock the secret store.

## Governance

Every MCP tool passes through the bundled `@governed_tool` harness:

- **Audit** — every call (params, result, status, duration, risk tier, and any operator-supplied approver/rationale) is logged to `~/.mysql-aiops/audit.db` (relocatable via `MYSQL_AIOPS_HOME`). The CLI writes the same row the MCP path does — there is no unaudited entry point.
- **Runaway guard** — a safety backstop, not an authorization gate: the same call hammered in a tight loop trips a circuit breaker. Disable with `MYSQL_RUNAWAY_MAX=0`; optional hard ceilings via `MYSQL_MAX_TOOL_CALLS` / `MYSQL_MAX_TOOL_SECONDS`.
- **Undo recording** — reversible writes record an inverse descriptor built from the fetched before-state.
- **Risk tier** — a descriptive label on the audit row derived from `risk_level`; it gates nothing.

## Scope

This is the **MySQL / MariaDB DBA-ops** member of the AIops-tools family
(governed AI-ops with audit + budget + undo + risk tiers). Do **NOT** use it
for PostgreSQL — use **postgres-aiops**. Do **NOT** use it for OT / industrial
edge — see the separate `industrial-aiops` line — nor for application-schema
migrations or ORM management.

## Missing a capability?

Coverage is intentionally a curated subset of MySQL's catalogs and maintenance
surface. Missing a view, a metric, or a maintenance command? **Open an issue or
PR** — contributions welcome. 缺功能提 issue/PR 欢迎留言。

## Verification status

**Live-verified against MySQL 8.4.10 and MariaDB 11.8.8 (2026-07-19/20).**
Connectivity, the reads, `analyze slow-query` on genuine full scans, and the full
governance loop (real `create_index` → audit row → undo actually dropping it) were
exercised against a real server. That run found and fixed a
real bug the mock suite could not see: `SUM()` aggregates come back as `Decimal`,
which is not JSON serializable.

The MariaDB branch is now verified too, including `analyze lock-waits` against real
row contention (it correctly identified the root blocker and the measured wait).
**Replication against a real replica** remains unverified.

[docs/VERIFICATION.md](docs/VERIFICATION.md) records exactly what was checked and what
is still open. `mysql-aiops doctor` is the fastest live check (connectivity, flavor,
performance_schema, replica role).
