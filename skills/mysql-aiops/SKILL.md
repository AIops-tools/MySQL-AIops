---
name: mysql-aiops
description: >
  Use this skill whenever the user needs to operate or troubleshoot a MySQL 8.x or MariaDB 10.6+ server as a DBA — a one-shot server health overview (version + flavor, connection headroom, replica role); server reads (global variables, status counters, databases, storage engines); activity (sessions/processlist, long-running queries, open InnoDB transactions, lock waits); query stats (performance_schema statement-digest top-N, EXPLAIN FORMAT=JSON); index health (unused indexes, redundant/duplicate indexes, cardinality); table health (sizes, data_free fragmentation, engine/row-format status); replication (replica IO/SQL thread state and lag, binlog/GTID status); four flagship analyses — slow-query RCA (worst digest + EXPLAIN → cited cause/action incl. full-scan and lock-time-dominant classification), InnoDB lock-wait & deadlock chain RCA (wait-for tree, root blocker, last deadlock parsed from SHOW ENGINE INNODB STATUS), replication lag RCA (thread state/error fields → cause+action), and table fragmentation analysis (data_free → OPTIMIZE candidates); and guarded writes (kill a session or query, OPTIMIZE/ANALYZE TABLE, create/drop an index, SET GLOBAL a variable, reset digest stats).
  Always use this skill for "mysql health check", "why is this query slow", "top queries by time", "EXPLAIN this", "table fragmentation", "which indexes are unused", "redundant index", "who is blocking whom", "deadlock", "kill the session holding the lock", "replication lag", "replica stopped", "seconds behind master/source", "OPTIMIZE this table", "create/drop an index", or "SET GLOBAL max_connections" when the context is a MySQL or MariaDB database.
  Do NOT use for PostgreSQL — use postgres-aiops. Do NOT use when the target is OT / industrial equipment (use industrial-aiops), a hypervisor, a storage appliance, a backup product, or a container/cluster orchestrator (negative routing hints only).
  Preview — common MySQL/MariaDB DBA operations with a built-in governance harness (audit, policy, token budget, undo, risk-tiers). Mock-validated only, not run against a live server.
installer:
  kind: uv
  package: mysql-aiops
argument-hint: "[session id / table / index name or describe your DBA task]"
allowed-tools:
  - Bash
metadata: {"openclaw":{"requires":{"env":["MYSQL_AIOPS_CONFIG"],"bins":["mysql-aiops"],"config":["~/.mysql-aiops/config.yaml","~/.mysql-aiops/secrets.enc"]},"optional":{"env":["MYSQL_AIOPS_MASTER_PASSWORD"]},"primaryEnv":"MYSQL_AIOPS_CONFIG","homepage":"https://github.com/AIops-tools/MySQL-AIops","emoji":"🐬","os":["macos","linux"]}}
compatibility: >
  Standalone, self-governed MySQL/MariaDB DBA operations (preview). The governance harness (audit, policy, token/runaway budget, undo, risk-tiers) is bundled in the package — no external skill-family dependency. Connects via PyMySQL (30s timeouts) and reads information_schema / performance_schema; the server flavor (mysql vs mariadb) is detected from version() and flavor-dependent statements branch (SHOW REPLICA STATUS vs SHOW SLAVE STATUS; performance_schema.data_lock_waits vs information_schema.innodb_lock_waits).
  All write operations are audited to a local SQLite DB under ~/.mysql-aiops/ (relocatable via MYSQL_AIOPS_HOME).
  Credentials: the MySQL account password is stored ENCRYPTED in ~/.mysql-aiops/secrets.enc (Fernet/AES-128 + scrypt-derived key) — never plaintext on disk. Run 'mysql-aiops init' to onboard, or 'mysql-aiops secret set <target>' to add one. The store is unlocked by a master password from MYSQL_AIOPS_MASTER_PASSWORD (non-interactive/MCP/CI) or an interactive prompt (CLI on a TTY). A legacy plaintext env var MYSQL_<TARGET_NAME_UPPER>_PASSWORD is still honoured as a fallback with a deprecation warning (migrate with 'mysql-aiops secret migrate'). The password is passed to pymysql.connect at connect time and held only in memory; it is never logged or echoed.
  SQL safety: all values are bound query parameters; the few identifiers that cannot be parameterised (schema/table/index/column/variable names, ORDER BY columns) are validated against a strict identifier charset / allow-lists and backtick-quoted before interpolation. EXPLAIN rejects multi-statement input; the drop_index undo replay path is shape-gated to CREATE [UNIQUE] INDEX statements.
  State-changing operations require double confirmation at the CLI layer and support --dry-run. All write tools pass through the @governed_tool decorator (pre-check + budget guard + audit + risk-tier gate) and take a dry_run preview. Reversible writes fetch the real before-state first and record a faithful inverse (create_index↔drop_index, where drop rebuilds the definition from SHOW CREATE TABLE; set_global_variable restores the prior value); irreversible ops (kill session/query, optimize/analyze, reset stats) record prior state only.
  Webhooks: none — no outbound network calls beyond the configured MySQL connection.
  TLS: ssl_mode follows MySQL client semantics (default preferred); set verify_ca/verify_identity (with ssl_ca) on untrusted networks.
  Transitive dependencies: PyMySQL (pure-Python MySQL driver) and the MCP SDK. No post-install scripts or background services.
  PREVIEW: mock-validated only; the information_schema / performance_schema queries are modelled from documented MySQL 8.x / MariaDB 10.6+ shapes and need live verification. Community-maintained; not affiliated with Oracle or the MariaDB Foundation — trademarks belong to their owners.
---

# MySQL AIops (preview)

> **Disclaimer**: Community-maintained open-source project, **not affiliated with, endorsed by, or sponsored by Oracle Corporation or the MariaDB Foundation.** "MySQL" and "MariaDB" trademarks belong to their owners. Source at [github.com/AIops-tools/MySQL-AIops](https://github.com/AIops-tools/MySQL-AIops) under the MIT license.

Governed MySQL / MariaDB DBA operations — **33 MCP tools**, every one wrapped with the bundled `@governed_tool` harness: a local unified audit log under `~/.mysql-aiops/`, policy engine, token/runaway budget guard, undo-token recording, and graduated-autonomy risk tiers. The account password is stored **encrypted** (`~/.mysql-aiops/secrets.enc`, Fernet + scrypt) — never plaintext on disk.

> **Standalone**: the governance harness is bundled in the package (`mysql_aiops.governance`) — mysql-aiops has no external skill-family dependency. **Preview / mock-only**: not run against a live server.

## What This Skill Does

| Domain | Tools | Count | Read or Write |
|--------|-------|:-----:|:-------------:|
| **Overview** | server health snapshot (version+flavor, connections, replica role) | 1 | 1 read |
| **Server** | version+flavor, variables, status, databases, engines, connection stats | 6 | 6 read |
| **Activity** | sessions, long-running queries, transactions, lock waits | 4 | 4 read |
| **Queries** | top-N statement digests, EXPLAIN FORMAT=JSON | 2 | 2 read |
| **Indexes** | unused, redundant/duplicate, cardinality stats | 3 | 3 read |
| **Tables** | sizes, data_free fragmentation, engine/row-format status | 3 | 3 read |
| **Replication** | replica status/lag, binlog/GTID | 2 | 2 read |
| **Analysis (flagship)** | slow-query RCA, lock-wait & deadlock RCA, replication-lag RCA, fragmentation | 4 | 4 read |
| **Writes** | kill-session, kill-query, drop-index | 3 | 3 write (high) |
| | optimize, analyze-table, create-index, SET GLOBAL, reset-stats | 5 | 5 write (medium) |

The flagship analyses accept injected records for pure/offline analysis, or pull live from a configured target. `top_queries` / `slow_query_rca` require `performance_schema=ON`; the read account should have `PROCESS`, `REPLICATION CLIENT` and `SELECT` on `performance_schema`.

## Quick Install

```bash
uv tool install mysql-aiops
mysql-aiops init       # interactive wizard: connection + encrypted password
mysql-aiops doctor     # connectivity + flavor + performance_schema + replica role
```

## When to Use This Skill

- Triage a server (`overview`): version + flavor, uptime, connection headroom, sessions by command, longest query, most fragmented table, replica role
- Root-cause a slow query (`analyze slow-query` / `slow_query_rca`): the worst statement digest + EXPLAIN → cited cause and action (full scan, lock-time dominant, tmp-disk spill, N+1)
- Untangle a lock pile-up or deadlock (`analyze lock-waits` / `lock_wait_rca`): the wait-for tree with the root blocker named + the last deadlock parsed from `SHOW ENGINE INNODB STATUS`
- Diagnose replication (`analyze replication` / `replication_lag_rca`): IO/SQL thread state, `Seconds_Behind_Source`, error fields → cause + action
- Decide what to OPTIMIZE (`analyze fragmentation` / `fragmentation_analysis`): tables ranked by reclaimable `data_free`
- Find unused / redundant indexes; check table sizes and engines; inspect binlog/GTID state
- Kill a session or its query, OPTIMIZE/ANALYZE a table, create/drop an index (reversible), or SET GLOBAL a variable — all with dry-run + double-confirm

**Do NOT use for PostgreSQL — use postgres-aiops.** Do NOT use when the target is OT/industrial equipment (use industrial-aiops), a hypervisor, a storage appliance, a backup product, or a container cluster.

## Related Skills — Skill Routing

| If the user wants… | Use |
|--------------------|-----|
| MySQL / MariaDB DBA-ops: slow queries, lock waits, replication, fragmentation | **mysql-aiops** (this skill) |
| PostgreSQL DBA-ops | **postgres-aiops** |
| OT / industrial edge (Modbus, OPC-UA, PLC, PROFINET) | the **industrial-aiops** line |
| Hypervisor VM lifecycle (power, snapshot, migrate) | a hypervisor ops skill |
| Container/cluster lifecycle | a cluster ops skill |

## Common Workflows

### Root-cause a slow database

1. `mysql-aiops analyze slow-query` → the worst statement digest with cited findings (full scan / no index used, lock time dominant, rows examined per sent, tmp-disk spill, high calls) and an action for each
2. `mysql-aiops query explain "<sql>"` → confirm the plan (look for `access_type: ALL` on a large table)
3. If the fix is an index: `mysql-aiops remediate create-index <table> <cols> --dry-run`, then re-run without `--dry-run`

### Break a lock pile-up / investigate a deadlock

1. `mysql-aiops analyze lock-waits` → the wait-for tree names the **root blocker** session, and the last deadlock (victim + statements) is attached
2. Inspect it with `mysql-aiops activity sessions` / `mysql-aiops activity transactions`
3. `mysql-aiops remediate kill-query <id>` (or `kill <id>`) — dry-run + double-confirm; the prior session/query is captured for audit

### Fix replication lag

1. `mysql-aiops analyze replication` → cited cause (IO thread stopped with `Last_IO_Error`, SQL thread stopped with `Last_SQL_Error`, applier lagging, intentional `SQL_Delay`)
2. `mysql-aiops repl status` / `mysql-aiops repl binlog` for the raw records
3. Apply the cited action (fix connectivity/credentials, repair the diverged row, enable parallel apply)

### Reclaim fragmentation safely (reversible index changes)

1. `mysql-aiops analyze fragmentation` → tables ranked by reclaimable `data_free` with cited numbers
2. `mysql-aiops remediate optimize <table> --dry-run` → preview, then re-run to OPTIMIZE
3. For a redundant index: `mysql-aiops remediate drop-index <table> <name>` — rebuilds the definition from `SHOW CREATE TABLE` first and records an inverse recreate undo descriptor

### Offline analysis (no live server)

Pass data straight to the analysis tools — `slow_query_rca(statements=[...])`, `lock_wait_rca(pairs=[...])`, `replication_lag_rca(status={...})`, or `fragmentation_analysis(tables=[...])` — to analyse an exported dataset without connecting.

## Governance & Safety

- Every tool is audited to `~/.mysql-aiops/audit.db` (relocatable via `MYSQL_AIOPS_HOME`).
- High-risk ops can require a named approver: set `MYSQL_AUDIT_APPROVED_BY` and `MYSQL_AUDIT_RATIONALE` (the env-var names the bundled harness reads).
- **Secure by default**: with no `~/.mysql-aiops/rules.yaml`, high/critical operations are denied unless `MYSQL_AUDIT_APPROVED_BY` names an approver. `mysql-aiops init` seeds a starter rules.yaml; an operator-authored rules file is honoured as-is.
- Writes support `--dry-run` / `dry_run=True` and double confirmation at the CLI.
- Reversible writes fetch the real before-state and record an inverse descriptor; irreversible ops (kill session/query, optimize/analyze, reset stats) record prior state only.
- All values are bound query parameters; identifiers that cannot be parameterised are validated and backtick-quoted.

## References

- `references/capabilities.md` — full tool + field reference
- `references/cli-reference.md` — CLI command reference
- `references/setup-guide.md` — onboarding, credentials, and connectivity
