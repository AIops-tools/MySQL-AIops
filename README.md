<!-- mcp-name: io.github.AIops-tools/mysql-aiops -->

# MySQL AIops (preview)

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Oracle Corporation or the MariaDB Foundation.** "MySQL" is a trademark of Oracle Corporation; "MariaDB" is a trademark of MariaDB plc; all product/trademark names belong to their respective owners. MIT licensed.

Governed AI-ops for **MySQL 8.x and MariaDB 10.6+ DBA operations** — connecting
to a server with **PyMySQL** and reading `information_schema` /
`performance_schema` — with a **built-in governance harness**: unified audit
log, policy engine, token/runaway budget guard, undo-token recording, and
graduated-autonomy risk tiers. The server **flavor** (mysql vs mariadb) is
detected from `version()` and flavor-dependent statements branch automatically
(`SHOW REPLICA STATUS` vs `SHOW SLAVE STATUS`).
**Preview — mock-validated only, not run against a live server.**

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
- **MCP server** (`mysql-aiops mcp` or `mysql-aiops-mcp`): **33 tools** (25 read, 8 write), every one wrapped with the bundled `@governed_tool` harness.
- **Encrypted credentials**: the account password lives in an encrypted store `~/.mysql-aiops/secrets.enc` (Fernet + scrypt) — **never plaintext on disk**. Unlock with a master password from `MYSQL_AIOPS_MASTER_PASSWORD` (MCP/CI) or an interactive prompt (CLI).
- **Reversibility**: mutating writes fetch the **real before-state first** and record a faithful inverse — `create_index`↔`drop_index`; `drop_index` captures the index definition out of `SHOW CREATE TABLE` so undo recreates it exactly; `set_global_variable` captures the prior value from `SHOW GLOBAL VARIABLES` so undo sets it back. Irreversible ops (`kill_session`, `kill_query`, `optimize_table`, `analyze_table`, `reset_query_stats`) record prior state for audit but declare no undo.
- **Safety**: every state-changing CLI op supports `--dry-run` and requires double confirmation; every write MCP tool takes a `dry_run` preview. All identifiers that cannot be parameterised (schema/table/index/column/variable names) are validated against a strict charset and backtick-quoted; all values are bound query parameters.

## Capability matrix (33 MCP tools)

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

The flagship analyses accept injected records for pure/offline analysis, or pull
live from a configured target. `top_queries`/`slow_query_rca` require
`performance_schema=ON`; the read account should have `PROCESS`,
`REPLICATION CLIENT` and `SELECT` on `performance_schema`.

## Support scope

| Platform | Status |
|----------|--------|
| MySQL 8.0 / 8.4 | supported (preview; `SHOW REPLICA STATUS`, `performance_schema.data_lock_waits`) |
| MariaDB 10.6+ / 11.x | supported (preview; `SHOW SLAVE STATUS`, `information_schema.innodb_lock_waits`) |
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

- **Audit** — every call (params, result, status, duration, risk tier, approver,
  rationale) is logged to `~/.mysql-aiops/audit.db` (relocatable via
  `MYSQL_AIOPS_HOME`).
- **Budget / runaway guard** — token and call budgets trip a circuit breaker
  (`MYSQL_MAX_TOOL_CALLS`, `MYSQL_MAX_TOOL_SECONDS`, `MYSQL_RUNAWAY_MAX`).
- **Risk tiers** — graduated autonomy, **secure by default**: with no
  `rules.yaml`, high-risk writes require a named approver
  (`MYSQL_AUDIT_APPROVED_BY` / `MYSQL_AUDIT_RATIONALE`); `mysql-aiops init`
  seeds an explicit starter `rules.yaml` with that dual-control tier.
- **Undo recording** — reversible writes record an inverse descriptor built from
  the fetched before-state, replayable through the tool it names.

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

## Status

**Preview — mock-validated only, not run against a live server.** The catalog
queries are modelled from the documented `information_schema` /
`performance_schema` shapes for MySQL 8.x and MariaDB 10.6+ and need live
verification. `mysql-aiops doctor` is the fastest live check (connectivity,
flavor, performance_schema, replica role).
