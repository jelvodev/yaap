"""Set or reset YAAP's password from the command line.

Inside the container: `docker compose exec -it yaap python -m app.scripts.set_password`. It asks for the password
twice; with `--stdin` it reads one line from standard input instead (the setup scripts use that, so the password never
appears on a command line). Setting it signs every browser out. Without `--reset`, an existing password is left alone,
so running the setup script again changes nothing.
"""

import argparse
import getpass
import sys

from app.auth.account import AccountError, default_hasher, end_other_sessions, password_is_set, set_password
from app.config import load_settings
from app.storage.db import create_database_engine, create_session_factory, run_migrations


def read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password for YAAP: ")
    second = getpass.getpass("The same password again: ")
    if first != second:
        raise AccountError("The two passwords differ; nothing was changed.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set or reset YAAP's password.")
    parser.add_argument("--stdin", action="store_true", help="read the password from one line of standard input")
    parser.add_argument("--reset", action="store_true", help="replace an existing password")
    parser.add_argument("--if-unset", action="store_true", help="say so and stop when a password is already set")
    arguments = parser.parse_args(argv)
    settings = load_settings()
    run_migrations(settings.database_url)
    engine = create_database_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            if password_is_set(session) and not arguments.reset:
                print("YAAP already has a password. Use --reset to replace it.")
                return 0 if arguments.if_unset else 1
            try:
                password = read_password(arguments.stdin)
                set_password(session, default_hasher(), password)
            except AccountError as error:
                print(f"Not set: {error}", file=sys.stderr)
                return 2
            ended = end_other_sessions(session)
            session.commit()
    finally:
        engine.dispose()
    print("The password is set." + (f" {ended} browser session(s) were signed out." if ended else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
