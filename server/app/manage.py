"""Administrator commands. Secrets are never accepted as command-line arguments."""

import argparse
import getpass
import re
import sys
import warnings
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .database import Base, database
from .models import Account, Invitation
from .schemas import AccountCreation
from .security import hash_password, invitation_hash


def positive_integer(value):
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def ensure_invitation(db, digest: str, max_uses: int, now: datetime) -> tuple[Invitation, bool]:
    """Create once; repeated deployment must not refill an existing invitation."""
    invitation = db.get(Invitation, digest)
    created = invitation is None
    if invitation is None:
        invitation = Invitation(code_hash=digest, max_uses=max_uses, used_count=0, created_at=now)
        db.add(invitation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            invitation = db.get(Invitation, digest)
            if invitation is None:
                raise
            created = False
    if invitation.max_uses != max_uses:
        raise ValueError("Invitation already exists with a different use limit; no changes made.")
    return invitation, created


def main():
    parser = argparse.ArgumentParser(description="GPU Radar account and invitation administration")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Create an account with an interactive password prompt")
    create.add_argument("username")
    invite = commands.add_parser("create-invite", help="Create an invitation without displaying or storing its plaintext")
    invite.add_argument("--max-uses", type=positive_integer, default=5)
    invite.add_argument("--code-stdin", action="store_true", help="Read the secret code from stdin instead of a hidden prompt")
    args = parser.parse_args()
    if args.command == "create-user":
        password = getpass.getpass("Password (10–128 characters): ")
        if password != getpass.getpass("Confirm password: "):
            parser.exit(1, "Passwords do not match.\n")
        try:
            payload = AccountCreation(username=args.username, password=password)
        except ValidationError:
            parser.exit(1, "Username must contain 3–48 letters, digits, dots, underscores or hyphens; password must contain 10–128 characters.\n")
    else:
        if args.code_stdin:
            code = sys.stdin.read(130).strip()
        else:
            # Never silently fall back to an echoed input when no terminal exists.
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                try:
                    code = getpass.getpass("Invitation code (8–128 letters, digits, _ or -): ").strip()
                except (getpass.GetPassWarning, EOFError):
                    parser.exit(1, "A terminal is required for hidden input; use --code-stdin for automation.\n")
        if re.fullmatch(r"[A-Za-z0-9_-]{8,128}", code) is None:
            parser.exit(1, "Invitation code must contain 8–128 letters, digits, underscores or hyphens.\n")
    engine, sessions = database(Settings.from_env().database_url)
    Base.metadata.create_all(engine)
    try:
        with sessions() as db:
            if args.command == "create-invite":
                invitation, created = ensure_invitation(db, invitation_hash(code), args.max_uses, datetime.now(timezone.utc))
                print(f"{'Created' if created else 'Existing'} invitation: {invitation.used_count}/{invitation.max_uses} uses consumed; "
                      f"{invitation.max_uses - invitation.used_count} remaining.")
            else:
                account = Account(username=payload.username, password_hash=hash_password(payload.password), created_at=datetime.now(timezone.utc))
                db.add(account)
                db.commit()
                print(f"Created account: {account.username}")
    except IntegrityError:
        print("Username is already registered.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
