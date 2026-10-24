"""Control-plane test fixtures: a real Postgres (pgserver locally, TEST_DATABASE_URL in CI) and the app with
the LLM and Plivo faked (rendering is real)."""
import asyncio
import dataclasses
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from tests.control.helpers import FakeSms, fake_draft, fake_edit


@pytest.fixture(scope="session")
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(tempfile.mkdtemp(prefix="boldoc-pg"), cleanup_mode="stop")
    yield srv.get_uri()
    srv.cleanup()


@pytest.fixture
def app_factory(pg_url, monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")

    async def reset():
        con = await asyncpg.connect(pg_url)
        await con.execute("drop schema public cascade; create schema public;")
        await con.close()
    asyncio.run(reset())
    FakeSms.sent = []
    from apps.control import service, settings, worker
    cfg = dataclasses.replace(settings.ControlSettings(), database_url=pg_url, internal_key="test-key",
                              sms_src="BOLDOC", plivo_auth_id="MAXXX", plivo_auth_token="tok",
                              public_host="boldoc.test", signature_mode="off", thinker_workers=2,
                              cookie_secure=False, otp_test_numbers=("+919999900001",))
    monkeypatch.setattr(service, "SETTINGS", cfg)
    monkeypatch.setattr(service, "PlivoSms", FakeSms)
    monkeypatch.setattr(worker, "draft", fake_draft)
    monkeypatch.setattr(worker, "edit", fake_edit)
    return lambda: TestClient(service.app)


def configure(monkeypatch, **settings):
    """The website's endpoint as the server would be configured (control reads it at startup)."""
    from apps.control import service
    monkeypatch.setattr(service, "SETTINGS", dataclasses.replace(
        service.SETTINGS, plivo_web_endpoint="boldocweb262294678398013331755703", **settings))


@pytest.fixture
def plivo(app_factory, monkeypatch):
    """Plivo's JWT API, faked: records each token request."""
    from apps.control.accounts import browser
    asked = []

    async def token(ttl_s):
        asked.append(ttl_s)
        return f"jwt-{len(asked)}"
    monkeypatch.setattr(browser, "plivo_token", token)
    configure(monkeypatch)
    return asked
