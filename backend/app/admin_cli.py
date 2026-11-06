"""Out-of-band server admin assignment (needs shell access to the container).

  python -m app.admin_cli set-admin EMAIL     # make this account the (only) server admin
  python -m app.admin_cli show                # who is admin
"""
import sys

import psycopg

from app.config import get_settings


def _conn():
    return psycopg.connect(get_settings().owner_db_url.replace("postgresql+psycopg://", "postgresql://", 1))


def main(argv: list[str]) -> int:
    if argv[:1] == ["show"]:
        with _conn() as c:
            for (e,) in c.execute("SELECT email FROM users WHERE is_server_admin"):
                print(e)
        return 0
    if len(argv) == 2 and argv[0] == "set-admin":
        with _conn() as c:
            n = c.execute("UPDATE users SET is_server_admin = (email = lower(%s))", (argv[1],)).rowcount
            if not c.execute("SELECT 1 FROM users WHERE is_server_admin").fetchone():
                c.rollback()
                print(f"No account with email {argv[1]}", file=sys.stderr)
                return 1
            c.commit()
        print(f"{argv[1]} is now the server admin")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
