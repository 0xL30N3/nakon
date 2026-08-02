"""Parse the ##nakon marker stream and report.tsv into a per-step result model.

Two sources for the same information, on purpose. report.tsv is authoritative and is pulled
back after the run, but if the channel dies mid-deploy there is no file to pull — so the live
marker stream is parsed as it arrives and used as a fallback. Either way the operator gets a
per-configuration failure list, the same thing the old deploy.py printed.
"""

from ..gen import MARKER_BEGIN, MARKER_DONE, MARKER_RC


class StepResult:
    __slots__ = ("index", "name", "kind", "rc", "seconds")

    def __init__(self, index, name, kind, rc=None, seconds=None):
        self.index = index
        self.name = name
        self.kind = kind
        self.rc = rc
        self.seconds = seconds

    @property
    def failed(self) -> bool:
        return self.rc is not None and self.rc != 0

    @property
    def incomplete(self) -> bool:
        return self.rc is None


class RunProgress:
    """Consumes output lines, tracking which step is running and how each finished."""

    def __init__(self, total: int = 0):
        self.total = total
        self.steps = {}
        self.order = []
        self.current = None
        self.done = False

    def feed(self, line: str) -> bool:
        """Absorb one output line. Returns True if it was a nakon marker (not real output)."""
        stripped = line.strip()

        if stripped.startswith(MARKER_BEGIN):
            # ##nakon begin <idx> <kind> <name>
            parts = stripped[len(MARKER_BEGIN):].strip().split(None, 2)
            if len(parts) >= 3:
                index, kind, name = parts[0], parts[1], parts[2]
                result = StepResult(index, name, kind)
                self.steps[index] = result
                self.order.append(index)
                self.current = result
            return True

        if stripped.startswith(MARKER_RC):
            # ##nakon rc <idx> <rc> <secs>
            parts = stripped[len(MARKER_RC):].strip().split()
            if len(parts) >= 2:
                index = parts[0]
                result = self.steps.get(index)
                if result is not None:
                    result.rc = _int_or_none(parts[1])
                    result.seconds = _int_or_none(parts[2]) if len(parts) > 2 else None
                if self.current is not None and self.current.index == index:
                    self.current = None
            return True

        if stripped.startswith(MARKER_DONE):
            self.done = True
            self.current = None
            return True

        return False

    def merge_report(self, text: str) -> None:
        """Overlay the authoritative report.tsv on top of what the stream showed."""
        for line in text.splitlines():
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4:
                continue
            index, name, kind, rc = fields[0], fields[1], fields[2], _int_or_none(fields[3])
            seconds = _int_or_none(fields[4]) if len(fields) > 4 else None
            result = self.steps.get(index)
            if result is None:
                result = StepResult(index, name, kind)
                self.steps[index] = result
                self.order.append(index)
            result.rc = rc
            result.seconds = seconds

    def results(self) -> list:
        return [self.steps[i] for i in self.order]

    def failures(self) -> list:
        return [r for r in self.results() if r.failed]

    def incomplete(self) -> list:
        return [r for r in self.results() if r.incomplete]


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_duration(seconds) -> str:
    if seconds is None:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"
