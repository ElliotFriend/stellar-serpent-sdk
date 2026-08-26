"""Spike 1 frontend: parse the DESIGNED contract-authoring style into a tiny IR.

This walks ``ast.parse(source)`` and pattern-matches the known API shapes used
by ``contract_src.py`` (``env.storage().instance().set(...)`` and friends) --
it does NOT do general name resolution. Every AST node that isn't one of the
handful of shapes this spike cares about raises ``SpikeCompileError`` with the
node's source location. This is throwaway spike code (see spikes/README.md):
the finding is "the designed surface parses cleanly," not a production
frontend.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass


class SpikeCompileError(Exception):
    """Raised for any AST node outside the small set this spike understands."""

    def __init__(self, msg: str, lineno: int, col: int) -> None:
        super().__init__(msg)
        self.msg = msg
        self.lineno = lineno
        self.col = col

    def __str__(self) -> str:
        return f"{self.msg} (line {self.lineno}, col {self.col})"


# ---- IR: expression node kinds ----


@dataclass
class Param:
    i: int


@dataclass
class LocalGet:
    name: str


@dataclass
class ConstU32:
    value: int


@dataclass
class ConstSymbol:
    value: str


@dataclass
class ConstString:
    value: str


@dataclass
class MakeStruct:
    struct_name: str
    fields: list[tuple[str, Expr]]


@dataclass
class GetField:
    obj: Expr
    field: str


@dataclass
class AddU32:
    left: Expr
    right: Expr


@dataclass
class GtU32:
    left: Expr
    right: Expr


@dataclass
class LoadInstance:
    key: Expr
    type: str


@dataclass
class LoadDurable:
    key: Expr
    type: str
    default: Expr | None


Expr = (
    Param
    | LocalGet
    | ConstU32
    | ConstSymbol
    | ConstString
    | MakeStruct
    | GetField
    | AddU32
    | GtU32
    | LoadInstance
    | LoadDurable
)


# ---- IR: statement node kinds ----


@dataclass
class LocalSet:
    name: str
    value: Expr


@dataclass
class StoreInstance:
    key: Expr
    value: Expr


@dataclass
class StoreDurable:
    key: Expr
    value: Expr


@dataclass
class IfRaise:
    cond: Expr
    code: int


@dataclass
class Return:
    value: Expr | None


Stmt = LocalSet | StoreInstance | StoreDurable | IfRaise | Return


@dataclass
class FuncIR:
    name: str
    params: list[tuple[str, str]]
    ret: str
    body: list[Stmt]


@dataclass
class ContractIR:
    name: str
    errors: dict[str, dict[str, int]]
    structs: dict[str, list[tuple[str, str]]]
    functions: list[FuncIR]


# ---- small predicates / helpers ----


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _decorator_name(dec: ast.expr) -> str | None:
    if isinstance(dec, ast.Name):
        return dec.id
    return None


def _type_str(node: ast.expr) -> str:
    if isinstance(node, ast.Constant) and node.value is None:
        return "None"
    if isinstance(node, ast.Name):
        return node.id
    raise SpikeCompileError(
        f"unsupported type annotation: {type(node).__name__}", node.lineno, node.col_offset
    )


def _single_str_arg(call: ast.Call) -> bool:
    return (
        len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
    )


def _single_int_arg(call: ast.Call) -> bool:
    return (
        len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, int)
    )


def _type_name_arg(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    raise SpikeCompileError(
        f"expected a bare type name, got {type(node).__name__}", node.lineno, node.col_offset
    )


def _method_chain(node: ast.expr) -> list[str] | None:
    """Textually match a chain like ``env.storage().instance().set(...)``.

    Returns the dotted/method name chain, e.g. ``["env", "storage",
    "instance", "set"]``, or None if `node` isn't a call whose func is a
    chain of zero-argument attribute calls rooted in a bare Name (only the
    outermost call -- the one `node` itself is -- may carry arguments).
    """
    if not isinstance(node, ast.Call):
        return None
    parts: list[str] = []
    cur: ast.expr = node
    outermost = True
    while isinstance(cur, ast.Call):
        if not outermost and (cur.args or cur.keywords):
            return None
        func = cur.func
        if not isinstance(func, ast.Attribute):
            return None
        parts.append(func.attr)
        cur = func.value
        outermost = False
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    return parts


@dataclass
class _FuncCtx:
    env_name: str
    param_index: dict[str, int]
    locals: set[str]


# ---- expression / statement resolution ----


def _resolve_expr(
    node: ast.expr,
    ctx: _FuncCtx,
    errors: dict[str, dict[str, int]],
    structs: dict[str, list[tuple[str, str]]],
) -> Expr:
    if isinstance(node, ast.Name):
        if node.id in ctx.locals:
            return LocalGet(name=node.id)
        if node.id in ctx.param_index:
            return Param(i=ctx.param_index[node.id])
        raise SpikeCompileError(f"unresolved name: {node.id!r}", node.lineno, node.col_offset)

    if isinstance(node, ast.Attribute):
        return GetField(obj=_resolve_expr(node.value, ctx, errors, structs), field=node.attr)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return AddU32(
            left=_resolve_expr(node.left, ctx, errors, structs),
            right=_resolve_expr(node.right, ctx, errors, structs),
        )

    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Gt)
        and len(node.comparators) == 1
    ):
        return GtU32(
            left=_resolve_expr(node.left, ctx, errors, structs),
            right=_resolve_expr(node.comparators[0], ctx, errors, structs),
        )

    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            fname = node.func.id
            if fname == "Symbol" and _single_str_arg(node):
                assert isinstance(node.args[0], ast.Constant)
                return ConstSymbol(value=node.args[0].value)
            if fname == "String" and _single_str_arg(node):
                assert isinstance(node.args[0], ast.Constant)
                return ConstString(value=node.args[0].value)
            if fname == "U32" and _single_int_arg(node):
                assert isinstance(node.args[0], ast.Constant)
                return ConstU32(value=node.args[0].value)
            if fname in structs:
                if node.args:
                    raise SpikeCompileError(
                        f"{fname}(...) must be constructed with keyword arguments only",
                        node.lineno,
                        node.col_offset,
                    )
                fields = [
                    (kw.arg, _resolve_expr(kw.value, ctx, errors, structs))
                    for kw in node.keywords
                    if kw.arg is not None
                ]
                return MakeStruct(struct_name=fname, fields=fields)

        chain = _method_chain(node)
        if chain == [ctx.env_name, "storage", "instance", "get"] and len(node.args) == 2:
            key = _resolve_expr(node.args[0], ctx, errors, structs)
            type_ = _type_name_arg(node.args[1])
            return LoadInstance(key=key, type=type_)
        if chain == [ctx.env_name, "storage", "persistent", "get"] and len(node.args) == 2:
            key = _resolve_expr(node.args[0], ctx, errors, structs)
            type_ = _type_name_arg(node.args[1])
            default: Expr | None = None
            for kw in node.keywords:
                if kw.arg == "default":
                    default = _resolve_expr(kw.value, ctx, errors, structs)
            return LoadDurable(key=key, type=type_, default=default)

        raise SpikeCompileError(
            f"unsupported call: {type(node.func).__name__}", node.lineno, node.col_offset
        )

    raise SpikeCompileError(
        f"unsupported expression: {type(node).__name__}", node.lineno, node.col_offset
    )


def _resolve_stmt(
    stmt: ast.stmt,
    ctx: _FuncCtx,
    errors: dict[str, dict[str, int]],
    structs: dict[str, list[tuple[str, str]]],
) -> Stmt:
    if isinstance(stmt, ast.Assign):
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            raise SpikeCompileError(
                "only single-name assignment is supported", stmt.lineno, stmt.col_offset
            )
        value = _resolve_expr(stmt.value, ctx, errors, structs)
        name = stmt.targets[0].id
        ctx.locals.add(name)
        return LocalSet(name=name, value=value)

    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        call = stmt.value
        chain = _method_chain(call)
        if (
            chain == [ctx.env_name, "storage", "instance", "set"]
            and len(call.args) == 2
            and not call.keywords
        ):
            key = _resolve_expr(call.args[0], ctx, errors, structs)
            value = _resolve_expr(call.args[1], ctx, errors, structs)
            return StoreInstance(key=key, value=value)
        if (
            chain == [ctx.env_name, "storage", "persistent", "set"]
            and len(call.args) == 2
            and not call.keywords
        ):
            key = _resolve_expr(call.args[0], ctx, errors, structs)
            value = _resolve_expr(call.args[1], ctx, errors, structs)
            return StoreDurable(key=key, value=value)
        raise SpikeCompileError(
            "unsupported call statement (not a recognized storage write)",
            stmt.lineno,
            stmt.col_offset,
        )

    if isinstance(stmt, ast.If):
        raise_stmt = stmt.body[0] if len(stmt.body) == 1 else None
        if (
            raise_stmt is not None
            and isinstance(raise_stmt, ast.Raise)
            and not stmt.orelse
            and isinstance(raise_stmt.exc, ast.Attribute)
            and isinstance(raise_stmt.exc.value, ast.Name)
            and raise_stmt.exc.value.id in errors
            and raise_stmt.exc.attr in errors[raise_stmt.exc.value.id]
        ):
            code = errors[raise_stmt.exc.value.id][raise_stmt.exc.attr]
            cond = _resolve_expr(stmt.test, ctx, errors, structs)
            return IfRaise(cond=cond, code=code)
        raise SpikeCompileError(
            "unsupported if-statement shape (only `if <cond>: raise "
            "<Error>.<Member>` is supported)",
            stmt.lineno,
            stmt.col_offset,
        )

    if isinstance(stmt, ast.Return):
        value = _resolve_expr(stmt.value, ctx, errors, structs) if stmt.value is not None else None
        return Return(value=value)

    raise SpikeCompileError(
        f"unsupported statement: {type(stmt).__name__}", stmt.lineno, stmt.col_offset
    )


# ---- class-level collection ----


def _collect_errors(cls: ast.ClassDef) -> dict[str, int]:
    members: dict[str, int] = {}
    for i, stmt in enumerate(cls.body):
        if i == 0 and _is_docstring(stmt):
            continue
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, int)
        ):
            members[stmt.targets[0].id] = stmt.value.value
            continue
        raise SpikeCompileError(
            f"unsupported statement in @contracterror class {cls.name!r}: {type(stmt).__name__}",
            stmt.lineno,
            stmt.col_offset,
        )
    return members


def _collect_struct_fields(cls: ast.ClassDef) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for i, stmt in enumerate(cls.body):
        if i == 0 and _is_docstring(stmt):
            continue
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.append((stmt.target.id, _type_str(stmt.annotation)))
            continue
        raise SpikeCompileError(
            f"unsupported statement in @contracttype class {cls.name!r}: {type(stmt).__name__}",
            stmt.lineno,
            stmt.col_offset,
        )
    return fields


def _collect_function(
    fn: ast.FunctionDef,
    errors: dict[str, dict[str, int]],
    structs: dict[str, list[tuple[str, str]]],
) -> FuncIR:
    args = fn.args.args
    if not args:
        raise SpikeCompileError(
            f"contract method {fn.name!r} must take env as its first parameter",
            fn.lineno,
            fn.col_offset,
        )
    for a in args:
        if a.annotation is None:
            raise SpikeCompileError(
                f"parameter {a.arg!r} of {fn.name!r} is missing a type annotation",
                a.lineno,
                a.col_offset,
            )
    if fn.returns is None:
        raise SpikeCompileError(
            f"{fn.name!r} is missing a return type annotation", fn.lineno, fn.col_offset
        )

    env_arg = args[0]
    rest = args[1:]
    param_index = {a.arg: i for i, a in enumerate(rest)}
    params = [(a.arg, _type_str(a.annotation)) for a in rest if a.annotation is not None]
    ret = _type_str(fn.returns)

    ctx = _FuncCtx(env_name=env_arg.arg, param_index=param_index, locals=set())
    body: list[Stmt] = []
    for i, stmt in enumerate(fn.body):
        if i == 0 and _is_docstring(stmt):
            continue
        body.append(_resolve_stmt(stmt, ctx, errors, structs))
    return FuncIR(name=fn.name, params=params, ret=ret, body=body)


def parse_contract(path: str) -> ContractIR:
    source = pathlib.Path(path).read_text()
    tree = ast.parse(source, filename=path)

    errors: dict[str, dict[str, int]] = {}
    structs: dict[str, list[tuple[str, str]]] = {}
    contract_classes: list[ast.ClassDef] = []

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ClassDef):
            decorators = {_decorator_name(d) for d in node.decorator_list}
            if "contracterror" in decorators:
                errors[node.name] = _collect_errors(node)
                continue
            if "contracttype" in decorators:
                structs[node.name] = _collect_struct_fields(node)
                continue
            if "contract" in decorators:
                contract_classes.append(node)
                continue
            raise SpikeCompileError(
                f"class {node.name!r} has no recognized serpent decorator",
                node.lineno,
                node.col_offset,
            )
        raise SpikeCompileError(
            f"unsupported top-level statement: {type(node).__name__}", node.lineno, node.col_offset
        )

    if len(contract_classes) != 1:
        raise SpikeCompileError(
            f"expected exactly one @contract class, found {len(contract_classes)}", 1, 0
        )

    contract_cls = contract_classes[0]
    functions: list[FuncIR] = []
    for i, stmt in enumerate(contract_cls.body):
        if i == 0 and _is_docstring(stmt):
            continue
        if isinstance(stmt, ast.FunctionDef):
            functions.append(_collect_function(stmt, errors, structs))
            continue
        raise SpikeCompileError(
            f"unsupported statement in @contract class {contract_cls.name!r}: "
            f"{type(stmt).__name__}",
            stmt.lineno,
            stmt.col_offset,
        )

    return ContractIR(name=contract_cls.name, errors=errors, structs=structs, functions=functions)
