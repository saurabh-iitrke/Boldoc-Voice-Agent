"""Retention: what the privacy page promises, enforced on a schedule.

* The conversation's text (task transcripts and instructions, the caller's replies to the writer's questions,
  and the words that test/debug traces keep) is removed 30 days after the work finished, or within the next run
  if the account chose "don't keep".
* Documents nobody claimed (the number never signed in) are deleted 30 days after their last change.
* Call records, per-turn metrics and SMS receipts (no words) are kept 1 year; expired sign-in sessions and
  links are cleaned up.
* Sessions that ended over a week ago leave the orchestrator's memory (they stay in Postgres; an earlier
  document a caller wants to change is loaded again on demand).

In-memory tasks are redacted in memory and flushed, so a later flush can't write the old text back; tasks that
only live in Postgres are redacted there.

The control process runs it every 6 hours (worker.Background). The CLI only shows what the next run would remove
in memory and which unclaimed documents it would delete (it never writes: control is the single writer):
  docker compose exec control python -m apps.control.retention
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Dict

from loguru import logger

from protocol.fsm import TaskState

REDACTED = "(removed: conversation text is kept for 30 days)"
DONE = (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED)


class Retention:
    def __init__(self, orch, store, *, transcript_days: float = 30, unclaimed_doc_days: float = 30,
                 record_days: float = 365, memory_days: float = 7, clock=time.time):
        self.orch, self.store, self.clock = orch, store, clock
        self.transcript_s, self.unclaimed_s = transcript_days * 86400, unclaimed_doc_days * 86400
        self.record_days, self.memory_s = record_days, memory_days * 86400

    async def run(self, dry_run: bool = False) -> Dict[str, int]:
        out: Dict[str, int] = {}
        async with self.store.pool.acquire() as con:
            no_keep = {r["user_id"] for r in await con.fetch("select user_id from profiles where retention_days = 0")}
        out["transcripts_in_memory"] = self._redact_memory(no_keep, dry_run)
        if not dry_run and out["transcripts_in_memory"]:
            await self.store.flush()
        async with self.store.pool.acquire() as con:
            out.update(await self._sql(con, no_keep, dry_run))
        out["sessions_evicted"] = self._evict(dry_run)
        logger.info(f"retention{' (dry run)' if dry_run else ''}: {out}")
        return out

    # -------------------------------------------------------------------------------------------- memory
    def _redact_memory(self, no_keep: set, dry_run: bool) -> int:
        now, n = self.clock(), 0
        s = self.orch.s
        for t in s.tasks.values():
            if t.state not in DONE or (not t.transcript and t.instructions == [REDACTED] and not t.big):
                continue
            sess = s.sessions.get(t.session_id)
            dont_keep = sess is not None and sess.user_id in no_keep
            age = now - (t.created_at or now)
            if dont_keep or age > self.transcript_s:
                n += 1
                if not dry_run:
                    t.transcript, t.instructions, t.utterance_ids = "", [REDACTED], []
                    t.big = {}  # a long document's brief and the caller's answers
                    for q in s.questions.values():  # the caller's replies to the writer's questions
                        if q.task_id == t.id and q.answer not in (None, q.default):
                            q.answer = REDACTED
        return n

    def _evict(self, dry_run: bool) -> int:
        """Sessions that went offline over `memory_days` ago, with no unfinished work, leave memory."""
        s, now = self.orch.s, self.clock()
        gone = []
        for sid, sess in s.sessions.items():
            if sess.state.value != "offline":
                continue
            tasks = [t for t in s.tasks.values() if t.session_id == sid]
            docs = [d for d in s.docs.values() if d.session_id == sid]
            last = max([t.created_at for t in tasks] + [d.updated_at for d in docs] + [0.0])
            if any(t.state not in DONE for t in tasks) or now - last < self.memory_s:
                continue
            if any(d.id in o.attached for d in docs for o in s.sessions.values() if o.id != sid):
                continue  # a later call still has one of its documents open
            gone.append(sid)
        if dry_run:
            return len(gone)
        for sid in gone:
            doc_ids = {d.id for d in s.docs.values() if d.session_id == sid}
            for tid in [t.id for t in s.tasks.values() if t.session_id == sid]:
                s.tasks.pop(tid, None)
            for qid in [q.id for q in s.questions.values() if q.task_id not in s.tasks]:
                s.questions.pop(qid, None)
            for key in [k for k, d in s.deliveries.items() if d.doc_id in doc_ids]:
                s.deliveries.pop(key, None)
            for key in [k for k in s.versions if k[0] in doc_ids]:
                s.versions.pop(key, None)
                s.version_meta.pop(key, None)
            for did in doc_ids:
                s.docs.pop(did, None)
            s.events.pop(sid, None)
            s.sessions.pop(sid, None)
        return len(gone)

    # -------------------------------------------------------------------------------------------- Postgres
    async def _sql(self, con, no_keep: set, dry_run: bool) -> Dict[str, int]:
        in_memory = list(self.orch.s.tasks)
        days = f"{int(self.transcript_s // 86400)} days"
        queries = {
            # tasks that only live in Postgres (in-memory ones were handled above)
            "transcripts_stored": (
                "update entities set data = data || jsonb_build_object('transcript', '', 'instructions', "
                "jsonb_build_array($1::text), 'utterance_ids', '[]'::jsonb, 'big', '{}'::jsonb) "
                "where kind = 'task' and not (id = any($2::text[])) and data->>'state' in "
                "('completed', 'failed', 'cancelled') and (data->>'transcript' <> '' or data->'instructions' <> "
                "jsonb_build_array($1::text) or coalesce(data->'big', '{}'::jsonb) <> '{}'::jsonb) and"
                f" (updated_at < now() - interval '{days}' or session_id in "
                "(select call_uuid from call_records where user_id = any($3::bigint[])))",
                [REDACTED, in_memory, list(no_keep)]),
            # the caller's replies to the writer's questions, for tasks that only live in Postgres
            "answers_stored": (
                "update entities set data = jsonb_set(data, '{answer}', to_jsonb($1::text)) "
                "where kind = 'question' and not (data->>'task_id' = any($2::text[])) and data->>'answer' "
                "is not null and data->>'answer' not in ($1, data->>'default') and (updated_at < now() - interval "
                f"'{days}' or session_id in (select call_uuid from call_records where user_id = any($3::bigint[])))",
                [REDACTED, in_memory, list(no_keep)]),
            # a long document's draft (the committed version is the document; the draft was the way there)
            "old_drafts": (f"delete from doc_drafts where updated_at < now() - interval '{days}'", []),
            # the text of the files a caller added: kept as long as transcripts
            "old_materials": (f"delete from materials where created_at < now() - interval '{days}'", []),
            # words only test/debug calls kept in their traces and reports
            "trace_words": (
                "update call_traces set trace = jsonb_set(trace, '{events}', coalesce((select jsonb_agg(e - 'text') "
                "from jsonb_array_elements(trace->'events') e), '[]'::jsonb)) || '{\"content\": false}'::jsonb "
                f"where trace->>'content' = 'true' and created_at < now() - interval '{days}'", []),
            "report_words": (
                "update call_reports set report = jsonb_set(jsonb_set(report, '{turns}', coalesce((select "
                "jsonb_agg(t - 'text' - 'reply_text') from jsonb_array_elements(report->'turns') t), '[]'::jsonb)), "
                "'{false_claims}', '[]'::jsonb) || '{\"content\": false}'::jsonb "
                f"where report->>'content' = 'true' and created_at < now() - interval '{days}'", []),
            "old_call_records": (f"delete from call_records where admitted_at < now() - interval "
                                 f"'{int(self.record_days)} days'", []),
            "old_turns": (f"delete from call_turns where at < now() - interval '{int(self.record_days)} days'", []),
            "old_sms_receipts": (f"delete from sms_receipts where at < now() - interval "
                                 f"'{int(self.record_days)} days'", []),
            "expired_sessions": ("delete from auth_sessions where expires_at < now() or revoked_at < now() - "
                                 "interval '1 day'", []),
            "expired_links": ("delete from links where expires_at < now() - interval '7 days'", []),
        }
        out: Dict[str, int] = {}
        for name, (sql, args) in queries.items():
            if dry_run:
                continue
            status = await con.execute(sql, *args)
            out[name] = int(status.split()[-1]) if status.split()[-1].isdigit() else 0
        out["unclaimed_documents"] = await self._delete_unclaimed(con, dry_run)
        return out

    async def _delete_unclaimed(self, con, dry_run: bool) -> int:
        """Documents whose number never signed in, untouched for 30 days: deleted with their files and links."""
        days = f"{int(self.unclaimed_s // 86400)} days"
        rows = await con.fetch(
            "select d.id from documents d left join users u on u.id = d.owner_user_id "
            f"where d.updated_at < now() - interval '{days}' and (u.id is null or u.status <> 'active') "
            "and not exists (select 1 from users a where a.phone_e164 = d.owner and a.status = 'active')")
        ids = [r["id"] for r in rows if r["id"] not in self.orch.s.docs]
        if dry_run or not ids:
            return len(ids)
        async with con.transaction():
            for table, col in (("renditions", "doc_id"), ("doc_versions", "doc_id"), ("links", "doc_id"),
                               ("materials", "doc_id")):
                await con.execute(f"delete from {table} where {col} = any($1::text[])", ids)
            await con.execute("delete from entities where kind = 'delivery' and data->>'doc_id' = any($1::text[])",
                              ids)
            await con.execute("delete from entities where kind = 'doc' and id = any($1::text[])", ids)
            await con.execute("delete from documents where id = any($1::text[])", ids)
        return len(ids)


async def _cli(argv) -> int:
    from .core.orchestrator import Orchestrator
    from .store_pg import PgStore

    orch = Orchestrator(clock=time.time)
    store = await PgStore.connect(os.environ["DATABASE_URL"], orch)
    try:
        await store.load()
        out = await Retention(orch, store).run(dry_run=True)
        print(json.dumps(out))
        return 0
    finally:
        await store.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli(sys.argv[1:])))
