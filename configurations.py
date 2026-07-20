"""
Resolves and installs `configurations` — the unified table of vulnerabilities/misconfigs
and service installs. Each configuration is exactly one script, optionally depending on
other configurations (with variables) or raw packages.

Called from deploy.py.
"""

import json
import shlex


class CycleError(Exception):
    pass


class UnknownConfigurationError(Exception):
    pass


def fetch(cursor, name: str):
    """Look up a configuration row by name. Returns a dict, or None if not found."""
    cursor.execute(
        "SELECT id, name, script, run_as, type, depends_on FROM configurations WHERE name = %s",
        (name,),
    )
    row = cursor.fetchone()
    if row is None:
        return None

    id_, name_, script, run_as, cfg_type, depends_on_raw = row
    if depends_on_raw is None:
        depends_on = []
    elif isinstance(depends_on_raw, list):
        depends_on = depends_on_raw
    else:
        depends_on = json.loads(depends_on_raw)

    cursor.execute(
        "SELECT id, original_name FROM attachments WHERE configuration_id = %s",
        (id_,),
    )
    attachments = [{"id": a_id, "original_name": original_name} for a_id, original_name in cursor.fetchall()]

    return {
        "id": id_,
        "name": name_,
        "script": script,
        "run_as": run_as,
        "type": cfg_type,
        "depends_on": depends_on,
        "attachments": attachments,
    }


def _normalize(entry):
    """A requested/depends_on entry is either a bare string or {"name", "vars"}."""
    if isinstance(entry, str):
        return entry, {}
    return entry["name"], entry.get("vars", {})


def resolve(cursor, requested: list):
    """
    Resolve a machine's requested configurations (plus their transitive depends_on) into:
      - ordered: list of configuration dicts (each with a "vars" key merged in), in
        dependency-first order, deduped by (name, vars).
      - fallback_packages: list of raw strings that didn't match any configuration name,
        to be installed via the generic package manager instead.
    """
    ordered = []
    fallback_packages = []
    visited = set()
    visiting = set()

    def visit(name, var_values):
        key = (name, tuple(sorted(var_values.items())))
        if key in visited:
            return

        row = fetch(cursor, name)
        if row is None:
            # Mark visited even in the fallback branch so a package requested twice
            # (e.g. directly and via depends_on) isn't installed twice.
            visited.add(key)
            if var_values:
                raise UnknownConfigurationError(
                    f"'{name}' has variables but doesn't match any configuration"
                )
            fallback_packages.append(name)
            return

        if key in visiting:
            raise CycleError(f"Circular dependency detected involving '{name}'")
        visiting.add(key)

        for dep_entry in row["depends_on"]:
            dep_name, dep_vars = _normalize(dep_entry)
            visit(dep_name, dep_vars)

        visiting.discard(key)
        visited.add(key)
        ordered.append({**row, "vars": var_values})

    for item in requested:
        name, var_values = _normalize(item)
        visit(name, var_values)

    return ordered, fallback_packages


def install_package(client, password: str, package: str):
    """
    Install a single package using the remote host's package manager.
    Auto-detects apt-get, dnf, or yum.

    The whole sequence runs under a single `sudo -S bash -c '<quoted>'` so that exactly
    one password prompt consumes the single password line we send on stdin. The package
    name is shlex-quoted so a hostile/odd name can't inject into the shell command.
    """
    # Boot-time unattended-upgrades holds the dpkg lock for up to ~2 min — wait it out
    # before apt-get (mirrors the same loop used in Terraform Step A for the scoring engine).
    dpkg_wait = (
        "for i in $(seq 1 60); do "
        "fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; "
        "sleep 2; done; "
    )
    safe_pkg = shlex.quote(package)
    inner = (
        f"if command -v apt-get > /dev/null; then "
        f"{dpkg_wait}"
        f"DEBIAN_FRONTEND=noninteractive apt-get install -y {safe_pkg}; "
        f"elif command -v dnf > /dev/null; then "
        f"dnf install -y {safe_pkg}; "
        f"elif command -v yum > /dev/null; then "
        f"yum install -y {safe_pkg}; "
        f"else echo 'No supported package manager found' >&2; exit 1; fi"
    )
    # Quote the whole script body as a single argv element to sudo so only the first
    # sudo call prompts (the inner apt/dnf/yum already run as root under that sudo).
    cmd = f"sudo -S bash -c {shlex.quote(inner)}"
    stdin, stdout, stderr = client.exec_command(cmd)
    stdin.write(password + "\n")
    stdin.flush()
    stdin.channel.shutdown_write()

    out = stdout.read().decode()
    err = stderr.read().decode()
    if err:
        print(f"  [pkg:{package}] stderr: {err}")
    return out, err
