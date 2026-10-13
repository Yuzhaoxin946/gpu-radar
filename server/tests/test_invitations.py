from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import StringIO
from threading import Barrier, Lock
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select

from app import main, manage
from app.config import Settings
from app.database import Base, database
from app.models import Account, AccountSession, Invitation, Server
from app.security import hash_password, invitation_hash

CODE = "isolated-test-invite"
PASSWORD = "invited-password-123"
NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def register(client, username="new-person", code=CODE):
    payload = {"username": username, "password": PASSWORD}
    if code is not None:
        payload["invite_code"] = code
    return client.post("/api/v1/auth/register", json=payload)


def invitation_state(app):
    with app.state.session_factory() as db:
        invitation = db.get(Invitation, invitation_hash(CODE))
        return invitation.used_count, invitation.max_uses


@pytest.fixture
def setup(tmp_path):
    app = main.create_app(Settings(database_url=f"sqlite:///{tmp_path / 'invitations.db'}", login_attempts=100))
    with TestClient(app) as client:
        with app.state.session_factory() as db:
            manage.ensure_invitation(db, invitation_hash(CODE), 5, NOW)
        yield app, client


def test_registration_requires_private_invitation_before_password_hashing(setup, monkeypatch):
    app, client = setup

    def no_password_work(_):
        pytest.fail("invalid invitations must be rejected before password hashing")

    monkeypatch.setattr(main, "hash_password", no_password_work)
    for code in (None, "", "   ", "wrong-test-invite", CODE.upper()):
        response = register(client, code=code)
        assert response.status_code == 403
        assert response.json()["detail"] == "Invitation code is invalid or fully used"
    assert invitation_state(app) == (0, 5)
    with app.state.session_factory() as db:
        assert db.scalar(select(Account)) is None
        assert db.scalar(select(AccountSession)) is None


def test_successes_count_once_and_exhaustion_survives_restart(setup, monkeypatch):
    app, client = setup
    for index in range(5):
        response = register(client, f"invited-{index}", "  " + CODE + "  ")
        assert response.status_code == 201, response.text
        assert CODE not in response.text and "invite_code" not in response.json()
    assert invitation_state(app) == (5, 5)

    def no_password_work(_):
        pytest.fail("an exhausted invitation must not hash a password")

    monkeypatch.setattr(main, "hash_password", no_password_work)
    restarted = main.create_app(app.state.settings)
    with TestClient(restarted) as other:
        assert register(other, "sixth-person").status_code == 403
        assert invitation_state(restarted) == (5, 5)
        assert other.post("/api/v1/auth/login", json={"username": "invited-0", "password": PASSWORD}).status_code == 200


def test_duplicate_username_and_invalid_payload_do_not_consume_invitation(setup, monkeypatch):
    app, client = setup
    assert register(client).status_code == 201

    def no_password_work(_):
        pytest.fail("a duplicate username must not hash another password")

    monkeypatch.setattr(main, "hash_password", no_password_work)
    assert register(client, "NEW-PERSON").status_code == 409
    assert client.post("/api/v1/auth/register", json={"username": "invalid-person", "password": "short", "invite_code": CODE}).status_code == 422
    assert invitation_state(app) == (1, 5)


def test_session_failure_rolls_back_account_and_invitation(setup):
    app, client = setup

    def fail_session(*_):
        raise RuntimeError("simulated session insert failure")

    event.listen(AccountSession, "before_insert", fail_session)
    try:
        with pytest.raises(RuntimeError, match="simulated session insert failure"):
            register(client)
    finally:
        event.remove(AccountSession, "before_insert", fail_session)
    assert invitation_state(app) == (0, 5)
    with app.state.session_factory() as db:
        assert db.scalar(select(Account)) is None
        assert db.scalar(select(AccountSession)) is None
    assert register(client).status_code == 201
    assert invitation_state(app) == (1, 5)


def test_concurrent_requests_across_app_instances_share_exactly_five_uses(setup, monkeypatch):
    app, client = setup
    other_app = main.create_app(app.state.settings)
    start = Barrier(8)
    hash_calls = 0
    lock = Lock()

    def lightweight_hash(_):
        nonlocal hash_calls
        with lock:
            hash_calls += 1
        time.sleep(0.02)
        return "test-only-hash"

    monkeypatch.setattr(main, "hash_password", lightweight_hash)
    with TestClient(other_app) as other:
        def concurrent_request(index):
            start.wait(timeout=10)
            return register(client if index % 2 else other, f"concurrent-{index}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(concurrent_request, range(8)))
    assert sorted(result.status_code for result in results) == [201] * 5 + [403] * 3
    assert hash_calls == 5
    assert invitation_state(app) == (5, 5)
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Account)).all()) == 5
        assert len(db.scalars(select(AccountSession)).all()) == 5


def test_concurrent_duplicate_does_not_use_extra_invitation_slots(setup, monkeypatch):
    app, client = setup
    start = Barrier(6)
    monkeypatch.setattr(main, "hash_password", lambda _: "test-only-hash")

    def same_username(_):
        start.wait(timeout=10)
        return register(client)

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(same_username, range(6)))
    assert sorted(result.status_code for result in results) == [201] + [409] * 5
    assert invitation_state(app) == (1, 5)


def test_create_invite_cli_is_private_idempotent_and_does_not_reset_quota(setup, monkeypatch, capsys):
    app, _ = setup
    monkeypatch.setattr(Settings, "from_env", lambda: app.state.settings)
    with app.state.session_factory() as db:
        db.get(Invitation, invitation_hash(CODE)).used_count = 2
        db.commit()
    monkeypatch.setattr("sys.argv", ["app.manage", "create-invite", "--max-uses", "5", "--code-stdin"])
    monkeypatch.setattr("sys.stdin", StringIO(CODE))
    assert manage.main() == 0
    output = capsys.readouterr().out
    assert "Existing invitation: 2/5" in output and "3 remaining" in output
    assert CODE not in output and invitation_hash(CODE) not in output
    assert invitation_state(app) == (2, 5)
    monkeypatch.setattr("sys.argv", ["app.manage", "create-invite", "--max-uses", "6"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda _: CODE)
    assert manage.main() == 1
    assert "different use limit" in capsys.readouterr().err
    assert invitation_state(app) == (2, 5)


def test_create_invite_cli_bootstraps_fresh_database_without_plaintext(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bootstrap.db"
    settings = Settings(database_url=f"sqlite:///{path}")
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr("sys.argv", ["app.manage", "create-invite"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda _: CODE)
    assert manage.main() == 0
    assert "Created invitation: 0/5" in capsys.readouterr().out
    with TestClient(main.create_app(settings)) as client:
        assert register(client).status_code == 201
    assert CODE.encode() not in path.read_bytes()


def test_adding_invitation_table_preserves_existing_account_device_and_login(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'legacy.db'}")
    engine, sessions = database(settings.database_url)
    Base.metadata.create_all(engine, tables=[Account.__table__, AccountSession.__table__, Server.__table__])
    assert "invitations" not in inspect(engine).get_table_names()
    with sessions() as db:
        account = Account(username="legacy-user", password_hash=hash_password(PASSWORD), created_at=NOW)
        db.add(account)
        db.flush()
        db.add(Server(account_id=account.id, name="legacy-server", hostname="legacy-host", device_token_hash="old-device-digest", created_at=NOW, gpus=[]))
        db.commit()
    engine.dispose()
    app = main.create_app(settings)
    with TestClient(app) as client:
        assert "invitations" in inspect(app.state.engine).get_table_names()
        assert register(client).status_code == 403
        login = client.post("/api/v1/auth/login", json={"username": "legacy-user", "password": PASSWORD})
        assert login.status_code == 200
        dashboard = client.get("/api/v1/servers", headers={"Authorization": "Bearer " + login.json()["access_token"]})
        assert dashboard.status_code == 200
        assert dashboard.json()["servers"][0]["name"] == "legacy-server"
