# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by Oracle Corporation or the MariaDB Foundation.** "MySQL" and
"MariaDB" trademarks belong to their respective owners. Source is publicly
auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/MySQL-AIops](https://github.com/AIops-tools/MySQL-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target MySQL account passwords live **encrypted** in
  `~/.mysql-aiops/secrets.enc` (Fernet/AES-128 + scrypt-derived key; chmod
  600), never in `config.yaml` and never in source. The master password is never
  stored — only a per-store random salt and the ciphertext are on disk.
- A legacy plaintext env var `MYSQL_<TARGET_NAME_UPPER>_PASSWORD` is still
  honoured as a fallback with a deprecation warning (migrate with
  `mysql-aiops secret migrate`).
- The password is passed to `pymysql.connect` at connect time and held only in
  memory. It is never logged or echoed; `config.yaml` holds only host, port,
  database, user, `ssl_mode` (and optionally `ssl_ca`). The redacted DSN
  (`dsn_redacted`) masks it.

### SQL-Injection Defenses
- **All values are bound query parameters** (session ids, thresholds, limits,
  variable values) — never string-formatted into SQL.
- The few identifiers that cannot be parameterised (schema/table/index/column
  names, global variable names, `ORDER BY` columns) are validated against a
  strict identifier charset / allow-lists (`mysql_aiops.ops._util`) and
  backtick-quoted before the single interpolation site; anything that is not a
  plain identifier is rejected, not interpolated.
- `EXPLAIN` rejects multi-statement input (an embedded `;` is refused).
- The `drop_index` undo replay path (`create_index(definition=...)`) is
  shape-gated: single statement, must be `CREATE [UNIQUE] INDEX ... ON ...`.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`mysql_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.mysql-aiops/`
  (relocatable via `MYSQL_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`MYSQL_MAX_TOOL_CALLS` /
  `MYSQL_MAX_TOOL_SECONDS` — the env-var names the bundled harness reads) plus
  an on-by-default guard that trips a tight poll/retry loop.
- **Risk tiers** — a descriptive label on the audit row derived from each
  tool's declared `risk_level`; it gates nothing. Whether a write is permitted
  is the connecting account's permissions or the agent's judgement, not this
  harness. `MYSQL_AUDIT_APPROVED_BY` / `MYSQL_AUDIT_RATIONALE` are optional
  annotations recorded on the audit row — never required, never blocking.
- **Undo-token recording** — reversible writes fetch the **real before-state
  first** and record a faithful inverse (`create_index`↔`drop_index`, where
  drop rebuilds the definition from `SHOW CREATE TABLE`;
  `set_global_variable` restores the prior value).

### State-Changing Operations
Every write supports `--dry-run` (CLI) / `dry_run=True` (MCP) and requires double
confirmation at the CLI layer. Destructive/irreversible ops (`kill_session`,
`kill_query`, `drop_index`) are `risk_level=high`; mutating maintenance ops
(`optimize_table`, `analyze_table`, `create_index`, `set_global_variable`,
`reset_query_stats`) are `medium`. Irreversible ops capture prior state for the
audit record but record no undo token. `SET GLOBAL` is runtime-only and reports
(but does not auto-perform) the my.cnf / SET PERSIST persistence step.

### SSL/TLS
`ssl_mode` follows MySQL client semantics (default `preferred`); set
`verify_ca` / `verify_identity` (with `ssl_ca`) on untrusted networks;
`disabled` is for isolated labs only.

### Output Sanitisation
All catalog- and query-returned text (query text, object names, error strings)
is passed through a `sanitize()` truncate + control-character strip before
reaching the agent. This bounds length and strips control/format characters;
semantic resistance to adversarial text must come from the consuming agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured MySQL
connection. No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r mysql_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
