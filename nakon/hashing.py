"""Canonical serialization and the content hashes bundles are built around.

Three distinct hashes, and conflating them is the trap this module exists to prevent:

- `request_key`  — identifies *what was asked for* (platform + ordered request list). Plans
  are looked up by this, never by machine name, so a bundle built from one team's machines
  serves every other team whose request list is identical.
- `plan_id`      — identifies a fully resolved, ordered step list including script and
  attachment content hashes.
- `bundle_id`    — identifies the whole bundle: its plans *and* its request→plan map.

`source_fingerprint` (see build.builder) is a fourth, deliberately cheaper hash used only as
a cache key; it is computed from MySQL alone so `nakon build` can decide whether to reuse a
cached bundle before downloading a single attachment byte.

Every hash is taken over a canonical JSON document, never over file or archive bytes — so a
tarball or gzip library upgrade can never change an id.
"""

import hashlib
import json

# Bump when the shape of a hashed document changes in a way that must invalidate existing
# bundles. It is part of every document below, so bumping it re-keys everything at once.
#
# Bumped 1 -> 2: nakon_step_root/nakon_step_user in gen/bash.py now `set -a` before sourcing
# a step's vars file so the assignments are exported into the step's own exec'd process —
# previously a plain (non-exported) shell variable, silently dropped across the `exec`, so
# every step with vars (install-package's $PACKAGE, enable-service's $SERVICE, ...) ran with
# them empty. bundle_id hashes resolved plan *content*, not nakon's own generator code, so
# without this bump every bundle built before the fix keeps being served from cache forever.
SCHEMA = 2


def canon(obj) -> bytes:
    """Serialize to canonical JSON: sorted keys, no whitespace, ASCII-escaped.

    ensure_ascii matters — attachment names in the live catalog contain non-ASCII characters
    (one object_key holds a narrow no-break space), and we need the same bytes on every
    platform regardless of filesystem encoding.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(obj) -> str:
    return sha256_bytes(canon(obj))


def sha256_file(path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_request(requested: list) -> list:
    """Normalize a machine's `configurations` list to the canonical [{name, vars}] form.

    Mirrors the old configurations._normalize: an entry is either a bare string or an object
    with `name` and optional `vars`. Order is preserved and is load-bearing — resolve() walks
    the request list in order and that determines the deployed step order, so two lists with
    the same members in a different order are genuinely different requests.
    """
    normalized = []
    for entry in requested:
        if isinstance(entry, str):
            normalized.append({"name": entry, "vars": {}})
        else:
            normalized.append({
                "name": entry["name"],
                # Coerce values to str here so {"PORT": 8080} and {"PORT": "8080"} hash the
                # same way they deploy — build_script's shlex.quote(str(value)) made them
                # identical on the wire, so they must be identical in the key too.
                "vars": {k: str(v) for k, v in (entry.get("vars") or {}).items()},
            })
    return normalized


def request_key(platform: str, requested: list) -> str:
    """Hash a machine's *request*: its platform plus its normalized, ordered config list.

    Machine name, IP, OS string and credentials are deliberately excluded. That is what lets
    a bundle built from a single team's config.json deploy every other team, and what makes
    re-IPing or renaming a box a no-op for the cache.
    """
    return sha256_json({
        "schema": SCHEMA,
        "kind": "request",
        "platform": platform,
        "requested": normalize_request(requested),
    })


def plan_id(plan_doc: dict) -> str:
    """Hash a resolved plan document (see build.builder.plan_document)."""
    return sha256_json(plan_doc)


def bundle_id(plans: dict, requests: dict) -> str:
    """Hash the bundle identity: every plan document plus the request→plan mapping.

    The requests map has to be in here. Without it, two different request lists that resolve
    to the same plan set (say ["a"] and ["a","a"]) would produce the same bundle id, the
    second build would hit the cache, and deploy would then fail to find a request key the
    cached manifest never recorded.
    """
    return sha256_json({
        "schema": SCHEMA,
        "kind": "bundle",
        "plans": plans,
        "requests": requests,
    })


def short(digest: str, length: int = 12) -> str:
    """Shorten a hex digest for display and for on-disk directory names."""
    return digest[:length]
