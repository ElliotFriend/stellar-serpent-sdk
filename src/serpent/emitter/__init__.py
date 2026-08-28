"""The WASM emitter (M1-D): lowers a compiled ``ContractIR`` to a Soroban module.

``build_wasm``/``build_file`` (Task 11) are the package's public surface, and
the only paths a caller should use -- everywhere else in ``serpent.emitter``
is an implementation seam, wired together as:

* ``encode`` -- LEB128 + section framing, and ``opcodes`` -- the
  provenance-pinned instruction/valtype/blocktype constants.
* ``frame`` -- the operand-stack-checked ``Fn`` builder, the exception
  taxonomy (``EmitError``/``BuildLimitError``), and the symbolic call sites
  (``CallImport``/``CallDefined``) review B1's pass-2 net resolves.
* ``layout`` -- the literal pool + scratch bump allocator (``Memory``, P12).
* ``arith``/``lower`` -- the guest-runtime parts and the statement/expression
  lowering that turns one ``FuncIR`` into a ``frame.Fn``.
* ``sections`` -- the three Soroban custom sections, delegating every byte to
  ``serpent.spec`` (the one place this package reaches ``stellar_sdk``).
* ``module`` -- ``assemble``: pass 1 lowers everything and freezes the
  layout, pass 2 lays out the sections and resolves every symbolic call site,
  and the result is an ``AssembledModule`` (wasm bytes plus the facts only
  assembly knows).
* ``validate`` -- ``validate_internal`` (an independent decoder, always run)
  and ``validate_external`` (the optional ``wasm-tools`` shell-out, ruling
  E5).

``build_wasm`` is where those last two modules meet the public API (S2/P8:
"an invalid module is a compile error, never an output file"): it assembles,
ALWAYS validates internally, validates externally per its own argument, and
maps every failure to the taxonomy a caller of ``compile_module`` already
knows -- a ``BuildLimitError`` becomes a located ``CompileError`` (a
registered SPT8xxx code, E15/M10), a reserved ``meta`` key is a plain
``ValueError`` raised before assembly even starts, and a bare ``EmitError``
(an emitter invariant break, never a user error) is re-raised as
``serpent.compiler.loader.CompilerBugError``. Only validated bytes are ever
returned to a caller.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from serpent.compiler.diagnostics import CompileError, Diagnostics, Loc
from serpent.compiler.frontend import CompiledModule, compile_module
from serpent.compiler.loader import CompilerBugError
from serpent.emitter import module as _module
from serpent.emitter import sections as _sections
from serpent.emitter import validate as _validate
from serpent.emitter.frame import BUILD_LIMITS, BuildLimitError, EmitError

__all__ = [
    "BuildLimitError",
    "BuildResult",
    "EmitError",
    "build_file",
    "build_wasm",
]


@dataclass(frozen=True)
class BuildResult:
    """A validated build: the wasm bytes plus every fact ``AssembledModule``
    carried through, and the two protocol numbers E9's ruling keeps apart.

    * ``wasm`` -- the validated module bytes; P8 means these are the only
      bytes a caller of ``build_wasm``/``build_file`` ever sees.
    * ``declared_protocol`` -- ``compiled.declared_protocol`` (ruling E9): the
      COMPUTED FLOOR for an ungated contract, or the requested target when
      one was given and accepted. This is what ``contractenvmetav0`` carries.
    * ``target_protocol`` -- the BUILD target ``build_file`` threaded through
      ``compile_module``, ``None`` when none was requested. A bare
      ``build_wasm`` call has no ``CompiledModule.target_protocol`` field to
      read (the frontend does not record it), so this is always ``None``
      coming from ``build_wasm`` directly -- E9's "both numbers" surface, kept
      honest rather than fabricated.
    * ``exports``/``imports`` -- the contract's exported names and the
      emitted host-fn import NAMES, both re-derived from the assembled bytes
      by ``module.assemble`` itself (review B1's net), never from the
      frontend's own (over-approximating, C21) sets.
    * ``runtime_parts_linked`` -- which guest-runtime parts THIS build linked
      (Task 13's superset of the frontend's hint).
    * ``needs_memory`` -- D's own post-lowering fact (ruling E10), i.e.
      ``AssembledModule.expect_memory``.
    * ``pool_size``/``scratch_size`` -- the literal pool's and the scratch
      allocator's byte counts (``layout.Memory``).
    * ``module_size`` -- ``len(wasm)``, S22's own limit compared against.
    """

    wasm: bytes
    declared_protocol: int
    target_protocol: int | None
    exports: tuple[str, ...]
    imports: tuple[str, ...]
    runtime_parts_linked: frozenset[str]
    needs_memory: bool
    pool_size: int
    scratch_size: int
    module_size: int


def _limit_code(limit: str) -> str:
    """``SPT800N`` for ``BUILD_LIMITS[N-1] == limit`` -- no second table.

    ``codes.py``'s SPT8xxx band is laid out in exactly ``BUILD_LIMITS``' own
    order (its docstring says so explicitly): ``module_size`` -> SPT8001,
    ``pool`` -> SPT8002, ``scratch`` -> SPT8003, ``unsupported`` -> SPT8004.
    Deriving the code from the tuple's position, rather than hand-writing a
    parallel ``{"module_size": "SPT8001", ...}`` dict, is what "without a
    second table" (codes.py) means: the two cannot drift apart because there
    is only the one ordering.
    """
    return f"SPT800{BUILD_LIMITS.index(limit) + 1}"


def _limit_compile_error(compiled: CompiledModule, exc: BuildLimitError) -> CompileError:
    """A ``BuildLimitError`` -> a located ``CompileError`` (E15/M10's split).

    One diagnostic, at ``Loc.whole_file(compiled.ir.path)``: a module that
    grew past a budget in aggregate has no single AST node to blame.
    """
    sink = Diagnostics()
    sink.error(_limit_code(exc.limit), Loc.whole_file(compiled.ir.path), str(exc))
    return CompileError(sink.diagnostics)


def _check_reserved_meta(meta: Mapping[str, str]) -> None:
    """Refuse a reserved ``meta`` key BEFORE assembly starts.

    An API-argument mistake, not a compile error: there is no source location
    to blame (the key came from the ``build_wasm``/``build_file`` call, not
    from the contract), so a registry code here would be dishonest -- SPT8004
    stays the emitter-coverage code and nothing else. ``build_meta`` (via
    ``module.assemble`` -> ``sections.meta_payload``) would raise the same
    ``ValueError`` at the very end of assembly; checking the same reserved set
    here, first, means a reserved key never pays for a lowering it was always
    going to lose.
    """
    reserved = sorted(key for key in meta if key in _sections.RESERVED_META_KEYS)
    if reserved:
        raise ValueError(
            f"meta key(s) {reserved} are reserved and written by serpent itself "
            f"(reserved: {', '.join(_sections.RESERVED_META_KEYS)})"
        )


def _run_external_validation(wasm: bytes, validate_external: bool | None) -> None:
    """``wasm-tools`` per ``validate_external`` (ruling E5): ``None`` = when
    available, ``True`` = required (a clear error if it is absent), ``False``
    = skip entirely.

    A ``False`` verdict -- the tool is present and rejects a module
    ``validate_internal`` already accepted -- is never passed through: P8
    ("an invalid module never reaches the caller") makes that disagreement a
    compiler bug, the same class a bare ``EmitError`` out of ``assemble``
    is, not a result a caller could act on.
    """
    if validate_external is False:
        return
    verdict = _validate.validate_external(wasm)
    if verdict is None:
        if validate_external:
            raise RuntimeError(
                "validate_external=True requires wasm-tools on PATH, but it was not "
                "found; install it (see .github/workflows/ci.yml for the pinned "
                "release) or pass validate_external=False to skip this gate"
            )
        return
    if not verdict:
        raise CompilerBugError(
            "wasm-tools rejected a module that validate_internal accepted; the two "
            "validators have diverged, which P8 treats as a compiler bug -- an "
            "invalid module must never reach a caller"
        )


def build_wasm(
    compiled: CompiledModule,
    *,
    meta: Mapping[str, str] | None = None,
    version: str | None = None,
    validate_external: bool | None = None,
) -> BuildResult:
    """Assemble, validate, and package ``compiled`` as a ``BuildResult``.

    Order: the reserved-``meta``-key check runs first (a ``ValueError``,
    before any assembly work), then ``module.assemble``, then
    ``validate.validate_internal`` ALWAYS, then ``wasm-tools`` per
    ``validate_external``. Error mapping (E15/M10's split):

    * ``BuildLimitError`` (a budget the contract outgrew) -> a located
      SPT8001/SPT8002/SPT8003 ``CompileError``.
    * a reserved ``meta`` key -> a plain ``ValueError``, named above, before
      assembly.
    * a bare ``EmitError`` out of ``assemble`` (an emitter invariant break,
      never a user error) -> re-raised as
      ``serpent.compiler.loader.CompilerBugError``.

    Only validated bytes are ever returned (P8).
    """
    pairs: Mapping[str, str] = {} if meta is None else meta
    _check_reserved_meta(pairs)

    try:
        assembled = _module.assemble(compiled, meta=pairs, version=version)
        # `validate_internal` shares this `try` (not a second one after it):
        # its own `BuildLimitError(limit="module_size")` -- S22's cap, the one
        # `assemble` cannot check because it does not know the customs'
        # combined size until they are appended -- gets exactly the same
        # located-`CompileError` treatment as the pool/scratch limits
        # `assemble` raises earlier (validate.py's own docstring: "Task 11
        # maps [it] to SPT8001").
        _validate.validate_internal(assembled.wasm, expect_memory=assembled.expect_memory)
    except BuildLimitError as exc:
        raise _limit_compile_error(compiled, exc) from exc
    except EmitError as exc:
        raise CompilerBugError(str(exc)) from exc

    _run_external_validation(assembled.wasm, validate_external)

    return BuildResult(
        wasm=assembled.wasm,
        declared_protocol=compiled.declared_protocol,
        target_protocol=None,
        exports=assembled.exports,
        imports=assembled.import_names,
        runtime_parts_linked=assembled.runtime_parts_linked,
        needs_memory=assembled.expect_memory,
        pool_size=assembled.pool_size,
        scratch_size=assembled.scratch_size,
        module_size=len(assembled.wasm),
    )


def build_file(
    path: str | Path,
    *,
    target_protocol: int | None = None,
    meta: Mapping[str, str] | None = None,
    version: str | None = None,
    validate_external: bool | None = None,
) -> BuildResult:
    """Compile the contract source at ``path`` and ``build_wasm`` it.

    ``path`` is read as UTF-8 text and passed to ``compile_module`` as both
    the source and (stringified) the display path, exactly the convention
    ``compile_module`` itself documents. ``target_protocol`` is threaded both
    into the compile (BL-1) and into the returned ``BuildResult`` -- unlike a
    bare ``build_wasm`` call, ``build_file`` really does know the target that
    produced ``declared_protocol``, so it is never fabricated as ``None``.
    """
    source_path = Path(path)
    source = source_path.read_text(encoding="utf-8")
    compiled = compile_module(source, str(source_path), target_protocol=target_protocol)
    result = build_wasm(compiled, meta=meta, version=version, validate_external=validate_external)
    return dataclasses.replace(result, target_protocol=target_protocol)
