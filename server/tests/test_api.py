from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.main import create_app
from app.models import Account, AccountSession, Server
from app.security import token_hash, verify_password

PASSWORD = "research-password-123"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def setup(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings)
    clock = Clock()
    app.state.clock = clock
    with TestClient(app) as client:
        yield client, clock, app


def bearer(token):
    return {"Authorization": "Bearer " + token}


def register(client, username="researcher"):
    response = client.post("/api/v1/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def enroll(client, account_token, name="lab-a"):
    response = client.post("/api/v1/agents/enroll", headers=bearer(account_token), json={"name": name, "hostname": "gpu-host"})
    assert response.status_code == 201, response.text
    return response.json()


def gpu(**changes):
    return {"uuid": "GPU-a", "index": 0, "name": "NVIDIA A100", "memory_total_mib": 81920,
            "memory_used_mib": 400, "utilization_gpu_percent": 0, "temperature_celsius": 35,
            "power_watts": 50, "processes_known": True, "processes": [], **changes}


def report(client, clock, device, **changes):
    payload = {"sampled_at": clock().isoformat(), "hostname": "gpu-host", "collection_status": "ok", "error": None, "gpus": [gpu()], **changes}
    response = client.post("/api/v1/agent/report", headers=bearer(device), json=payload)
    assert response.status_code == 200, response.text
    return response


def view(client, token):
    response = client.get("/api/v1/servers", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()["servers"][0]


def green(client, clock, device, **gpu_changes):
    report(client, clock, device, gpus=[gpu(**gpu_changes)])
    for _ in range(10):
        clock.advance(30)
        report(client, clock, device, gpus=[gpu(**gpu_changes)])


def test_account_isolation_and_device_revocation(setup):
    client, clock, _ = setup
    alice, bob = register(client, "alice"), register(client, "bobby")
    a = enroll(client, alice)
    b = enroll(client, bob, "bob-server")
    report(client, clock, a["device_token"])
    assert view(client, alice)["name"] == "lab-a"
    assert view(client, bob)["name"] == "bob-server"
    assert client.delete("/api/v1/servers/" + a["server_id"], headers=bearer(bob)).status_code == 404
    assert client.delete("/api/v1/servers/" + a["server_id"], headers=bearer(alice)).status_code == 204
    assert client.get("/api/v1/servers", headers=bearer(alice)).json()["servers"] == []
    payload = {"sampled_at": clock().isoformat(), "hostname": "host", "collection_status": "ok", "gpus": []}
    assert client.post("/api/v1/agent/report", json=payload, headers=bearer(a["device_token"])).status_code == 401
    report(client, clock, b["device_token"])


def test_authentication_token_scope_logout_expiry_and_hashes(setup):
    client, clock, app = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    assert client.get("/api/v1/health").json()["status"] == "ok"
    for headers in ({}, bearer("gra_wrong"), bearer(device)):
        assert client.get("/api/v1/servers", headers=headers).status_code == 401
    payload = {"sampled_at": clock().isoformat(), "hostname": "host", "collection_status": "ok", "gpus": []}
    assert client.post("/api/v1/agent/report", json=payload, headers=bearer(token)).status_code == 401
    with app.state.session_factory() as db:
        account = db.scalar(select(Account))
        assert account.password_hash != PASSWORD and verify_password(PASSWORD, account.password_hash)
        assert db.scalar(select(AccountSession)).token_hash == token_hash(token)
        assert db.scalar(select(Server)).device_token_hash == token_hash(device)
    assert client.post("/api/v1/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
    # Logging out the enrollment session must not affect the device credential.
    report(client, clock, device)
    login = client.post("/api/v1/auth/login", json={"username": "RESEARCHER", "password": PASSWORD})
    assert login.status_code == 200
    new_token = login.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers=bearer(new_token)).json()["username"] == "researcher"
    clock.advance(31 * 86400)
    assert client.get("/api/v1/auth/me", headers=bearer(new_token)).status_code == 401
    report(client, clock, device)


def test_pending_then_continuous_confirmation_and_staleness(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    pending = view(client, token)
    assert pending["offline"] and pending["collection_status"] == "pending"
    assert pending["last_seen"] is None and pending["gpus"] == []
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["idle_since"] is None
    for _ in range(9):
        clock.advance(30)
        report(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "unknown"
    assert view(client, token)["gpus"][0]["utilization_window_seconds"] == 270
    # Time passing alone must not promote the last observation to idle.
    clock.advance(30)
    assert view(client, token)["gpus"][0]["state"] == "unknown"
    assert view(client, token)["gpus"][0]["utilization_window_seconds"] == 270
    report(client, clock, device)
    row = view(client, token)["gpus"][0]
    assert row["state"] == "idle" and row["idle_since"] is not None
    assert row["utilization_avg_5m_percent"] == 0 and row["utilization_window_seconds"] == 300
    clock.advance(31)
    stale = view(client, token)
    assert stale["offline"] and stale["gpus"][0]["state"] == "unknown"
    assert stale["gpus"][0]["idle_since"] is None
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "unknown"
    assert view(client, token)["gpus"][0]["utilization_window_seconds"] == 0


def test_health_returns_current_server_time_in_utc(setup):
    client, clock, _ = setup
    health = client.get("/api/v1/health").json()
    assert health == {"status": "ok", "server_time": "2026-10-13T01:00:00Z"}
    assert datetime.fromisoformat(health["server_time"]).utcoffset() == timedelta(0)
    clock.advance(7)
    assert client.get("/api/v1/health").json()["server_time"] == "2026-10-13T01:00:07Z"


def test_dashboard_includes_same_utc_clock_used_for_classification(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    report(client, clock, device)
    clock.advance(31)
    dashboard = client.get("/api/v1/servers", headers=bearer(token)).json()
    assert dashboard["server_time"] == "2026-10-13T01:00:31Z"
    assert datetime.fromisoformat(dashboard["server_time"]).utcoffset() == timedelta(0)
    assert dashboard["servers"][0]["offline"] is True
    assert dashboard["servers"][0]["gpus"][0]["state"] == "unknown"


def test_collection_error_preserves_rows_and_resets_confirmation(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "idle"
    clock.advance(5)
    report(client, clock, device, collection_status="error", error="NVML unavailable", gpus=[])
    failed = view(client, token)
    assert not failed["offline"] and failed["error"] == "NVML unavailable"
    assert failed["gpus"][0]["uuid"] == "GPU-a"
    assert failed["gpus"][0]["state"] == "unknown" and failed["gpus"][0]["idle_since"] is None
    clock.advance(5)
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "unknown"


@pytest.mark.parametrize("changes,expected", [
    ({"memory_used_mib": None}, "unknown"),
    ({"memory_total_mib": None}, "unknown"),
    ({"utilization_gpu_percent": None}, "unknown"),
    ({"processes_known": False}, "idle"),
    ({"memory_total_mib": 0, "memory_used_mib": 0}, "unknown"),
    ({"utilization_gpu_percent": 80, "processes_known": False}, "busy"),
    ({"memory_used_mib": 70000}, "uncertain"),
    ({"utilization_gpu_percent": 20}, "busy"),
    ({"utilization_gpu_percent": 10}, "busy"),
    ({"utilization_gpu_percent": 9.999}, "idle"),
    ({"utilization_gpu_percent": 5}, "idle"),
    ({"memory_used_mib": 8192}, "uncertain"),
    ({"memory_used_mib": 8191}, "idle"),
    ({"memory_used_mib": 2048}, "idle"),
    ({"memory_used_mib": None, "utilization_gpu_percent": 10}, "busy"),
    ({"processes": [{"pid": 123, "username": None, "name": "python", "memory_used_mib": 300}]}, "idle"),
])
def test_unknown_metrics_and_resource_classification(setup, changes, expected):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device, **changes)
    assert view(client, token)["gpus"][0]["state"] == expected


def test_old_duplicate_out_of_order_and_future_timestamps(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    original = clock().isoformat()
    report(client, clock, device, gpus=[gpu(utilization_gpu_percent=90)])
    clock.advance(10)
    report(client, clock, device, sampled_at=original)
    unchanged = view(client, token)
    assert unchanged["gpus"][0]["utilization_gpu_percent"] == 90
    assert unchanged["gpus"][0]["utilization_window_seconds"] == 0
    assert unchanged["last_seen"].replace("Z", "+00:00") == original
    report(client, clock, device, sampled_at=(clock() - timedelta(seconds=15)).isoformat())
    assert view(client, token)["gpus"][0]["utilization_gpu_percent"] == 90
    clock.advance(30)
    report(client, clock, device, sampled_at=original)
    assert view(client, token)["offline"]
    # A newer-but-stale sample can update the snapshot but never makes it green.
    report(client, clock, device, sampled_at=(clock() - timedelta(seconds=35)).isoformat())
    assert view(client, token)["gpus"][0]["state"] == "unknown"
    payload = {"sampled_at": (clock() + timedelta(seconds=31)).isoformat(), "hostname": "host", "collection_status": "ok", "gpus": [gpu()]}
    assert client.post("/api/v1/agent/report", headers=bearer(device), json=payload).status_code == 422


def test_gpu_identity_survives_index_change_but_not_missing_samples(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device)
    since = view(client, token)["gpus"][0]["idle_since"]
    clock.advance(5)
    report(client, clock, device, gpus=[gpu(index=1), gpu(uuid="GPU-b", index=0)])
    rows = view(client, token)["gpus"]
    assert [row["uuid"] for row in rows] == ["GPU-b", "GPU-a"]
    assert rows[1]["state"] == "idle" and rows[1]["idle_since"] == since
    assert rows[0]["state"] == "unknown"
    clock.advance(5)
    report(client, clock, device, gpus=[])
    clock.advance(5)
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "unknown"


def test_null_utilization_resets_but_missing_memory_preserves_utilization_window(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device)
    clock.advance(5)
    report(client, clock, device, gpus=[gpu(memory_used_mib=None)])
    row = view(client, token)["gpus"][0]
    assert row["state"] == "unknown" and row["utilization_window_seconds"] == 300
    assert row["utilization_avg_5m_percent"] == 0
    clock.advance(5)
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["state"] == "idle"
    clock.advance(5)
    report(client, clock, device, gpus=[gpu(utilization_gpu_percent=None)])
    row = view(client, token)["gpus"][0]
    assert row["state"] == "unknown" and row["utilization_window_seconds"] == 0
    assert row["utilization_avg_5m_percent"] is None
    clock.advance(5)
    report(client, clock, device)
    assert view(client, token)["gpus"][0]["utilization_window_seconds"] == 0


def test_sample_gap_resets_window_even_when_receipts_are_frequent(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device)
    clock.advance(5)
    report(client, clock, device, sampled_at=(clock() + timedelta(seconds=26)).isoformat())
    row = view(client, token)["gpus"][0]
    assert row["state"] == "unknown" and row["utilization_window_seconds"] == 0


def test_history_survives_restart_and_is_never_exposed(setup):
    client, clock, app = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    green(client, clock, device)
    with app.state.session_factory() as db:
        assert db.scalar(select(Server)).gpus[0]["_utilization_history"]
    restarted = create_app(app.state.settings)
    restarted.state.clock = clock
    with TestClient(restarted) as other:
        assert view(other, token)["gpus"][0]["state"] == "idle"
        clock.advance(5)
        report(other, clock, device)
        row = view(other, token)["gpus"][0]
        assert row["state"] == "idle" and row["utilization_window_seconds"] == 300
        assert not any(key.startswith("_") for key in row)


def test_old_snapshot_without_history_warms_up_without_schema_migration(setup):
    client, clock, app = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    report(client, clock, device)
    with app.state.session_factory() as db:
        server = db.scalar(select(Server))
        server.gpus = [{**gpu(), "idle_since": "2026-10-13T00:00:00Z"}]
        db.commit()
    legacy = view(client, token)["gpus"][0]
    assert legacy["state"] == "unknown" and legacy["idle_since"] is None
    clock.advance(5)
    report(client, clock, device)
    row = view(client, token)["gpus"][0]
    assert row["state"] == "unknown" and row["utilization_window_seconds"] == 0


def test_limits_and_invalid_payloads(setup):
    client, clock, _ = setup
    token = register(client)
    device = enroll(client, token)["device_token"]
    valid = {"sampled_at": clock().isoformat(), "hostname": "host", "collection_status": "ok", "gpus": [gpu()]}
    for changes in [
        {"gpus": [gpu(), gpu()]},
        {"gpus": [gpu(uuid=f"GPU-{i}", index=i) for i in range(65)]},
        {"gpus": [gpu(processes=[{"pid": i + 1} for i in range(257)])]},
        {"gpus": [gpu(memory_total_mib=20, memory_used_mib=30)]},
        {"gpus": [gpu(utilization_gpu_percent=101)]},
        {"sampled_at": "2026-10-13T01:00:00"},
        {"collection_status": "error"},
        {"extra_unexpected": "unbounded extra data"},
    ]:
        assert client.post("/api/v1/agent/report", json={**valid, **changes}, headers=bearer(device)).status_code == 422
    assert client.post("/api/v1/agent/report", content=b"x" * 1_048_577, headers=bearer(device)).status_code == 413


def test_login_throttle_and_disabled_registration(setup, tmp_path):
    client, clock, _ = setup
    register(client)
    for _ in range(8):
        assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code == 429
    clock.advance(601)
    assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code == 200
    locked = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'locked.db'}", registration_enabled=False))
    with TestClient(locked) as other:
        assert other.post("/api/v1/auth/register", json={"username": "researcher", "password": PASSWORD}).status_code == 403


def test_attacker_login_cannot_reset_another_accounts_failure_budget(setup):
    client, _, _ = setup
    register(client, "victim")
    register(client, "attacker")
    for _ in range(8):
        assert client.post("/api/v1/auth/login", json={"username": "victim", "password": "wrong"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": "attacker", "password": PASSWORD}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "victim", "password": "wrong"}).status_code == 429


def test_successful_logins_cannot_reset_source_ip_budget(setup):
    client, clock, app = setup
    register(client)
    app.state.login_ip_limiter.attempts = 4
    for _ in range(4):
        assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code == 429
    clock.advance(601)
    assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD}).status_code == 200


def test_account_failure_budget_applies_across_source_ips(setup):
    client, _, app = setup
    register(client)
    for index in range(8):
        with TestClient(app, client=(f"192.0.2.{index + 1}", 50000)) as remote:
            assert remote.post("/api/v1/auth/login", json={"username": "researcher", "password": "wrong"}).status_code == 401
    with TestClient(app, client=("198.51.100.1", 50000)) as remote:
        assert remote.post("/api/v1/auth/login", json={"username": "researcher", "password": "wrong"}).status_code == 429


def test_correct_login_clears_only_its_own_account_failures(setup):
    client, _, app = setup
    register(client, "alice")
    register(client, "bobby")
    app.state.login_failures.attempts = 2
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "bobby", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "bobby", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "bobby", "password": PASSWORD}).status_code == 429


def test_concurrent_login_attempts_reserve_failure_budget_before_verification():
    from concurrent.futures import ThreadPoolExecutor
    from app.security import LoginFailureLimiter, LoginLimiter

    limiter = LoginFailureLimiter(8, 600)
    with ThreadPoolExecutor(max_workers=16) as executor:
        accepted = list(executor.map(lambda _: limiter.begin("target", 1000), range(64)))
    assert sum(accepted) == 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: limiter.finish("target", 1001, success=False), range(8)))
    assert not limiter.begin("target", 1002)
    assert limiter.begin("target", 1602)
    limiter.finish("target", 1602, success=True)
    assert limiter.begin("target", 1602)

    ip_limiter = LoginLimiter(12, 600)
    with ThreadPoolExecutor(max_workers=16) as executor:
        accepted = list(executor.map(lambda _: ip_limiter.allow("192.0.2.1", 1000), range(64)))
    assert sum(accepted) == 12


def test_successful_login_does_not_drop_other_inflight_failures():
    from app.security import LoginFailureLimiter

    limiter = LoginFailureLimiter(2, 600)
    assert limiter.begin("alice", 1000)
    assert limiter.begin("alice", 1000)
    limiter.finish("alice", 1001, success=True)
    # The other already-running request still owns one slot.
    assert limiter.begin("alice", 1001)
    assert not limiter.begin("alice", 1001)
    limiter.finish("alice", 1002, success=False)
    limiter.finish("alice", 1002, success=False)
    assert not limiter.begin("alice", 1003)


def test_cli_bootstrap_when_public_registration_disabled(tmp_path, monkeypatch, capsys):
    from app import manage

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'cli.db'}", registration_enabled=False)
    monkeypatch.setattr(Settings, "from_env", lambda: settings)
    monkeypatch.setattr("sys.argv", ["app.manage", "create-user", "Admin"])
    monkeypatch.setattr(manage.getpass, "getpass", lambda _: PASSWORD)
    assert manage.main() == 0
    assert "Created account: admin" in capsys.readouterr().out
    with TestClient(create_app(settings)) as client:
        result = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert result.status_code == 200


def test_password_change_checks_current_password_rotates_sessions_and_preserves_devices(setup):
    client, clock, _ = setup
    alice = register(client, "alice")
    bobby = register(client, "bobby")
    second = client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}).json()["access_token"]
    device = enroll(client, alice)["device_token"]
    new_password = "new-research-password-456"
    payload = {"current_password": PASSWORD, "new_password": new_password}
    assert client.post("/api/v1/auth/password", json=payload).status_code == 401
    assert client.post("/api/v1/auth/password", headers=bearer(device), json=payload).status_code == 401
    wrong = client.post("/api/v1/auth/password", headers=bearer(alice), json={**payload, "current_password": "wrong"})
    assert wrong.status_code == 403 and wrong.json()["detail"] == "Current password is incorrect"
    assert client.get("/api/v1/auth/me", headers=bearer(alice)).status_code == 200
    assert client.post("/api/v1/auth/password", headers=bearer(alice), json={**payload, "new_password": "short"}).status_code == 422
    assert client.post("/api/v1/auth/password", headers=bearer(alice), json={**payload, "username": "bobby"}).status_code == 422
    changed = client.post("/api/v1/auth/password", headers=bearer(alice), json=payload)
    assert changed.status_code == 200, changed.text
    result = changed.json()
    assert result["account"]["username"] == "alice" and result["token_type"] == "bearer" and result["expires_at"]
    new_token = result["access_token"]
    assert new_token not in {alice, second}
    for old in (alice, second):
        assert client.get("/api/v1/auth/me", headers=bearer(old)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(new_token)).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(bobby)).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "alice", "password": new_password}).status_code == 200
    report(client, clock, device)
    assert view(client, new_token)["last_seen"] is not None


def test_password_change_and_login_share_account_failure_budget(setup):
    client, _, app = setup
    token = register(client)
    app.state.login_failures.attempts = 2
    bad = {"current_password": "wrong", "new_password": "new-research-password-456"}
    assert client.post("/api/v1/auth/password", headers=bearer(token), json=bad).status_code == 403
    assert client.post("/api/v1/auth/login", json={"username": "researcher", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/password", headers=bearer(token), json=bad).status_code == 429


def test_concurrent_password_changes_only_one_can_use_old_credentials(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import main

    client, _, _ = setup
    token = register(client)
    synchronize = Barrier(2)
    real_hash = main.hash_password

    def hash_together(password):
        synchronize.wait(timeout=10)
        return real_hash(password)

    monkeypatch.setattr(main, "hash_password", hash_together)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda index: client.post("/api/v1/auth/password", headers=bearer(token),
                    json={"current_password": PASSWORD, "new_password": f"new-password-choice-{index}"}), range(2)))
    assert sorted(result.status_code for result in results) == [200, 409]
    winner = next(result for result in results if result.status_code == 200)
    assert client.get("/api/v1/auth/me", headers=bearer(winner.json()["access_token"])).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401


def test_login_verified_before_password_rotation_cannot_issue_session_afterwards(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from app import main

    client, _, _ = setup
    token = register(client)
    verified, release = Event(), Event()
    mutex = Lock()
    calls = 0
    real_verify = main.verify_password

    def delayed_first_verification(password, stored):
        nonlocal calls
        valid = real_verify(password, stored)
        with mutex:
            calls += 1
            first = calls == 1
        if first:
            verified.set()
            assert release.wait(timeout=10)
        return valid

    monkeypatch.setattr(main, "verify_password", delayed_first_verification)
    with ThreadPoolExecutor(max_workers=1) as executor:
        login = executor.submit(client.post, "/api/v1/auth/login", json={"username": "researcher", "password": PASSWORD})
        assert verified.wait(timeout=10)
        try:
            changed = client.post("/api/v1/auth/password", headers=bearer(token),
                      json={"current_password": PASSWORD, "new_password": "new-password-after-rotation"})
            assert changed.status_code == 200, changed.text
        finally:
            release.set()
        stale_login = login.result(timeout=10)
    assert stale_login.status_code == 409, stale_login.text
    assert "access_token" not in stale_login.json()
    assert client.get("/api/v1/auth/me", headers=bearer(changed.json()["access_token"])).status_code == 200


def test_postgres_schema_compiles_without_sqlite_specific_types():
    from sqlalchemy import create_mock_engine
    from app.database import Base

    statements = []
    engine = create_mock_engine("postgresql+psycopg://", lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))))
    Base.metadata.create_all(engine)
    assert any("CREATE TABLE servers" in statement for statement in statements)
