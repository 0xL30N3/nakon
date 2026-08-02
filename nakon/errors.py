"""Exception types shared by the build and deploy halves of nakon."""


class NakonError(Exception):
    """Base for every error nakon raises deliberately (as opposed to a bug)."""


class CycleError(NakonError):
    """A configuration's depends_on graph contains a cycle."""


class UnknownConfigurationError(NakonError):
    """A requested name matched no configuration and can't be a package fallback."""


class PlatformMismatchError(NakonError):
    """A configuration's script `type` can't run on the machine's platform.

    The old deploy.py would happily hand a powershell script to bash on Ubuntu; catching
    this at build time is the whole point of resolving ahead of the deploy.
    """


class BundleError(NakonError):
    """A bundle is missing, malformed, or doesn't cover what config.json asks for."""


class CatalogError(NakonError):
    """The vulndb catalog (MySQL) or attachment store (vulndb-ui/MinIO) failed."""
