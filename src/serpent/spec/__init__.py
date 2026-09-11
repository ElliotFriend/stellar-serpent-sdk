"""serpent's contract-spec emission: the build-time XDR half of the toolchain.

**This is the one subpackage that depends on `stellar_sdk`** (declared as the
`spec` extra), per the recorded dependency-boundary decision: spec Sec.7
mandates emitting the Soroban custom sections through the SDK's generated XDR
classes rather than hand-rolling them, and the usage is build-time only, so an
authored contract stays dependency-free at runtime. `serpent` core --
`val`/`types`/`errors`/`decorators`/`env`/`_host` -- imports nothing external,
and `tests/unit/test_core_zero_dep.py` enforces both halves of that boundary.

Consequently **the package root never re-exports this subpackage**: `import
serpent` must not be able to drag `stellar_sdk` in, so `serpent.spec` is
imported explicitly by the compiler (sub-plan D) and by tests.

Re-exports only; nothing is defined here. `typemap` turns one annotation into
an `SCSpecTypeDef`; `sections` builds the three custom-section payloads out of
`_serpent_type_` metadata and those type defs; `decode` reads those same three
payloads back, so the CLI's `inspect` has no XDR of its own (ruling E3).
"""

from serpent.spec.decode import (
    decode_env_meta,
    decode_meta,
    decode_spec_entries,
    spec_entry_names_by_kind,
)
from serpent.spec.sections import (
    SpecDocError,
    SpecNameError,
    build_env_meta,
    build_meta,
    build_spec_entries,
    class_doc,
    own_doc,
)
from serpent.spec.typemap import SpecTypeError, to_spec_type

__all__ = [
    "SpecDocError",
    "SpecNameError",
    "SpecTypeError",
    "build_env_meta",
    "build_meta",
    "build_spec_entries",
    "class_doc",
    "decode_env_meta",
    "decode_meta",
    "decode_spec_entries",
    "own_doc",
    "spec_entry_names_by_kind",
    "to_spec_type",
]
