#!/usr/bin/env python3
"""Create and check a live SQLite backup; never overwrite or prune existing files."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, nargs="?")
    parser.add_argument("--source", type=Path, default=Path("/var/lib/gpu-radar/radar.db"))
    parser.add_argument("--directory", type=Path, default=Path("/var/lib/gpu-radar/backups"))
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if args.destination is None:
        args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        destination = args.directory / ("radar-" + stamp + ".db")
    else:
        destination = args.destination
    destination = destination.absolute()
    if not destination.parent.is_dir():
        parser.error("Destination directory must already exist")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as copy:
                original.backup(copy)
                if copy.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    print(destination)


if __name__ == "__main__":
    main()
