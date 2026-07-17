# Changelog

All notable changes to mysql-aiops are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning (currently 0.x preview — the API may change).

## v0.1.0 — 2026-07-17

Initial preview release: governed AI-ops for **MySQL 8.x and MariaDB 10.6+ DBA
operations** — connecting via **PyMySQL** and reading `information_schema` /
`performance_schema` — with a bundled governance harness. **Mock-validated only
— not run against a live server.** Community-maintained; not affiliated with
Oracle or the MariaDB Foundation.

### Added

- **PyMySQL connection layer** (`mysql_aiops.connection`) — parameterised reads
  with a `DictCursor`, autocommit for maintenance statements (OPTIMIZE /
  ANALYZE / KILL), 30s connect/read/write timeouts, an injectable connection
  for tests, teaching error translation (`MySQLError`, with connect / privilege
  / missing-performance_schema hints), and **flavor detection** (mysql vs
  mariadb from `version()`) so flavor-dependent statements branch
  (`SHOW REPLICA STATUS` vs `SHOW SLAVE STATUS`;
  `performance_schema.data_lock_waits` vs `information_schema.innodb_lock_waits`).
- **33 governed MCP tools**, every one wrapped with `@governed_tool`:
  - **Overview** — `overview` (one-shot server health snapshot incl. flavor,
    connection headroom, replica role).
  - **Server** — `server_version`, `show_variables`, `show_status`,
    `list_databases`, `list_engines`, `connection_stats`.
  - **Activity** — `list_sessions`, `long_running_queries`,
    `list_transactions`, `lock_waits`.
  - **Queries** — `top_queries` (statement digests), `explain_query`
    (EXPLAIN FORMAT=JSON).
  - **Indexes** — `unused_indexes`, `redundant_indexes`, `index_stats`.
  - **Tables** — `table_sizes`, `table_fragmentation`, `table_status`.
  - **Replication** — `replica_status`, `binlog_status`.
  - **Analysis (flagship)** — `slow_query_rca`, `lock_wait_rca` (incl. last
    deadlock parsed from `SHOW ENGINE INNODB STATUS`), `replication_lag_rca`,
    `fragmentation_analysis`.
  - **Writes** — `kill_session` (high), `kill_query` (high), `drop_index`
    (high), `optimize_table` (medium), `analyze_table` (medium), `create_index`
    (medium), `set_global_variable` (medium), `reset_query_stats` (medium).
- **Guarded writes** — every write supports a `dry_run` preview and (at the
  CLI) double confirmation. Reversible writes fetch the **real before-state**
  and record a faithful inverse: `create_index`↔`drop_index` (drop rebuilds the
  index definition from `SHOW CREATE TABLE` so undo recreates it exactly;
  the descriptor replays through `create_index(definition=...)`);
  `set_global_variable` captures the prior value from `SHOW GLOBAL VARIABLES`.
  Irreversible ops record prior state for audit but no undo.
- **SQL-injection defenses** — all values are bound query parameters; the few
  identifiers that cannot be parameterised (schema/table/index/column/variable
  names, ORDER BY columns) are validated against a strict charset / allow-lists
  and backtick-quoted before interpolation. EXPLAIN rejects multi-statement
  input.
- **Bundled governance harness** (`mysql_aiops.governance`) — audit log, policy
  engine, token/runaway budget guard, undo-token recording, graduated risk
  tiers (secure by default: high-risk needs a named approver), output
  `sanitize`. State under `~/.mysql-aiops/` (relocatable via
  `MYSQL_AIOPS_HOME`).
- **Encrypted secret store** — account passwords in `~/.mysql-aiops/secrets.enc`
  (Fernet + scrypt); legacy `MYSQL_<TARGET>_PASSWORD` env fallback +
  `secret migrate`.
- **CLI** — `init` wizard (targets, TLS mode with verify_ca/verify_identity,
  encrypted password, seeded rules.yaml), `overview`, `server`, `activity`,
  `query`, `index`, `table`, `repl`, `analyze`, `remediate`, `secret`,
  `doctor` (connectivity + flavor + performance_schema + replica-role probes),
  `mcp`.

### Known limitations

- Preview / mock-only: the `information_schema` / `performance_schema` queries
  need live verification against MySQL 8.x and MariaDB 10.6+.
- `top_queries` / `slow_query_rca` require `performance_schema=ON`.
- `SET GLOBAL` changes are runtime-only (persist in my.cnf / SET PERSIST
  yourself).
- Coverage is a curated subset of MySQL's surface; open an issue/PR for gaps.

[v0.1.0]: https://github.com/AIops-tools/MySQL-AIops/releases/tag/v0.1.0
