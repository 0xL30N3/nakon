# nakon

Vulndb client for the dawgsec competition team.

`nakon` reads a list of target machines and the configurations assigned to each one from a
MySQL database, then SSHes into every machine and runs each configuration's script.

It's built for standing up intentionally-vulnerable boxes for blue/red team exercises (e.g.
CCDC-style competitions) from a central catalog instead of configuring each box by hand.

## How it works

```
config.json ──┐
              ├─▶ deploy.py ─▶ resolves + runs each machine's configurations over SSH
.env + MySQL ─┘
```

- **MySQL database** stores `configurations` — each row is a named, reusable unit made of
  exactly one script:
  - `name` — unique kebab-case slug, e.g. `suid-find`, `apache`, `create-user`
  - `platform` (`linux`/`windows`/`other`), `category` (`misconfiguration`/`service`/
    `vulnerability`) — descriptive metadata, not read by `nakon`
  - `type` (`bash`/`powershell`/`command`), `script`, `run_as` (free text, e.g. `root`)
  - `depends_on` — JSON array of other configurations (optionally with variables) or raw
    package names this one needs first; see below
  - plus an `attachments` table (managed by `vulndb-ui`, see below) for files a configuration's
    script needs alongside it (payloads, installers, PoCs)
- **`config.json`** lists target machines and which configurations to run on each.
- **`deploy.py`** is the entry point: for each machine, it resolves the full dependency graph
  of its assigned configurations, installs any raw package fallbacks, then runs every
  configuration's script in dependency-first order over a single SSH connection (via
  `paramiko`), elevating with `sudo` when `run_as` is `"root"`.

A vulnerability and a service install are both just configurations — nothing is conditional on
some other configuration happening to need it. List a configuration directly on a machine and
it always runs.

### Configurations and `depends_on`

Each configuration is exactly one script. Multi-step setups aren't expressed as multiple
scripts under one entry — if a step is reusable, it becomes its own configuration, pulled in
via `depends_on`. This is also how you chain vulnerabilities for multi-step entry paths (e.g.
an `apache-runas-root` misconfig as a dependency of a `web-rce` configuration that finishes the
chain).

`depends_on` is a JSON array. Each entry is either:
- a bare string — a raw package/service name with no matching configuration, installed via the
  remote host's package manager (`apt-get`/`dnf`/`yum`) as a fallback, or
- an object naming another configuration, optionally with variables to pass it:
  ```json
  { "name": "create-user", "vars": { "USERNAME": "splunk" } }
  ```

A configuration's `script` can reference `vars` as real shell variables — `deploy.py` prepends
them as shell-quoted exports before uploading the script:

```bash
#!/bin/bash
useradd -m "$USERNAME"
```

The same configuration requested with different `vars` runs once per distinct set of values;
requested twice with identical `vars` (or none), it only runs once.

`vulndb-ui` ships a few reusable building blocks meant to be depended on rather than
duplicated — `install-package` (takes `PACKAGE`), `create-user` (takes `USERNAME`), and
`enable-service` (takes `SERVICE`). Prefer depending on these over hand-rolling
apt/dnf/yum branching in a new configuration's own script.

### Attachments

A configuration can have file attachments managed through `vulndb-ui` (backed by MinIO) — a
malicious config file, an installer binary, a PoC, etc. For each configuration that has
attachments, `deploy.py` downloads them from `vulndb-ui` (`GET
/api/attachments/:id/download`, a redirect to a short-lived presigned MinIO URL) into a local
temp directory, then SFTPs them onto the target machine's `/tmp` before running the script.
The script can reference them by relative path since `deploy.py` `cd`s into `/tmp` before
running it:

```bash
cp ./malicious.conf /etc/vsftpd.conf
```

This requires `VULNDB_UI_URL` to point at a reachable `vulndb-ui` instance (see below) — it's
only used for attachment downloads, everything else talks to MySQL directly.

## Setup

### 1. Install dependencies

```bash
pip install mysql-connector-python paramiko python-dotenv requests
```

### 2. Configure the database connection

Create a `.env` file in the project root (this is gitignored):

```env
host=127.0.0.1
user=nakon
password=your-db-password
database=vulndb
VULNDB_UI_URL=http://10.0.0.118:3000
```

### 3. Configure target machines

Copy the example config and fill in your environment's machines:

```bash
cp config-example.json config.json
```

```json
{
  "machines": [
    {
      "id": 1,
      "name": "web01",
      "ip": "10.67.2.10",
      "os": "ubuntu22",
      "user": "root",
      "password": "ChangeMe123!",
      "configurations": [
        "apache",
        "suid-find",
        { "name": "splunk", "vars": { "USERNAME": "splunk2" } }
      ]
    }
  ]
}
```

Each entry under `configurations` is either a bare configuration name, or an object with a
`name` and a `vars` override. `config.json` is gitignored since it contains live credentials
and target IPs.

### 4. Run it

```bash
python deploy.py
```

This will, for every machine in `config.json`:

1. Resolve its assigned configurations' full `depends_on` graph (deduped, dependency-first)
2. SSH in, install any raw package-manager fallbacks, then upload and run each resolved
   configuration's script in order (with `sudo` if `run_as` is `"root"`)

## Adding a new configuration

Insert a row into the `configurations` table (e.g. via the `vulndb-ui` admin app) — there's no
plugin system to register. A configuration that other configurations should be able to depend
on (a reusable building block like `create-user`) just needs a unique `name` and a `script`
that reads any `vars` it needs as shell variables.

## Security note

This tool intentionally deploys vulnerable configurations and SSHes around with plaintext
passwords from `config.json`/`.env`. It's meant for isolated competition/lab environments only —
never point it at production infrastructure or expose these credentials.
