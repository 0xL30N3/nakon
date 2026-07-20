#!/usr/bin/env python3
"""
Seed / restore the vulndb `configurations` + `attachments` catalog.

This is the committed, reproducible source of truth for the vulndb contents. The live
database (a MySQL/MariaDB VM) can be rebuilt from here at any time:

    python3 seed_vulndb.py            # apply schema.sql (idempotent) then seed.sql
    python3 seed_vulndb.py --reset    # DROP + recreate tables, then re-seed (schema.sql
                                      # already DROPs, so this is the same as default today,
                                      # but kept explicit for intent)

Connection details are read from ../.env (the same file nakon itself uses):
    host=... user=... password=... database=...

schema.sql / seed.sql are produced with mysqldump; regenerate them after editing the live
catalog with:
    mysqldump -u<user> -p<pw> --no-data --skip-comments --skip-dump-date <db> configurations attachments > schema.sql
    mysqldump -u<user> -p<pw> --no-create-info --complete-insert --skip-comments \
        --skip-dump-date --skip-extended-insert <db> configurations attachments > seed.sql
"""

import os
import sys
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ENV = HERE.parent / ".env"


def run_sql_file(cursor, path: Path):
    sql = path.read_text()
    # mysql.connector executes one statement at a time; split on ';' at line ends.
    # The dumps use plain statements (no stored routines), so a naive split is safe.
    statements = [s.strip() for s in sql.split(";\n") if s.strip()]
    count = 0
    for stmt in statements:
        # Skip pure comment / sandbox / SET-directive lines that mysql.connector rejects
        if stmt.startswith("/*") and stmt.endswith("*/"):
            continue
        try:
            cursor.execute(stmt)
            count += 1
        except mysql.connector.Error as e:
            # Tolerate conditional-comment SET directives the connector doesn't like
            if stmt.startswith("/*"):
                continue
            raise RuntimeError(f"Failed on statement:\n{stmt[:200]}\n-> {e}") from e
    return count


def main():
    load_dotenv(ENV)
    cfg = {
        "host": os.getenv("host"),
        "user": os.getenv("user"),
        "password": os.getenv("password"),
        "database": os.getenv("database"),
    }
    if not all(cfg.values()):
        sys.exit(f"[seed] missing DB connection details in {ENV}")

    print(f"[seed] connecting to {cfg['user']}@{cfg['host']}/{cfg['database']}")
    conn = mysql.connector.connect(**cfg)
    cur = conn.cursor()

    n = run_sql_file(cur, HERE / "schema.sql")
    print(f"[seed] applied schema.sql ({n} statements)")
    n = run_sql_file(cur, HERE / "seed.sql")
    print(f"[seed] applied seed.sql ({n} statements)")

    conn.commit()
    cur.execute("SELECT category, COUNT(*) FROM configurations GROUP BY category")
    for cat, cnt in cur.fetchall():
        print(f"[seed]   {cat}: {cnt}")
    cur.close()
    conn.close()
    print("[seed] done")


if __name__ == "__main__":
    main()
