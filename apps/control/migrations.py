"""Versioned schema changes on top of the baseline (`store_pg.SCHEMA`, created idempotently at startup).

Each migration runs once, in order, inside one transaction holding an advisory lock, so two control processes
starting together never race. Never edit a migration that has shipped: add a new one.
"""
from __future__ import annotations

from typing import List, Tuple

LOCK_ID = 7466001  # pg_advisory_xact_lock key for schema changes

MIGRATIONS: List[Tuple[int, str, str]] = [
    (2, "call records, per-turn metrics, call reports, sms receipts, web events", """
create table call_records (
  call_uuid text primary key,
  channel text not null,                    -- pstn | sip | test
  caller text,                              -- +91… mobile, +<digits> other numbers; null for browser/withheld
  caller_kind text not null,                -- mobile | other | withheld | browser | test
  called text,
  decision text not null,                   -- admit | refuse | unrecorded (hangup seen without an admission)
  reason text,
  max_call_s int not null default 0,
  test boolean not null default false,
  first_call boolean not null default false,
  degraded boolean not null default false,  -- admitted by the talker while control was unreachable
  admitted_at timestamptz not null default now(),
  streams int not null default 0,
  first_stream_at timestamptz,
  ended_at timestamptz,
  duration_s int, bill_s int, cost_usd numeric(12,5), bill_rate_usd numeric(12,5),
  hangup_cause text, hangup_source text,
  meta jsonb not null default '{}');
create index call_records_admitted on call_records (admitted_at desc);
create index call_records_caller on call_records (caller, admitted_at desc);
create table call_reports (
  session_id text primary key, report jsonb not null, created_at timestamptz not null default now());
create table call_turns (
  session_id text not null, turn int not null, at timestamptz not null,
  reply text not null, verdict text, words int,
  v2v_ms int, eot_ms int, llm_wait_ms int, ttft_ms int, tts_ms int, hold_ms int,
  flags text[] not null default '{}',
  primary key (session_id, turn));
create index call_turns_at on call_turns (at desc);
create table sms_receipts (
  id bigserial primary key, message_uuid text not null, status text, error_code text, units int,
  amount_usd numeric(12,5), rate_usd numeric(12,5), mcc text, mnc text, at timestamptz not null default now());
create index sms_receipts_message on sms_receipts (message_uuid);
create table web_events (day date not null, kind text not null, n bigint not null default 0,
  primary key (day, kind));
"""),
    (3, "accounts: users, web sessions, credit ledger, profiles, consents", """
create table users (
  id bigserial primary key,
  phone_e164 text not null unique,
  email text,
  status text not null default 'trial',      -- trial (called, not verified) | active | suspended | deleted
  source text not null,                      -- call | web
  created_at timestamptz not null default now(),
  verified_at timestamptz,
  meta jsonb not null default '{}');
create table phone_tombstones (              -- deleted accounts: no second sign-up bonus for the same number
  hash text primary key, deleted_at timestamptz not null default now());
create table auth_sessions (
  id_hash text primary key,                  -- sha256 of the cookie token (the token itself is never stored)
  user_id bigint not null references users(id) on delete cascade,
  created_at timestamptz not null default now(), expires_at timestamptz not null,
  last_seen_at timestamptz, ip text, ua text, revoked_at timestamptz);
create index auth_sessions_user on auth_sessions (user_id);
create table credit_ledger (                 -- append-only; balances are derived, never edited
  id bigserial primary key,
  user_id bigint not null references users(id),
  kind text not null,                        -- signup_bonus | call_minutes | doc_create | doc_edit | admin_adjust
  amount int not null,                       -- + credit, - debit (1 credit = 1 rupee)
  balance_after int not null,
  idem_key text not null unique,             -- one debit per task / call, whatever retries happen
  ref text, actor text not null default 'system', reason text,
  created_at timestamptz not null default now());
create index credit_ledger_user on credit_ledger (user_id, id desc);
create table profiles (                      -- "My details": the thinker uses them as known facts
  user_id bigint primary key references users(id) on delete cascade,
  name text, designation text, organisation text, address text,
  default_lang text, ui_lang text, retention_days int not null default 30,
  updated_at timestamptz not null default now());
create table consents (
  id bigserial primary key, subject text not null, notice_version text not null, channel text not null,
  at timestamptz not null default now());
alter table call_records add column user_id bigint references users(id);
alter table call_records add column counted boolean not null default false;
create index call_records_user on call_records (user_id, admitted_at desc);
"""),
    (4, "documents belong to accounts (a later call can change an earlier document)", """
alter table documents add column owner_user_id bigint;
create index documents_owner_user on documents (owner_user_id, updated_at desc);
update documents d set owner_user_id = c.user_id from call_records c
  where c.call_uuid = d.session_id and c.user_id is not null and d.owner_user_id is null;
"""),
    (5, "more numbers on one account (Settings: Your numbers)", """
create table user_phones (                  -- each proven by a code sent to it; the sign-in number stays in users
  phone_e164 text primary key,              -- a number belongs to one account at most
  user_id bigint not null references users(id) on delete cascade,
  added_at timestamptz not null default now());
create index user_phones_user on user_phones (user_id);
"""),
    (6, "a document link opens on one device (links.py)", """
alter table links add column device_hash text;              -- the browser that opened it first (a hash)
alter table links add column claimed_at timestamptz;
"""),
    (7, "admin console: admins, their sessions, the audit log (apps/control/admin)", """
create table admin_users (
  id bigserial primary key,
  username text not null unique,
  pw_hash text not null,                    -- argon2id
  totp_secret text not null,                -- base32, shown once when the admin is created
  last_totp_step bigint,                    -- a code works once: steps at or before this are refused
  created_at timestamptz not null default now(),
  last_login_at timestamptz,
  disabled_at timestamptz);
create table admin_sessions (
  id_hash text primary key,                 -- sha256 of the cookie token (the token itself is never stored)
  admin_id bigint not null references admin_users(id) on delete cascade,
  created_at timestamptz not null default now(), expires_at timestamptz not null,
  ip text, ua text, revoked_at timestamptz);
create table audit_log (                    -- every sign-in, every look at personal data, every change
  id bigserial primary key,
  actor text not null,                      -- the admin's username, or "cli"
  action text not null,
  target text,
  detail jsonb not null default '{}',
  ip text,
  at timestamptz not null default now());
create index audit_log_at on audit_log (at desc);
"""),
    (8, "long documents: the draft Hermes writes, section by section (apps/control/bigdoc.py)", """
create table doc_drafts (
  task_id text primary key,
  doc_id text not null,
  sections jsonb not null default '{}',     -- section id -> DocEngine blocks, as the writer last wrote them
  finished jsonb,                           -- {at, note} once the writer called finish
  updated_at timestamptz not null default now());
"""),
    (9, "a callback's consent keeps the caller's own words", """
alter table consents add column words text;
"""),
    (10, "files a caller adds for BolDoc: their text, never the file", """
create table if not exists materials (
  id text primary key,
  user_id bigint not null,
  doc_id text,                              -- the long document it is for; null: the owner's next one
  task_id text,                             -- the writer's task it went to
  name text not null,
  kind text not null,                       -- pdf | docx | xlsx | pptx | csv | txt | image
  size_bytes integer not null,
  sha256 text not null,
  status text not null default 'reading',   -- reading | ready | failed
  method text,                              -- text | vision (Sarvam's document reader)
  pages integer,
  text text,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now());
create index if not exists materials_user on materials (user_id, created_at desc);
create index if not exists materials_doc on materials (doc_id);
"""),
    (11, "the language a caller's last call ended in: their next call starts in it, however they call", """
alter table users add column if not exists call_lang text;  -- en-IN | hi-IN (Hindi/Hinglish) | ta-IN | ...
"""),
    (12, "agent settings published from the admin console, one row a version", """
create table if not exists agent_config (
  version serial primary key,
  settings jsonb not null,                  -- every editable setting, as published (a version stands on its own)
  changes jsonb not null default '{}',      -- what this version changed: {key: [before, after]}
  note text not null,
  published_by text not null,
  published_at timestamptz not null default now(),
  restored_from integer);                   -- a restore publishes an earlier version's settings again
"""),
    (13, "an admin's own mobile for sign-in codes by SMS; an admin made without a password can't sign in yet", """
alter table admin_users add column if not exists phone_e164 text;  -- sign-in codes by SMS go to this number only
alter table admin_users alter column pw_hash drop not null;         -- null: no password set yet, no sign-in
"""),
]


async def migrate(con) -> List[int]:
    """Apply pending migrations; returns the versions applied now."""
    await con.execute("create table if not exists schema_migrations (version int primary key, name text not null, "
                      "applied_at timestamptz not null default now())")
    applied: List[int] = []
    async with con.transaction():
        await con.execute("select pg_advisory_xact_lock($1)", LOCK_ID)
        done = {r["version"] for r in await con.fetch("select version from schema_migrations")}
        for version, name, sql in MIGRATIONS:
            if version in done:
                continue
            await con.execute(sql)
            await con.execute("insert into schema_migrations (version, name) values ($1, $2)", version, name)
            applied.append(version)
    return applied
