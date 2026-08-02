"""nakon — builds reproducible deployment bundles from the vulndb catalog and applies them.

Two halves that deliberately run in different places:

  nakon build   — wherever the vulndb is reachable. Resolves every machine's configuration
                  graph, downloads attachment media, and writes a self-contained,
                  content-addressed bundle. Needs mysql-connector + requests.

  nakon deploy  — wherever the target boxes are reachable. Pushes a bundle and runs it.
                  Needs paramiko and nothing else: no database, no vulndb-ui, no .env.

That split is the point. A saved bundle deploys byte-identical scripts and media however
much the catalog has moved on since, and the deploy host never holds vulndb credentials.
"""

__version__ = "2.0.0"
