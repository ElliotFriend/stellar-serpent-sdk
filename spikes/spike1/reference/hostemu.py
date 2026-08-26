"""Minimal pure-Python Soroban host emulator, running the SAME wasm that runs on-chain.
Proof of the hybrid architecture: compiled guest + Python host for native testing."""
from wasmtime import Store, Module, Linker, Engine, FuncType, ValType, Func

# ---- Val tagged-integer codec (the whole ABI, in ~30 lines) ----
TAG_FALSE, TAG_TRUE, TAG_VOID, TAG_ERROR = 0, 1, 2, 3
TAG_U32, TAG_I32, TAG_U64_SMALL, TAG_I64_SMALL = 4, 5, 6, 7
TAG_SYMBOL_SMALL = 14

def tag(v): return v & 0xFF
def body(v): return v >> 8

def u32val(x): return (x << 32) | TAG_U32
def from_u32val(v):
    assert tag(v) == TAG_U32, f"expected U32Val, got tag {tag(v)}"
    return v >> 32

def _code(c):
    if c == "_": return 1
    if "0" <= c <= "9": return 2 + ord(c) - ord("0")
    if "A" <= c <= "Z": return 12 + ord(c) - ord("A")
    if "a" <= c <= "z": return 38 + ord(c) - ord("a")
    raise ValueError(c)

def symbol_small(s):
    a = 0
    for c in s: a = (a << 6) | _code(c)
    return (a << 8) | TAG_SYMBOL_SMALL

_REV = {1: "_"}
for i in range(10): _REV[2 + i] = chr(ord("0") + i)
for i in range(26): _REV[12 + i] = chr(ord("A") + i)
for i in range(26): _REV[38 + i] = chr(ord("a") + i)

def symbol_str(v):
    assert tag(v) == TAG_SYMBOL_SMALL
    b, out = body(v), []
    while b:
        out.append(_REV[b & 63]); b >>= 6
    return "".join(reversed(out))

def sv(v):
    """pretty-print a Val for assertions/debugging"""
    t = tag(v)
    return {TAG_FALSE: "false", TAG_TRUE: "true", TAG_VOID: "void"}.get(t) or (
        f"U32({v >> 32})" if t == TAG_U32 else
        f"Symbol({symbol_str(v)!r})" if t == TAG_SYMBOL_SMALL else f"Val(tag={t})")

STORAGE_NAMES = {0: "temporary", 1: "persistent", 2: "instance"}


class SorobanHost:
    """Emulates the subset of Soroban host functions our guest imports."""
    def __init__(self):
        self.storage = {0: {}, 1: {}, 2: {}}
        self.trace = []

    # --- ledger module "l" ---
    def put_contract_data(self, k, v, t):
        self.trace.append(f"put[{STORAGE_NAMES[t]}] {sv(k)} = {sv(v)}")
        self.storage[t][k] = v
        return TAG_VOID

    def has_contract_data(self, k, t):
        r = k in self.storage[t]
        self.trace.append(f"has[{STORAGE_NAMES[t]}] {sv(k)} -> {r}")
        return TAG_TRUE if r else TAG_FALSE

    def get_contract_data(self, k, t):
        if k not in self.storage[t]:
            raise RuntimeError(f"missing key {sv(k)}")
        v = self.storage[t][k]
        self.trace.append(f"get[{STORAGE_NAMES[t]}] {sv(k)} -> {sv(v)}")
        return v


class Contract:
    """Loads a compiled Soroban wasm and wires its imports to the Python host."""
    def __init__(self, path, host=None):
        self.host = host or SorobanHost()
        self.engine = Engine()
        self.store = Store(self.engine)
        module = Module.from_file(self.engine, path)
        linker = Linker(self.engine)

        i64 = ValType.i64()
        def bind(mod, name, arity, fn):
            ft = FuncType([i64] * arity, [i64])
            linker.define(self.store, mod, name,
                          Func(self.store, ft, lambda *a, _f=fn: _f(*a)))

        bind("l", "_", 3, self.host.put_contract_data)
        bind("l", "0", 2, self.host.has_contract_data)
        bind("l", "1", 2, self.host.get_contract_data)

        self.instance = linker.instantiate(self.store, module)

    def call(self, name, *args):
        f = self.instance.exports(self.store)[name]
        return f(self.store, *args)


if __name__ == "__main__":
    c = Contract("counter.wasm")
    print("get       ->", sv(c.call("get")))
    for _ in range(3):
        print("increment ->", sv(c.call("increment")))
    print("get       ->", sv(c.call("get")))
    print()
    assert from_u32val(c.call("get")) == 3, "counter should be 3"
    print("ASSERTION PASSED: counter == 3 (matches on-chain result)")
    print("\n--- host call trace (last 6) ---")
    for line in c.host.trace[-6:]:
        print("  " + line)
