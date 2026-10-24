"""Postgres persistence for the orchestrator (the control plane is the single writer).

The orchestrator mutates its in-memory aggregate synchronously (no awaits inside a command), then `flush` writes every
changed entity, new document versions, staged renditions/links and new events in ONE transaction before the caller is
answered. Events become visible to consumers (talker long-poll, website feed) only after they are durable (transactional
outbox), so nobody hears "it's ready" for a version whose files aren't stored yet.

Tables:
  entities(kind, id)      JSONB snapshots of sessions, docs, tasks, questions, deliveries, idempotency results
  doc_versions            immutable document versions (canonical JSON) + "what changed"
  renditions              rendered files per (doc, version, format)
  events                  per-session ordered event log (seq)
  links                   public link tokens (sha256 only; the token itself is never stored)
  documents               read model for link pages and "my documents"
"""
from __future__ import annotations

import asyncio
import dataclasses
import enum
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from loguru import logger

from docengine.model import Document
from protocol.fsm import ConvState, TaskState
from protocol.messages import Event

from .accounts import ledger
from .core.orchestrator import CommandResult, Delivery, DocRecord, Orchestrator, Question, Session, Task

SCHEMA = """
create table if not exists entities (
  kind text not null, id text not null, session_id text, data jsonb not null,
  updated_at timestamptz not null default now(), primary key (kind, id));
create index if not exists entities_session on entities (session_id);
create table if not exists doc_versions (
  doc_id text not null, version int not null, document jsonb not null, meta jsonb not null default '{}',
  created_at timestamptz not null default now(), primary key (doc_id, version));
create table if not exists renditions (
  doc_id text not null, version int not null, fmt text not null, filename text not null, mime text not null,
  data bytea not null, sha256 text not null, created_at timestamptz not null default now(),
  primary key (doc_id, version, fmt));
create table if not exists events (
  session_id text not null, seq int not null, data jsonb not null, created_at timestamptz not null default now(),
  primary key (session_id, seq));
create table if not exists links (
  token_hash text primary key, doc_id text not null, created_at timestamptz not null default now(),
  expires_at timestamptz not null);
create index if not exists links_doc on links (doc_id);
create table if not exists documents (
  id text primary key, session_id text not null, owner text, title text not null default '',
  head_version int not null default 0, formats jsonb not null default '[]', doc_lang text not null default 'en-IN',
  created_at timestamptz not null default now(), updated_at timestamptz not null default now());
create index if not exists documents_owner on documents (owner, updated_at desc);
create table if not exists call_traces (
  id bigserial primary key, session_id text not null, call_id text not null, started_at timestamptz not null,
  trace jsonb not null, created_at timestamptz not null default now());
create index if not exists call_traces_session on call_traces (session_id, created_at desc);
"""

_KINDS = {"session": Session, "doc": DocRecord, "task": Task, "question": Question, "delivery": Delivery,
          "idem": CommandResult}


def _plain(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    return obj


def _revive(kind: str, data: dict) -> Any:
    data = dict(data)
    if kind == "session":
        data["state"] = ConvState(data["state"])
    elif kind == "task":
        data["state"] = TaskState(data["state"])
    elif kind == "delivery":
        data["key"] = tuple(data["key"])
    return _KINDS[kind](**data)


def _ts(t: Optional[float]) -> datetime:
    return datetime.fromtimestamp(t or time.time(), tz=timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PgStore:
    def __init__(self, pool: asyncpg.Pool, orch: Orchestrator):
        self.pool, self.orch = pool, orch
        self._seen: Dict[Tuple[str, str], str] = {}
        self._versions_saved: set = set()
        self._events_saved: Dict[str, int] = {}
        self._staged_renditions: List[tuple] = []
        self._staged_links: List[tuple] = []
        self._staged_debits: List[tuple] = []
        self._lock = asyncio.Lock()
        self.visible = asyncio.Condition()
        self.on_durable_events = None  # async callback(rows) after events are committed (Redis fan-out)

    @classmethod
    async def connect(cls, dsn: str, orch: Orchestrator) -> PgStore:
        from .migrations import migrate

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8)
        async with pool.acquire() as con:
            await con.execute(SCHEMA)
            await migrate(con)
        return cls(pool, orch)

    async def close(self) -> None:
        await self.pool.close()

    # ------------------------------------------------------------------ load (startup)
    async def load(self, since_days: float = 7.0) -> int:
        """Restore recent sessions (and everything they own) into the orchestrator. Older sessions stay in Postgres
        only: their links keep working through the read model."""
        s = self.orch.s
        async with self.pool.acquire() as con:
            sids = [r["id"] for r in await con.fetch(
                "select id from entities where kind='session' and updated_at > now() - make_interval(secs => $1)",
                since_days * 86400)]
            rows = await con.fetch("select kind, id, data from entities where session_id = any($1::text[]) "
                                   "or kind = 'idem' and updated_at > now() - interval '1 day'", sids)
            for r in rows:
                data = json.loads(r["data"])
                obj = _revive(r["kind"], data)
                self._seen[(r["kind"], r["id"])] = json.dumps(data, sort_keys=True, ensure_ascii=False)
                if r["kind"] == "session":
                    s.sessions[obj.id] = obj
                elif r["kind"] == "doc":
                    s.docs[obj.id] = obj
                elif r["kind"] == "task":
                    s.tasks[obj.id] = obj
                elif r["kind"] == "question":
                    s.questions[obj.id] = obj
                elif r["kind"] == "delivery":
                    s.deliveries[obj.key] = obj
                else:
                    s.idem[r["id"]] = obj
            for r in await con.fetch("select doc_id, version, document, meta from doc_versions "
                                     "where doc_id = any($1::text[])", list(s.docs)):
                s.versions[(r["doc_id"], r["version"])] = Document.model_validate_json(r["document"])
                s.version_meta[(r["doc_id"], r["version"])] = json.loads(r["meta"])
                self._versions_saved.add((r["doc_id"], r["version"]))
            for r in await con.fetch("select session_id, data from events where session_id = any($1::text[]) "
                                     "order by session_id, seq", sids):
                s.events.setdefault(r["session_id"], []).append(Event.model_validate_json(r["data"]))
        self._events_saved = {sid: len(evs) for sid, evs in s.events.items()}
        return len(sids)

    async def load_doc(self, doc_id: str) -> bool:
        """Bring one document (and its versions and deliveries) into memory: an earlier document a caller wants
        to change on a later call, older than what startup loaded. True if it is (now) in memory."""
        s = self.orch.s
        if doc_id in s.docs:
            return True
        async with self.pool.acquire() as con:
            row = await con.fetchrow("select data from entities where kind='doc' and id=$1", doc_id)
            if row is None:
                return False
            data = json.loads(row["data"])
            doc = _revive("doc", data)
            dels = await con.fetch("select id, data from entities where kind='delivery' and data->>'doc_id' = $1",
                                   doc_id)
            versions = await con.fetch("select version, document, meta from doc_versions where doc_id=$1", doc_id)
        self._seen[("doc", doc_id)] = json.dumps(data, sort_keys=True, ensure_ascii=False)
        s.docs[doc_id] = doc
        for r in dels:
            d = json.loads(r["data"])
            self._seen[("delivery", r["id"])] = json.dumps(d, sort_keys=True, ensure_ascii=False)
            obj = _revive("delivery", d)
            s.deliveries[obj.key] = obj
        for r in versions:
            s.versions[(doc_id, r["version"])] = Document.model_validate_json(r["document"])
            s.version_meta[(doc_id, r["version"])] = json.loads(r["meta"])
            self._versions_saved.add((doc_id, r["version"]))
        return True

    async def doc_owner(self, doc_id: str) -> Optional[dict]:
        """Who a stored document belongs to: its account, and the number that created it (older documents)."""
        async with self.pool.acquire() as con:
            r = await con.fetchrow(
                "select d.owner_user_id, d.owner, c.user_id as call_user, (select p.user_id from user_phones p "
                "where p.phone_e164 = d.owner) as owner_linked_to from documents d left join call_records c "
                "on c.call_uuid = d.session_id where d.id = $1", doc_id)
        return dict(r) if r else None

    async def recent_docs(self, user_id: Optional[int], phone: Optional[str], exclude_session: str,
                          limit: int = 5) -> List[dict]:
        """The caller's latest documents from earlier calls, for "change my leave application" on a new call."""
        if not user_id and not phone:
            return []
        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "select id, title, head_version, updated_at from documents where head_version > 0 and "
                "session_id <> $3 and (owner_user_id = $1 or (owner_user_id is null and owner = $2) "
                "or owner in (select phone_e164 from user_phones where user_id = $1)) "
                "order by updated_at desc limit $4", user_id, phone, exclude_session, limit)
        return [{"id": r["id"], "title": r["title"] or "Untitled", "head_version": r["head_version"],
                 "updated_at": r["updated_at"].isoformat()} for r in rows]

    # ------------------------------------------------------------------ staging (same tick as the commit)
    def stage_renditions(self, doc_id: str, version: int, files: List[Any]) -> None:
        for f in files:
            self._staged_renditions.append((doc_id, version, f.fmt, f.filename, f.mime, f.data,
                                            hashlib.sha256(f.data).hexdigest()))

    def stage_debit(self, user_id: int, kind: str, amount: int, idem_key: str, ref: str) -> None:
        """A credit-ledger entry written in the same transaction as the commit it pays for (crash-safe, once)."""
        self._staged_debits.append((user_id, kind, amount, idem_key, ref))

    def stage_link(self, doc_id: str, token: str, ttl_days: float) -> None:
        self._staged_links.append((token_hash(token), doc_id, _ts(time.time() + ttl_days * 86400)))

    # ------------------------------------------------------------------ flush
    def _collect(self) -> dict:
        s = self.orch.s
        sid_of_doc = {d.id: d.session_id for d in s.docs.values()}
        sources = [("session", s.sessions.values(), lambda o: o.id, lambda o: o.id),
                   ("doc", s.docs.values(), lambda o: o.id, lambda o: o.session_id),
                   ("task", s.tasks.values(), lambda o: o.id, lambda o: o.session_id),
                   ("question", s.questions.values(), lambda o: o.id,
                    lambda o: s.tasks[o.task_id].session_id if o.task_id in s.tasks else None),
                   ("delivery", s.deliveries.values(), lambda o: o.id, lambda o: sid_of_doc.get(o.doc_id)),
                   ("idem", [(k, v) for k, v in s.idem.items()], lambda kv: kv[0], lambda kv: None)]
        entities, docs = [], []
        for kind, objs, id_of, sid_of in sources:
            for o in objs:
                data = _plain(o[1] if kind == "idem" else o)
                j = json.dumps(data, sort_keys=True, ensure_ascii=False)
                key = (kind, id_of(o))
                if self._seen.get(key) != j:
                    entities.append((kind, key[1], sid_of(o), j))
                    if kind == "doc":
                        owner = s.sessions[o.session_id].caller if o.session_id in s.sessions else None
                        docs.append((o.id, o.session_id, owner, o.title, o.head_version, json.dumps(o.formats),
                                     o.doc_lang, _ts(o.created_at), _ts(o.updated_at), o.owner_user_id))
        versions = [(d, v, doc.model_dump_json(), json.dumps(s.version_meta.get((d, v), {}), ensure_ascii=False))
                    for (d, v), doc in s.versions.items() if (d, v) not in self._versions_saved]
        events = []
        for sid, evs in s.events.items():
            events += [(sid, ev.seq, ev.model_dump_json()) for ev in evs[self._events_saved.get(sid, 0):]]
        return {"entities": entities, "docs": docs, "versions": versions, "events": events,
                "renditions": self._staged_renditions, "links": self._staged_links, "debits": self._staged_debits}

    async def flush(self) -> None:
        async with self._lock:  # one writer, in order: a later flush never writes older state
            batch = self._collect()
            self._staged_renditions, self._staged_links, self._staged_debits = [], [], []
            if not any(batch.values()):
                return
            try:
                async with self.pool.acquire() as con, con.transaction():
                    if batch["renditions"]:
                        await con.executemany(
                            "insert into renditions (doc_id, version, fmt, filename, mime, data, sha256) "
                            "values ($1,$2,$3,$4,$5,$6,$7) on conflict (doc_id, version, fmt) do update set "
                            "filename=excluded.filename, mime=excluded.mime, data=excluded.data, "
                            "sha256=excluded.sha256", batch["renditions"])
                    if batch["versions"]:
                        await con.executemany(
                            "insert into doc_versions (doc_id, version, document, meta) values ($1,$2,$3::jsonb,"
                            "$4::jsonb) on conflict do nothing", batch["versions"])
                    if batch["entities"]:
                        await con.executemany(
                            "insert into entities (kind, id, session_id, data) values ($1,$2,$3,$4::jsonb) "
                            "on conflict (kind, id) do update set data=excluded.data, "
                            "session_id=coalesce(excluded.session_id, entities.session_id), updated_at=now()",
                            batch["entities"])
                    if batch["docs"]:
                        await con.executemany(
                            "insert into documents (id, session_id, owner, title, head_version, formats, doc_lang, "
                            "created_at, updated_at, owner_user_id) values ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10) "
                            "on conflict (id) do update set title=excluded.title, head_version=excluded.head_version, "
                            "formats=excluded.formats, doc_lang=excluded.doc_lang, updated_at=excluded.updated_at, "
                            "owner_user_id=coalesce(excluded.owner_user_id, documents.owner_user_id)",
                            batch["docs"])
                    if batch["links"]:
                        await con.executemany("insert into links (token_hash, doc_id, expires_at) values ($1,$2,$3) "
                                              "on conflict do nothing", batch["links"])
                    if batch["events"]:
                        await con.executemany("insert into events (session_id, seq, data) values ($1,$2,$3::jsonb) "
                                              "on conflict do nothing", batch["events"])
                    for user_id, kind, amount, idem_key, ref in batch["debits"]:
                        try:
                            async with con.transaction():  # savepoint: a bad debit never blocks what it rides on
                                await ledger.post(con, user_id, kind, amount, idem_key, ref=ref)
                        except Exception as e:
                            logger.error(f"debit dropped {idem_key}: {type(e).__name__}: {e}")
            except Exception:
                # Nothing is marked saved: the next flush retries the same rows (staged files included).
                self._staged_renditions = batch["renditions"] + self._staged_renditions
                self._staged_links = batch["links"] + self._staged_links
                self._staged_debits = batch["debits"] + self._staged_debits
                raise
            for kind, id_, _sid, j in batch["entities"]:
                self._seen[(kind, id_)] = j
            self._versions_saved.update((d, v) for d, v, _, _ in batch["versions"])
            for sid, seq, _ in batch["events"]:
                self._events_saved[sid] = max(self._events_saved.get(sid, 0), seq)
        if batch["events"]:
            async with self.visible:
                self.visible.notify_all()
            if self.on_durable_events is not None:
                await self.on_durable_events(batch["events"])

    def visible_events(self, session_id: str, after: int) -> List[Event]:
        """Durable events only (seq <= what has been flushed)."""
        upto = self._events_saved.get(session_id, 0)
        return [e for e in self.orch.s.events.get(session_id, [])[after:upto]]

    async def wait_events(self, session_id: str, after: int, timeout_s: float) -> List[Event]:
        evs = self.visible_events(session_id, after)
        if evs:
            return evs
        try:
            async with self.visible:
                await asyncio.wait_for(self.visible.wait_for(lambda: bool(self.visible_events(session_id, after))),
                                       timeout_s)
        except asyncio.TimeoutError:
            return []
        return self.visible_events(session_id, after)

    async def known_facts(self, user_id: int) -> dict:
        """The account's saved "My details" for the thinker; never fails a draft (empty on any error)."""
        from .accounts.web import known_facts
        try:
            async with self.pool.acquire() as con:
                return await known_facts(con, user_id)
        except Exception:
            return {}

    async def store_trace(self, session_id: str, trace: dict) -> None:
        async with self.pool.acquire() as con:
            await con.execute("insert into call_traces (session_id, call_id, started_at, trace) values "
                              "($1, $2, $3, $4::jsonb)", session_id, str(trace.get("call_id", ""))[:100],
                              _ts(trace.get("started_wall")), json.dumps(trace, ensure_ascii=False))

    # ------------------------------------------------------------------ read model (link pages, downloads)
    async def resolve_link(self, token: str) -> Optional[dict]:
        async with self.pool.acquire() as con:
            r = await con.fetchrow("select doc_id, expires_at, device_hash from links where token_hash=$1",
                                   token_hash(token))
        if r is None or r["expires_at"] < datetime.now(timezone.utc):
            return None
        return {"doc_id": r["doc_id"], "expires_at": r["expires_at"], "device_hash": r["device_hash"]}

    async def claim_link(self, token: str, device: str) -> bool:
        """The first browser to open a link keeps it (links.py). True if this browser holds it now."""
        async with self.pool.acquire() as con:
            held = await con.fetchval(
                "update links set device_hash = coalesce(device_hash, $2), claimed_at = coalesce(claimed_at, now()) "
                "where token_hash=$1 and expires_at > now() returning device_hash", token_hash(token), device)
        return held == device

    async def stop_links(self, doc_id: str) -> int:
        """Stop every live link to a document (the owner asked for a new one)."""
        async with self.pool.acquire() as con:
            r = await con.execute("update links set expires_at = now() where doc_id=$1 and expires_at > now()",
                                  doc_id)
        return int(r.split()[-1])

    async def document_view(self, doc_id: str) -> Optional[dict]:
        async with self.pool.acquire() as con:
            d = await con.fetchrow("select * from documents where id=$1", doc_id)
            if d is None or d["head_version"] == 0:
                return None
            versions = await con.fetch("select version, meta, created_at from doc_versions where doc_id=$1 "
                                       "order by version desc", doc_id)
            head = await con.fetchval("select document from doc_versions where doc_id=$1 and version=$2", doc_id,
                                      d["head_version"])
            fmts = [r["fmt"] for r in await con.fetch(
                "select fmt from renditions where doc_id=$1 and version=$2", doc_id, d["head_version"])]
            dels = await con.fetch("select data from entities where kind='delivery' and data->>'doc_id'=$1 "
                                   "order by updated_at desc limit 1", doc_id)
        wanted = json.loads(d["formats"])
        return {"doc": json.loads(head), "formats": [f for f in wanted if f in fmts] + [f for f in fmts
                                                                                       if f not in wanted],
                "versions": [{"version": r["version"], "at": r["created_at"].isoformat(),
                              "changes": json.loads(r["meta"]).get("changes", [])} for r in versions],
                "delivery": json.loads(dels[0]["data"]) if dels else None,
                "updated_at": d["updated_at"].isoformat()}

    async def head_version(self, doc_id: str) -> Optional[int]:
        async with self.pool.acquire() as con:
            return await con.fetchval("select head_version from documents where id=$1", doc_id)

    async def save_rendition(self, doc_id: str, version: int, fmt: str, filename: str, mime: str, data: bytes) -> None:
        """A file made after the version was committed (a preview of it): kept with its renditions, once."""
        async with self.pool.acquire() as con:
            await con.execute("insert into renditions (doc_id, version, fmt, filename, mime, data, sha256) values "
                              "($1, $2, $3, $4, $5, $6, $7) on conflict do nothing", doc_id, version, fmt, filename,
                              mime, data, hashlib.sha256(data).hexdigest())

    async def rendition(self, doc_id: str, fmt: str, version: Optional[int] = None) -> Optional[dict]:
        async with self.pool.acquire() as con:
            if version is None:
                version = await con.fetchval("select head_version from documents where id=$1", doc_id)
            r = await con.fetchrow("select filename, mime, data, sha256 from renditions where doc_id=$1 and "
                                   "version=$2 and fmt=$3", doc_id, version, fmt)
        return dict(r) if r else None
