# nakon

Vulndb client for the dawgsec competition team.

`nakon` turns the vulndb configuration catalog into a **content-addressed bundle**, then
applies that bundle to target machines over SSH. It's built for standing up
intentionally-vulnerable boxes for blue/red team exercises (e.g. CCDC-style competitions)
from a central catalog instead of configuring each box by hand.

## Two commands, two places

```
                  (needs the vulndb)                    (needs the boxes)
config.json ──▶ nakon build ──▶ bundles/<id>/ ──▶ nakon deploy ──▶ targets
.env + MySQL ──┘   + vulndb-ui/MinIO media          paramiko only
```

- **`nakon build`** runs wherever the vulndb is reachable — normally your laptop. It resolves
  every machine's dependency graph, downloads attachment media, and writes a self-contained
  bundle of flat, generated scripts.
- **`nakon deploy`** runs wherever the target boxes are reachable — for competitions, the
  scoring engine, since it's the only host that routes into the isolated team subnets. It
  needs `paramiko` and a bundle. **No database, no vulndb-ui, no `.env`.**

Two things fall out of that split:

1. **Saved competitions replay exactly.** A bundle pins script *content*, not configuration
   *names*. Re-deploying last month's bundle applies last month's scripts even if someone has
   since edited them in vulndb-ui. `nakon diff` tells you when that has happened.
2. **The deploy host never holds vulndb credentials.** Previously the driver copied `.env`,
   with the database password, onto the scoring engine.

### What a bundle does *not* pin

Your scripts and media — not the internet. `apache`, `bind`, `nginx`, `ssh` and
`install-package` all still call `apt-get install`, and `splunk` still downloads ~500 MB from
`download.splunk.com`. A bundle is reproducible in its *inputs*; the deploy still needs
working DNS, NAT and package mirrors. It is not an offline install.

## The catalog

A **MySQL database** stores `configurations` — each row is a named, reusable unit made of
exactly one script:

- `name` — unique kebab-case slug, e.g. `suid-find`, `apache`, `create-user`
- `platform` (`linux`/`windows`/`other`), `category` (`misconfiguration`/`service`/
  `vulnerability`)
- `type` (`bash`/`powershell`/`command`), `script`, `run_as` (e.g. `root`)
- `depends_on` — JSON array of other configurations (optionally with variables) or raw
  package names this one needs first
- plus an `attachments` table (managed by `vulndb-ui`, backed by MinIO) for files a script
  needs alongside it — payloads, installers, PoCs

A vulnerability and a service install are both just configurations. List one on a machine and
it always runs.

### `depends_on`

Each entry is either:

- a bare string — a raw package name with no matching configuration, installed via the remote
  host's package manager (`apt-get`/`dnf`/`yum`/`apk`), or
- an object naming another configuration, optionally with variables:
  ```json
  { "name": "create-user", "vars": { "USERNAME": "splunk" } }
  ```

A configuration's `script` references `vars` as ordinary shell variables:

```bash
#!/bin/bash
useradd -m "$USERNAME"
```

The same configuration requested with different `vars` runs once per distinct set; requested
twice identically, it runs once. `vulndb-ui` ships reusable blocks meant to be depended on
rather than duplicated — `install-package` (`PACKAGE`), `create-user` (`USERNAME`),
`enable-service` (`SERVICE`). Prefer those over hand-rolling apt/dnf branching.

### Attachments

Attachments are downloaded at **build** time and travel inside the bundle. Each step gets its
own working directory containing its attachments, so a script refers to them by relative path:

```bash
cp ./malicious.conf /etc/vsftpd.conf
```

(Before bundles, attachments were dropped in `/tmp` and the script ran with `/tmp` as its
working directory. The relative-path contract is unchanged; a script that hardcoded
`/tmp/<name>` instead would now break, and `nakon build` warns if it finds one.)

## What a bundle looks like

```
bundles/
  .cache/attachments/<sha256>       # download cache — deletable, never referenced by a manifest
  <bundle-id>/
    manifest.json                   # provenance, request→plan map, per-step detail
    blobs/<sha256>                  # scripts and media, deduped
    plans/<plan-id>/
      run.sh | run.ps1              # generated, straight-line driver
      steps/NNN-<name>.sh           # each catalog script, byte-for-byte
      vars/NNN.env                  # shell-quoted variables for that step
      files/NNN/                    # that step's attachments = that step's working directory
    plans/<plan-id>.tar.gz          # per-plan transport slice
```

**Plans are keyed by the request** — platform plus the ordered configuration list — never by
machine name. Every team gets the same list per box type, so `web01-team101/102/103` share
one plan, and a bundle built from one team's `config.json` deploys all of them.

Nothing is interpreted on the target. `run.sh` is flat generated bash you can read; script
bodies are never inlined into it, so a configuration's own `set -e` or `exit 0` stays inside
that configuration and one failure never aborts the rest of the box.

## Setup

```bash
pip install mysql-connector-python paramiko python-dotenv requests   # build host
pip install paramiko                                                 # deploy host only
```

Create `.env` in the project root (gitignored) — **build side only**:

```env
host=127.0.0.1
user=nakon
password=your-db-password
database=vulndb
VULNDB_UI_URL=http://10.0.0.118:3000
```

`config.json` lists the target machines (gitignored — live credentials and IPs):

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

`config-example.json` is a starting point. Generate one from the catalog with
`python3 -m nakon randomize`.

## Usage

```bash
python3 -m nakon build                       # -> bundles/<id>/, or reuses a matching one
python3 -m nakon build --rebuild             # ignore the cache
python3 -m nakon build --export cde-2026.tar.gz   # archive it alongside the competition

python3 -m nakon show   --bundle bundles/<id>     # what's in it (offline)
python3 -m nakon diff   --bundle bundles/<id>     # has the catalog moved since?

python3 -m nakon deploy --bundle bundles/<id>
python3 -m nakon deploy --bundle bundles/<id> --jobs 4 --strict
```

`build` prints whether it built fresh or reused a cached bundle. The cache key is the catalog
state, so re-running after no vulndb changes costs one round of queries.

Useful `deploy` flags:

| flag | effect |
|---|---|
| `--jobs N` | deploy N machines in parallel (default 1) |
| `--strict` | exit non-zero if any configuration failed (same as `NAKON_STRICT=1`) |
| `--only NAME...` | deploy just these machines, by name or IP |
| `--keep-remote` | leave the unpacked plan on each box for debugging |
| `--no-logs` | don't save per-machine logs under `bundles/<id>/runs/` |

A configuration exiting non-zero is reported, not fatal, and one unreachable machine never
stops the others — `--strict` is there for an orchestrator that wants to abort.

### On-target footprint

The plan is unpacked to a `mktemp -d` under `/root` (mode 0700) and **deleted when the run
finishes**. That's deliberate: the bundle lists every vulnerability planted on that box, the
competition's own setup grants the default user passwordless sudo, and team1's disk gets
cloned to every other team. `--keep-remote` opts out; don't leave it on for a live event.

## Adding a configuration

Insert a row into the `configurations` table (e.g. via the `vulndb-ui` admin app) — there's
no plugin system. A configuration other configurations can depend on just needs a unique
`name` and a `script` that reads any `vars` it needs as shell variables. Then rebuild:
`nakon diff` will show you exactly what changed.

## Tests

```bash
python3 -m pytest                # unit tier: no database, no MinIO, no VMs
python3 -m pytest -m proxmox     # live tier: clones real VMs (see tests/proxmox/README.md)
```

The unit tier executes generated `run.sh` files for real to prove step isolation, and asserts
that a team1-only build and a full-competition build produce the same bundle id.

## Windows

Windows configurations generate a `run.ps1` with a real elevation guard — nakon does not
self-elevate over SSH, so the SSH principal must already be an administrator. Transport is
OpenSSH + SFTP with a zip instead of a tarball.

**This path has never run against a real Windows box** — there is no Windows template on the
team's Proxmox, so it is covered by generation-level tests only. Treat the first Windows box
as a bring-up, not a deploy.

## Security note

This tool intentionally deploys vulnerable configurations and SSHes around with plaintext
passwords from `config.json`/`.env`. Built bundles contain the full list of what gets planted
where. It's meant for isolated competition/lab environments only — never point it at
production infrastructure or expose these credentials.
