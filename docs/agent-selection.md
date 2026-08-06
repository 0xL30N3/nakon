# Choosing a competition's misconfigurations with an agent

This describes how an agent — Claude Code, hermes, anything that can run a command and read
JSON — picks the misconfigurations for a competition. It is deliberately tool-agnostic: the
interface is a CLI and two JSON files, nothing else.

The division of labour matters. **nakon supplies context and verification; the agent supplies
judgement.** Nothing in nakon ranks configurations or decides what makes a good competition —
it answers "what is available?" and "is what you picked coherent?" and leaves the choosing to
you. There is no model call anywhere in the toolchain.

## The loop

```
  nakon catalog list --json          →   read what exists
           ↓
  (you decide)                       →   pick a set per box, using the rubric below
           ↓
  nakon catalog check --box-vulns    →   have the machine check your work
           ↓
  competitions/<id>/box_vulns.json   →   write the answer where the deploy reads it
```

## 1. Read the catalog

```bash
python3 -m nakon catalog list --platform linux --json
```

Every record comes back whole — `name`, `description`, `category`, `platform`, `depends_on`,
the script itself, and attachment names. Read the script. The description says what a
configuration is *for*; the script is the only thing that says exactly what it does.

Useful filters:

```bash
python3 -m nakon catalog list --category misconfiguration      # just the vulns
python3 -m nakon catalog list --search ssh                     # name, description and script
python3 -m nakon catalog show suid-find www-data-shell         # detail + what each pulls in
```

`show` is worth using before committing to anything unfamiliar: it resolves the configuration
and tells you which *other* configurations, packages and services come with it.

By default the listing hides `install-package`, `create-user` and `enable-service`. Those are
reusable building blocks that other configurations pull in through `depends_on`. Never select
one directly — it plants a step that does nothing.

### Where it reads from

`--source auto` (the default) uses vulndb-ui over HTTP when `VULNDB_UI_URL` is set, and falls
back to MySQL. The HTTP path needs no database credentials, which is usually the right one for
an agent. `--source mysql` forces the direct path.

## 2. Choose

There is no scoring formula. Some things that make a set good:

- **Keep the box plausible for its role.** A mail server that has `dovecot` and `postfix`
  misconfigured is a scenario; one carrying an unrelated grab-bag is a puzzle.
- **Mix obvious and subtle.** Something a competent defender finds in the first ten minutes
  (`ssh-root-login`), something they find only by looking properly (`bad-perms-userConfig`),
  and ideally one that rewards actually reading the box.
- **Don't repeat a technique.** `suid-find`, `suid-vim` and `suid-nano` are one idea three
  times. Pick one unless the point is specifically to test thoroughness.
- **Watch what a misconfiguration drags in.** `unauthorized-ftp-server` installs vsftpd. If the
  competition also scores FTP, the two interact — check the description and the script.
- **Respect the box's scored services.** A misconfiguration that stops a scored service is a
  different exercise from one that leaves it running. Read the description before assuming.
- **`splunk` and `roundcube` are slow.** Splunk pulls a large installer per box and roundcube
  drags in apache, mariadb and php. Both are excluded from random picking for that reason. They
  are fine to choose deliberately; just know a deploy will take considerably longer.
- **Scale with difficulty, not just count.** The `Compfile` difficulty (1–10) historically drove
  *how many* were planted. Choosing well means the count matters less than the mix.

## 3. Check

```bash
python3 -m nakon catalog check --platform linux \
    --select suid-find,ssh-root-login,www-data-shell
```

or, for a whole competition:

```bash
python3 -m nakon catalog check \
    --box-vulns    competitions/cde-2026/box_vulns.json \
    --box-services competitions/cde-2026/box_services.json
```

Add `--json` for a machine-readable report. Exit status is 0 when clean, 1 when there are
errors, and `--strict` also fails on warnings.

**Errors** — the selection will not do what you meant:

| code | meaning |
| --- | --- |
| `unknown-name` | No configuration by that name. It would be treated as a raw package name and quietly installed with apt/dnf, planting nothing. This is what a typo looks like. |
| `building-block` | `install-package` / `create-user` / `enable-service` requested directly. |
| `platform-mismatch`, `type-mismatch` | A windows configuration on a linux box, or vice versa. |
| `unresolvable` | A dependency cycle, or a dependency that can't be satisfied. |

**Warnings** — it will deploy, but look again:

| code | meaning |
| --- | --- |
| `no-op` | Empty script and no dependencies. The step runs and does nothing. |
| `duplicate` | The same name selected twice on one box. |
| `implicit-services` | A dependency pulled in a service nobody selected. Usually fine, occasionally a surprise. |
| `no-description` | Nothing to go on but the slug and the script. Worth fixing — see below. |

## 4. Write the answer

Two files per competition, both keyed by **box type**, not per team — every team must defend
the identical set for the scoring engine's wildcard IP checks to work:

`competitions/<id>/box_vulns.json` — what gets planted:

```json
{
  "web01": ["ssh-root-login", "www-data-shell", "bad-perms-userConfig"],
  "mail01": ["suid-find", "unauthorized-ftp-server"]
}
```

`competitions/<id>/box_services.json` — the scoreable services on each box:

```json
{
  "web01": ["apache"],
  "mail01": ["postfix", "dovecot"]
}
```

Either file on its own is enough to pin a competition. If neither exists,
`create-competition.py` randomises and then writes both, so a re-run reproduces the same
competition exactly.

## Improving the catalog as you go

> **Current gap (as of 2026-08-06):** only 30 of 61 catalog rows have a description. The newer
> linux misconfigurations — ids 36–66 (`ssh-x11-forwarding`, `suid-python`, `apparmor-disabled`,
> `selinux-permissive`, `dnf-gpgcheck-disabled`, …) — were added with none, so `check` warns on any
> selection touching them. Writing those descriptions is genuinely useful, unclaimed work; see below.

A configuration with no description is one nothing can reason about. If you work out what one
does by reading its script, write that down:

```bash
vulndb-cli describe www-data-shell \
  "Gives the www-data service account an interactive login shell in /etc/passwd, so any web
   compromise becomes a usable foothold. Real systems leave it as /usr/sbin/nologin." --yes
```

A good description says what it changes on the box, why a real system would plausibly have it,
and what a defender would notice. That prose is the only place difficulty, realism and
cross-configuration couplings are recorded — there are no columns for them.

To add a configuration outright:

```bash
vulndb-cli create --file new-misconfig.json --yes
```

Both commands write to a catalog the whole team shares and the API has no authentication, so
they show what they are about to do and ask first; `--yes` is required when stdin isn't a
terminal.
