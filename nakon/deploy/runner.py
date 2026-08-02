"""Per-machine deploy orchestration: push a plan, run it, collect what happened.

Failure policy is inherited from the old deploy.py on purpose: a configuration exiting
non-zero is reported, not fatal — a benign script returning 1 shouldn't abandon a box
mid-competition — and one machine failing entirely (bad credentials, host down) never stops
the others. `--strict` / NAKON_STRICT=1 turns any recorded failure into a non-zero exit for
an orchestrator that wants to abort.
"""

import os
import tempfile
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..hashing import short
from . import ssh
from .report import RunProgress, format_duration

_print_lock = threading.Lock()


class MachineOutcome:
    def __init__(self, machine, plan_id=None):
        self.machine = machine
        self.plan_id = plan_id
        self.progress = RunProgress()
        self.error = None
        self.exit_status = None
        self.log_lines = []

    @property
    def name(self):
        return self.machine["name"]

    def failures(self):
        """(ip, step_name, reason) tuples, matching what deploy.py used to report."""
        if self.error is not None:
            return [(self.machine["ip"], "(machine)", self.error)]
        out = [
            (self.machine["ip"], r.name, f"exit {r.rc}")
            for r in self.progress.failures()
        ]
        out += [
            (self.machine["ip"], r.name, "did not finish (channel closed?)")
            for r in self.progress.incomplete()
        ]
        return out


def deploy_machine(bundle, machine, keep_remote=False, log_dir=None, emit=print) -> MachineOutcome:
    """Deploy one machine's plan. Never raises for a remote failure — records it instead."""
    outcome = MachineOutcome(machine)
    prefix = f"[{machine['name']}]"
    platform = machine["platform"]

    def line(text):
        with _print_lock:
            emit(f"{prefix} {text}")

    client = None
    paths = None
    plan_dir = None
    completed = False

    try:
        plan_id, plan_entry = bundle.plan_for(machine)
        outcome.plan_id = plan_id
        archive = bundle.archive_path(plan_entry)
        step_count = len(plan_entry["steps"])
        outcome.progress.total = step_count

        recorded = bundle.inventory_entry(machine["name"])
        if recorded is not None and recorded.get("ip") != machine["ip"]:
            line(f"note: bundle recorded this machine at {recorded['ip']}, deploying to "
                 f"{machine['ip']} (fine — plans are matched by configuration set, not address)")

        line(f"plan {short(plan_id)} — {step_count} step(s), {archive.stat().st_size} bytes")

        client = ssh.connect(machine)
        paths = ssh.remote_paths(platform)

        bootstrap = (
            ssh.render_bootstrap_ps1(paths["archive"], keep_remote)
            if platform == "windows"
            else ssh.render_bootstrap_sh(paths["archive"], keep_remote)
        )

        ssh.put_file(client, archive, paths["archive"], mode=0o600)
        with tempfile.NamedTemporaryFile("w", suffix=".boot", delete=False, newline="\n") as handle:
            handle.write(bootstrap)
            local_bootstrap = handle.name
        try:
            ssh.put_file(client, local_bootstrap, paths["bootstrap"], mode=0o700)
        finally:
            os.unlink(local_bootstrap)

        # Report framing: everything between the markers is raw report.tsv, not output.
        state = {"in_report": False, "report": []}

        def on_line(text):
            outcome.log_lines.append(text)
            stripped = text.strip()

            if stripped == ssh.REPORT_BEGIN:
                state["in_report"] = True
                return
            if stripped == ssh.REPORT_END:
                state["in_report"] = False
                return
            if state["in_report"]:
                state["report"].append(text)
                return
            if stripped.startswith(ssh.PLANDIR_MARKER):
                state["plan_dir"] = stripped[len(ssh.PLANDIR_MARKER):].strip()
                return

            if outcome.progress.feed(text):
                current = outcome.progress.current
                if current is not None:
                    line(f"step {current.index}/{step_count} {current.kind}: {current.name}")
                else:
                    done = [r for r in outcome.progress.results() if r.rc is not None]
                    if done:
                        last = done[-1]
                        status = "ok" if last.rc == 0 else f"FAILED rc={last.rc}"
                        line(f"  └─ {last.name}: {status} ({format_duration(last.seconds)})")
                return

            if stripped:
                line(f"  {text}")

        def on_idle(seconds):
            current = outcome.progress.current
            where = f"step {current.index} ({current.name})" if current else "bootstrap"
            line(f"… still running: {where}, quiet for {format_duration(int(seconds))}")

        command = ssh.bootstrap_command(platform, paths["bootstrap"])
        outcome.exit_status = ssh.run_streaming(
            client,
            command,
            machine.get("password"),
            on_line=on_line,
            on_idle=on_idle,
        )
        plan_dir = state.get("plan_dir")

        if state["report"]:
            outcome.progress.merge_report("\n".join(state["report"]))
        elif not outcome.progress.results():
            outcome.error = (
                f"no output from the remote plan (exit {outcome.exit_status}) — "
                f"check credentials and sudo access"
            )
        completed = outcome.progress.done or bool(state["report"])

    except Exception as exc:
        outcome.error = str(exc) or exc.__class__.__name__
        line(f"FAILED: {outcome.error}")
        if os.getenv("NAKON_DEBUG"):
            line(traceback.format_exc())

    finally:
        if client is not None:
            # The bootstrap removes its own artifacts on a clean exit; this covers the case
            # where the channel died first and the plan would be left on the box.
            if not completed and not keep_remote and paths is not None:
                ssh.force_cleanup(client, platform, paths, machine.get("password"), plan_dir)
            client.close()

    if log_dir is not None and outcome.log_lines:
        _write_logs(Path(log_dir), outcome)

    return outcome


def _write_logs(log_dir: Path, outcome: MachineOutcome) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    safe = outcome.name.replace("/", "_")
    (log_dir / f"{safe}.log").write_text("\n".join(outcome.log_lines) + "\n")
    rows = [
        f"{r.index}\t{r.name}\t{r.kind}\t{r.rc if r.rc is not None else ''}\t"
        f"{r.seconds if r.seconds is not None else ''}"
        for r in outcome.progress.results()
    ]
    if rows:
        (log_dir / f"{safe}.tsv").write_text("\n".join(rows) + "\n")


def deploy(bundle, machines, keep_remote=False, jobs=1, log_dir=None, emit=print) -> list:
    """Deploy every machine. Returns the list of MachineOutcomes, in input order."""
    if jobs <= 1:
        return [
            deploy_machine(bundle, machine, keep_remote, log_dir, emit)
            for machine in machines
        ]

    # Per-machine work is atomic now, so parallelism is just a thread pool. Output stays
    # readable because every line carries its machine's prefix and printing is locked.
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [
            pool.submit(deploy_machine, bundle, machine, keep_remote, log_dir, emit)
            for machine in machines
        ]
        return [future.result() for future in futures]


def summarize(outcomes: list, emit=print) -> list:
    """Print the end-of-run summary and return the flat failure list."""
    failures = []
    for outcome in outcomes:
        failures.extend(outcome.failures())

    emit("")
    for outcome in outcomes:
        results = outcome.progress.results()
        if outcome.error is not None:
            emit(f"[nakon] {outcome.name}: FAILED — {outcome.error}")
            continue
        bad = len(outcome.progress.failures())
        missing = len(outcome.progress.incomplete())
        status = "all steps ok" if not (bad or missing) else f"{bad} failed, {missing} incomplete"
        emit(f"[nakon] {outcome.name}: {len(results)} step(s), {status}")

    if failures:
        emit(f"\n[nakon] ── {len(failures)} failure(s) ──")
        for ip, name, why in failures:
            emit(f"[nakon]   {ip}  {name}: {why}")
    else:
        emit("\n[nakon] All configurations completed with exit 0.")

    return failures
