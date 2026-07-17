"""Flagship signature analyses over MySQL telemetry (pure analysis).

The differentiators — transparent heuristics, every flag reported with its number
so a DBA can see *why* something was ranked, never a black-box verdict:

  1. ``slow_query_rca`` — take the worst statement digest (+ an optional
     EXPLAIN plan) and map it to a likely cause + concrete action.
  2. ``lock_wait_rca`` — build the wait-for tree from InnoDB lock-wait pairs,
     name the root blocker, and parse the last deadlock out of
     ``SHOW ENGINE INNODB STATUS``.
  3. ``replication_lag_rca`` — map replica thread state / lag / error fields to
     a cited cause and action.
  4. ``fragmentation_analysis`` — rank tables by reclaimable ``data_free``
     into OPTIMIZE TABLE candidates.

All four are pure functions (no I/O): pass them telemetry (from the reads in
the other ops modules, or injected) and they return the analysis.
"""

from __future__ import annotations

import re
from typing import Any

MAX_ROWS = 100


# ── 1. slow query RCA ───────────────────────────────────────────────────────
# Thresholds that flip a signal on (each reported with its measured number).
_SLOW_MEAN_MS = 100.0
_NO_INDEX_PCT_WARN = 50.0
_LOCK_TIME_PCT_WARN = 50.0
_EXAMINED_PER_SENT_WARN = 100.0
_HIGH_CALLS = 100_000


def _plan_access_types(plan: Any, found: set[str]) -> None:
    """Recursively collect ``access_type`` values from an EXPLAIN JSON plan."""
    if isinstance(plan, dict):
        access = plan.get("access_type")
        if isinstance(access, str):
            found.add(access)
        for value in plan.values():
            _plan_access_types(value, found)
    elif isinstance(plan, list):
        for item in plan:
            _plan_access_types(item, found)


def _slow_findings(worst: dict, access_types: set[str]) -> list[dict]:
    """Build the list of cited findings (cause + action) for the worst digest."""
    findings: list[dict] = []
    mean = float(worst.get("meanTimeMs") or 0)
    no_index_pct = float(worst.get("noIndexUsedPct") or 0)
    lock_pct = float(worst.get("lockTimePct") or 0)
    examined_per_sent = worst.get("rowsExaminedPerSent")
    tmp_disk = int(worst.get("tmpDiskTables") or 0)
    calls = int(worst.get("calls") or 0)

    if no_index_pct >= _NO_INDEX_PCT_WARN or "ALL" in access_types:
        detail = f"noIndexUsedPct {no_index_pct}% >= {_NO_INDEX_PCT_WARN}%"
        if "ALL" in access_types:
            detail += "; EXPLAIN access_type=ALL (full table scan)"
        findings.append({
            "signal": "full scan / no index used",
            "detail": detail,
            "cause": "The statement scans a table with no usable index (SUM_NO_INDEX_USED).",
            "action": "Add an index on the filter/join columns; confirm with EXPLAIN FORMAT=JSON.",
        })
    if lock_pct >= _LOCK_TIME_PCT_WARN:
        findings.append({
            "signal": "lock time dominant",
            "detail": f"lockTimePct {lock_pct}% >= {_LOCK_TIME_PCT_WARN}% of total time",
            "cause": "The statement spends most of its time waiting on locks, not working.",
            "action": (
                "Run lock_wait_rca to find the blocking chain; shorten conflicting "
                "transactions or split the hot rows."
            ),
        })
    if isinstance(examined_per_sent, (int, float)) and examined_per_sent >= _EXAMINED_PER_SENT_WARN:
        findings.append({
            "signal": "high rows examined per row sent",
            "detail": f"rowsExaminedPerSent {examined_per_sent} >= {_EXAMINED_PER_SENT_WARN}",
            "cause": "The server discards most rows it reads — the index is not selective.",
            "action": "Add/extend a composite index so the WHERE clause filters inside the index.",
        })
    if tmp_disk > 0:
        findings.append({
            "signal": "temporary tables spilled to disk",
            "detail": f"tmpDiskTables={tmp_disk} — sorts/GROUP BY spilled",
            "cause": "tmp_table_size / max_heap_table_size is too small for this query.",
            "action": "Raise tmp_table_size AND max_heap_table_size, or reduce the sorted set.",
        })
    if calls >= _HIGH_CALLS:
        findings.append({
            "signal": "very high call count",
            "detail": f"calls={calls} >= {_HIGH_CALLS}",
            "cause": "A cheap statement is executed enormously often (possible N+1).",
            "action": "Batch/cache at the application, or use a set-based query.",
        })
    if not findings:
        findings.append({
            "signal": "no dominant signal",
            "detail": f"mean {mean}ms, calls {calls}",
            "cause": "The statement is costly but shows no single clear driver.",
            "action": "EXPLAIN FORMAT=JSON it and inspect the most expensive plan node.",
        })
    return findings


def slow_query_rca(statements: list[dict], explain: dict | None = None) -> dict:
    """[READ] RCA for the worst statement digest (+ optional EXPLAIN plan).

    Picks the digest with the greatest total time, then maps its numbers
    (no-index share, lock-time share, examined/sent ratio, tmp-disk spill,
    call count) — and any EXPLAIN plan access types supplied — to cited causes
    and concrete actions.
    """
    ranked = sorted(
        (st for st in (statements or []) if isinstance(st, dict)),
        key=lambda x: float(x.get("totalTimeMs") or 0),
        reverse=True,
    )
    if not ranked:
        return {"evaluated": 0, "worst": None, "findings": [], "note": "No statements supplied."}

    worst = ranked[0]
    access_types: set[str] = set()
    if explain:
        _plan_access_types(explain.get("plan", explain), access_types)
    findings = _slow_findings(worst, access_types)
    return {
        "evaluated": len(ranked),
        "worst": {
            "schema": worst.get("schema"),
            "digest": worst.get("digest"),
            "query": worst.get("query"),
            "calls": worst.get("calls"),
            "totalTimeMs": worst.get("totalTimeMs"),
            "meanTimeMs": worst.get("meanTimeMs"),
            "lockTimePct": worst.get("lockTimePct"),
            "noIndexUsedPct": worst.get("noIndexUsedPct"),
            "rowsExaminedPerSent": worst.get("rowsExaminedPerSent"),
            "tmpDiskTables": worst.get("tmpDiskTables"),
        },
        "planAccessTypes": sorted(access_types),
        "findings": findings,
        "note": (
            "Advisory read-only heuristic over statement digests; every finding "
            "cites the measured number. Worst = greatest total time."
        ),
    }


# ── 2. InnoDB lock-wait & deadlock chain RCA ────────────────────────────────


def _descendants(root: int, children: dict[int, list[int]]) -> set[int]:
    """All session ids transitively blocked by ``root`` (BFS, cycle-safe)."""
    seen: set[int] = set()
    stack = list(children.get(root, []))
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        stack.extend(children.get(sid, []))
    return seen


_DEADLOCK_SECTION_RE = re.compile(
    r"LATEST DETECTED DEADLOCK\s*\n-+\n(.*?)(?:\n-{4,}|\Z)", re.DOTALL
)


def parse_last_deadlock(innodb_status: str) -> dict | None:
    """Extract the LATEST DETECTED DEADLOCK section from SHOW ENGINE INNODB STATUS.

    Returns {detectedAt, victim, transactions:[{index, query}], raw} or None
    when the server has recorded no deadlock since startup.
    """
    if not innodb_status:
        return None
    m = _DEADLOCK_SECTION_RE.search(innodb_status)
    if not m:
        return None
    section = m.group(1)
    first_line = section.strip().splitlines()[0].strip() if section.strip() else ""
    transactions: list[dict] = []
    for tm in re.finditer(
        r"\*\*\* \((\d+)\) TRANSACTION:\s*\n(.*?)(?=\n\*\*\*|\Z)", section, re.DOTALL
    ):
        body = tm.group(2)
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        query_lines: list[str] = []
        for ln in lines[1:]:
            if ln.startswith(("RECORD LOCKS", "TABLE LOCK", "MySQL thread id")):
                if ln.startswith("MySQL thread id"):
                    continue
                break
            query_lines.append(ln)
        transactions.append({
            "index": int(tm.group(1)),
            "query": " ".join(query_lines)[:500],
        })
    victim_m = re.search(r"\*\*\* WE ROLL BACK TRANSACTION \((\d+)\)", section)
    return {
        "detectedAt": first_line[:64],
        "victim": int(victim_m.group(1)) if victim_m else None,
        "transactions": transactions,
        "raw": section.strip()[:4000],
    }


def lock_wait_rca(pairs: list[dict], innodb_status: str | None = None) -> dict:
    """[READ] Build the wait-for tree from lock-wait pairs and name the root blocker.

    Pure analysis over pairs ({blockedId, blockingId, blockedQuery,
    blockingQuery, waitSeconds, ...}). A root blocker blocks others but is
    itself blocked by none; the worst root is the one with the most
    transitively-blocked sessions. A cycle (everyone blocked) is reported as a
    live deadlock. If ``innodb_status`` text is supplied, the LATEST DETECTED
    DEADLOCK section is parsed and attached.
    """
    edges = [
        p for p in (pairs or [])
        if isinstance(p, dict) and p.get("blockedId") and p.get("blockingId")
    ]
    last_deadlock = parse_last_deadlock(innodb_status or "")

    if not edges:
        return {
            "blockedSessions": 0,
            "roots": [],
            "lastDeadlock": last_deadlock,
            "note": "No lock waits detected."
            + (" A past deadlock was found in the InnoDB status." if last_deadlock else ""),
        }

    children: dict[int, list[int]] = {}
    blocking_query: dict[int, str] = {}
    max_wait: dict[int, int] = {}
    blocked_ids: set[int] = set()
    blocking_ids: set[int] = set()
    for e in edges:
        b, g = e["blockedId"], e["blockingId"]
        children.setdefault(g, []).append(b)
        blocked_ids.add(b)
        blocking_ids.add(g)
        blocking_query.setdefault(g, e.get("blockingQuery") or "")
        try:
            max_wait[g] = max(max_wait.get(g, 0), int(e.get("waitSeconds") or 0))
        except (TypeError, ValueError):
            pass

    root_ids = [sid for sid in blocking_ids if sid not in blocked_ids]
    if not root_ids:
        return {
            "blockedSessions": len(blocked_ids),
            "roots": [],
            "deadlockSuspected": True,
            "lastDeadlock": last_deadlock,
            "note": (
                "Every blocker is itself blocked — a live cycle. InnoDB usually "
                "resolves true deadlocks automatically by rolling a victim back; "
                "if this persists, kill_session one participant to break it."
            ),
        }

    roots = []
    for sid in root_ids:
        blocked = _descendants(sid, children)
        roots.append({
            "rootId": sid,
            "blockedCount": len(blocked),
            "blockedIds": sorted(blocked),
            "maxWaitSeconds": max_wait.get(sid, 0),
            "rootQuery": blocking_query.get(sid, ""),
            "action": (
                f"Session {sid} is the head of the chain — investigate its "
                "transaction; kill_query(session_id) to stop its statement, or "
                "kill_session(session_id) to end it and release the locks."
            ),
        })
    roots.sort(key=lambda r: r["blockedCount"], reverse=True)
    return {
        "blockedSessions": len(blocked_ids),
        "rootCount": len(roots),
        "worstRootId": roots[0]["rootId"],
        "roots": roots[:MAX_ROWS],
        "lastDeadlock": last_deadlock,
        "note": (
            "Advisory read-only heuristic: a root blocker holds locks others "
            "wait on but waits on nobody; worst root blocks the most sessions."
        ),
    }


# ── 3. replication lag RCA ──────────────────────────────────────────────────
_LAG_WARN_SECONDS = 60


def replication_lag_rca(status: dict) -> dict:
    """[READ] Map replica thread state / lag / error fields to cause + action.

    Pure analysis over one normalised replica record (as from replica_status:
    {ioThreadRunning, sqlThreadRunning, secondsBehindSource, lastIoError,
    lastSqlError, sqlDelay, ...}).
    """
    replicas = (status or {}).get("replicas") or []
    if isinstance(status, dict) and not replicas and status.get("ioThreadRunning") is not None:
        replicas = [status]  # a bare record was passed directly
    if not replicas:
        return {
            "isReplica": False,
            "findings": [],
            "note": "Not a replica (no replication channel configured) — nothing to analyse.",
        }

    rec = replicas[0]
    io_running = str(rec.get("ioThreadRunning") or "").lower() == "yes"
    sql_running = str(rec.get("sqlThreadRunning") or "").lower() == "yes"
    behind = rec.get("secondsBehindSource")
    sql_delay = rec.get("sqlDelay")

    findings: list[dict] = []
    if not io_running:
        findings.append({
            "signal": "IO thread not running",
            "detail": f"ioThreadRunning={rec.get('ioThreadRunning')}; "
                      f"lastIoError={rec.get('lastIoError') or '(none)'}",
            "cause": (
                "The replica cannot fetch binlog events — source unreachable, "
                "bad credentials, or a purged binlog position."
            ),
            "action": (
                "Check lastIoError: fix connectivity/credentials, or re-seed the "
                "replica if the source has purged the required binlogs."
            ),
        })
    if not sql_running:
        findings.append({
            "signal": "SQL thread not running",
            "detail": f"sqlThreadRunning={rec.get('sqlThreadRunning')}; "
                      f"lastSqlError={rec.get('lastSqlError') or '(none)'}",
            "cause": (
                "The applier stopped — usually a statement that failed on the "
                "replica (duplicate key, missing row, schema drift)."
            ),
            "action": (
                "Check lastSqlError; fix the data/schema divergence, then START "
                "REPLICA. Skipping events hides divergence — prefer repairing it."
            ),
        })
    if io_running and sql_running and isinstance(behind, (int, float)):
        if sql_delay and int(sql_delay or 0) > 0:
            findings.append({
                "signal": "intentional apply delay configured",
                "detail": f"sqlDelay={sql_delay}s; secondsBehindSource={behind}",
                "cause": "A deliberate delayed replica (SQL_Delay) — lag is by design.",
                "action": "No action if the delay is intentional; otherwise remove SQL_Delay.",
            })
        elif behind >= _LAG_WARN_SECONDS:
            findings.append({
                "signal": "replica lagging",
                "detail": f"secondsBehindSource {behind}s >= {_LAG_WARN_SECONDS}s",
                "cause": (
                    "The applier cannot keep up — large transactions, "
                    "single-threaded apply, or replica I/O saturation."
                ),
                "action": (
                    "Break up bulk writes on the source; enable parallel apply "
                    "(replica_parallel_workers); check replica disk/CPU headroom."
                ),
            })
    if not findings:
        findings.append({
            "signal": "replication healthy",
            "detail": f"both threads running; secondsBehindSource={behind}",
            "cause": "IO and SQL threads are running with negligible lag.",
            "action": "No action needed.",
        })
    return {
        "isReplica": True,
        "ioThreadRunning": io_running,
        "sqlThreadRunning": sql_running,
        "secondsBehindSource": behind,
        "findings": findings,
        "note": (
            "Advisory read-only heuristic over the replica status record; "
            f"lag threshold {_LAG_WARN_SECONDS}s."
        ),
    }


# ── 4. table fragmentation analysis ─────────────────────────────────────────
_FREE_PCT_WARN = 25.0
_FREE_BYTES_WARN = 100 * 1024 * 1024  # 100 MB


def _fragmentation_recommendation(row: dict) -> dict | None:
    """Return a cited recommendation for one table, or None if it looks healthy."""
    free_pct = float(row.get("freePct") or 0)
    free_bytes = int(row.get("freeBytes") or 0)
    if free_pct < _FREE_PCT_WARN or free_bytes < _FREE_BYTES_WARN:
        return None
    return {
        "schema": row.get("schema"),
        "table": row.get("table"),
        "engine": row.get("engine"),
        "freePct": free_pct,
        "freeBytes": free_bytes,
        "freePretty": row.get("freePretty"),
        "reasons": [
            f"freePct {free_pct}% >= {_FREE_PCT_WARN}%",
            f"freeBytes {free_bytes} >= {_FREE_BYTES_WARN}",
        ],
        "action": (
            "Run optimize_table (rebuilds the table, reclaims data_free). "
            "InnoDB uses online DDL but still takes a brief lock — schedule "
            "off-peak for hot tables."
        ),
    }


def fragmentation_analysis(tables: list[dict]) -> dict:
    """[READ] Rank tables by reclaimable data_free into OPTIMIZE TABLE candidates.

    Pure analysis over fragmentation rows ({schema, table, engine, freePct,
    freeBytes, ...}). Each recommendation cites the numbers that triggered it;
    healthy tables are omitted.
    """
    recs = [
        rec for rec in (_fragmentation_recommendation(r) for r in (tables or [])) if rec
    ]
    recs.sort(key=lambda r: r["freeBytes"], reverse=True)
    return {
        "tablesEvaluated": len(tables or []),
        "needsAttentionCount": len(recs),
        "thresholds": {"freePct": _FREE_PCT_WARN, "freeBytes": _FREE_BYTES_WARN},
        "recommendations": recs[:MAX_ROWS],
        "note": (
            "Advisory read-only heuristic: flags freePct >= "
            f"{_FREE_PCT_WARN}% AND data_free >= {_FREE_BYTES_WARN} bytes. "
            "data_free is a coarse tablespace metric — partitioned tables and "
            "shared tablespaces can distort it."
        ),
    }
