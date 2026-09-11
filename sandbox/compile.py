"""Superseded by the CLI (M1-G): `stellar serpent build <contract.py>`.

Kept as a pointer for anyone with the old command in their shell history.
`build_file` is the public API this used to call by hand; the CLI adds the
sha256, the declared-vs-target protocol line, and `--meta`.
"""

import sys

from serpent.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["build", *sys.argv[1:]]))
