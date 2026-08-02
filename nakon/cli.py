"""Command-line entry point.

Import discipline matters here. `nakon deploy` runs on the scoring engine, which has paramiko
and nothing else — no mysql-connector, no requests, no python-dotenv. So every build-side
import is done inside the subcommand that needs it. A top-level `import mysql.connector` in
this file would make deploy fail on the one host that has to run it.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .errors import BundleError, NakonError
from .hashing import short


def _load_env():
    """Load .env if python-dotenv is available. Build-side convenience only."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _default_config(args) -> Path:
    return Path(args.config)


def cmd_build(args) -> int:
    _load_env()
    from .build.builder import build
    from .catalog.source import MySQLCatalog

    source = MySQLCatalog.from_env()
    try:
        result = build(
            source,
            Path(args.config),
            Path(args.out),
            rebuild=args.rebuild,
        )
    finally:
        source.close()

    if args.export:
        _export(Path(result["path"]), Path(args.export))
        print(f"[nakon] exported {short(result['bundle_id'])} to {args.export}")

    if args.json:
        # Last line, so a caller can take stdout.splitlines()[-1] regardless of log noise.
        print(json.dumps(result))
    return 0


def _export(bundle_dir: Path, dest: Path) -> None:
    """Archive a whole bundle for archival next to a saved competition."""
    from .build.tarball import write_tar_gz

    write_tar_gz(bundle_dir, dest)


def cmd_deploy(args) -> int:
    from .build.builder import load_machines
    from .deploy.bundle import Bundle
    from .deploy.runner import deploy, summarize

    bundle = Bundle.load(args.bundle)
    machines = load_machines(Path(args.config))

    if args.only:
        wanted = set(args.only)
        machines = [m for m in machines if m["name"] in wanted or m["ip"] in wanted]
        if not machines:
            raise NakonError(f"--only {sorted(wanted)} matched no machine in {args.config}")

    print(f"[nakon] bundle {short(bundle.bundle_id)} "
          f"(built {bundle.manifest.get('built_at')} by {bundle.manifest.get('built_by')})")

    # Pre-flight: every machine must map to a plan before anything is deployed to any of
    # them. A machine the bundle doesn't cover is an operator error — the wrong bundle, or a
    # config.json that drifted since it was built — and it means nothing at all would be
    # applied to that box. That is categorically different from a host being unreachable, so
    # it fails hard rather than being collected as a per-machine failure and reported at the
    # end with exit 0, which would let an orchestrator run `check=True` straight past it.
    uncovered = []
    for machine in machines:
        try:
            bundle.plan_for(machine)
        except BundleError as exc:
            uncovered.append(str(exc))
    if uncovered:
        for message in uncovered:
            print(f"[nakon] error: {message}", file=sys.stderr)
        print(f"\n[nakon] {len(uncovered)} machine(s) are not covered by this bundle; "
              f"nothing was deployed.", file=sys.stderr)
        return 1

    print(f"[nakon] deploying {len(machines)} machine(s)"
          + (f" with {args.jobs} in parallel" if args.jobs > 1 else ""))

    log_dir = None
    if not args.no_logs:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_dir = Path(bundle.path) / "runs" / stamp

    outcomes = deploy(
        bundle,
        machines,
        keep_remote=args.keep_remote,
        jobs=args.jobs,
        log_dir=log_dir,
    )
    failures = summarize(outcomes)

    if log_dir is not None and log_dir.exists():
        print(f"[nakon] logs: {log_dir}")

    strict = args.strict or os.getenv("NAKON_STRICT")
    if failures and strict:
        return 1
    return 0


def cmd_diff(args) -> int:
    _load_env()
    from .catalog.source import MySQLCatalog
    from .deploy.bundle import Bundle
    from .hashing import sha256_text

    bundle = Bundle.load(args.bundle)
    provenance = bundle.manifest.get("provenance", {})
    recorded = provenance.get("configurations", {})
    recorded_attachments = provenance.get("attachments", {})

    source = MySQLCatalog.from_env()
    changes = []
    try:
        for config_id, entry in sorted(recorded.items(), key=lambda kv: kv[1]["name"]):
            live = source.fetch(entry["name"])
            if live is None:
                changes.append(("removed", entry["name"], "no longer in the catalog"))
                continue
            if sha256_text(live["script"]) != entry["script_sha256"]:
                changes.append(("changed", entry["name"], "script differs"))
            if (live.get("run_as") or "root") != entry["run_as"]:
                changes.append(
                    ("changed", entry["name"],
                     f"run_as {entry['run_as']} -> {live.get('run_as')}")
                )
            if (live.get("type") or "bash").lower() != entry["type"]:
                changes.append(
                    ("changed", entry["name"], f"type {entry['type']} -> {live.get('type')}")
                )
            live_ids = {str(a["id"]) for a in live["attachments"]}
            bundled = {
                aid for aid, a in recorded_attachments.items() if a["configuration"] == entry["name"]
            }
            for gone in sorted(bundled - live_ids):
                changes.append(
                    ("removed", entry["name"],
                     f"attachment {recorded_attachments[gone]['original_name']} deleted")
                )
            for added in sorted(live_ids - bundled):
                name = next(a["original_name"] for a in live["attachments"] if str(a["id"]) == added)
                changes.append(("added", entry["name"], f"new attachment {name}"))
    finally:
        source.close()

    print(f"[nakon] bundle {short(bundle.bundle_id)} vs the live catalog "
          f"({len(recorded)} configuration(s) recorded)")
    if not changes:
        print("[nakon] no drift — the catalog still matches this bundle.")
        return 0

    print(f"[nakon] {len(changes)} difference(s):")
    for kind, name, detail in changes:
        print(f"[nakon]   {kind:8} {name}: {detail}")
    print("[nakon] Deploying this bundle still applies the ORIGINAL content above; "
          "run `nakon build` to pick up the changes.")
    return 2 if args.exit_code else 0


def cmd_show(args) -> int:
    from .deploy.bundle import Bundle

    bundle = Bundle.load(args.bundle)
    manifest = bundle.manifest

    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    print(f"bundle       {manifest['bundle_id']}")
    print(f"fingerprint  {manifest['source_fingerprint']}")
    print(f"built        {manifest.get('built_at')} by {manifest.get('built_by')}")
    print(f"nakon        {manifest.get('nakon_version')}")
    src = manifest.get("source", {})
    print(f"catalog      {src.get('database')}@{src.get('db_host')}  media {src.get('vulndb_ui_url')}")

    print(f"\nplans ({len(manifest['plans'])})")
    for plan_id, plan in manifest["plans"].items():
        machines = [
            entry["name"]
            for entry in manifest.get("inventory", [])
            if manifest["requests"].get(entry["request_key"], {}).get("plan_id") == plan_id
        ]
        print(f"  {short(plan_id)}  {plan['platform']}  {len(plan['steps'])} step(s)  "
              f"{plan['tarball_bytes']} bytes")
        print(f"    machines: {', '.join(machines) or '(none in this bundle\'s inventory)'}")
        for step in plan["steps"]:
            if step["kind"] == "package":
                print(f"    {step['index']:>3}  package  {step['package']}")
            else:
                extra = f"  vars={step['vars']}" if step["vars"] else ""
                files = (
                    "  files=" + ",".join(a["original_name"] for a in step["attachments"])
                    if step["attachments"] else ""
                )
                print(f"    {step['index']:>3}  {step['type']:<10} {step['name']}"
                      f"  run_as={step['run_as']}{extra}{files}")
    return 0


def cmd_randomize(args) -> int:
    _load_env()
    from .catalog.randomize import main as randomize_main

    randomize_main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nakon",
        description="Build reproducible deployment bundles from the vulndb catalog, and apply them.",
        epilog="`build` needs the vulndb; `deploy` needs only paramiko and a bundle.",
    )
    parser.add_argument("--version", action="version", version=f"nakon {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="resolve the catalog into a content-addressed bundle")
    p_build.add_argument("--config", default="config.json", help="machine list (default: config.json)")
    p_build.add_argument("--out", default="bundles", help="bundle directory (default: ./bundles)")
    p_build.add_argument("--rebuild", action="store_true",
                         help="build even if a bundle matching the current catalog exists")
    p_build.add_argument("--export", metavar="TARBALL",
                         help="also write the whole bundle to this .tar.gz for archival")
    p_build.add_argument("--json", action="store_true",
                         help="print a JSON summary as the final line, for orchestrators")
    p_build.set_defaults(func=cmd_build)

    p_deploy = sub.add_parser("deploy", help="apply a built bundle to the machines in config.json")
    p_deploy.add_argument("--bundle", required=True, help="path to a bundle directory")
    p_deploy.add_argument("--config", default="config.json",
                          help="machine list, supplying addresses and credentials")
    p_deploy.add_argument("--jobs", type=int, default=1, help="machines to deploy in parallel")
    p_deploy.add_argument("--strict", action="store_true",
                          help="exit non-zero if any configuration failed (same as NAKON_STRICT=1)")
    p_deploy.add_argument("--keep-remote", action="store_true",
                          help="leave the unpacked plan on each target for debugging "
                               "(it lists every vulnerability planted on that box)")
    p_deploy.add_argument("--only", nargs="+", metavar="NAME",
                          help="deploy only these machines, by name or IP")
    p_deploy.add_argument("--no-logs", action="store_true",
                          help="don't save per-machine logs under the bundle's runs/ directory")
    p_deploy.set_defaults(func=cmd_deploy)

    p_diff = sub.add_parser("diff", help="compare a bundle against the live catalog")
    p_diff.add_argument("--bundle", required=True)
    p_diff.add_argument("--exit-code", action="store_true", help="exit 2 when drift is found")
    p_diff.set_defaults(func=cmd_diff)

    p_show = sub.add_parser("show", help="describe a bundle (offline)")
    p_show.add_argument("--bundle", required=True)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    p_rand = sub.add_parser("randomize", help="generate a config.json from the catalog")
    p_rand.set_defaults(func=cmd_randomize)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except NakonError as exc:
        print(f"[nakon] error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[nakon] interrupted", file=sys.stderr)
        return 130
