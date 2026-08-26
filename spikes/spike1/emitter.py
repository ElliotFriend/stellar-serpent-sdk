"""Spike 1 backend: lower ``ContractIR`` to a real Soroban WASM module.

Hand-rolled binary emitter -- no wasm library, no LLVM. The LEB128 and section
framing come from ``reference/pycomp.py`` (proven on testnet); the code
generation is new, because the reference had no returns, no linear memory, no
error codes, and no validation.

What this spike is trying to find out, and where each answer lives:

* long symbols (>9 chars can't be a SymbolSmall) -- :meth:`_Fn.symbol`
* linear memory + a data section -- :class:`_Memory`
* struct construction via ``map_new_from_linear_memory`` -- :meth:`_Fn.make_struct`
* contract error codes that survive to the client -- :meth:`_Fn.if_raise`
* real ``return`` opcodes with a checked operand stack -- :class:`_Fn`

Value model
-----------
Everything on the operand stack is an i64 holding a Soroban ``Val`` (an
8-bit tag in the low byte, the body above it), except transient i32s used as
``if`` conditions and store addresses. The stack tracker below records the
wasm type of every pushed operand and refuses to emit a module whose stack
does not balance at every ``end`` -- the failure lands here, with a Python
traceback, rather than in ``wasm-tools validate`` or (worse) on-chain.

Memory layout (one 64 KiB page, exported as ``memory``)
------------------------------------------------------
=================== ==========================================================
``0x0000`` ..       static literal pool, emitted as the module's one active
                    data segment: long symbol names, string literals, and the
                    key descriptors below. Interned, so equal bytes are stored
                    once.
``0x1000`` ..       scratch, statically bump-allocated per ``MakeStruct`` call
                    site (never freed, never reused). Zero-initialized by wasm,
                    written at runtime with ``i64.store``.
=================== ==========================================================

The split is a fixed constant rather than "wherever the pool ended" so that
scratch addresses are known while a function body is still being compiled;
:func:`emit_module` asserts the pool never reaches ``0x1000``.

The two arrays ``map_new_from_linear_memory`` reads are **not** symmetrical,
which env.json's own docs spell out and which is worth restating because
guessing wrong here produces a module that validates and then panics on-chain:

* **keys** are ``len`` 8-byte *descriptors* -- a 4-byte little-endian pointer
  followed by a 4-byte little-endian length -- pointing at the raw name bytes.
  They are not ``Val``s, so nothing about them depends on runtime state: the
  whole keys array is a static blob in the data segment, and a field name too
  long for a ``SymbolSmall`` costs nothing extra here. The host requires them
  sorted ascending as byte strings, which is done at compile time.
* **values** are 8-byte ``Val``s, and those genuinely are runtime state (a
  host handle for the string literal, the caller's argument for the u32). That
  array -- and only that array -- is what scratch exists for.

Long field names still force ``symbol_new_from_linear_memory`` on the *read*
side: ``map_get`` takes a real ``Symbol`` ``Val``, and ``counter_limit`` is 13
characters.
"""

from __future__ import annotations

import json
import pathlib
import struct
from dataclasses import dataclass, field

import sections
from frontend import (
    AddU32,
    ConstString,
    ConstSymbol,
    ConstU32,
    ContractIR,
    Expr,
    FuncIR,
    GetField,
    GtU32,
    IfRaise,
    LoadDurable,
    LoadInstance,
    LocalGet,
    LocalSet,
    MakeStruct,
    Param,
    Return,
    Stmt,
    StoreDurable,
    StoreInstance,
)


class EmitError(Exception):
    """Raised for anything the backend cannot lower, or any stack imbalance.

    Deliberately an exception rather than ``assert`` so the checks survive
    ``python -O``: an unbalanced module must never reach a file.
    """


# ---------------------------------------------------------------- LEB128 etc.
# (lifted from reference/pycomp.py -- the parts that were already right)


def uleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def sleb(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        done = (n == 0 and not b & 0x40) or (n == -1 and b & 0x40)
        out.append(b if done else b | 0x80)
        if done:
            return bytes(out)


def vec(items: list[bytes]) -> bytes:
    return uleb(len(items)) + b"".join(items)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + uleb(len(payload)) + payload


def wasm_name(s: str) -> bytes:
    return uleb(len(s.encode())) + s.encode()


def custom_section(name: str, payload: bytes) -> bytes:
    return section(0, wasm_name(name) + payload)


# ------------------------------------------------------------- Soroban Val ABI

TAG_FALSE = 0
TAG_TRUE = 1
TAG_VOID = 2
TAG_ERROR = 3
TAG_U32 = 4
TAG_SYMBOL = 14

VOID_VAL = TAG_VOID
SYMBOL_SMALL_MAX = 9

# StorageType is marshalled as a *bare* u64 scalar, not a tagged Val.
STORAGE_TEMPORARY = 0
STORAGE_PERSISTENT = 1
STORAGE_INSTANCE = 2

# Contract error code raised when an exported function is handed an argument
# whose Val tag is not the one its signature declares (spec §4's tag check).
ERR_BAD_ARG_TAG = 0xFFFF_FFFF


def _sym_char_code(c: str) -> int:
    if c == "_":
        return 1
    if "0" <= c <= "9":
        return 2 + ord(c) - ord("0")
    if "A" <= c <= "Z":
        return 12 + ord(c) - ord("A")
    if "a" <= c <= "z":
        return 38 + ord(c) - ord("a")
    raise ValueError(f"character {c!r} is not legal in a Soroban symbol")


def symbol_small(s: str) -> int:
    """Pack <=9 chars into a ``SymbolSmall`` ``Val`` (6 bits/char, high first).

    Raises ``ValueError`` above 9 characters instead of silently truncating --
    that overflow is exactly the bug this spike exists to rule out.
    """
    if len(s) > SYMBOL_SMALL_MAX:
        raise ValueError(
            f"symbol {s!r} is {len(s)} chars; SymbolSmall holds at most {SYMBOL_SMALL_MAX}"
        )
    body = 0
    for c in s:
        body = (body << 6) | _sym_char_code(c)
    return (body << 8) | TAG_SYMBOL


def pack_u32val(x: int) -> int:
    """A raw u32 as a ``U32Val``."""
    if not 0 <= x <= 0xFFFF_FFFF:
        raise ValueError(f"{x} is out of range for a u32")
    return (x << 32) | TAG_U32


def error_val(code: int) -> int:
    """A contract error ``Val``: type ``SCE_CONTRACT`` (0), code in the high word."""
    if not 0 <= code <= 0xFFFF_FFFF:
        raise ValueError(f"{code} is out of range for a contract error code")
    return (code << 32) | TAG_ERROR


def as_i64(v: int) -> int:
    """Reinterpret an unsigned 64-bit ``Val`` as the signed value ``i64.const`` takes."""
    return v - (1 << 64) if v >= (1 << 63) else v


# ---------------------------------------------------------------- host imports


@dataclass(frozen=True)
class HostFn:
    """One entry from the vendored ``env.json``."""

    name: str
    module: str
    field: str
    nargs: int
    min_protocol: int | None


def load_host_fns(env_json: pathlib.Path, names: set[str]) -> dict[str, HostFn]:
    """Look the required host functions up *by name* in the pinned env.json.

    The module/field codes (``l._``, ``b.j``, ...) are never hardcoded here:
    they are what the pinned env.json says they are, so re-pinning the file is
    the only way to change them.
    """
    doc = json.loads(env_json.read_text())
    found: dict[str, HostFn] = {}
    for mod in doc["modules"]:
        for fn in mod["functions"]:
            if fn["name"] in names:
                found[fn["name"]] = HostFn(
                    name=fn["name"],
                    module=mod["export"],
                    field=fn["export"],
                    nargs=len(fn["args"]),
                    min_protocol=fn.get("min_supported_protocol"),
                )
    missing = names - found.keys()
    if missing:
        raise EmitError(f"host functions absent from {env_json}: {sorted(missing)}")
    return found


HOST_FN_NAMES = {
    "put_contract_data",
    "has_contract_data",
    "get_contract_data",
    "map_new_from_linear_memory",
    "map_get",
    "symbol_new_from_linear_memory",
    "string_new_from_linear_memory",
    "fail_with_error",
}


def protocol_floor(host_fns: dict[str, HostFn], base: int) -> int:
    """The lowest protocol that can run these imports.

    None of the eight carries a ``min_supported_protocol`` in v28.0.2, so this
    is the base protocol -- but the computation is real, not assumed, so the
    number moves on its own when a newer host function is used.
    """
    return max([base] + [f.min_protocol for f in host_fns.values() if f.min_protocol is not None])


# ------------------------------------------------------------------- opcodes

END = 0x0B
RETURN = 0x0F
CALL = 0x10
DROP = 0x1A
LOCAL_GET = 0x20
LOCAL_SET = 0x21
I64_STORE = 0x37
I32_CONST = 0x41
I64_CONST = 0x42
I64_EQ = 0x51
I64_NE = 0x52
I64_GT_U = 0x56
I64_ADD = 0x7C
I64_AND = 0x83
I64_OR = 0x84
I64_SHL = 0x86
I64_SHR_U = 0x88
I64_EXTEND_I32_U = 0xAD
IF = 0x04
ELSE = 0x05

I64 = 0x7E
BLOCK_VOID = 0x40


# ------------------------------------------------------------- linear memory

PAGE = 65536
SCRATCH_BASE = 0x1000


class _Memory:
    """The literal pool plus a compile-time bump allocator for scratch."""

    def __init__(self) -> None:
        self.pool = bytearray()
        self._interned: dict[bytes, int] = {}
        self._scratch = SCRATCH_BASE

    def intern(self, blob: bytes, align: int = 1) -> int:
        """Offset of ``blob`` in the data segment, storing it if new."""
        if blob not in self._interned:
            pad = (-len(self.pool)) % align
            self.pool += b"\x00" * pad
            self._interned[blob] = len(self.pool)
            self.pool += blob
        return self._interned[blob]

    def scratch(self, nbytes: int) -> int:
        """Reserve 8-byte-aligned scratch for one call site, forever."""
        off = self._scratch
        self._scratch += (nbytes + 7) & ~7
        return off

    def check(self) -> None:
        if len(self.pool) > SCRATCH_BASE:
            raise EmitError(
                f"literal pool ({len(self.pool)}B) has grown into scratch at {SCRATCH_BASE:#x}"
            )
        if self._scratch > PAGE:
            raise EmitError(f"scratch ({self._scratch}B) does not fit in one {PAGE}B page")


# ------------------------------------------------ one function + stack tracker


@dataclass
class _Ctrl:
    """A control frame: the stack height on entry and the block's result type."""

    base: int
    result: str | None
    # Whether the code *around* the frame was already unreachable; each arm
    # starts reachable, and the frame's end restores the enclosing state.
    entry_unreachable: bool


@dataclass
class _Fn:
    """A function body under construction, with its operand stack tracked.

    ``stack`` holds the wasm type ("i64"/"i32") of every live operand.
    ``unreachable`` mirrors wasm's polymorphic-stack rule: once ``return`` has
    executed, the validator stops caring what is on the stack, and so do we.
    """

    name: str
    nparams: int
    code: bytearray = field(default_factory=bytearray)
    stack: list[str] = field(default_factory=list)
    ctrl: list[_Ctrl] = field(default_factory=list)
    unreachable: bool = False
    locals: dict[str, int] = field(default_factory=dict)

    # -- stack bookkeeping --

    def push(self, t: str) -> None:
        if not self.unreachable:
            self.stack.append(t)

    def pop(self, t: str) -> None:
        if self.unreachable:
            return
        if not self.stack:
            raise EmitError(f"{self.name}: pop {t} from an empty operand stack")
        got = self.stack.pop()
        if got != t:
            raise EmitError(f"{self.name}: expected {t} on the operand stack, found {got}")

    # -- raw emission --

    def op(self, opcode: int, *args: bytes) -> None:
        self.code.append(opcode)
        for a in args:
            self.code += a

    def i64_const(self, v: int) -> None:
        self.op(I64_CONST, sleb(as_i64(v)))
        self.push("i64")

    def i32_const(self, v: int) -> None:
        self.op(I32_CONST, sleb(v))
        self.push("i32")

    def binop_i64(self, opcode: int) -> None:
        self.pop("i64")
        self.pop("i64")
        self.op(opcode)
        self.push("i64")

    def relop_i64(self, opcode: int) -> None:
        self.pop("i64")
        self.pop("i64")
        self.op(opcode)
        self.push("i32")

    def local_index(self, n: str) -> int:
        if n not in self.locals:
            self.locals[n] = self.nparams + len(self.locals)
        return self.locals[n]

    @property
    def nlocals(self) -> int:
        return len(self.locals)

    # -- control flow --

    def begin_if(self, result: str | None) -> None:
        self.pop("i32")
        self.ctrl.append(
            _Ctrl(base=len(self.stack), result=result, entry_unreachable=self.unreachable)
        )
        self.op(IF, bytes([I64 if result == "i64" else BLOCK_VOID]))

    def else_(self) -> None:
        frame = self.ctrl[-1]
        self._check_frame(frame, "else")
        del self.stack[frame.base :]
        self.unreachable = frame.entry_unreachable
        self.op(ELSE)

    def end_if(self) -> None:
        frame = self.ctrl.pop()
        self._check_frame(frame, "end")
        del self.stack[frame.base :]
        self.unreachable = frame.entry_unreachable
        self.op(END)
        if frame.result:
            self.push(frame.result)

    def _check_frame(self, frame: _Ctrl, where: str) -> None:
        if self.unreachable:
            return
        want = frame.base + (1 if frame.result else 0)
        if len(self.stack) != want:
            raise EmitError(
                f"{self.name}: operand stack is {self.stack} at {where}; "
                f"expected {want} value(s) (frame base {frame.base}, "
                f"result {frame.result or 'void'})"
            )

    def ret(self) -> None:
        """A real ``return`` (0x0F). Everything after it is unreachable."""
        self.pop("i64")
        self.op(RETURN)
        self.unreachable = True

    def finish(self) -> bytes:
        """Close the body, refusing to hand back bytes from an unbalanced stack."""
        if self.ctrl:
            raise EmitError(f"{self.name}: {len(self.ctrl)} control frame(s) left open")
        if not self.unreachable and self.stack != ["i64"]:
            raise EmitError(
                f"{self.name}: operand stack is {self.stack} at the end of the body; "
                "expected exactly one i64 result"
            )
        return bytes(self.code) + bytes([END])


# ------------------------------------------------------------------- compiler


class Compiler:
    """Lowers one ``ContractIR`` into the pieces of a wasm module."""

    def __init__(self, ir: ContractIR, host_fns: dict[str, HostFn]) -> None:
        self.ir = ir
        self.host = host_fns
        self.mem = _Memory()
        # Import order fixes the host function indices; defined functions follow.
        self.import_order = sorted(host_fns)
        self.host_index = {n: i for i, n in enumerate(self.import_order)}
        self.first_defined = len(self.import_order)

    # -- helpers --

    def call_host(self, fn: _Fn, name: str) -> None:
        h = self.host[name]
        for _ in range(h.nargs):
            fn.pop("i64")
        fn.op(CALL, uleb(self.host_index[name]))
        fn.push("i64")  # every host function used here returns a Val

    def symbol(self, fn: _Fn, text: str) -> None:
        """Push a ``Symbol`` ``Val``: a constant when it fits, a host call when not."""
        if len(text) <= SYMBOL_SMALL_MAX:
            fn.i64_const(symbol_small(text))
            return
        blob = text.encode()
        off = self.mem.intern(blob)
        fn.i64_const(pack_u32val(off))
        fn.i64_const(pack_u32val(len(blob)))
        self.call_host(fn, "symbol_new_from_linear_memory")

    def string(self, fn: _Fn, text: str) -> None:
        blob = text.encode()
        off = self.mem.intern(blob)
        fn.i64_const(pack_u32val(off))
        fn.i64_const(pack_u32val(len(blob)))
        self.call_host(fn, "string_new_from_linear_memory")

    def store_val(self, fn: _Fn, addr: int, value: Expr) -> None:
        """``i64.store`` the Val that ``value`` evaluates to at a fixed address."""
        fn.i32_const(addr)
        self.expr(fn, value)
        fn.pop("i64")
        fn.pop("i32")
        fn.op(I64_STORE, uleb(3), uleb(0))  # align=2^3=8 bytes, offset=0

    def unpack_u32(self, fn: _Fn) -> None:
        """U32Val -> raw u32 (zero-extended in the i64)."""
        fn.i64_const(32)
        fn.binop_i64(I64_SHR_U)

    def pack_u32(self, fn: _Fn) -> None:
        """raw u32 -> U32Val. ``shl 32`` also does the u32 wraparound for us."""
        fn.i64_const(32)
        fn.binop_i64(I64_SHL)
        fn.i64_const(TAG_U32)
        fn.binop_i64(I64_OR)

    # -- expressions --

    def expr(self, fn: _Fn, e: Expr) -> None:
        """Compile ``e``, leaving exactly one i64 ``Val`` on the stack."""
        if isinstance(e, Param):
            fn.op(LOCAL_GET, uleb(e.i))
            fn.push("i64")
        elif isinstance(e, LocalGet):
            fn.op(LOCAL_GET, uleb(fn.local_index(e.name)))
            fn.push("i64")
        elif isinstance(e, ConstU32):
            fn.i64_const(pack_u32val(e.value))
        elif isinstance(e, ConstSymbol):
            self.symbol(fn, e.value)
        elif isinstance(e, ConstString):
            self.string(fn, e.value)
        elif isinstance(e, MakeStruct):
            self.make_struct(fn, e)
        elif isinstance(e, GetField):
            self.expr(fn, e.obj)
            self.symbol(fn, e.field)
            self.call_host(fn, "map_get")
        elif isinstance(e, AddU32):
            self.expr(fn, e.left)
            self.unpack_u32(fn)
            self.expr(fn, e.right)
            self.unpack_u32(fn)
            fn.binop_i64(I64_ADD)
            self.pack_u32(fn)
        elif isinstance(e, GtU32):
            # As a *value* the comparison is a Bool Val, and Bool Vals are
            # literally 0 (false) / 1 (true), so the i32 extends straight in.
            self.cond(fn, e)
            fn.pop("i32")
            fn.op(I64_EXTEND_I32_U)
            fn.push("i64")
        elif isinstance(e, LoadInstance):
            self.expr(fn, e.key)
            fn.i64_const(STORAGE_INSTANCE)
            self.call_host(fn, "get_contract_data")
        elif isinstance(e, LoadDurable):
            self.load_durable(fn, e)
        else:
            raise EmitError(f"{fn.name}: cannot compile expression {type(e).__name__}")

    def cond(self, fn: _Fn, e: Expr) -> None:
        """Compile ``e`` down to an i32 branch condition."""
        if isinstance(e, GtU32):
            self.expr(fn, e.left)
            self.unpack_u32(fn)
            self.expr(fn, e.right)
            self.unpack_u32(fn)
            fn.relop_i64(I64_GT_U)
            return
        # Anything else: compile it as a Val and test it against Bool true.
        self.expr(fn, e)
        fn.i64_const(TAG_TRUE)
        fn.relop_i64(I64_EQ)

    def make_struct(self, fn: _Fn, e: MakeStruct) -> None:
        """A struct literal is a host Map: a static keys blob + a scratch vals array.

        Per env.json, the keys array holds 8-byte ``(u32 pointer, u32 length)``
        descriptors of the *name bytes* -- not Symbol ``Val``s -- so it is
        entirely compile-time data and lives in the data segment. Only the
        values array is written at runtime.

        Field names are sorted here, on their bytes: the host requires keys in
        ascending order and panics otherwise.
        """
        fields = sorted(e.fields, key=lambda kv: kv[0].encode())
        n = len(fields)
        keys_blob = b""
        for fname, _ in fields:
            name_bytes = fname.encode()
            keys_blob += struct.pack("<II", self.mem.intern(name_bytes), len(name_bytes))
        keys_off = self.mem.intern(keys_blob, align=8)

        vals_off = self.mem.scratch(8 * n)
        for i, (_fname, fexpr) in enumerate(fields):
            self.store_val(fn, vals_off + 8 * i, fexpr)

        fn.i64_const(pack_u32val(keys_off))
        fn.i64_const(pack_u32val(vals_off))
        fn.i64_const(pack_u32val(n))
        self.call_host(fn, "map_new_from_linear_memory")

    def load_durable(self, fn: _Fn, e: LoadDurable) -> None:
        if e.default is None:
            self.expr(fn, e.key)
            fn.i64_const(STORAGE_PERSISTENT)
            self.call_host(fn, "get_contract_data")
            return
        # has_contract_data returns a Bool Val (0/1); compare to true, then
        # pick a branch of an i64-typed `if`.
        self.expr(fn, e.key)
        fn.i64_const(STORAGE_PERSISTENT)
        self.call_host(fn, "has_contract_data")
        fn.i64_const(TAG_TRUE)
        fn.relop_i64(I64_EQ)
        fn.begin_if("i64")
        self.expr(fn, e.key)
        fn.i64_const(STORAGE_PERSISTENT)
        self.call_host(fn, "get_contract_data")
        fn.else_()
        self.expr(fn, e.default)
        fn.end_if()

    # -- statements --

    def stmt(self, fn: _Fn, s: Stmt) -> None:
        if isinstance(s, LocalSet):
            self.expr(fn, s.value)
            fn.pop("i64")
            fn.op(LOCAL_SET, uleb(fn.local_index(s.name)))
        elif isinstance(s, StoreInstance):
            self.put(fn, s.key, s.value, STORAGE_INSTANCE)
        elif isinstance(s, StoreDurable):
            self.put(fn, s.key, s.value, STORAGE_PERSISTENT)
        elif isinstance(s, IfRaise):
            self.if_raise(fn, s)
        elif isinstance(s, Return):
            if s.value is None:
                fn.i64_const(VOID_VAL)
            else:
                self.expr(fn, s.value)
            fn.ret()
        else:
            raise EmitError(f"{fn.name}: cannot compile statement {type(s).__name__}")

    def put(self, fn: _Fn, key: Expr, value: Expr, storage: int) -> None:
        self.expr(fn, key)
        self.expr(fn, value)
        fn.i64_const(storage)
        self.call_host(fn, "put_contract_data")
        fn.pop("i64")  # the Void Val it returns
        fn.op(DROP)

    def if_raise(self, fn: _Fn, s: IfRaise) -> None:
        """``if cond: raise Error.X`` -> ``fail_with_error`` with the code intact.

        No ``unreachable``: the host traps inside ``fail_with_error``, and an
        ``unreachable`` after it would replace the contract error the client
        needs to see with a generic VM trap. The Void Val it nominally returns
        is dropped purely to keep the block's stack balanced for the validator.
        """
        self.cond(fn, s.cond)
        fn.begin_if(None)
        self.raise_error(fn, s.code)
        fn.end_if()

    def raise_error(self, fn: _Fn, code: int) -> None:
        fn.i64_const(error_val(code))
        self.call_host(fn, "fail_with_error")
        fn.pop("i64")
        fn.op(DROP)

    # -- functions --

    def abi_prologue(self, fn: _Fn, ir_fn: FuncIR) -> None:
        """Reject arguments whose Val tag isn't the declared one (spec §4).

        Only U32 is checked -- it is the only scalar in the fixture, and this
        spike is measuring the shape of the check, not its coverage.
        """
        for i, (_pname, ptype) in enumerate(ir_fn.params):
            if ptype != "U32":
                continue
            fn.op(LOCAL_GET, uleb(i))
            fn.push("i64")
            fn.i64_const(0xFF)
            fn.binop_i64(I64_AND)
            fn.i64_const(TAG_U32)
            fn.relop_i64(I64_NE)
            fn.begin_if(None)
            self.raise_error(fn, ERR_BAD_ARG_TAG)
            fn.end_if()

    def compile_function(self, ir_fn: FuncIR) -> _Fn:
        fn = _Fn(name=ir_fn.name, nparams=len(ir_fn.params))
        self.abi_prologue(fn, ir_fn)
        for s in ir_fn.body:
            self.stmt(fn, s)
        if not fn.unreachable:
            # Falling off the end of a contract function returns Void.
            fn.i64_const(VOID_VAL)
            fn.ret()
        return fn


# --------------------------------------------------------------- module layout


def _func_type(nparams: int, nresults: int) -> bytes:
    return (
        bytes([0x60])
        + uleb(nparams)
        + bytes([I64] * nparams)
        + uleb(nresults)
        + bytes([I64] * nresults)
    )


def emit_module(
    ir: ContractIR,
    host_fns: dict[str, HostFn],
    *,
    protocol: int,
    meta_pairs: dict[str, str],
) -> bytes:
    """Compile ``ir`` to a complete Soroban wasm module.

    Every function body is compiled -- and its operand stack checked -- before
    a single byte of the module is laid out, so a codegen bug raises
    ``EmitError`` instead of producing a plausible-looking file.
    """
    comp = Compiler(ir, host_fns)
    bodies = [(f, comp.compile_function(f)) for f in ir.functions]
    comp.mem.check()

    # -- type section: dedupe (nparams -> results=1); all params/results are i64
    types: list[bytes] = []

    def type_index(nparams: int) -> int:
        t = _func_type(nparams, 1)
        if t not in types:
            types.append(t)
        return types.index(t)

    import_entries = [
        (
            wasm_name(host_fns[n].module)
            + wasm_name(host_fns[n].field)
            + b"\x00"
            + uleb(type_index(host_fns[n].nargs))
        )
        for n in comp.import_order
    ]
    func_types = [uleb(type_index(len(f.params))) for f in ir.functions]

    exports = [
        wasm_name(f.name) + b"\x00" + uleb(comp.first_defined + i)
        for i, f in enumerate(ir.functions)
    ]
    exports.append(wasm_name("memory") + b"\x02" + uleb(0))

    code_entries = []
    for _ir_fn, fn in bodies:
        decl = vec([uleb(fn.nlocals) + bytes([I64])]) if fn.nlocals else vec([])
        body = decl + fn.finish()
        code_entries.append(uleb(len(body)) + body)

    out = bytearray(b"\x00asm\x01\x00\x00\x00")
    out += section(1, vec(types))
    out += section(2, vec(import_entries))
    out += section(3, vec(func_types))
    out += section(5, vec([b"\x00" + uleb(1)]))  # one page, no maximum
    out += section(7, vec(exports))
    out += section(10, vec(code_entries))
    if comp.mem.pool:
        # One active segment in memory 0 at offset 0.
        seg = uleb(0) + bytes([I32_CONST]) + sleb(0) + bytes([END])
        seg += uleb(len(comp.mem.pool)) + bytes(comp.mem.pool)
        out += section(11, vec([seg]))

    out += custom_section("contractenvmetav0", sections.env_meta(protocol))
    out += custom_section("contractspecv0", sections.spec_entries(ir))
    out += custom_section("contractmetav0", sections.meta(meta_pairs))
    return bytes(out)
