"""The admin console's API, /admin/api/*, answered only on the admin hosts (ADMIN_HOSTS); anywhere else it doesn't
exist (404). The session cookie is the admin host's alone (no Domain attribute), httpOnly, Secure, SameSite=Strict;
every request that changes something also carries X-BolDoc-Admin: 1, which no cross-site form can send."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, Response
from loguru import logger
from pydantic import BaseModel

from ..accounts import web as accounts_web
from ..api_errors import ApiError
from ..phones import masked
from ..testcall.runner import CHOICES, LANGS, RunRefused
from . import auth, metrics

router = APIRouter(prefix="/admin/api")
COOKIE = "boldoc_admin"


class Ctx:
    """Set by the control service at startup."""
    pool = None
    bus = None
    hosts: tuple = ()
    secure_cookie = True
    tests = None  # testcall.runner.TestCalls: the admin's test calls
    agent = None  # agent_config.AgentConfig: the agent's settings
    second_factor = "optional"  # the authenticator or SMS code: "optional" or "required" (ADMIN_SECOND_FACTOR)


CTX = Ctx()


class Login(BaseModel):
    username: str
    password: str
    code: str = ""  # none: the username and password alone, unless the second factor is required
    method: str = "totp"  # totp: the authenticator app's code; sms: the code sent to the admin's own mobile


class SmsCode(BaseModel):
    username: str
    password: str


def _host_ok(request: Request) -> None:
    if request.headers.get("host", "").split(":")[0].lower() not in CTX.hosts:
        raise ApiError(404, "not_found", "Not found.")


def _client(request: Request) -> None:
    if request.headers.get("X-BolDoc-Admin") != "1":
        raise ApiError(403, "forbidden", "This request must come from the admin console.")


def _ip(request: Request) -> str:
    return request.headers.get("X-Forwarded-For", request.client.host if request.client else "").split(",")[0].strip()


async def _admin(request: Request, con) -> Dict[str, Any]:
    _host_ok(request)
    who = await auth.session_admin(con, request.cookies.get(COOKIE))
    if who is None:
        raise ApiError(401, "signed_out", "Please sign in again.")
    return who


async def _attempt(ip: str, username: str) -> None:
    """Every try at an admin's password counts, asking for an SMS code included."""
    if CTX.bus is None or not await CTX.bus.allow(f"admin:login:ip:{ip}", 20, 3600, fail_open=False) \
            or not await CTX.bus.allow(f"admin:login:user:{username}", 5, 900, fail_open=False):
        raise ApiError(429, "too_many_attempts", "Too many sign-in attempts. Try again in a while.")


@router.post("/login/sms")
async def login_sms(body: SmsCode, request: Request) -> Dict[str, Any]:
    """Signing in with a code by SMS instead of the authenticator app: the right username and password send a code to
    the admin's own mobile (set on the server: python -m apps.control.admin set-phone). Nothing is sent before the
    password is right, so nobody can make an admin's phone fill with codes."""
    _host_ok(request)
    _client(request)
    ip, username = _ip(request), body.username.strip().lower()[:64]
    await _attempt(ip, username)
    async with CTX.pool.acquire() as con:
        row = await con.fetchrow("select * from admin_users where username=$1 and disabled_at is null", username)
        if not auth.password_ok(row["pw_hash"] if row else None, body.password):
            why = "unknown_user" if row is None else "password"
            await auth.audit(con, username or "?", "login_failed", detail={"why": why, "method": "sms"}, ip=ip)
            logger.warning(f"admin sign-in failed ({why}) from {ip}")
            raise ApiError(401, "bad_login", "Wrong username or password.")
        if not row["phone_e164"]:
            raise ApiError(409, "no_phone", "This admin has no mobile number for codes. Use the authenticator app, or "
                           "add a number on the server: python -m apps.control.admin set-phone.")
        resend = await accounts_web.send_code(request, row["phone_e164"], "admin",
                                              host=request.headers.get("host", "").split(":")[0].lower())
        await auth.audit(con, username, "login_code_sent", detail={"to": masked(row["phone_e164"])}, ip=ip)
    return {"sent_to": masked(row["phone_e164"]), "resend_after_s": resend}


async def _sms_code_ok(row: Optional[Dict[str, Any]], code: str) -> Optional[ApiError]:
    """None when the code is the live one sent to this admin's mobile (it is then used up); else why not."""
    if row is None or not row["phone_e164"]:
        return ApiError(401, "bad_login", "Wrong username, password or code.")
    try:
        await accounts_web.check_code(row["phone_e164"], code, "admin")
    except ApiError as e:  # expired, wrong (with the tries left), or too many tries
        return ApiError(401, e.code, e.message, e.extra) if e.status == 400 else e
    return None


@router.post("/login")
async def login(body: Login, request: Request, response: Response) -> Dict[str, Any]:
    _host_ok(request)
    _client(request)
    ip, username = _ip(request), body.username.strip().lower()[:64]
    method = "sms" if body.method == "sms" else "totp"
    await _attempt(ip, username)
    async with CTX.pool.acquire() as con:
        row = await con.fetchrow("select * from admin_users where username=$1 and disabled_at is null", username)
        good_password = auth.password_ok(row["pw_hash"] if row else None, body.password)
        refused: Optional[ApiError] = None
        if not good_password:
            won = False
        elif not any(ch.isdigit() for ch in body.code):  # no code: the password alone, while that is allowed
            if CTX.second_factor == "required":
                refused = ApiError(401, "code_needed", "Enter the code from your authenticator app, or get one by SMS.")
                won = False
            else:
                method = "password"
                won = await con.fetchval(
                    "update admin_users set last_login_at=now() where id=$1 returning id", row["id"])
        elif method == "sms":
            refused = await _sms_code_ok(dict(row), body.code)
            won = refused is None and await con.fetchval(
                "update admin_users set last_login_at=now() where id=$1 returning id", row["id"])
        else:
            step = auth.totp_step(body.code, row["totp_secret"], row["last_totp_step"])
            # a code works once: two sign-ins racing with the same code, only one wins
            won = step is not None and await con.fetchval(
                "update admin_users set last_totp_step=$2, last_login_at=now() where id=$1 and "
                "(last_totp_step is null or last_totp_step < $2) returning id", row["id"], step)
        if not won:
            why = "unknown_user" if row is None else ("password" if not good_password else "code")
            await auth.audit(con, username or "?", "login_failed", detail={"why": why, "method": method}, ip=ip)
            logger.warning(f"admin sign-in failed ({why}, {method}) from {ip}")
            if refused is not None:  # the password was right: say what was wrong with the SMS code
                raise refused
            raise ApiError(401, "bad_login", "Wrong username, password or code.")
        token = await auth.new_session(con, row["id"], ip, request.headers.get("user-agent") or "")
        await auth.audit(con, username, "login", detail={"method": method}, ip=ip)
    response.set_cookie(COOKIE, token, max_age=auth.SESSION_HOURS * 3600, httponly=True, secure=CTX.secure_cookie,
                        samesite="strict", path="/")
    return {"username": username}


@router.post("/logout")
async def logout(request: Request, response: Response) -> Dict[str, Any]:
    _host_ok(request)
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await auth.session_admin(con, request.cookies.get(COOKIE))
        await auth.end_session(con, request.cookies.get(COOKIE))
        if who:
            await auth.audit(con, who["username"], "logout", ip=_ip(request))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
    return {"username": who["username"]}


@router.get("/overview")
async def overview(request: Request, range: str = "7d", kind: str = "real") -> Dict[str, Any]:
    if range not in metrics.RANGES or kind not in ("real", "test", "all"):
        raise ApiError(422, "bad_request", "range is today, 7d or 30d; kind is real, test or all.")
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
        return await metrics.overview(con, range, kind)


def _day(value: Optional[str], default: datetime) -> datetime:
    if not value:
        return default
    try:
        return datetime.combine(date.fromisoformat(value), time(0), tzinfo=metrics.IST).astimezone(timezone.utc)
    except ValueError as e:
        raise ApiError(422, "bad_request", "Dates are YYYY-MM-DD.") from e


@router.get("/calls")
async def calls(request: Request, start: Optional[str] = None, end: Optional[str] = None, kind: str = "real",
                channel: str = "", q: str = "", min_v2v: Optional[float] = None, has_doc: bool = False,
                flag: str = "") -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = _day(start, now - timedelta(days=7))
    until = _day(end, now) + (timedelta(days=1) if end else timedelta(0))
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
        rows = await metrics.call_list(con, since, until, kind=kind, channel=channel, q=q, min_v2v=min_v2v,
                                       has_doc=has_doc, flag=flag)
    return {"calls": rows, "flags": [label for key, label in metrics.FLAGS.items() if key not in metrics.CAUGHT]}


async def _detail(con, who: Dict[str, Any], call_id: str, request: Request) -> Dict[str, Any]:
    detail = await metrics.call_detail(con, call_id)
    if detail is None:
        raise ApiError(404, "not_found", "No such call.")
    report = detail.get("report") or {}
    if report.get("content"):  # the words of a call: only test calls and the owner's debug phones keep them
        await auth.audit(con, who["username"], "view_transcript", call_id, ip=_ip(request))
    return detail


@router.get("/calls/latest")
async def latest_call(request: Request, kind: str = "real") -> Dict[str, Any]:
    """The newest answered call, in full: the console's "Last call"."""
    if kind not in ("real", "test", "all"):
        raise ApiError(422, "bad_request", "kind is real, test or all.")
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
        call_id = await metrics.latest_call_id(con, kind)
        if call_id is None:
            raise ApiError(404, "no_calls", "No answered calls yet.")
        return await _detail(con, who, call_id, request)


@router.get("/calls/{call_id}")
async def call(call_id: str, request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
        return await _detail(con, who, call_id, request)


@router.post("/calls/{call_id}/reveal")
async def reveal_caller(call_id: str, request: Request) -> Dict[str, Any]:
    """The caller's full number, for one call (audited, like a user's numbers)."""
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
        number = await con.fetchval("select caller from call_records where call_uuid=$1", call_id)
        if number is None:
            raise ApiError(404, "not_found", "No number for this call.")
        await auth.audit(con, who["username"], "reveal_number", f"call:{call_id}", ip=_ip(request))
    return {"number": number}


@router.get("/users")
async def users(request: Request, q: str = "") -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
        return {"users": await metrics.users(con, q)}


@router.post("/users/{user_id}/reveal")
async def reveal(user_id: int, request: Request) -> Dict[str, Any]:
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
        numbers = await metrics.user_numbers(con, user_id)
        if numbers is None:
            raise ApiError(404, "not_found", "No such user.")
        await auth.audit(con, who["username"], "reveal_number", f"user:{user_id}", ip=_ip(request))
    return {"numbers": numbers}


async def audit_entry(actor: str, action: str, target: Optional[str], detail: Dict[str, Any]) -> None:
    """For the test-call runner: who ran a test call, which, and how it went."""
    async with CTX.pool.acquire() as con:
        await auth.audit(con, actor, action, target, detail)


class TestCallStart(BaseModel):
    scenario: str
    lang: Optional[str] = None


def _tests():
    if CTX.tests is None:
        raise ApiError(503, "test_calls_unavailable", "Test calls aren't set up on this server.")
    return CTX.tests


@router.get("/test-calls")
async def test_calls(request: Request) -> Dict[str, Any]:
    """Recent test calls, the one running (if any), and what a new one can be."""
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
    tests = _tests()
    return {"runs": await tests.recent(), "running": await tests.running(),
            "free_lines": max(0, await tests.free_lines()),
            "choices": [{"id": k, **v} for k, v in CHOICES.items()],
            "langs": [{"id": k, "label": v} for k, v in LANGS.items()]}


@router.post("/test-calls", status_code=202)
async def start_test_call(body: TestCallStart, request: Request) -> Dict[str, Any]:
    """A scripted call against the live talker, then its report: does everything work?"""
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
    try:
        run = await _tests().start(who["username"], body.scenario, body.lang)
    except RunRefused as e:
        raise ApiError(e.status, e.code, e.message, {"running": e.running} if e.running else None) from e
    return {"run": run}


@router.get("/test-calls/{run_id}")
async def test_call(run_id: str, request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
    run = await _tests().get(run_id)
    if run is None:
        raise ApiError(404, "not_found", "No such test call.")
    return {"run": run}


# The agent's settings: what may change without a deploy, each range-checked, every publish a version --------
GROUPS = [{"id": "call", "label": "Call and voice", "when": "From the next call"},
          {"id": "timing", "label": "Timing and waiting", "when": "From the next call"},
          {"id": "prices", "label": "Prices and plan limits", "when": "From the next charge or call admitted"}]


class AgentPublish(BaseModel):
    base_version: int
    changes: Dict[str, Any]
    note: str


class AgentRestore(BaseModel):
    version: int
    note: str


def _agent():
    if CTX.agent is None:
        raise ApiError(503, "agent_settings_unavailable", "Agent settings aren't set up on this server.")
    return CTX.agent


async def _agent_view() -> Dict[str, Any]:
    from ..agent_config import SCHEMA
    agent = _agent()
    return {"groups": GROUPS, "schema": [st.as_dict() for st in SCHEMA], "live": await agent.live(),
            "history": await agent.history()}


@router.get("/agent")
async def agent_settings(request: Request) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
    return await _agent_view()


def _audit_from(request: Request):
    ip = _ip(request)

    async def write(con, who, action, target, detail):
        await auth.audit(con, who, action, target, detail, ip)
    return write


@router.post("/agent/publish")
async def publish_agent_settings(body: AgentPublish, request: Request) -> Dict[str, Any]:
    """A new version of the settings: the live one plus these changes, with a note; from the next call or charge."""
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
    try:
        done = await _agent().publish(body.base_version, body.changes, body.note, who["username"],
                                      audit=_audit_from(request))
    except LookupError as e:
        raise ApiError(409, "published_meanwhile", "Someone published a newer version meanwhile. Reload to see it.",
                       {"live_version": e.args[0] if e.args else None}) from e
    except ValueError as e:
        raise ApiError(400, "invalid_settings", "Some values aren't allowed.",
                       {"errors": e.args[0] if e.args else {}}) from e
    logger.info(f"agent settings v{done['version']} published by {who['username']}: {sorted(done['changes'])}")
    return {"published": done, **await _agent_view()}


@router.post("/agent/restore")
async def restore_agent_settings(body: AgentRestore, request: Request) -> Dict[str, Any]:
    """An earlier version's settings, published again as a new version (with a note)."""
    _client(request)
    async with CTX.pool.acquire() as con:
        who = await _admin(request, con)
    try:
        done = await _agent().restore(body.version, body.note, who["username"], audit=_audit_from(request))
    except KeyError as e:
        raise ApiError(404, "not_found", "No such version.") from e
    except LookupError as e:
        raise ApiError(409, "published_meanwhile", "Someone published a newer version meanwhile. Reload to see it.",
                       {"live_version": e.args[0] if e.args else None}) from e
    except ValueError as e:
        raise ApiError(400, "invalid_settings", "That version can't be restored as it is.",
                       {"errors": e.args[0] if e.args else {}}) from e
    logger.info(f"agent settings v{body.version} restored as v{done['version']} by {who['username']}")
    return {"published": done, **await _agent_view()}


@router.get("/audit")
async def audit_log(request: Request, limit: int = 100) -> Dict[str, Any]:
    async with CTX.pool.acquire() as con:
        await _admin(request, con)
        rows = await con.fetch("select actor, action, target, detail, ip, at from audit_log order by id desc limit $1",
                               max(1, min(limit, 500)))
    return {"entries": [{"actor": r["actor"], "action": r["action"], "target": r["target"],
                         "detail": metrics._loads(r["detail"]), "ip": r["ip"], "at": r["at"].isoformat()}
                        for r in rows]}
