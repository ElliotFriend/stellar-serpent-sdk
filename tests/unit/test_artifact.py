"""`inspect_artifact`: the facts about a built module the stock CLI cannot know (ruling E3, F.1.2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from serpent._host import CONSTRUCTOR_MIN_PROTOCOL, HOST_FUNCTIONS, HostFn
from serpent.emitter import build_file
from serpent.emitter.artifact import MalformedArtifact, inspect_artifact
from tests.unit.test_emitter_end_to_end import EXAMPLES

DEPLOYED_SHAPES = (
    Path(__file__).resolve().parents[1]
    / "real_host"
    / "fixtures"
    / "testnet"
    / "shapes"
    / "deployed.wasm"
)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_declared_equals_recomputed_for_every_example(path: Path) -> None:
    """F.1.2: the floor RE-DERIVED from the artifact's imports (+ the D9
    constructor gate) must equal what the compiler declared."""
    built = build_file(path)
    art = inspect_artifact(built.wasm)
    assert art.declared_protocol == built.declared_protocol
    assert art.recomputed_protocol == built.declared_protocol
    assert not art.protocol_mismatch
    assert art.has_constructor == ("__constructor" in built.exports)
    assert art.sha256 == hashlib.sha256(built.wasm).hexdigest()
    assert art.size == len(built.wasm)
    assert set(art.exports) == set(built.exports)
    assert {i.host_fn for i in art.imports} == set(built.imports)


def test_the_deployed_shapes_bytes_declare_20_and_recompute_20() -> None:
    art = inspect_artifact(DEPLOYED_SHAPES.read_bytes())
    assert art.declared_protocol == 20 and art.recomputed_protocol == 20
    assert not art.has_constructor
    assert art.spec is not None and "area" in art.spec["functions"]
    assert art.spec["unions"] == ["Shape"] and art.spec["enums"] == ["Color"]
    assert art.meta is not None and dict(art.meta)["name"] == "Drawing"


def test_a_constructor_export_raises_the_recomputed_floor_to_22() -> None:
    errors = next(p for p in EXAMPLES if p.stem == "errors")
    art = inspect_artifact(build_file(errors).wasm)
    assert art.has_constructor and art.recomputed_protocol == CONSTRUCTOR_MIN_PROTOCOL == 22


def test_sections_are_listed_with_names_and_sizes() -> None:
    art = inspect_artifact(build_file(EXAMPLES[0]).wasm)
    names = [s.name for s in art.sections if s.id == 0]
    assert names == ["contractenvmetav0", "contractspecv0", "contractmetav0"]
    assert all(s.size > 0 for s in art.sections)


def test_imports_carry_their_protocol_gates() -> None:
    art = inspect_artifact(build_file(EXAMPLES[0]).wasm)
    by_name = {fn.name: fn for fn in HOST_FUNCTIONS}
    for imp in art.imports:
        assert imp.host_fn in by_name
        assert imp.min_protocol == by_name[imp.host_fn].min_protocol


def _gated_witness() -> HostFn:
    """The lowest-gated REAL host function strictly above BASE_PROTOCOL [B3].

    `protocol_gated_dummy` (min_protocol 19, the synthetic pin entry that also
    carries the only `max_protocol`) is excluded by name: a gate at or below
    `BASE_PROTOCOL` (20) contributes nothing to `compute_protocol_floor`, so it
    cannot witness a recomputation. Today this selects a protocol-21 function.
    """
    from serpent._host import BASE_PROTOCOL

    candidates = [
        fn
        for fn in HOST_FUNCTIONS
        if fn.min_protocol is not None
        and fn.min_protocol > BASE_PROTOCOL
        and fn.name != "protocol_gated_dummy"
    ]
    return min(candidates, key=lambda f: f.min_protocol or 0)


def test_a_gated_import_recomputes_its_gate() -> None:
    """A hand-made module importing ONE gated host function recomputes exactly
    that gate; declared is None (no env-meta), so `protocol_mismatch` is False
    by definition."""
    gated = _gated_witness()
    assert gated.min_protocol is not None and gated.min_protocol > 20
    wasm = _module_importing(gated.module, gated.export, params=len(gated.arg_types))
    art = inspect_artifact(wasm)
    assert art.declared_protocol is None
    assert art.recomputed_protocol == gated.min_protocol
    assert not art.protocol_mismatch


def test_a_mismatch_is_reported() -> None:
    """The same module, with a contractenvmetav0 declaring 20 spliced in."""
    from serpent.spec import build_env_meta

    gated = _gated_witness()
    wasm = _module_importing(
        gated.module,
        gated.export,
        params=len(gated.arg_types),
        env_meta=build_env_meta(20),
    )
    art = inspect_artifact(wasm)
    assert art.declared_protocol == 20 and art.recomputed_protocol == gated.min_protocol
    assert art.protocol_mismatch


def test_not_a_wasm_module_is_malformed() -> None:
    with pytest.raises(MalformedArtifact):
        inspect_artifact(b"\x00asm\x01\x00\x00\x00truncated")
    with pytest.raises(MalformedArtifact):
        inspect_artifact(b"hello")


def test_a_non_function_import_says_it_is_not_a_serpent_artifact() -> None:
    """[m15]: a module importing a memory is a legitimate wasm module that
    serpent did not build, and the message must say so rather than blaming
    the section framing (which is intact)."""
    from serpent.emitter import encode

    memory_import = (
        encode.wasm_name("env") + encode.wasm_name("memory") + bytes([0x02, 0x00]) + encode.uleb(1)
    )
    wasm = b"\x00asm\x01\x00\x00\x00" + encode.section(2, encode.uleb(1) + memory_import)
    with pytest.raises(MalformedArtifact, match="^not a serpent artifact:"):
        inspect_artifact(wasm)


def _module_importing(
    module: str, field: str, *, params: int, env_meta: bytes | None = None
) -> bytes:
    """The smallest valid module importing one i64^params -> i64 function.

    Hand-encoded with `serpent.emitter.encode` (LEB128 + section framing), so
    this test does not depend on the emitter's higher layers; see
    `tests/unit/test_emitter_validate.py` for the same hand-assembly idiom.
    """
    from serpent.emitter import encode

    functype = bytes([0x60, params, *([0x7E] * params), 0x01, 0x7E])
    type_section = encode.section(1, encode.uleb(1) + functype)
    import_entry = (
        encode.wasm_name(module) + encode.wasm_name(field) + bytes([0x00]) + encode.uleb(0)
    )
    import_section = encode.section(2, encode.uleb(1) + import_entry)
    custom = b""
    if env_meta is not None:
        custom = encode.custom_section("contractenvmetav0", env_meta)
    return b"\x00asm\x01\x00\x00\x00" + type_section + import_section + custom
