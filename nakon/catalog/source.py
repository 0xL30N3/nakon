"""Catalog sources — where configuration rows come from.

`MySQLCatalog` is the real vulndb; `DictCatalog` backs the hermetic tests. Everything that
resolves or builds takes a source rather than a cursor, so the whole build path is testable
without a database.

IMPORTANT: `mysql.connector` is imported *inside* the functions that need it, never at module
scope. `nakon deploy` runs on the scoring engine where only paramiko is installed, and it
imports this module transitively; a top-level import would kill it there.
"""

import json
import os

from ..errors import CatalogError

# Column order shared by every configuration query, so row unpacking stays in one place.
_CONFIG_COLUMNS = "id, name, platform, category, script, run_as, type, depends_on"


def _parse_depends_on(raw):
    """depends_on is a JSON column; the connector may hand back a list, a str, or None."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def _row_to_dict(row, attachments):
    id_, name, platform, category, script, run_as, cfg_type, depends_on = row
    return {
        "id": id_,
        "name": name,
        "platform": platform,
        "category": category,
        "script": script,
        "run_as": run_as,
        "type": cfg_type,
        "depends_on": _parse_depends_on(depends_on),
        "attachments": attachments,
    }


class DictCatalog:
    """In-memory catalog for tests and for replaying a bundle's provenance.

    Accepts rows in the same dict shape MySQLCatalog returns; missing keys get sane defaults
    so a fixture can stay terse.
    """

    def __init__(self, rows):
        self.rows = {}
        for row in rows:
            self.rows[row["name"]] = {
                "id": row.get("id"),
                "name": row["name"],
                "platform": row.get("platform", "linux"),
                "category": row.get("category", "misconfiguration"),
                "script": row["script"],
                "run_as": row.get("run_as", "root"),
                "type": row.get("type", "bash"),
                "depends_on": row.get("depends_on") or [],
                "attachments": row.get("attachments") or [],
            }

    def fetch(self, name):
        row = self.rows.get(name)
        return dict(row) if row is not None else None

    def all_names(self):
        return sorted(self.rows)

    def close(self):
        pass


class MySQLCatalog:
    """The live vulndb catalog. Build-side only — never imported on the deploy host."""

    def __init__(self, connection):
        self.connection = connection
        # Buffered so a SELECT that returns rows is fully drained before the next query;
        # otherwise mysql.connector raises "Unread result found" (same reason the old
        # deploy.py used a buffered cursor).
        self.cursor = connection.cursor(buffered=True)
        self._cache = {}

    @classmethod
    def from_env(cls):
        """Connect using the lowercase .env keys the rest of the toolchain already uses."""
        import mysql.connector  # noqa: PLC0415 — deploy hosts don't have this installed

        try:
            connection = mysql.connector.connect(
                host=os.getenv("host"),
                user=os.getenv("user"),
                password=os.getenv("password"),
                database=os.getenv("database"),
            )
        except Exception as exc:
            raise CatalogError(
                f"could not connect to the vulndb MySQL catalog at {os.getenv('host')!r}: {exc}\n"
                f"`nakon build` must run somewhere the vulndb is reachable (check .env)."
            ) from exc
        return cls(connection)

    def fetch(self, name: str):
        """Look up a configuration by name, with its attachments. None if absent."""
        if name in self._cache:
            cached = self._cache[name]
            return dict(cached) if cached is not None else None

        self.cursor.execute(
            f"SELECT {_CONFIG_COLUMNS} FROM configurations WHERE name = %s", (name,)
        )
        row = self.cursor.fetchone()
        if row is None:
            self._cache[name] = None
            return None

        self.cursor.execute(
            "SELECT id, object_key, original_name, mime_type, size_bytes "
            "FROM attachments WHERE configuration_id = %s ORDER BY id",
            (row[0],),
        )
        attachments = [
            {
                "id": a_id,
                "object_key": object_key,
                "original_name": original_name,
                "mime_type": mime_type,
                "size_bytes": size_bytes,
            }
            for a_id, object_key, original_name, mime_type, size_bytes in self.cursor.fetchall()
        ]

        result = _row_to_dict(row, attachments)
        self._cache[name] = result
        return dict(result)

    def all_names(self):
        self.cursor.execute("SELECT name FROM configurations ORDER BY name")
        return [name for (name,) in self.cursor.fetchall()]

    def close(self):
        try:
            self.cursor.close()
        finally:
            self.connection.close()
