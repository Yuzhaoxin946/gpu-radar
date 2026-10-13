"""Administrator commands. Passwords are never accepted as command-line arguments."""

import argparse
import getpass
import sys
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .database import Base, database
from .models import Account
from .schemas import Registration
from .security import hash_password


def main():
    parser = argparse.ArgumentParser(description="GPU Radar account administration")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Create an account with an interactive password prompt")
    create.add_argument("username")
    args = parser.parse_args()
    password = getpass.getpass("Password (10–128 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.exit(1, "Passwords do not match.\n")
    try:
        payload = Registration(username=args.username, password=password)
    except ValidationError:
        parser.exit(1, "Username must contain 3–48 letters, digits, dots, underscores or hyphens; password must contain 10–128 characters.\n")
    engine, sessions = database(Settings.from_env().database_url)
    Base.metadata.create_all(engine)
    try:
        with sessions() as db:
            account = Account(username=payload.username, password_hash=hash_password(payload.password), created_at=datetime.now(timezone.utc))
            db.add(account)
            db.commit()
            print(f"Created account: {account.username}")
    except IntegrityError:
        print("Username is already registered.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
