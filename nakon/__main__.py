"""`python3 -m nakon` — the only entry point. No console_scripts, so the scoring engine can
run nakon from a plain directory copy with nothing installed but paramiko."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
