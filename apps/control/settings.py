"""Control-plane configuration (environment variables; secrets from files mounted by the deployer)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _secret(env: str, file_env: str) -> str:
    """Read a secret from `$env`, or from the file named by `$file_env` (first line), never from the repo."""
    if os.environ.get(env):
        return os.environ[env].strip()
    path = os.environ.get(file_env)
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip().splitlines()[0].strip()
    return ""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class ControlSettings:
    public_host: str = field(default_factory=lambda: _env("PUBLIC_HOST", "localhost"))
    # every host Plivo may call us on (the domain and its sslip.io alias), for webhook signature URLs
    public_hosts: tuple = field(default_factory=lambda: tuple(h.strip() for h in (
        _env("PUBLIC_HOSTS") or _env("PUBLIC_HOST", "localhost")).split(",") if h.strip()))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL"))
    internal_key: str = field(default_factory=lambda: _env("INTERNAL_KEY"))
    sarvam_api_key: str = field(default_factory=lambda: _secret("SARVAM_API_KEY", "SARVAM_API_KEY_FILE"))
    plivo_auth_id: str = field(default_factory=lambda: _secret("PLIVO_AUTH_ID", "PLIVO_AUTH_ID_FILE"))
    plivo_auth_token: str = field(default_factory=lambda: _secret("PLIVO_AUTH_TOKEN", "PLIVO_AUTH_TOKEN_FILE"))
    sms_src: str = field(default_factory=lambda: _env("SMS_SRC"))           # Plivo number used as SMS sender
    sms_enabled: bool = field(default_factory=lambda: _env("SMS_ENABLED", "1") == "1")
    dial_in_e164: str = field(default_factory=lambda: _env("DIAL_IN_E164", "+918035449833"))
    # the number signed-in users call, and Call me rings from; the public pages keep DIAL_IN_E164
    member_dial_in_e164: str = field(default_factory=lambda: _env("MEMBER_DIAL_IN_E164", ""))
    link_ttl_days: float = field(default_factory=lambda: float(_env("LINK_TTL_DAYS", "7")))
    thinker_workers: int = field(default_factory=lambda: int(_env("THINKER_WORKERS", "2")))
    signature_mode: str = field(default_factory=lambda: _env("VALIDATE_PLIVO_SIGNATURES", "enforce"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL"))
    temporal_address: str = field(default_factory=lambda: _env("TEMPORAL_ADDRESS"))
    temporal_namespace: str = field(default_factory=lambda: _env("TEMPORAL_NAMESPACE", "default"))
    sms_daily_limit: int = field(default_factory=lambda: int(_env("SMS_DAILY_LIMIT", "10")))
    max_docs_per_call: int = field(default_factory=lambda: int(_env("MAX_DOCS_PER_CALL", "5")))
    max_edits_per_call: int = field(default_factory=lambda: int(_env("MAX_EDITS_PER_CALL", "40")))
    # Calls at once. sarvam-105b allows ~40 requests/min on our tier, shared by every call's turns, the turn
    # classifier and drafting: about 3 calls. Above the cap callers hear "busy" instead of a slow, broken call.
    max_concurrent_calls: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_CALLS", "3")))
    # Calls the primary talker LLM (Sarvam) takes before new calls go to OVERFLOW_TALKER_LLM (if one is set).
    primary_llm_calls: int = field(default_factory=lambda: int(_env("PRIMARY_LLM_CALLS", "3")))
    overflow_talker: str = field(default_factory=lambda: _env("OVERFLOW_TALKER_LLM"))
    max_call_s: int = field(default_factory=lambda: int(_env("MAX_CALL_S", "1800")))
    otp_secret: str = field(default_factory=lambda: _secret("OTP_SECRET", "OTP_SECRET_FILE"))
    # Long documents: Hermes Agent's API server writes them through our tools (apps/control/bigdoc.py).
    # Off unless all three are set. HERMES_MCP_KEY is what Hermes sends to our tools and to the metering proxy.
    hermes_url: str = field(default_factory=lambda: _env("HERMES_URL"))
    hermes_key: str = field(default_factory=lambda: _secret("HERMES_API_KEY", "HERMES_API_KEY_FILE"))
    hermes_mcp_key: str = field(default_factory=lambda: _secret("HERMES_MCP_KEY", "HERMES_MCP_KEY_FILE"))
    big_doc_work_s: int = field(default_factory=lambda: int(_env("BIG_DOC_WORK_S", "1200")))      # Hermes' own work
    big_doc_answer_s: int = field(default_factory=lambda: int(_env("BIG_DOC_ANSWER_S", "1500")))  # caller's answers
    big_doc_token_cap: int = field(default_factory=lambda: int(_env("BIG_DOC_TOKEN_CAP", "1500000")))
    big_doc_research_token_cap: int = field(default_factory=lambda: int(_env("BIG_DOC_RESEARCH_TOKEN_CAP", "2250000")))
    serper_key: str = field(default_factory=lambda: _secret("SERPER_API_KEY", "SERPER_API_KEY_FILE"))  # web research
    # The document preview: Word, Excel and PowerPoint files as PDFs, laid out by LibreOffice (infra/convert)
    convert_url: str = field(default_factory=lambda: _env("CONVERT_URL", "http://convert:3000"))
    # Test numbers sign in without an SMS (their code is read on the server: python -m apps.control.accounts.cli)
    # and are never sent any SMS, document links included
    otp_test_numbers: tuple = field(default_factory=lambda: tuple(n.strip() for n in _env("OTP_TEST_NUMBERS").split(",")
                                                                   if n.strip()))
    cookie_secure: bool = field(default_factory=lambda: _env("COOKIE_SECURE", "1") == "1")
    # The admin console's own origin: its API answers only on these hosts (comma-separated)
    admin_hosts: tuple = field(default_factory=lambda: tuple(h.strip().lower() for h in _env(
        "ADMIN_HOSTS", "admin.boldoc.in").split(",") if h.strip()))
    # The admin sign-in's second factor (authenticator or SMS code): "optional" signs in with the username and password
    # alone, a code given is still checked; "required" asks for the code every time
    admin_second_factor: str = field(default_factory=lambda: _env("ADMIN_SECOND_FACTOR", "optional").strip().lower())
    # Test calls from the admin console: the talker's stream, reached over the compose network, and its key
    stream_key: str = field(default_factory=lambda: _secret("STREAM_KEY", "STREAM_KEY_FILE"))
    talker_stream_url: str = field(default_factory=lambda: _env("TALKER_STREAM_URL", "ws://talker:8090/plivo/stream"))
    # "Talk in browser": the website's shared Plivo endpoint (username) and its application
    plivo_web_endpoint: str = field(default_factory=lambda: _env("PLIVO_WEB_ENDPOINT"))
    plivo_app_id: str = field(default_factory=lambda: _env("PLIVO_APP_ID"))


SETTINGS = ControlSettings()
