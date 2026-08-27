"""serpent.compiler: Python contract source -> resolved, typed IR + diagnostics.

Stays INSIDE the zero-dep walk (`tests/unit/test_core_zero_dep.py`): it must
reach `stellar_sdk` only transitively through `serpent.spec`, and this
subpackage never imports `stellar_sdk` directly (`serpent.spec` is the
sanctioned transitive path -- individual submodules, e.g. `types_.py`, import
it directly when they need it).

The public surface is the diagnostics core (Task 1), the frozen `SPT####` code
registry (`serpent.compiler.codes`), and `compile_module` (Task 10) -- the one
entry point that turns contract source into resolved, typed IR plus the facts
sub-plan D consumes.
"""

from serpent.compiler import codes  # noqa: F401 -- imported first: diagnostics.py needs it
from serpent.compiler.diagnostics import (
    CompileError,
    Diagnostic,
    Diagnostics,
    Loc,
    LocKind,
)
from serpent.compiler.frontend import (
    CompiledModule,
    LiteralInventory,
    SpecInputs,
    compile_module,
)

__all__ = [
    "CompileError",
    "CompiledModule",
    "Diagnostic",
    "Diagnostics",
    "LiteralInventory",
    "Loc",
    "LocKind",
    "SpecInputs",
    "compile_module",
]
