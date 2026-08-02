"""Compatibility shim — the implementation now lives in nakon/catalog/randomize.py.

Kept at the repo root because tezcatlipoca's create-competition.py loads this file *by path*
(importlib.util.spec_from_file_location) and then reaches into it for `mysql.connector`,
`load_configurations`, `os_to_platform` and `pick_configurations`. Moving the module without
leaving this behind breaks competition generation before it starts.

Prefer `python3 -m nakon randomize`.
"""

# create-competition.py calls `nr.mysql.connector.connect(...)` through this module, so the
# attribute has to exist here even though nothing in this file uses it directly.
import mysql.connector  # noqa: F401

# Loaded via importlib.util.spec_from_file_location (see the module docstring), which does not
# add this file's directory to sys.path the way `python3 -m nakon` or direct execution would —
# without this, `import nakon.catalog` below fails with ModuleNotFoundError when loaded by path.
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nakon.catalog.randomize import (  # noqa: F401,E402
    DIFFICULTY_MAX,
    DIFFICULTY_MIN,
    EXCLUDED_NAMES,
    collect_machines,
    dep_names,
    fetch_terraform_machines,
    find_terraform_dir,
    load_configurations,
    main,
    os_to_platform,
    pick_configurations,
    prompt_int,
    prompt_machine,
    service_closure,
)

if __name__ == "__main__":
    main()
