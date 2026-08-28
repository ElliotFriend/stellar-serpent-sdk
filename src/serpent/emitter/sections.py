"""The three Soroban custom sections, delegating every byte to ``serpent.spec``.

This is the ONE module in ``serpent/emitter/`` allowed to import
``serpent.spec`` -- and therefore the one place the emitter reaches
``stellar_sdk`` at all, transitively (plan Global Constraints; the zero-dep walk
in ``tests/unit/test_core_zero_dep.py`` exempts ``serpent/spec/`` and nothing
else). Nothing here encodes XDR: every payload is a call into the M1-B builders,
so a custom section and the `stellar-cli`'s reading of it cannot drift.

The three payloads, per dossier B.1's custom-section row:

* ``contractenvmetav0`` <- ``build_env_meta(compiled.declared_protocol)``
  (ruling E9). ``declared_protocol`` already encodes B4's whole contract -- the
  COMPUTED FLOOR when no target was requested, the requested target when one
  was, "users may raise, never lower" (S6) -- so there is nothing for this
  module to decide (review M5).
* ``contractspecv0`` <- ``build_spec_entries(contract_cls,
  types=declared_types_in_order)`` (B8/B9). ``spec_inputs.events`` is NEVER
  passed (review B10): `_serpent_type_` carries no topic/data split, so an
  event handed to ``types=`` is a hard failure at emission rather than a
  valid-but-lying spec.
* ``contractmetav0`` <- ``build_meta(contract name, version, user pairs)``
  (ruling E8). The contract's own name is the ``@contract`` CLASS name;
  ``version`` is written only when the caller supplied one, because inventing a
  contract version is a lie; ``serpentver`` is written by ``build_meta``
  itself.

Every ``None`` a caller cannot see is asserted rather than dereferenced:
``ir.contract`` and ``spec_inputs.contract_cls`` are ``None`` only alongside a
diagnostic, and a ``CompiledModule`` with a diagnostic never reaches the emitter
(C14/dossier C.3), so ``None`` here is a compiler bug and says so.
"""

from collections.abc import Mapping

from serpent.compiler.frontend import CompiledModule
from serpent.emitter.frame import EmitError
from serpent.spec import build_env_meta, build_meta, build_spec_entries

__all__ = [
    "ENV_META_SECTION_NAME",
    "META_SECTION_NAME",
    "SPEC_SECTION_NAME",
    "env_meta_payload",
    "meta_payload",
    "spec_payload",
]

#: The custom-section names the Soroban host and the Stellar CLI read, spelled
#: exactly as the on-chain-verified artifact spells them
#: (`spikes/spike1/emitter.py:876-878`).
ENV_META_SECTION_NAME = "contractenvmetav0"
SPEC_SECTION_NAME = "contractspecv0"
META_SECTION_NAME = "contractmetav0"


def env_meta_payload(compiled: CompiledModule) -> bytes:
    """``contractenvmetav0``: the protocol this artifact declares (ruling E9).

    The number is ``compiled.declared_protocol`` verbatim. For an ungated
    contract that is the computed floor -- 20 today, not the 27 the Phase 0
    artifact declared -- which the ruling calls out as by design: the build
    target is a FRONTEND gate, and the artifact declares what its imports
    actually require.
    """
    return build_env_meta(compiled.declared_protocol)


def spec_payload(compiled: CompiledModule) -> bytes:
    """``contractspecv0``: the exported interface plus the declared types (B8/B9).

    ``types=`` takes ``declared_types_in_order`` -- B9's "declared, not
    discovered" inventory, in declaration order -- and never
    ``spec_inputs.events`` (B10).
    """
    contract_cls = compiled.spec_inputs.contract_cls
    if contract_cls is None:
        raise EmitError(
            "spec_inputs.contract_cls is None; that state always comes with a "
            "diagnostic, and a module with diagnostics never reaches the emitter "
            "(dossier C.3) -- reaching it here is a compiler bug"
        )
    return build_spec_entries(contract_cls, types=compiled.spec_inputs.declared_types_in_order)


def meta_payload(compiled: CompiledModule, meta: Mapping[str, str], version: str | None) -> bytes:
    """``contractmetav0``: name, optional version, ``serpentver``, user pairs (E8).

    ``version=None`` omits the entry entirely rather than inventing one. A user
    pair colliding with a reserved key raises ``ValueError`` from ``build_meta``
    naming the reserved set: that is an API-argument mistake, deliberately not a
    registry diagnostic (Task 11 raises the same error before assembly even
    starts).
    """
    contract = compiled.ir.contract
    if contract is None:
        raise EmitError(
            "ir.contract is None; that state always comes with a diagnostic, and a "
            "module with diagnostics never reaches the emitter (C14) -- reaching it "
            "here is a compiler bug"
        )
    return build_meta(contract.name, version, meta)
