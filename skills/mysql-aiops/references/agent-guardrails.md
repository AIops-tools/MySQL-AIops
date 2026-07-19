# Agent guardrails — running mysql-aiops with a smaller / local model

If you drive these tools with a local model (Llama, Qwen, Mistral … via Goose,
Ollama, LM Studio, or any OpenAI-compatible runtime), you will get noticeably
better results with a short system prompt. This page gives you one, and — more
importantly — tells you which guardrails you **no longer need to write**, because
the tool now enforces them itself.

The distinction matters. A guardrail in a prompt is a request. A guardrail in the
harness is a guarantee. Anything below that we could move into the harness, we did.

## What the tool now enforces — do not waste prompt budget on these

| You might be tempted to prompt | Why you don't need to |
|---|---|
| "Work read-only, never change the database" | Set `MYSQL_READ_ONLY=1`. The nine write tools (`create_index`, `drop_index`, `kill_query`, `kill_session`, `optimize_table`, `analyze_table`, `set_global_variable`, `reset_query_stats`, `undo_apply`) are then **not registered at all** — they never appear in the tool list, so the model cannot call one even if it tries. The `@governed_tool` harness independently refuses writes, so the CLI is covered too. |
| "Never write SQL that modifies data" | The tool exposes no arbitrary-SQL surface at all. Every statement is built from a fixed template; identifiers are validated against a strict charset and backtick-quoted, and values are always bound as query parameters. `explain_query` runs `EXPLAIN`, not your statement. |
| "Don't invent a value when a field is missing" | A NULL column comes back as `null`, never as `""`. A sleeping session's `query` is `null` (it is running nothing), not blank; MariaDB's absent `gtid_mode` is `null`, not `""`. |
| "Tell me if the output was cut off" | `top_queries`, `table_sizes`, `table_fragmentation` and `table_status` return `{"statements"/"tables": [...], "returned": N, "limit": L, "truncated": true/false}`. Truncation is measured — one extra row is requested — not guessed from a length coincidence. |
| "Preserve the ordering / tell me what's most urgent" | `slow_query_rca`, `lock_wait_rca` and `replication_lag_rca` rank findings worst-first with the measured number attached. Priority is in the payload, not implied by list position. |
| "Confirm before anything destructive" | `drop_index`, `kill_query`/`kill_session` and `optimize_table` require a `--dry-run`-able preview plus double confirmation at the CLI, and a named approver (`MYSQL_AUDIT_APPROVED_BY`) for high-risk tiers. `drop_index` captures the index's `SHOW CREATE` definition first, so the undo token can recreate it exactly. |
| "Log what you did" | Every governed call is audited to `~/.mysql-aiops/audit.db` regardless of what the model says it did. |

## What still needs a prompt

These are model-behaviour problems the harness cannot fix from the outside.
Copy this into your agent's system prompt:

```text
You operate a MySQL or MariaDB server through the mysql-aiops MCP tools.

TOOL USE
- Before answering any question about the current database, you MUST call a
  tool. Never answer from memory or assumption.
- Actually invoke the tool. Do not describe the call you would make, and do not
  emit an example JSON response in place of calling it.
- If a tool call fails, report the real error verbatim. Never fill the gap with
  a plausible-sounding answer.

READING RESULTS
- Read the whole result before concluding. If a result contains a "truncated"
  field that is true, say so and re-run with a higher limit. The slowest query
  on the server may be the one just past the cut-off.
- A null field means the server returned NULL or had no such value. Report it
  as "not available" — never infer it. A session with a null "query" is idle,
  not running an unknown statement.
- Report values exactly as returned. Times are already converted to
  milliseconds; do not re-scale them. Do not prettify digests or table names.
- Cite the measured number. "mean_time 240ms over 15,000 calls" is useful;
  "this query is slow" is not.

SCOPE
- Separate observation from interpretation. State what the tools returned, then
  any interpretation, clearly marked as such.
- Do not recommend an index unless unused_indexes / redundant_indexes or an
  EXPLAIN in the result supports it. Adding an index is not free.
- Do not attribute replication lag to a cause the replication tools did not
  measure. Check whether the IO and SQL threads are actually running first.
- Do not confuse a thread id with a digest, a schema with a table, or
  rows_examined with rows_sent — the gap between those last two is the point.
- MySQL and MariaDB differ. The flavor is reported by server_version; do not
  suggest a MySQL-only feature (like gtid_mode) on MariaDB.
- performance_schema may be OFF, in which case top_queries returns nothing.
  That is a configuration fact, not "the server has no slow queries".
```

## Recommended setup for a local model

```bash
# Read-only until you trust the setup — this is enforced, not advisory.
export MYSQL_READ_ONLY=1
mysql-aiops doctor
```

Best paired with a database account that is itself read-only — defence in depth,
so a bug in this tool cannot do what the grant does not allow. Then, when you are
ready to allow writes, unset it and set an approver so the high-risk tier has an
accountable name on it:

```bash
unset MYSQL_READ_ONLY
export MYSQL_AUDIT_APPROVED_BY="your.name@example.com"
export MYSQL_AUDIT_RATIONALE="index cleanup, change ticket DB-4412"
```

## If your model still struggles

Some behaviours are model-capacity limits rather than prompt problems:

- **Multi-tool workflows time out or drift.** Prefer the RCA tools —
  `slow_query_rca` correlates digests, index usage and examined-row ratios
  inside one call, so the model does not have to chain `top_queries`,
  `explain_query` and `index_stats` and keep digests straight.
- **The model ignores later tool results in a long context.** Statement digests
  are the big payload here. Use `--limit` deliberately rather than pulling 200
  digests when you want the top 10.
- **The model describes calls instead of making them.** This is usually a
  runtime/tool-calling-format mismatch, not a prompt problem — check that your
  client advertises the tools in the format your model was trained on.

Feedback on running this with a specific local model is genuinely useful —
open an issue at
[github.com/AIops-tools/MySQL-AIops](https://github.com/AIops-tools/MySQL-AIops/issues)
with the model, runtime, and what went wrong.
