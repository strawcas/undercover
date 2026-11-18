"""Function URL requests exercise the exported handler and its database wiring."""

import asyncio
import base64
import json
from typing import cast

import psycopg
import pytest
from mangum.types import LambdaContext
from sqlalchemy import event
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg

from backend import main
from backend.dashboard import install_dashboard
from backend.database import LAMBDA_MARKER, SCHEMA, engine, load
from backend.tests.auth_support import AGENT_HEADERS, authorization

PAGE = b"<!doctype html><div id='root'></div>"
SCRIPT = b"console.log('dashboard');"


def function_event(path, headers=None, method="GET"):
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "local-function.invalid", **(headers or {})},
        "requestContext": {
            "domainName": "local-function.invalid",
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "127.0.0.1",
            },
        },
    }


def decoded(response):
    body = response["body"]
    return base64.b64decode(body) if response["isBase64Encoded"] else body.encode()


@pytest.fixture
def invoke(monkeypatch):
    """Send a Function URL event to the exported handler on a dedicated loop."""

    def startup(*args):
        pytest.fail("Lambda ran the app startup hook")

    monkeypatch.setattr(main, "seed", startup)
    monkeypatch.setattr(main, "is_seeded", startup)
    assert main.handler.lifespan == "off"

    def call(path, headers=None, method="GET"):
        return main.handler(
            function_event(path, headers, method), cast(LambdaContext, object())
        )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield call
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@pytest.fixture
def installed_dashboard(tmp_path, monkeypatch):
    """Use a stand-in read-only build even if the real build exists."""
    assets = tmp_path / "assets"
    assets.mkdir()
    index, script = tmp_path / "index.html", assets / "main.js"
    index.write_bytes(PAGE)
    script.write_bytes(SCRIPT)
    for path in (index, script):
        path.chmod(0o444)
    monkeypatch.setattr(
        main.app, "exception_handlers", dict(main.app.exception_handlers)
    )
    monkeypatch.setattr(main.app, "middleware_stack", None)
    install_dashboard(main.app, tmp_path)
    try:
        yield
    finally:
        for path in (index, script):
            path.chmod(0o644)


@pytest.mark.usefixtures("installed_dashboard")
def test_function_url_serves_the_dashboard_and_preserves_api_routing(invoke):
    for path in ("/", "/claims/CLM-1001"):
        response = invoke(path)
        assert response["statusCode"] == 200
        assert response["headers"]["content-type"].startswith("text/html")
        assert decoded(response) == PAGE

    asset = invoke("/assets/main.js")
    assert asset["statusCode"] == 200
    assert "javascript" in asset["headers"]["content-type"]
    assert decoded(asset) == SCRIPT

    health = invoke("/api/health")
    assert health["statusCode"] == 200
    assert json.loads(health["body"]) == {"status": "ok"}
    missing = invoke("/api/unknown")
    assert missing["statusCode"] == 404
    assert json.loads(missing["body"]) == {"detail": "Not Found"}
    assert invoke("/api/health", method="POST")["statusCode"] == 405
    assert invoke("/api/health/")["statusCode"] == 307


@pytest.mark.usefixtures("installed_dashboard")
def test_function_url_reads_the_seeded_database_with_startup_skipped(
    invoke, monkeypatch
):
    monkeypatch.setenv(LAMBDA_MARKER, "undercover-local-test")
    opened, closed = [], []
    db = engine()
    event.listen(db, "connect", lambda connection, _: opened.append(connection))
    event.listen(db, "close", lambda connection, _: closed.append(connection))
    for _ in range(2):
        claims = invoke("/api/claims", authorization())
        assert claims["statusCode"] == 200
        assert len(json.loads(claims["body"])) == 26
    assert len(opened) == len(closed) == 2
    assert opened[0] is not opened[1]
    assert closed == opened


class PolicyCursor:
    description = None
    rowcount = -1

    def __init__(self, statements, policy):
        self.statements = statements
        self.rows = iter([(policy,)])

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        if statement.lstrip().startswith("SELECT policies.record"):
            self.description = [("record", None, None, None, None, None, None)]

    def fetchone(self):
        return next(self.rows, None)

    def close(self):
        pass


class PolicyConnection:
    def __init__(self, options, policy):
        self.options = options
        self.policy = policy
        self.statements = []
        self.closed = False

    def cursor(self):
        return PolicyCursor(self.statements, self.policy)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_two_function_requests_use_fresh_postgres_driver_connections(
    invoke, monkeypatch
):
    policy = next(p for p in load("policies.json") if p["policy_id"] == "POL-2001")
    connections = []

    def connect(**options):
        connection = PolicyConnection(options, policy)
        connections.append(connection)
        return connection

    monkeypatch.setenv(LAMBDA_MARKER, "undercover-driver-test")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres@pooler.invalid:6543/postgres"
    )
    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(PGDialect_psycopg, "initialize", lambda *args: None)
    monkeypatch.setattr(PGDialect_psycopg, "on_connect", lambda *args: None)
    for _ in range(2):
        response = invoke("/api/policies/POL-2001", AGENT_HEADERS)
        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == policy
    assert len(connections) == 2
    assert connections[0] is not connections[1]
    for connection in connections:
        assert connection.closed
        assert "prepare_threshold" in connection.options
        assert connection.options["prepare_threshold"] is None
        schema, application_query = connection.statements
        assert schema == f"SET LOCAL search_path TO {SCHEMA}"
        assert application_query.lstrip().startswith("SELECT policies.record")
