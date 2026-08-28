"""`serpent.emitter`'s public build API (Task 11): `build_wasm`/`build_file`,
`BuildResult`, and the error-mapping split (E15/M10).

This is the ONE place the emitter's error taxonomy is tested end-to-end, from
the caller's side: a `CompiledModule` (or a source file) in, a `BuildResult`
out, and every failure mode -- a user-visible build limit, a reserved `meta`
key, an emitter invariant break, a missing `wasm-tools` -- mapped to exactly
the exception a caller of `compile_module` already knows how to catch.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import serpent
from serpent._host import declared_protocol
from serpent.compiler.diagnostics import CompileError, LocKind
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.loader import CompilerBugError
from serpent.emitter import BuildResult, build_file, build_wasm
from serpent.emitter import module as emitter_module
from serpent.emitter import validate as emitter_validate
from serpent.emitter.frame import EmitError

_SPIKE1 = Path(__file__).resolve().parent.parent / "fixtures" / "spike1_reauthored.py"

_MAGIC = b"\x00asm\x01\x00\x00\x00"


def _compiled(path: Path = _SPIKE1, *, target_protocol: int | None = None) -> CompiledModule:
    source = path.read_text(encoding="utf-8")
    return compile_module(source, str(path), target_protocol=target_protocol)


# ===========================================================================
# build_file: the end-to-end happy path
# ===========================================================================


def test_build_file_returns_validated_bytes_with_the_expected_export_list() -> None:
    result = build_file(_SPIKE1)
    assert isinstance(result, BuildResult)
    assert result.wasm[:8] == _MAGIC
    assert result.exports == ("setup", "bump")
    assert result.module_size == len(result.wasm)
    # `build_wasm` already ran `validate_internal`; re-running it here is the
    # test's own proof that what came back really is validated, not merely a
    # claim -- an independent decoder over the returned bytes, not a call
    # into the emitter's own bookkeeping.
    emitter_validate.validate_internal(result.wasm, expect_memory=result.needs_memory)


def test_build_wasm_accepts_a_compiled_module_directly() -> None:
    result = build_wasm(_compiled())
    assert result.wasm[:8] == _MAGIC
    assert result.target_protocol is None


# ===========================================================================
# Determinism (ruling E7): pool/scratch offsets are a pure function of the
# LiteralInventory, never of Python's randomized `hash()`.
# ===========================================================================


def _build_digest(hashseed: str) -> str:
    """Build `_SPIKE1` in a FRESH interpreter seeded with `hashseed`, and
    return the sha256 hex digest of the wasm it produced.

    A separate subprocess, not merely a re-seeded dict inside this process:
    `PYTHONHASHSEED` is read once, at interpreter start, and randomizes
    `hash()` for every `str`/`bytes` object for that process's whole
    lifetime -- so "different seed" only means something across two
    interpreters, never within one.
    """
    code = (
        "import hashlib, sys\n"
        "from serpent.emitter import build_file\n"
        f"result = build_file({str(_SPIKE1)!r})\n"
        "sys.stdout.write(hashlib.sha256(result.wasm).hexdigest())\n"
    )
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    digest = done.stdout.strip()
    assert len(digest) == hashlib.sha256(b"").digest_size * 2, (done.stdout, done.stderr)
    return digest


def test_the_same_fixture_builds_byte_identically_under_different_hash_seeds() -> None:
    """E7, end to end: two separate interpreters, seeded 1 and 2, must
    produce byte-identical wasm for the same source. A pool offset (or a
    scratch address, or an import/export order) that quietly depended on
    dict/set iteration order would diverge here and nowhere else -- every
    single-process test in this suite shares one interpreter's hash seed."""
    assert _build_digest("1") == _build_digest("2")


# ===========================================================================
# Protocol cross-check (F.2.11): the declared protocol, recomputed from the
# EMITTED import names, must agree with what the frontend declared.
# ===========================================================================


def test_declared_protocol_recomputes_from_the_emitted_imports_with_no_target() -> None:
    result = build_file(_SPIKE1)
    assert declared_protocol(list(result.imports), None) == result.declared_protocol
    assert result.target_protocol is None


def test_declared_protocol_recomputes_from_the_emitted_imports_with_a_target() -> None:
    target = 21
    result = build_file(_SPIKE1, target_protocol=target)
    assert result.target_protocol == target
    assert declared_protocol(list(result.imports), target) == result.declared_protocol


# ===========================================================================
# validate_external (ruling E5): None/True/False, and the "required but
# absent" error `True` must raise.
# ===========================================================================


def test_validate_external_true_raises_clearly_when_wasm_tools_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="wasm-tools"):
        build_wasm(_compiled(), validate_external=True)


def test_validate_external_false_skips_the_shell_out_even_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = build_wasm(_compiled(), validate_external=False)
    assert result.wasm[:8] == _MAGIC


@pytest.mark.skipif(
    shutil.which("wasm-tools") is None,
    reason="wasm-tools is not installed (ruling E5: optional-if-present)",
)
def test_validate_external_none_runs_wasm_tools_when_it_is_present() -> None:
    """The default: `build_wasm` does not merely accept the tool's absence,
    it actually calls it when present -- proven with the tool for real,
    rather than by mocking `validate_external` to return `True`."""
    result = build_wasm(_compiled())
    assert result.wasm[:8] == _MAGIC


# ===========================================================================
# Error mapping (E15/M10's split)
# ===========================================================================


def test_a_build_limit_error_becomes_a_located_spt8001_compile_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S22's module-size cap, forced without a 131072-byte fixture nobody
    could read: `validate.MAX_MODULE_SIZE` is patched down to a size every
    real module exceeds, over the SAME fixture every other test in this file
    uses. `assemble` never sees a fake limit -- only `validate_internal`
    does, exactly where S22's check actually lives (validate.py's own
    docstring: "Task 11 maps [it] to SPT8001")."""
    monkeypatch.setattr(emitter_validate, "MAX_MODULE_SIZE", 8)
    compiled = _compiled()
    with pytest.raises(CompileError) as excinfo:
        build_wasm(compiled)
    (diagnostic,) = excinfo.value.diagnostics
    assert diagnostic.code == "SPT8001"
    assert diagnostic.loc.kind is LocKind.WHOLE_FILE
    assert diagnostic.loc.path == compiled.ir.path


def test_a_reserved_meta_key_is_a_plain_value_error_raised_before_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API-argument mistake, checked BEFORE any assembly work: patching
    `assemble` to blow up if it is ever reached turns "raised before
    assembly" from a claim into something this test would fail on if it
    stopped being true."""

    def _must_not_be_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("assemble must not run when a meta key is reserved")

    monkeypatch.setattr(emitter_module, "assemble", _must_not_be_called)
    with pytest.raises(ValueError, match="name"):
        build_wasm(_compiled(), meta={"name": "not mine to set"})


def test_a_bare_emit_error_from_assemble_is_a_compiler_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An emitter invariant break -- never a user error -- must not be
    catchable alongside `CompileError` (both taxonomies are `ValueError`
    subclasses; `CompilerBugError` is deliberately an `AssertionError`,
    dossier F.1.14)."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise EmitError("sentinel: assemble refused to cooperate")

    monkeypatch.setattr(emitter_module, "assemble", _boom)
    with pytest.raises(CompilerBugError, match="sentinel"):
        build_wasm(_compiled())


# ===========================================================================
# serpent.__all__ stays untouched by Task 11
# ===========================================================================


def test_serpent_dunder_all_does_not_leak_the_emitter_build_api() -> None:
    """`serpent.__all__` is the authoring surface (`test_public_api.py`'s own
    frozen list); `build_wasm`/`build_file`/`BuildResult` are a TOOLING API a
    build script imports from `serpent.emitter` directly, and must never
    show up on the package root."""
    assert not {"build_wasm", "build_file", "BuildResult"} & set(serpent.__all__)
