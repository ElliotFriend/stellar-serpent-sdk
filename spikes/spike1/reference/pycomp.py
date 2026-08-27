"""Spike: Python AST -> Soroban WASM, with a hand-rolled binary encoder.
Purpose is to MEASURE the effort of the custom-compiler route, not to be complete.
No linear memory, no GC, no runtime. Rich values stay host-side as handles."""
import ast, json, struct, sys

# ============================ WASM binary encoder ============================
def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | 0x80 if n else b)
        if not n: return bytes(out)

def sleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        done = (n == 0 and not b & 0x40) or (n == -1 and b & 0x40)
        out.append(b if done else b | 0x80)
        if done: return bytes(out)

def vec(items): return uleb(len(items)) + b"".join(items)
def section(sid, payload): return bytes([sid]) + uleb(len(payload)) + payload
def name(s): return uleb(len(s.encode())) + s.encode()

I64 = 0x7E
OP = dict(end=0x0B, call=0x10, drop=0x1A, local_get=0x20, local_set=0x21,
          local_tee=0x22, i64_const=0x42, i64_eq=0x51, i64_ne=0x52,
          i64_lt_s=0x53, i64_gt_s=0x55, i64_le_s=0x57, i64_ge_s=0x59,
          i64_add=0x7C, i64_sub=0x7D, i64_mul=0x7E, i64_div_s=0x7F,
          i64_rem_s=0x81, i64_and=0x83, i64_or=0x84, i64_shl=0x86,
          i64_shr_s=0x87, i64_shr_u=0x88,
          if_=0x04, else_=0x05, block=0x02, loop=0x03, br=0x0C, br_if=0x0D,
          i32_eqz=0x45, i64_extend_i32_u=0xAD)


class WasmModule:
    def __init__(self):
        self.types, self.imports, self.funcs, self.exports = [], [], [], []

    def _type(self, nparams, nresults):
        t = bytes([0x60]) + uleb(nparams) + bytes([I64] * nparams) + \
            uleb(nresults) + bytes([I64] * nresults)
        if t not in self.types: self.types.append(t)
        return self.types.index(t)

    def add_import(self, mod, fld, nparams, nresults=1):
        ti = self._type(nparams, nresults)
        self.imports.append(name(mod) + name(fld) + b"\x00" + uleb(ti))
        return len(self.imports) - 1          # function index

    def add_func(self, nparams, nresults, nlocals, code, export=None):
        ti = self._type(nparams, nresults)
        self.funcs.append((ti, nlocals, code))
        idx = len(self.imports) + len(self.funcs) - 1
        if export: self.exports.append(name(export) + b"\x00" + uleb(idx))
        return idx

    def emit(self):
        out = b"\x00asm\x01\x00\x00\x00"
        out += section(1, vec(self.types))
        if self.imports: out += section(2, vec(self.imports))
        out += section(3, vec([uleb(t) for t, _, _ in self.funcs]))
        out += section(7, vec(self.exports))
        bodies = []
        for _, nl, code in self.funcs:
            locals_ = vec([uleb(nl) + bytes([I64])]) if nl else vec([])
            body = locals_ + code + bytes([OP["end"]])
            bodies.append(uleb(len(body)) + body)
        out += section(10, vec(bodies))
        return out


# ============================ Soroban Val ABI ============================
TAG_VOID, TAG_U32, TAG_SYMBOL = 2, 4, 14

def _code(c):
    if c == "_": return 1
    if "0" <= c <= "9": return 2 + ord(c) - ord("0")
    if "A" <= c <= "Z": return 12 + ord(c) - ord("A")
    if "a" <= c <= "z": return 38 + ord(c) - ord("a")
    raise ValueError(c)

def symbol_small(s):
    a = 0
    for c in s: a = (a << 6) | _code(c)
    return (a << 8) | TAG_SYMBOL


# ============================ Compiler ============================
class CompileError(Exception): pass

# host functions this spike knows about, loaded from env.json in the real thing
HOST = {
    "put_contract_data": ("l", "_", 3),
    "has_contract_data": ("l", "0", 2),
    "get_contract_data": ("l", "1", 2),
}

BINOP = {ast.Add: "i64_add", ast.Sub: "i64_sub", ast.Mult: "i64_mul",
         ast.FloorDiv: "i64_div_s", ast.Mod: "i64_rem_s",
         ast.BitAnd: "i64_and", ast.BitOr: "i64_or",
         ast.LShift: "i64_shl", ast.RShift: "i64_shr_s"}
CMPOP = {ast.Eq: "i64_eq", ast.NotEq: "i64_ne", ast.Lt: "i64_lt_s",
         ast.Gt: "i64_gt_s", ast.LtE: "i64_le_s", ast.GtE: "i64_ge_s"}


class FuncCompiler:
    """Compiles one function body. Everything is an i64 (a Val or a raw int)."""
    def __init__(self, mod, host_idx, params, user_idx=None):
        self.mod, self.host_idx = mod, host_idx
        self.user_idx = user_idx or {}
        self.locals = {p: i for i, p in enumerate(params)}
        self.nparams = len(params)
        self.code = bytearray()

    def local(self, n):
        if n not in self.locals: self.locals[n] = len(self.locals)
        return self.locals[n]

    def op(self, o, *args):
        self.code.append(OP[o])
        for a in args: self.code += a

    def const(self, v): self.op("i64_const", sleb(v))

    def expr(self, n):
        if isinstance(n, ast.Constant):
            if isinstance(n.value, bool): self.const(1 if n.value else 0)
            elif isinstance(n.value, int): self.const(n.value)
            else: raise CompileError(f"unsupported constant {n.value!r}")
        elif isinstance(n, ast.Name):
            if n.id not in self.locals:
                raise CompileError(f"undefined name {n.id!r}")
            self.op("local_get", uleb(self.locals[n.id]))
        elif isinstance(n, ast.BinOp):
            self.expr(n.left); self.expr(n.right)
            k = type(n.op)
            if k not in BINOP: raise CompileError(f"unsupported operator {k.__name__}")
            self.op(BINOP[k])
        elif isinstance(n, ast.Compare):
            if len(n.ops) != 1: raise CompileError("chained comparison")
            self.expr(n.left); self.expr(n.comparators[0])
            self.op(CMPOP[type(n.ops[0])])
            self.op("i64_extend_i32_u")   # keep the "everything is i64" invariant
        elif isinstance(n, ast.Call):
            self.call(n)
        else:
            raise CompileError(f"unsupported expression {type(n).__name__}")

    def call(self, n):
        if not isinstance(n.func, ast.Name):
            raise CompileError("only direct calls supported")
        fn = n.func.id
        if fn == "symbol":                       # compile-time symbol literal
            self.const(symbol_small(n.args[0].value)); return
        if fn == "u32":                           # box raw int -> U32Val
            self.expr(n.args[0]); self.const(32); self.op("i64_shl")
            self.const(TAG_U32); self.op("i64_or"); return
        if fn == "unbox_u32":                     # U32Val -> raw int
            self.expr(n.args[0]); self.const(32); self.op("i64_shr_u"); return
        if fn in HOST:
            for a in n.args: self.expr(a)
            self.op("call", uleb(self.host_idx[fn])); return
        if fn in self.user_idx:
            for a in n.args: self.expr(a)
            self.op("call", uleb(self.user_idx[fn])); return
        raise CompileError(f"unknown function {fn!r}")

    def cond(self, n):
        """Emit an i32 condition from an i64-valued expression (truthy = nonzero)."""
        self.expr(n); self.const(0); self.op("i64_ne")

    def _is_value_if(self, n):
        """True if both arms end in `return` -> compile as an i64-valued if/else."""
        return (n.body and isinstance(n.body[-1], ast.Return)
                and n.orelse and isinstance(n.orelse[-1], ast.Return))

    def stmt(self, n):
        if isinstance(n, ast.Assign):
            if len(n.targets) != 1 or not isinstance(n.targets[0], ast.Name):
                raise CompileError("only simple assignment")
            self.expr(n.value)
            self.op("local_set", uleb(self.local(n.targets[0].id)))
        elif isinstance(n, ast.AugAssign):
            t = n.target.id
            self.op("local_get", uleb(self.local(t)))
            self.expr(n.value); self.op(BINOP[type(n.op)])
            self.op("local_set", uleb(self.local(t)))
        elif isinstance(n, ast.Return):
            self.expr(n.value)
        elif isinstance(n, ast.Expr):
            if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                return                      # docstring -> spec doc field, not code
            self.expr(n.value); self.op("drop")
        elif isinstance(n, ast.While):
            # block { loop { if !test br 1; body; br 0 } }
            self.code += bytes([OP["block"], 0x40])
            self.code += bytes([OP["loop"], 0x40])
            self.cond(n.test)
            self.op("i32_eqz")                        # exit when test is false
            self.code += bytes([OP["br_if"]]) + uleb(1)
            for s in n.body: self.stmt(s)
            self.code += bytes([OP["br"]]) + uleb(0)
            self.code.append(OP["end"])
            self.code.append(OP["end"])
        elif isinstance(n, ast.If) and not self._is_value_if(n):
            self.cond(n.test)
            self.code += bytes([OP["if_"], 0x40])     # void-typed if
            for s in n.body: self.stmt(s)
            if n.orelse:
                self.code.append(OP["else_"])
                for s in n.orelse: self.stmt(s)
            self.code.append(OP["end"])
        elif isinstance(n, ast.If):
            self.cond(n.test)
            self.code += bytes([OP["if_"], I64])   # if with i64 result
            for s in n.body: self.stmt(s)
            self.code.append(OP["else_"])
            for s in n.orelse: self.stmt(s)
            self.code.append(OP["end"])
        else:
            raise CompileError(f"unsupported statement {type(n).__name__}")


def compile_contract(src):
    tree = ast.parse(src)
    mod = WasmModule()
    host_idx = {n: mod.add_import(m, f, a) for n, (m, f, a) in HOST.items()}
    # pass 1: assign function indices so calls can be resolved in any order
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    user_idx = {f.name: len(host_idx) + i for i, f in enumerate(fns)}
    spec = []
    for node in fns:
        exported = any(isinstance(d, ast.Name) and d.id == "export"
                       for d in node.decorator_list)
        params = [a.arg for a in node.args.args]
        fc = FuncCompiler(mod, host_idx, params, user_idx)
        for s in node.body: fc.stmt(s)
        nlocals = len(fc.locals) - fc.nparams
        mod.add_func(len(params), 1, nlocals, bytes(fc.code),
                     export=node.name if exported else None)
        if exported: spec.append(node.name)
    return mod.emit(), spec


if __name__ == "__main__":
    src = open(sys.argv[1]).read()
    wasm, spec = compile_contract(src)
    open(sys.argv[2], "wb").write(wasm)
    print(f"compiled {sys.argv[1]} -> {sys.argv[2]}: {len(wasm)} bytes, exports={spec}")
