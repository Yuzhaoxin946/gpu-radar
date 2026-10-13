from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .classification import next_gpu_snapshot, server_view
from .config import Settings
from .database import Base, database
from .models import Account, AccountSession, Invitation, Server
from .schemas import Enrollment, Login, PasswordChange, Registration, Report
from .security import LoginFailureLimiter, LoginLimiter, hash_password, invitation_hash, issue_token, timestamp, token_hash, utc, verify_password

bearer = HTTPBearer(auto_error=False)
# Match password-hashing work for unknown accounts without storing any usable credential.
_DUMMY_HASH = hash_password("not-an-account-" + issue_token("")[0])


class BodyLimitMiddleware:
    """Read at most the configured JSON limit, including chunked requests."""
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                response = JSONResponse({"detail": "Request body exceeds size limit"}, status_code=413)
                return await response(scope, receive, send)
            messages.append(message)
            if not message.get("more_body", False):
                break
        queued = iter(messages)

        async def buffered_receive():
            try:
                return next(queued)
            except StopIteration:
                return await receive()

        await self.app(scope, buffered_receive, send)


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]
Credentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def unauthorized():
    return HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})


def account_session(request: Request, credentials: Credentials, db: DB) -> AccountSession:
    if credentials is None or not credentials.credentials.startswith("gra_") or len(credentials.credentials) > 128:
        raise unauthorized()
    session = db.scalar(select(AccountSession).where(AccountSession.token_hash == token_hash(credentials.credentials)))
    if session is None or utc(session.expires_at) <= request.app.state.clock():
        raise unauthorized()
    return session


AccountAuth = Annotated[AccountSession, Depends(account_session)]


def device_server(credentials: Credentials, db: DB) -> Server:
    if credentials is None or not credentials.credentials.startswith("grd_") or len(credentials.credentials) > 128:
        raise unauthorized()
    server = db.scalar(select(Server).where(Server.device_token_hash == token_hash(credentials.credentials)).with_for_update())
    if server is None:
        raise unauthorized()
    return server


DeviceAuth = Annotated[Server, Depends(device_server)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine, session_factory = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="GPU Radar", version="0.3.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.clock = lambda: datetime.now(timezone.utc)
    app.state.registration_limiter = LoginLimiter(settings.login_attempts, settings.login_window_seconds)
    app.state.login_ip_limiter = LoginLimiter(settings.login_ip_attempts, settings.login_window_seconds)
    app.state.login_failures = LoginFailureLimiter(settings.login_attempts, settings.login_window_seconds)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)

    def issue_session(account: Account, db: Session) -> dict:
        now = app.state.clock()
        # Expired credentials are removed during the next sign-in for this account.
        db.execute(delete(AccountSession).where(AccountSession.account_id == account.id, AccountSession.expires_at <= now))
        token, digest = issue_token("gra_")
        expires = now + timedelta(days=settings.session_ttl_days)
        db.add(AccountSession(account_id=account.id, token_hash=digest, expires_at=expires))
        db.commit()
        return {"access_token": token, "token_type": "bearer", "expires_at": timestamp(expires),
                "account": {"id": account.id, "username": account.username}}

    def attempt(request: Request, limiter: LoginLimiter):
        address = request.client.host if request.client else "unknown"
        # Do not trust user-supplied forwarded headers; let a trusted reverse proxy configure the peer IP.
        if not limiter.allow(address, app.state.clock().timestamp()):
            raise HTTPException(429, "Too many attempts; try again later", headers={"Retry-After": str(settings.login_window_seconds)})

    def verify_account_password(account: Account | None, username: str, password: str) -> bool:
        if not app.state.login_failures.begin(username, app.state.clock().timestamp()):
            raise HTTPException(429, "Too many failed attempts for this account; try again later", headers={"Retry-After": str(settings.login_window_seconds)})
        valid = False
        try:
            valid = verify_password(password, account.password_hash if account else _DUMMY_HASH) and account is not None
            return valid
        finally:
            app.state.login_failures.finish(username, app.state.clock().timestamp(), success=valid)

    def compare_and_lock_password(db: Session, account: Account, expected: str, replacement: str):
        # A conditional write also locks SQLite, where SELECT FOR UPDATE is ignored.
        # Login and password rotation share this gate so a previously verified old
        # password cannot mint a fresh session after a concurrent rotation commits.
        result = db.execute(update(Account).where(Account.id == account.id, Account.password_hash == expected)
                            .values(password_hash=replacement))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Account credentials changed; sign in again")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "server_time": timestamp(app.state.clock())}

    @app.post("/api/v1/auth/register", status_code=201)
    def register(payload: Registration, request: Request, db: DB):
        if not settings.registration_enabled:
            raise HTTPException(403, "Account registration is disabled")
        attempt(request, app.state.registration_limiter)
        if not payload.invite_code:
            raise HTTPException(403, "Invitation code is invalid or fully used")
        # A conditional UPDATE reserves a use and takes a write lock on SQLite
        # as well as PostgreSQL. Keep it in the account/session transaction:
        # failed registration never consumes a use, even under concurrency.
        reserved = db.execute(update(Invitation).where(
            Invitation.code_hash == invitation_hash(payload.invite_code),
            Invitation.used_count < Invitation.max_uses,
        ).values(used_count=Invitation.used_count + 1))
        if reserved.rowcount != 1:
            db.rollback()
            raise HTTPException(403, "Invitation code is invalid or fully used")
        try:
            if db.scalar(select(Account.id).where(Account.username == payload.username)) is not None:
                raise HTTPException(409, "Username is already registered")
            # Reject missing, invalid and exhausted invitations before scrypt.
            account = Account(username=payload.username, password_hash=hash_password(payload.password), created_at=app.state.clock())
            db.add(account)
            db.flush()
            return issue_session(account, db)
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Username is already registered") from None
        except Exception:
            db.rollback()
            raise

    @app.post("/api/v1/auth/login")
    def login(payload: Login, request: Request, db: DB):
        # The source-IP budget includes successful requests and is never reset by sign-in.
        attempt(request, app.state.login_ip_limiter)
        account = db.scalar(select(Account).where(Account.username == payload.username))
        valid = verify_account_password(account, payload.username, payload.password)
        if account is None or not valid:
            raise unauthorized()
        compare_and_lock_password(db, account, account.password_hash, account.password_hash)
        return issue_session(account, db)

    @app.post("/api/v1/auth/password")
    def change_password(payload: PasswordChange, request: Request, auth: AccountAuth, db: DB):
        attempt(request, app.state.login_ip_limiter)
        account = db.get(Account, auth.account_id)
        if account is None or not verify_account_password(account, account.username, payload.current_password):
            raise HTTPException(403, "Current password is incorrect")
        expected = account.password_hash
        replacement = hash_password(payload.new_password)
        compare_and_lock_password(db, account, expected, replacement)
        # Check again after obtaining the account write lock, then rotate everything
        # in one transaction. Device rows are intentionally not changed.
        active = db.scalar(select(AccountSession.id).where(AccountSession.id == auth.id,
                           AccountSession.expires_at > app.state.clock()))
        if active is None:
            db.rollback()
            raise unauthorized()
        db.execute(delete(AccountSession).where(AccountSession.account_id == account.id))
        return issue_session(account, db)

    @app.get("/api/v1/auth/me")
    def me(auth: AccountAuth, db: DB):
        account = db.get(Account, auth.account_id)
        return {"id": account.id, "username": account.username}

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(auth: AccountAuth, db: DB):
        db.delete(auth)
        db.commit()
        return Response(status_code=204)

    @app.post("/api/v1/agents/enroll", status_code=201)
    def enroll(payload: Enrollment, auth: AccountAuth, db: DB):
        token, digest = issue_token("grd_")
        server = Server(account_id=auth.account_id, name=payload.name, hostname=payload.hostname,
                        device_token_hash=digest, created_at=app.state.clock(), gpus=[], collection_status="pending")
        db.add(server)
        db.commit()
        return {"server_id": server.id, "device_token": token, "name": server.name}

    @app.post("/api/v1/agent/report")
    def report(payload: Report, server: DeviceAuth, db: DB):
        now = app.state.clock()
        if (utc(payload.sampled_at) - now).total_seconds() > settings.max_future_seconds:
            raise HTTPException(422, "sampled_at is too far in the future; check the server clock")
        if server.sampled_at is not None and utc(payload.sampled_at) <= utc(server.sampled_at):
            # Idempotent retry or delayed packet: do not overwrite, renew freshness, or confirm idleness.
            return {"accepted": True}
        server.gpus = next_gpu_snapshot(server, payload, now, settings)
        server.hostname = payload.hostname
        server.collection_status = payload.collection_status
        server.error = payload.error
        server.last_seen = now
        server.sampled_at = utc(payload.sampled_at)
        try:
            db.commit()
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Concurrent report or device revocation; retry the latest sample") from None
        return {"accepted": True}

    @app.get("/api/v1/servers")
    def servers(auth: AccountAuth, db: DB):
        records = db.scalars(select(Server).where(Server.account_id == auth.account_id).order_by(Server.name, Server.created_at)).all()
        now = app.state.clock()
        return {"servers": [server_view(record, now, settings) for record in records], "server_time": timestamp(now)}

    @app.delete("/api/v1/servers/{server_id}", status_code=204)
    def unbind(server_id: str, auth: AccountAuth, db: DB):
        server = db.scalar(select(Server).where(Server.id == server_id, Server.account_id == auth.account_id).with_for_update())
        if server is None:
            raise HTTPException(404, "Server not found")
        db.delete(server)
        try:
            db.commit()
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Concurrent device update; retry unbinding") from None
        return Response(status_code=204)

    return app


app = create_app()
