"""`FuncCtx` and its supporting per-function state (dossier MJ-10, §C.3).

`FuncCtx` is what every checker from Task 5 onward threads through while
compiling ONE function body -- an exported method, `__init__`, or a
module-level/private helper (SS C.3's "Function" scope: params + locals, no
nesting, no closures). It is defined here, in Task 4, because `Ty` and the
annotation resolver it is built from (`serpent.compiler.types_`) are also
Task 4's, and because Tasks 5 through 9 all need the SAME shape to consume
(MJ-10) -- defining it once, here, is what keeps them from inventing five
slightly-different function-context objects.

Three pieces of per-function STATE live alongside `FuncCtx`:

* `SlotTable` -- the flat local-slot list SS C.3 describes ("no block
  scoping"), with the two structural rules Task 4 owns outright: single-typed
  locals (rule 1, `SPT3017`) and shadowing (rule 4, `SPT2004`). The other two
  `Locals` rules -- definite assignment (rule 2, `SPT7002`) and definite
  return (rule 3, `SPT7001`) -- are FLOW-sensitive and are Task 6's analysis;
  `LocalSlot.definitely_assigned` is the state that analysis reads and
  writes, not the analysis itself.
* `AliasTable` -- the E11 alias-analysis STATE (which container-typed local
  slots are C-owned vs. aliased), plus the one walk that state has to share:
  `mark_escapes`, "every mutable-container local this expression can BE loses
  ownership". The analysis that DECIDES ownership transitions is Task 7b's
  (`recognize.note_local_binding`/`note_escapes`, E11/BL-3); the walk lives
  here because Task 8 needs it too, from `expr.py`'s internal-call site, and
  `expr.py` cannot import `recognize.py` (that module imports `check_expr`).
  A second copy of a five-line soundness walk is exactly the drift this file
  exists to prevent, so there is one copy and `recognize.note_escapes`
  delegates to it.
* `Ownership` -- the two-value answer `AliasTable` tracks.
* `InternalSig` -- the CALL-SITE view of an internal call target (E8): a
  module-level helper or a private method, resolved once by
  `decls.check_declarations` and consumed by `expr.py`. It lives here for the
  same reason `FuncCtx` does (MJ-10): `expr.py` is below `decls.py` in the
  import order, so the shape they share has to be defined below both.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, auto

from serpent.compiler import codes
from serpent.compiler.diagnostics import Diagnostics, Loc
from serpent.compiler.ir import IfExp, IRExpr, LocalRef
from serpent.compiler.loader import LoadedModule
from serpent.compiler.types_ import Ty, TyTag

__all__ = [
    "MUTABLE_TAGS",
    "AliasTable",
    "FuncCtx",
    "InternalSig",
    "LocalSlot",
    "Ownership",
    "SlotTable",
]

#: The tags whose values are MUTABLE at tier 1 -- the ones E11's alias
#: analysis tracks. `Bytes` has no mutating method and a `@contracttype` struct
#: is a frozen dataclass, so neither can ever need a rebind.
MUTABLE_TAGS: frozenset[TyTag] = frozenset({TyTag.VEC, TyTag.MAP})

#: `code -> message_intent`, matching `loader.py`/`types_.py`'s convention.
_INTENT: dict[str, str] = {entry.code: entry.message_intent for entry in codes.REGISTRY}

_SHADOW_HELP = (
    "give the local a name no parameter, module constant, import, or declared type already uses"
)
_REBIND_HELP = (
    "a local's type is fixed by its first binding; use a different local name for the new type"
)


@dataclass
class LocalSlot:
    """One function-body local: its slot index, name, fixed `Ty`, and whether
    it is definitely assigned yet on the path currently being checked.

    `definitely_assigned` is mutable (this is NOT a frozen dataclass) because
    Task 6's flow analysis flips it as it walks statements; `slot`/`name`/
    `ty` never change after `SlotTable.declare` first creates the slot (SS
    C.3 rule 1: the first binding fixes the type for good).
    """

    slot: int
    name: str
    ty: Ty
    definitely_assigned: bool = False


class SlotTable:
    """The flat per-function local-slot table (SS C.3's "Locals").

    No block scoping: every distinct local NAME a function body ever binds
    gets exactly one slot, numbered in first-binding order -- an `if`/`else`
    arm does not get its own scope, which is what makes "definitely assigned
    on every path" (rule 2, Task 6) a meaningful question about a single flat
    slot rather than a merge across scopes.

    `reserved` is the set of names a NEW local may not shadow (rule 4): the
    function's own parameters, module-level constants, imported names, and
    declared type names. It is supplied by the CALLER (Task 5/6, which is
    what actually knows a given function's parameter list and has `loaded`
    in hand for the module-level names) as a `name -> human-readable kind`
    mapping, so a shadowing diagnostic can say what it collided with
    ("a parameter", "a module constant", ...).
    """

    def __init__(self, reserved: Mapping[str, str] | None = None) -> None:
        self._reserved: Mapping[str, str] = dict(reserved) if reserved is not None else {}
        self._by_name: dict[str, LocalSlot] = {}
        self._order: list[LocalSlot] = []

    @property
    def slots(self) -> tuple[LocalSlot, ...]:
        """Every declared slot, in first-binding order."""
        return tuple(self._order)

    def declare(self, name: str, ty: Ty, loc: Loc, sink: Diagnostics) -> LocalSlot | None:
        """Bind `name` to `ty` at `loc`: a NEW slot, the EXISTING one (same
        type -- just a later assignment to it), or a reported error.

        Returns `None` (sink convention, minor 13) for either failure: a
        rebind at a different type (`SPT3017`, SS C.3 rule 1) or a new name
        that shadows a reserved one (`SPT2004`, rule 4). Once a name is
        legitimately declared, later `declare` calls for it never re-run the
        shadow check -- only the first binding of a name can shadow anything.
        """
        existing = self._by_name.get(name)
        if existing is not None:
            if existing.ty != ty:
                sink.error(
                    "SPT3017",
                    loc,
                    _INTENT["SPT3017"],
                    help=_REBIND_HELP,
                    notes=(
                        (
                            f"`{name}` was first bound as {existing.ty.render()}; "
                            f"this binds it as {ty.render()}"
                        ),
                    ),
                )
                return None
            return existing

        reserved_kind = self._reserved.get(name)
        if reserved_kind is not None:
            sink.error(
                "SPT2004",
                loc,
                _INTENT["SPT2004"],
                help=_SHADOW_HELP,
                notes=(f"`{name}` already names {reserved_kind}",),
            )
            return None

        slot = LocalSlot(slot=len(self._order), name=name, ty=ty)
        self._by_name[name] = slot
        self._order.append(slot)
        return slot

    def mark_assigned(self, name: str) -> None:
        """Record that `name`'s current slot is definitely assigned on the
        path being checked (Task 6's flow analysis is what calls this)."""
        self._by_name[name].definitely_assigned = True

    def lookup(self, name: str) -> LocalSlot | None:
        """The slot for `name`, or `None` if no local of that name has been
        declared yet."""
        return self._by_name.get(name)

    def __iter__(self) -> Iterator[LocalSlot]:
        return iter(self._order)

    def __len__(self) -> int:
        return len(self._order)


class Ownership(Enum):
    """E11 alias-analysis state for one container-typed local slot.

    `OWNED`: this local's CURRENT binding is the only reference the compiler
    tracks, so lowering a mutating method to the host's functional op
    (`v = vec_push_back(v, x)`) only changes this one binding -- sound.
    `ALIASED`: the binding came from somewhere the compiler cannot prove
    exclusive ownership of -- another container-typed local (`a = b`), a
    parameter, a field-get result, or a subscript result -- so mutating it
    would silently diverge from the host's functional semantics (E11's
    "highest-severity silent divergence in C").
    """

    OWNED = auto()
    ALIASED = auto()


def _escaping_locals(value: IRExpr) -> Iterator[LocalRef]:
    """Every local whose own handle `value` can BE.

    A `LocalRef` is the local itself; an `IfExp` can be either arm. Everything
    else -- a `HostCall`, a `Make*` node, a `FieldGet` -- produces a NEW value
    from its operands, so a local mentioned inside one of those does not escape
    through it. That precision matters: treating any nested mention as an
    escape would reject `w = Vec(U32, [v.get(U32(0))]); v.push_back(x)`, where
    only an ELEMENT of `v` was copied out and both tiers agree.
    """
    if isinstance(value, LocalRef):
        yield value
    elif isinstance(value, IfExp):
        yield from _escaping_locals(value.then)
        yield from _escaping_locals(value.orelse)


@dataclass
class AliasTable:
    """Per-function E11 state: container-typed LOCAL slot -> `Ownership`.

    This module defines the state, its accessors, and the one walk that
    several callers share (`mark_escapes`). The PASS that decides when a
    slot's binding is `OWNED` vs. `ALIASED` -- construction is `OWNED`;
    `a = b` where `b` names a container-typed local makes `a` `ALIASED`; a
    parameter, a field-get result, and a subscript result are never `OWNED`; a
    temporary receiver such as `Vec(U32).pop_back()` has no slot at all and is
    rejected before it would ever reach this table -- is Task 7b's
    (`recognize.note_local_binding`, E11/BL-3).

    A slot with no entry here is simply "not yet classified" rather than
    defaulting to either `Ownership`, so Task 7b's pass (and any test of it)
    can tell that apart from an explicit `ALIASED` classification.
    """

    _ownership: dict[int, Ownership] = field(default_factory=dict)

    def mark_owned(self, slot: int) -> None:
        self._ownership[slot] = Ownership.OWNED

    def mark_aliased(self, slot: int) -> None:
        self._ownership[slot] = Ownership.ALIASED

    def mark_escapes(self, values: Iterable[IRExpr]) -> None:
        """Mark every mutable-container local whose handle ESCAPES into
        `values` as `ALIASED` (E11).

        A local stops being exclusively C's the moment its handle is stored
        somewhere else -- an item of a `Vec`, a key or value of a `Map`, a
        field of a struct, an argument of a MUTATION, or an argument of an
        INTERNAL CALL (E8: a callee can embed a passed local in a container of
        its own, and C classifies conservatively without inspecting the
        callee). After any of those, tier 1 sees a later `own.push_back(x)`
        through the container that now holds the same object, and on chain it
        cannot: the rebind touches only `own`.

        `recognize.note_escapes` is the documented entry point for this rule
        (it carries the full rationale, including the three recognized
        argument positions that deliberately do NOT count as escapes); it
        delegates here so the WALK exists once. `expr.py`'s internal-call site
        calls this method directly, because it cannot import `recognize.py`.
        """
        for value in values:
            for ref in _escaping_locals(value):
                if ref.ty.tag in MUTABLE_TAGS:
                    self.mark_aliased(ref.slot)

    def ownership_of(self, slot: int) -> Ownership | None:
        """`None` means "not (yet) known to be a container-typed slot"."""
        return self._ownership.get(slot)


@dataclass(frozen=True)
class InternalSig:
    """One internal-call target's signature, as a CALL SITE needs it (E8).

    An internal call is a module-level helper (`bump(env, amount)`) or a
    private, underscore-prefixed method (`self._bump(env, amount)`) -- both
    compile to a non-exported wasm function, and both are `InternalCall` in
    the IR. `decls.check_declarations` resolves every target ONCE, reporting
    any bad declaration against the declaration itself, and hands the result
    to `FuncCtx.internal_sigs`; `expr.py` then only ever consumes it, so a
    broken callee cannot produce one diagnostic per call site.

    * `surface` is how the call is SPELLED -- the bare name for a helper,
      `self.<name>` for a private method. It is the table's key, which also
      means a helper and a private method of the same name are distinct
      surfaces (`decls` rejects that collision separately: both compile to one
      internal-function namespace).
    * `fn_name` is what `InternalCall.fn_name` carries: the target's Python
      name (`FuncIR.export_name` is unused for an INTERNAL function).
    * `params` drops `self` AND a leading `env: Env` (SS C.3), exactly like
      `FuncCtx.params` -- so `takes_env` is what tells a call site that the
      source must pass `env` in that position, and that the argument
      contributes NO IR node (there is no `Env` value on chain).
    * `decl_loc` locates the callee's own declaration, for a diagnostic that
      needs to name it.
    """

    surface: str
    fn_name: str
    params: tuple[tuple[str, Ty, Loc], ...]
    ret: Ty
    takes_env: bool
    decl_loc: Loc

    def render(self) -> str:
        """The signature as a diagnostic can spell it back to the author."""
        names = ["env"] if self.takes_env else []
        names += [f"{name}: {ty.render()}" for name, ty, _loc in self.params]
        return f"{self.surface}({', '.join(names)}) -> {self.ret.render()}"


@dataclass
class FuncCtx:
    """Per-function compiler context (dossier MJ-10).

    Field by field:

    * `loaded` -- the whole module this function lives in (declarations,
      other decorated types, module consts, helpers); checkers need it for
      name resolution beyond this one function's own params/locals.
    * `sink` -- the `Diagnostics` this function's checking reports through
      (collect-all, E16); typically the SAME sink the whole module compile
      shares, so one `CompileError` covers every function.
    * `params` -- `(name, Ty, Loc)` in declaration order, with `self` AND a
      leading `Env` param BOTH already dropped (SS C.3's Function scope:
      "index 0 = self, ignored; index 1 = env: Env, dropped"). Building this
      list is where a caller must special-case `Env` positionally BEFORE
      calling `resolve_annotation` -- see that function's docstring.
    * `locals` -- the flat `SlotTable` for names this function body itself
      binds (never params -- params are already committed to `params` above
      and are not re-declared as slots; they still occupy the SAME namespace
      for `SlotTable`'s shadowing check via its `reserved` mapping).
    * `loop_depth` -- how many `while`/`for`-desugared loops currently
      enclose the statement being checked; `break`/`continue` outside a loop
      (`SPT7003`, Task 6) is `loop_depth == 0` at the point they appear.
    * `return_ty` -- this function's declared return type, already resolved
      to a `Ty` (a bare `-> None` becomes `Ty.Void`, constructed directly by
      whoever builds this `FuncCtx` -- never through `resolve_annotation`,
      per that function's own documented position rule).
    * `alias_sets` -- the E11 `AliasTable` STATE for this function's
      container-typed locals; Task 7b's analysis reads and writes it.
    * `fn_name` -- the function's Python name (`__init__` included as
      written; export-name translation to `__constructor` is a later task's
      concern, not this context's).
    * `path` -- the source file path, for building `Loc`s while checking this
      function's body.
    * `internal_sigs` -- `surface -> InternalSig` for every internal-call
      target the module declares (E8), keyed as the call is spelled (`bump`,
      `self._bump`). Built once by `decls.check_declarations`; DEFAULTS TO
      EMPTY, which means "this context was built without a declaration pass",
      and an internal call then resolves to nothing. The default exists so a
      unit test that only exercises expression checking need not build a
      declaration table, not as a licence for the assembly task to skip it:
      `decls` reports a bad callee declaration ONCE, against the declaration,
      and a call site that finds no signature for a target that DOES exist
      stays silent rather than cascading.
    """

    loaded: LoadedModule
    sink: Diagnostics
    params: list[tuple[str, Ty, Loc]]
    locals: SlotTable
    loop_depth: int
    return_ty: Ty
    alias_sets: AliasTable
    fn_name: str
    path: str
    internal_sigs: Mapping[str, InternalSig] = field(default_factory=dict)
