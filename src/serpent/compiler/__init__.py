"""serpent.compiler: Python contract source -> resolved, typed IR + diagnostics.

Stays INSIDE the zero-dep walk (`tests/unit/test_core_zero_dep.py`): it must
reach `stellar_sdk` only transitively through `serpent.spec`, and this
subpackage never imports `serpent.spec` directly.

Task 1 lands the diagnostics core and the complete `SPT####` code registry
(`serpent.compiler.codes`). The rest of the public surface --
`compile_module`, `compile_expression` -- lands in later tasks (frontend.py,
Task 10); importing them here before then would break every import of this
package, so they are not re-exported yet.
"""

from serpent.compiler import codes  # noqa: F401 -- imported first: diagnostics.py needs it
from serpent.compiler.diagnostics import (
    CompileError,
    Diagnostic,
    Diagnostics,
    Loc,
    LocKind,
)

__all__ = [
    "CompileError",
    "Diagnostic",
    "Diagnostics",
    "Loc",
    "LocKind",
]
