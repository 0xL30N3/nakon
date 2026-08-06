"""Deprecated compatibility shim — the implementation lives in nakon/catalog/randomize.py.

This existed because tezcatlipoca's create-competition.py loaded this file *by path*
(importlib.util.spec_from_file_location) and then reached through it for `mysql.connector`,
`load_configurations`, `os_to_platform` and `pick_configurations`. It now imports
`nakon.catalog.randomize` directly, so nothing in the toolchain needs this file.

It is kept for one release in case something out of tree still loads it. Import from
`nakon.catalog.randomize`, or run `python3 -m nakon randomize`.
"""

import warnings

# Loaded via importlib.util.spec_from_file_location by older callers, which does not add this
# file's directory to sys.path the way `python3 -m nakon` or direct execution would — without
# this, `import nakon.catalog` below fails with ModuleNotFoundError when loaded by path.
import sys
from pathlib import Path

warnings.warn(
    "randomize_config is deprecated; import nakon.catalog.randomize instead.",
    DeprecationWarning,
    stacklevel=2,
)

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
