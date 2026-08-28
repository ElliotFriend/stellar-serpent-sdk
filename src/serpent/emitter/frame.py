"""The operand-stack and control-frame tracker: one function body under construction.

Ported **by copy** from ``spikes/spike1/emitter.py:358-506`` (R5: ``spikes/``
is read-only evidence, never imported from), then extended for D's larger
language. The ported half is dossier P13, which the on-chain-verified spike
artifact exercised end to end; the extensions are the ones §C.1 names.

## What is kept verbatim from P13

* Typed ``push``/``pop`` over a stack of wasm type names (``"i64"``/``"i32"``).
* ``unreachable`` mirroring wasm's polymorphic-stack rule: once ``return``
  (or ``unreachable``, or an unconditional ``br``) has executed, the
  validator stops caring what is on the stack, and so do we -- ``push`` and
  ``pop`` become no-ops and every height check is skipped.
* ``_check_frame`` asserting ``len(stack) == base + (1 if result else 0)`` at
  **both** ``else`` and ``end``.

  **A deliberate divergence from the wasm algorithm, recorded here:** the
  spec's ``unreachable`` is *per control frame* -- a frame opened inside dead
  code is still validated as reachable, because its body is well-typed
  independently of whether anything jumps to it. Ours (like the spike's) is
  **function-global**: once the function goes unreachable, every height check
  inside every frame opened after that point is skipped, until the frame
  whose ``entry_unreachable`` was ``False`` restores reachability. The
  consequence is that this tracker is *more permissive* than wasm inside dead
  code -- it never rejects a module wasm would accept, it just declines to
  catch a lowering bug in a region no execution can reach. Kept because it is
  the on-chain-verified behavior (P13) and because dead-code regions are
  produced by, not consumed by, D's lowerings.
* ``else_``/``end`` **restoring** the enclosing frame's ``entry_unreachable``.
* **The balance check at ``ret()``, not at ``finish()``** (P2) -- see
  ``ret``'s docstring, which is the single most important paragraph here.
* ``EmitError`` is an **exception, never an ``assert``**, so every check
  survives ``python -O`` (P13, ``spikes/spike1/emitter.py:93-98``). There is
  a test (`test_frame_py_uses_no_bare_asserts`) that pins the absence of
  ``assert`` in this module.

## What is added for D (§C.1)

* Frames carry a ``kind`` (``BLOCK``/``LOOP``/``IF``) so ``br_break`` and
  ``br_continue`` compute their **relative** depth from the frame stack
  rather than from an ad-hoc counter -- "this is where an off-by-one becomes
  a silently wrong branch". A block is a `break` target only if it was opened
  ``breakable=True``, so an unrelated block inside a loop body cannot capture
  the branch.
* An ``if`` frame records whether its ``else`` arm ran, so a result-bearing
  ``if`` cannot be closed one-armed (invalid wasm) and a second ``else``
  cannot be emitted.
* A **br-target arity check**: a ``br`` to a block with a result type must
  leave exactly that value on the stack; a ``br`` to a loop targets the loop
  *header*, whose branch arity is the loop's parameter types (none), so it
  requires the stack emptied down to the frame's base.
* ``br_if_break`` -- `while`'s conditional exit (§B.3.2). It shares
  ``_br_target``'s scan with ``br_break``/``br_continue`` (one place turns
  "which frame" into "how many levels up") but pops an i32 condition and,
  unlike ``br``, leaves the function REACHABLE: a conditional branch has a
  fall-through, and marking the rest of the loop body dead would switch off
  every height check inside it.
* A **local index range check** covering params, the declared locals, and
  the hidden temps ``new_local()`` hands out (review M11).
* ``expr_scope(is_void)`` -- the cheap structural form of S2's "asserts at
  every control-flow merge": every expression lowering leaves a net ``+1``
  ``i64`` on the stack, or a net ``0`` for a Void expression (review M1).
* ``results=()`` void helpers: ``ret()`` pops per ``results`` and ``finish()``
  compares against ``list(results)`` (E11ii, review M2).
* **Symbolic call sites** (review B1): ``code`` is a list of
  ``bytes | CallImport | CallDefined`` rather than one flat ``bytearray``.
  See ``CallImport``.
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto

from serpent import val
from serpent.emitter import encode, opcodes

__all__ = [
    "BUILD_LIMITS",
    "BuildLimitError",
    "CallDefined",
    "CallImport",
    "CodeItem",
    "EmitError",
    "Fn",
    "Frame",
    "FrameKind",
]


# --- The exception taxonomy (E15, sharpened by review M10) -------------------


class EmitError(Exception):
    """An emitter **invariant break**: a compiler bug, not a user error.

    A well-formed contract cannot produce one of these -- the frontend already
    proved the program well-formed, so an unbalanced operand stack, a stray
    control frame, or a branch with no target means *this* code is wrong.
    Task 11's ``build_wasm`` re-raises a bare ``EmitError`` as
    ``serpent.compiler.loader.CompilerBugError``.

    Deliberately an exception rather than an ``assert`` (P13), so every check
    in this module survives ``python -O``.
    """


BUILD_LIMITS: tuple[str, ...] = ("module_size", "pool", "scratch", "unsupported")
"""The ``BuildLimitError.limit`` discriminators Task 10 maps to SPT8xxx codes."""


class BuildLimitError(EmitError):
    """A **user-visible** build limit: the contract is too big for some budget.

    The subclass Task 3 (pool/scratch), Task 8 (unsupported), and Task 10
    (module size) raise, carrying a ``limit`` discriminator from
    ``BUILD_LIMITS`` that ``build_wasm`` turns into a located SPT8xxx
    diagnostic. Bare ``EmitError`` stays the invariant-break class above; the
    split exists so a user's oversized contract never reads as a compiler bug
    (and vice versa).
    """

    def __init__(self, limit: str, message: str) -> None:
        if limit not in BUILD_LIMITS:
            raise EmitError(f"unknown build limit {limit!r}; expected one of {BUILD_LIMITS}")
        super().__init__(message)
        self.limit = limit


# --- Symbolic call sites (review B1) -----------------------------------------


@dataclass(frozen=True)
class CallImport:
    """A call to a host import, recorded **by name** and serialized in pass 2.

    Review B1: no function index is ever baked into a body before the section
    that defines it is frozen. Lowering does the full stack accounting now and
    records *which* function was called; pass 2, which owns the final import
    list, emits ``0x10 + uleb(import_index[name])``. The
    wrong-target-that-still-validates bug class is then structurally
    impossible rather than merely avoided.
    """

    name: str


@dataclass(frozen=True)
class CallDefined:
    """A call to a defined function by its **defined-space** index (review B1).

    ``defidx`` counts from the first defined function, not from zero in the
    combined index space: pass 2 emits ``0x10 + uleb(n_imports + defidx)``.
    """

    defidx: int


CodeItem = bytes | CallImport | CallDefined
"""One item of a finished body: a run of raw bytes, or a call site to resolve."""


# --- Control frames -----------------------------------------------------------


class FrameKind(Enum):
    """What a control frame is, which is what decides where a ``br`` lands.

    A ``br`` to a ``BLOCK`` or ``IF`` frame jumps **forward** to its ``end``;
    a ``br`` to a ``LOOP`` frame jumps **backward** to its header. `while` is
    lowered as ``block { loop { ... } }``, so `continue` targets the nearest
    ``LOOP`` and `break` targets that loop's **breakable** exit block (see
    ``Frame.breakable``).
    """

    BLOCK = auto()
    LOOP = auto()
    IF = auto()


@dataclass
class Frame:
    """A control frame: its kind, the stack height on entry, and its result type."""

    kind: FrameKind
    base: int
    result: str | None
    # Whether the code *around* the frame was already unreachable; each arm
    # starts reachable, and the frame's end restores the enclosing state.
    entry_unreachable: bool
    # BLOCK frames only: is this the `break` label of a loop? A plain block
    # opened for any other reason (an expression scope, a dispatch arm) must
    # NOT capture a `break` aimed at the enclosing loop -- retargeting one
    # would still validate, which is the silent-wrong-branch class §C.1 names.
    breakable: bool = False
    # IF frames only: has `else_()` run? An `if` with a result type is
    # invalid wasm without an `else` (the elided arm would have to be
    # `[] -> [i64]`), and a second `else` is invalid outright.
    saw_else: bool = False


# --- One function body under construction --------------------------------------


class Fn:
    """A function body under construction, with its operand stack tracked.

    ``stack`` holds the wasm type (``"i64"``/``"i32"``) of every live operand.
    ``code`` holds the body as a list of byte runs and symbolic call sites
    (review B1); adjacent raw emissions are coalesced into one ``bytes`` item.

    Shape arguments (review M11): ``nparams`` params occupy indices
    ``[0, nparams)``, the ``nlocals_declared`` locals the frontend declared
    occupy ``[nparams, nparams + nlocals_declared)``, and ``new_local()``
    hands out hidden temps **after** those -- so ``local_set(nparams + slot)``
    for a declared slot can never collide with a temp or trip the index check.

    ``results`` is ``("i64",)`` for exports and value-returning helpers, or
    ``()`` for a void internal helper (E11ii, review M2).
    """

    def __init__(
        self,
        name: str,
        nparams: int,
        nlocals_declared: int,
        results: tuple[str, ...],
    ) -> None:
        if nparams < 0:
            raise EmitError(f"{name}: nparams must be >= 0, got {nparams}")
        if nlocals_declared < 0:
            raise EmitError(f"{name}: nlocals_declared must be >= 0, got {nlocals_declared}")
        # Multi-value is not in the enabled feature set (S23), and every
        # Soroban Val is an i64: the only two legal shapes are these.
        if results not in ((), ("i64",)):
            raise EmitError(f"{name}: results must be () or ('i64',), got {results!r}")

        self.name = name
        self.nparams = nparams
        self.nlocals_declared = nlocals_declared
        self.results = results
        self.code: list[CodeItem] = []
        self.stack: list[str] = []
        self.ctrl: list[Frame] = []
        self.unreachable = False
        self._pending = bytearray()
        self._n_hidden = 0
        self._finished = False

    # -- stack bookkeeping (P13, verbatim) --------------------------------

    def push(self, t: str) -> None:
        if not self.unreachable:
            self.stack.append(t)

    def pop(self, t: str) -> None:
        """Pop one operand of type ``t``, never reaching below the current frame.

        wasm's ``pop_val`` refuses to see past the innermost control frame's
        height: operands pushed *outside* a block are not addressable from
        inside it. Without that rule an instruction inside a block could
        "borrow" a value belonging to the enclosing code and the frame's own
        height check at ``end`` would still balance.
        """
        if self.unreachable:
            return
        if not self.stack:
            raise EmitError(f"{self.name}: pop {t} from an empty operand stack")
        base = self.ctrl[-1].base if self.ctrl else 0
        if len(self.stack) <= base:
            raise EmitError(
                f"{self.name}: pop {t} below the innermost control frame's base "
                f"{base} (operand stack {self.stack}); operands pushed outside a "
                "frame are not visible inside it"
            )
        got = self.stack.pop()
        if got != t:
            raise EmitError(f"{self.name}: expected {t} on the operand stack, found {got}")

    # -- raw emission ------------------------------------------------------

    def op(self, opcode: int, *args: bytes) -> None:
        """Append one instruction: its opcode byte, then any immediate operands."""
        self._pending.append(opcode)
        for a in args:
            self._pending += a

    def _flush(self) -> None:
        """Close the current byte run so a symbolic item can follow it."""
        if self._pending:
            self.code.append(bytes(self._pending))
            self._pending = bytearray()

    def i64_const(self, v: int) -> None:
        """Push a Val-shaped 64-bit word (A3: the operand is ``sleb(as_i64(v))``)."""
        self.op(opcodes.I64_CONST, encode.sleb(val.as_i64(v)))
        self.push("i64")

    def i32_const(self, v: int) -> None:
        """Push an i32 immediate, accepting either signed or unsigned spelling.

        ``i32.const``'s operand is a **signed** LEB of a 32-bit word, but the
        i32s this emitter builds are addresses and masks, naturally written
        unsigned. Normalize the unsigned half of the range the way ``i64_const``
        leans on ``val.as_i64`` (A3), and refuse anything that is not a 32-bit
        word at all -- ``sleb`` would otherwise happily encode a 40-bit
        "address" that wasm rejects much later, or never terminate on a value
        this function had no business accepting.
        """
        if not -(1 << 31) <= v < (1 << 32):
            raise EmitError(
                f"{self.name}: i32 constant {v} is not a 32-bit word "
                f"(expected {-(1 << 31)} <= v < {1 << 32})"
            )
        self.op(opcodes.I32_CONST, encode.sleb(v - (1 << 32) if v >= 1 << 31 else v))
        self.push("i32")

    def drop(self, t: str = "i64") -> None:
        """Discard the top operand: pop the type **and** emit ``drop``, together.

        The two halves are one operation and are exposed as one, so a caller
        cannot emit the byte without the accounting (or vice versa). P2's bug
        -- a missing ``drop`` after a void host call -- is caught by ``ret()``;
        this makes the *inverse* slip, a ``drop`` byte with no pop, unforgeable.
        """
        self.pop(t)
        self.op(opcodes.DROP)

    def binop_i64(self, opcode: int) -> None:
        self.pop("i64")
        self.pop("i64")
        self.op(opcode)
        self.push("i64")

    def relop_i64(self, opcode: int) -> None:
        """A comparison: two i64 operands in, one **i32** out (0 or 1)."""
        self.pop("i64")
        self.pop("i64")
        self.op(opcode)
        self.push("i32")

    # -- locals (review M11) -----------------------------------------------

    @property
    def n_hidden(self) -> int:
        """How many hidden temps ``new_local()`` has handed out."""
        return self._n_hidden

    @property
    def nlocals(self) -> int:
        """Declared locals plus hidden temps -- what the code section declares."""
        return self.nlocals_declared + self._n_hidden

    def new_local(self) -> int:
        """Allocate one hidden ``i64`` temp, **after** every declared slot."""
        idx = self.nparams + self.nlocals_declared + self._n_hidden
        self._n_hidden += 1
        return idx

    def _check_local(self, i: int) -> None:
        limit = self.nparams + self.nlocals_declared + self._n_hidden
        if not 0 <= i < limit:
            raise EmitError(
                f"{self.name}: local index {i} is out of range: expected "
                f"0 <= i < {limit} ({self.nparams} param(s) + "
                f"{self.nlocals_declared} declared + {self._n_hidden} hidden)"
            )

    def local_get(self, i: int) -> None:
        self._check_local(i)
        self.op(opcodes.LOCAL_GET, encode.uleb(i))
        self.push("i64")

    def local_set(self, i: int) -> None:
        self._check_local(i)
        self.pop("i64")
        self.op(opcodes.LOCAL_SET, encode.uleb(i))

    def local_tee(self, i: int) -> None:
        """Store and keep: pop the value, write it, push it straight back."""
        self._check_local(i)
        self.pop("i64")
        self.op(opcodes.LOCAL_TEE, encode.uleb(i))
        self.push("i64")

    # -- control flow -------------------------------------------------------

    def _blocktype(self, result: str | None) -> int:
        if result is None:
            return opcodes.BLOCKTYPE_VOID
        if result == "i64":
            return opcodes.BLOCKTYPE_I64
        raise EmitError(
            f"{self.name}: unsupported block result type {result!r}; "
            "a frame is either void or i64 (multi-value is off, S23)"
        )

    def begin_if(self, result: str | None) -> None:
        blocktype = self._blocktype(result)
        self.pop("i32")
        self.ctrl.append(
            Frame(
                kind=FrameKind.IF,
                base=len(self.stack),
                result=result,
                entry_unreachable=self.unreachable,
            )
        )
        self.op(opcodes.IF, bytes([blocktype]))

    def begin_block(self, result: str | None, *, breakable: bool = False) -> None:
        """Open a ``block``: a forward label, ending at its ``end``.

        Pass ``breakable=True`` **only** for a loop's exit block -- the label
        `break` is meant to reach. ``br_break`` scans for the nearest frame so
        marked and skips every other block, so a block opened inside a loop
        body for an unrelated reason cannot silently steal a `break`. That
        retargeted branch would still *validate*; nothing downstream would
        catch it (§C.1's "an off-by-one here is a silently wrong branch").
        """
        blocktype = self._blocktype(result)
        self.ctrl.append(
            Frame(
                kind=FrameKind.BLOCK,
                base=len(self.stack),
                result=result,
                entry_unreachable=self.unreachable,
                breakable=breakable,
            )
        )
        self.op(opcodes.BLOCK, bytes([blocktype]))

    def begin_loop(self) -> None:
        """Open a ``loop``: a backward label, which is what `continue` targets.

        Always void. A loop's *result* type would describe what falls out of
        its ``end``; nothing this emitter builds needs one, and keeping it
        void keeps the branch arity of ``br_continue`` trivially empty.
        """
        self.ctrl.append(
            Frame(
                kind=FrameKind.LOOP,
                base=len(self.stack),
                result=None,
                entry_unreachable=self.unreachable,
            )
        )
        self.op(opcodes.LOOP, bytes([opcodes.BLOCKTYPE_VOID]))

    def _check_frame(self, frame: Frame, where: str) -> None:
        if self.unreachable:
            return
        want = frame.base + (1 if frame.result else 0)
        if len(self.stack) != want:
            raise EmitError(
                f"{self.name}: operand stack is {self.stack} at {where}; "
                f"expected {want} value(s) (frame base {frame.base}, "
                f"result {frame.result or 'void'})"
            )

    def else_(self) -> None:
        if not self.ctrl or self.ctrl[-1].kind is not FrameKind.IF:
            raise EmitError(f"{self.name}: else with no open if frame")
        frame = self.ctrl[-1]
        if frame.saw_else:
            raise EmitError(f"{self.name}: a second else on one if frame")
        self._check_frame(frame, "else")
        frame.saw_else = True
        del self.stack[frame.base :]
        # The `then` arm may have diverged; the `else` arm starts from the
        # reachability the code *around* the `if` had.
        self.unreachable = frame.entry_unreachable
        self.op(opcodes.ELSE)

    def end_if(self) -> None:
        """Close an ``if`` frame, pushing its result type if it has one."""
        self._close(if_frame=True)

    def end(self) -> None:
        """Close a ``block`` or ``loop`` frame."""
        self._close(if_frame=False)

    def _close(self, *, if_frame: bool) -> None:
        called = "end_if" if if_frame else "end"
        if not self.ctrl:
            raise EmitError(f"{self.name}: {called}() with no open control frame")
        if (self.ctrl[-1].kind is FrameKind.IF) is not if_frame:
            raise EmitError(
                f"{self.name}: {called}() was called but the innermost frame is a "
                f"{self.ctrl[-1].kind.name.lower()}; end_if() closes an if frame and "
                "end() closes a block or loop frame"
            )
        frame = self.ctrl[-1]
        if frame.kind is FrameKind.IF and frame.result is not None and not frame.saw_else:
            # wasm elides a missing `else` into an empty arm, which would have
            # to have type [] -> [i64]: impossible, so the module is rejected.
            # Caught here, at the node that built it.
            raise EmitError(
                f"{self.name}: an if with result {frame.result} was closed with no "
                "else arm; a result-bearing if needs both arms"
            )
        self.ctrl.pop()
        self._check_frame(frame, "end")
        del self.stack[frame.base :]
        self.unreachable = frame.entry_unreachable
        self.op(opcodes.END)
        if frame.result:
            self.push(frame.result)

    # -- branches ------------------------------------------------------------

    def br_break(self) -> None:
        """``br`` to the nearest enclosing **breakable** block -- `break`.

        Precondition: the loop being broken out of was opened as
        ``begin_block(None, breakable=True)`` around its ``loop``. Blocks
        opened for any other reason are skipped, so a block nested inside a
        loop body cannot capture the branch.
        """
        self._br(FrameKind.BLOCK, "br_break")

    def br_continue(self) -> None:
        """``br`` to the nearest enclosing ``loop`` -- what `continue` lowers to."""
        self._br(FrameKind.LOOP, "br_continue")

    def br_if_break(self) -> None:
        """``br_if`` to the nearest breakable block -- `while`'s exit test.

        The conditional twin of ``br_break``, and the only conditional branch
        this emitter emits: ``While`` lowers to ``block { loop { <not cond>;
        br_if $exit; ... } }`` (§B.3.2), so the target is always the loop's own
        exit block and the name says so rather than leaving it implied.

        Two differences from ``_br``, both load-bearing:

        * it **pops one i32** (the condition) before the branch-arity check, so
          the arity question is asked about what the target frame would
          actually receive;
        * it does **not** set the unreachable state. A conditional branch has a
          fall-through, and marking the code after it dead would switch off
          every height check over the rest of the loop body -- silently, and
          only inside loops.

        The depth comes from ``_br_target``, the same frame scan ``br_break``
        and ``br_continue`` use: there is exactly one place that turns "which
        frame" into "how many levels up", because an off-by-one here is a
        silently wrong branch (§C.1).
        """
        target, depth = self._br_target(FrameKind.BLOCK, "br_if_break")
        self.pop("i32")
        self._check_br_arity(target, depth, target.result, "br_if_break")
        self.op(opcodes.BR_IF, encode.uleb(depth))

    def _br(self, kind: FrameKind, where: str) -> None:
        target, depth = self._br_target(kind, where)
        # Branch arity. A `br` to a BLOCK targets that block's `end`, so it
        # must supply the block's result type. A `br` to a LOOP targets the
        # loop HEADER, whose arity is the loop's PARAMETER types -- none, for
        # every loop this emitter builds -- so `continue` must instead leave
        # the stack emptied down to the frame's base.
        arity = target.result if kind is FrameKind.BLOCK else None
        self._check_br_arity(target, depth, arity, where)
        self.op(opcodes.BR, encode.uleb(depth))
        # Everything after an unconditional branch is dead code; the frame's
        # `end` (or `else`) restores the enclosing reachability.
        self.unreachable = True

    def _br_target(self, kind: FrameKind, where: str) -> tuple[Frame, int]:
        """The nearest enclosing frame of ``kind`` and its RELATIVE depth.

        THE frame scan: every branch this module emits resolves its label here,
        so a `break` and a `br_if` to the same exit block cannot disagree about
        how many levels up it is.
        """
        for i, frame in enumerate(reversed(self.ctrl)):
            # A BLOCK is only a `break` label if it was marked as one; every
            # other block between here and the loop is jumped over.
            if frame.kind is kind and (kind is not FrameKind.BLOCK or frame.breakable):
                return frame, i
        what = "breakable block" if kind is FrameKind.BLOCK else kind.name.lower()
        raise EmitError(f"{self.name}: {where} with no enclosing {what} frame")

    def _check_br_arity(self, target: Frame, depth: int, arity: str | None, where: str) -> None:
        """A branch must leave exactly what its target's label expects."""
        if self.unreachable:
            return
        want = target.base + (1 if arity else 0)
        if len(self.stack) != want or (arity is not None and self.stack[-1] != arity):
            raise EmitError(
                f"{self.name}: operand stack is {self.stack} at {where}; "
                f"expected {want} value(s) with branch arity "
                f"{arity or 'none'} (target frame base {target.base}, "
                f"relative depth {depth})"
            )

    def unreachable_(self) -> None:
        """Emit ``unreachable`` (0x00) -- C1's diverging tail -- and go polymorphic."""
        self.op(opcodes.UNREACHABLE)
        self.unreachable = True

    # -- calls (review B1: symbolic now, indices in pass 2) ------------------

    def call_import(self, name: str, nargs: int, has_result: bool) -> None:
        """Call a host import by NAME; pass 2 resolves it to an import index."""
        self._call(CallImport(name), nargs, ("i64",) if has_result else ())

    def call_defined(self, defidx: int, nargs: int, results: tuple[str, ...]) -> None:
        """Call a defined function by its defined-space index (review B1)."""
        if defidx < 0:
            raise EmitError(f"{self.name}: defidx must be >= 0, got {defidx}")
        self._call(CallDefined(defidx), nargs, results)

    def _call(self, site: CallImport | CallDefined, nargs: int, results: tuple[str, ...]) -> None:
        if nargs < 0:
            raise EmitError(f"{self.name}: nargs must be >= 0, got {nargs}")
        for t in results:
            if t != "i64":
                raise EmitError(f"{self.name}: a call result must be i64, got {t!r}")
        for _ in range(nargs):
            self.pop("i64")
        self._flush()
        self.code.append(site)
        for t in results:
            self.push(t)

    # -- return and finish ----------------------------------------------------

    def ret(self) -> None:
        """A real ``return`` (0x0F). Everything after it is unreachable.

        The balance check has to happen *here*, not at ``finish()``. ``return``
        puts wasm's validator into its polymorphic-stack state, which happily
        tolerates operands left dangling underneath the result -- so a body
        that ends in ``return`` (every body this emitter produces) would sail
        past a check at ``end``. Concretely: drop the ``drop`` after
        ``put_contract_data`` and the leaked Void ``Val`` produces a module
        that still validates. Catch it at the ``return`` instead, while the
        stack still means something. (P2 -- the single most important line in
        this tracker.)

        Pops per ``results``: one i64 for a value-returning function, nothing
        for a ``results=()`` void helper (review M2).
        """
        for t in reversed(self.results):
            self.pop(t)
        if not self.unreachable:
            base = self.ctrl[-1].base if self.ctrl else 0
            if len(self.stack) != base:
                raise EmitError(
                    f"{self.name}: operand stack is {self.stack} at return; "
                    f"expected it emptied down to {base} once the result is "
                    "popped (a value was pushed and never consumed)"
                )
        self.op(opcodes.RETURN)
        self.unreachable = True

    def finish(self) -> list[CodeItem]:
        """Close the body, refusing to hand back code from an unbalanced stack.

        Returns the item list (review B1): runs of raw bytes interleaved with
        the symbolic call sites pass 2 resolves. The trailing ``end`` that
        every function body needs is appended here.
        """
        if self._finished:
            raise EmitError(f"{self.name}: finish() called twice")
        if self.ctrl:
            raise EmitError(f"{self.name}: {len(self.ctrl)} control frame(s) left open")
        if not self.unreachable and self.stack != list(self.results):
            raise EmitError(
                f"{self.name}: operand stack is {self.stack} at the end of the "
                f"body; expected exactly {list(self.results)}"
            )
        self._finished = True
        self.op(opcodes.END)
        self._flush()
        return list(self.code)

    # -- the structural S2 check (review M1) ----------------------------------

    @contextlib.contextmanager
    def expr_scope(self, is_void: bool) -> Iterator[None]:
        """Assert the typed NET stack delta across one expression lowering.

        The value model D commits to: **every expression lowering leaves net
        +1 i64**, or net 0 for a Void expression (review M1). Wrapping each
        ``lower_expr`` call in this is §C.1's cheap structural version of S2's
        "asserts at every control-flow merge" -- it catches a missing ``drop``
        or a double push at the node that caused it rather than three frames
        later.

        Skipped entirely when the function is in the unreachable state at
        either end of the scope: stack heights are meaningless there, and
        inventing an error would be worse than checking nothing.
        """
        before = len(self.stack)
        entry_unreachable = self.unreachable
        yield
        if self.unreachable or entry_unreachable:
            return
        want = before + (0 if is_void else 1)
        if len(self.stack) != want:
            raise EmitError(
                f"{self.name}: expression lowering left a net stack delta of "
                f"{len(self.stack) - before} (stack {self.stack}); expected "
                f"{'net 0 for a Void expression' if is_void else 'net +1 i64'}"
            )
        if not is_void and self.stack[-1] != "i64":
            raise EmitError(
                f"{self.name}: expression lowering left {self.stack[-1]} on top "
                "of the operand stack; expected i64 (every Val is an i64)"
            )
