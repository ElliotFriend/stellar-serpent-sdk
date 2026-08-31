"""The COMPLETE `SPT####` diagnostic code registry.

This is Task 1's primary deliverable and review gate (M1-C plan, BL-4): a
stable, public registry of every diagnostic the serpent compiler can raise.
Codes are public API (dossier E17) -- stable under message rewording, so
`tests/must_reject/` fixtures and generated docs (`docs/subset.md`, S14) can
cite them without drifting when wording improves.

## How this table was derived

Every row traces to one of four dossier sources
(docs/superpowers/specs/2026-08-27-m1c-inputs-dossier.md), per the task
brief's rule "one row per SS B.1/B.2 REJECT line, per SS B.3 declaration
check, per SS C.4 'not in M1' surface, per SS C.3 scope/flow rule":

* SS B.1 (statements) and SS B.2 (expressions) -- every table row whose
  M1-C column reads REJECT (or "REJECT (M1)"/"REJECT *"), plus the
  handful of SUPPORT rows whose own prose names a distinct embedded
  reject (e.g. "Break/Continue -- SUPPORT ... Reject outside a loop");
  each row's *message*, not its AST node count, drives the code count --
  a row naming several AST node kinds that share one rejection message
  (e.g. `AsyncFunctionDef`/`AsyncFor`/`AsyncWith`/`Await`) is ONE code,
  the same way `errors.CODE_ABI_CHECK_FAILED` is one code for every
  argument position (position is a message concern, not a code concern) --
  and conversely, a row whose prose blends two genuinely different
  rewrites (e.g. `in` vs. `is`) gets two codes.
* SS B.3 -- every declaration-shape check `decorators.py`/`sections.py`
  already enforce as a location-free `ValueError`, which C must re-report
  located.
* SS C.4 -- the Env-API recognition table's "not in M1" surfaces
  (recognized host names deferred to M2), the `topics[0]`-must-be-a-short-
  Symbol convention (S11), and the `Event.publish(env)` ruling (E12).
* SS C.3 -- the four `Locals` rules (single-typed, definite assignment,
  definite return, shadowing) plus the "`self` binds but any use is a
  diagnostic" scope fact.

Several dossier rulings named directly in the plan's Global Constraints
(E8, E9-E13, E19, D6, MJ-1, MJ-11, S8, S11, T1/T5) also get their own rows
even where the literal SS B table row is a SUPPORT line with an embedded
reject clause, because a later task (3 through 11b) explicitly needs the
code and a "complete" registry cannot make that task invent one out of
band -- see the task-1 report for the exact row count and a full citation
per row. This includes MJ-11's exhaustive-dispatch default branch (any AST
node kind `NODE_KIND_CODES` does not otherwise cover) and E8's call-graph
cycle rejection, neither of which is one specific SS B/B.3/C.3/C.4 line but
both of which a downstream task cannot ship without.

`codes.validate()` is the automated form of this discipline: uniqueness,
band-prefix correctness, non-empty owning task, and `NO_FIXTURE_ALLOWLIST`
(subset) of the registry. `CODES` is the derived set `diagnostics.py` uses
to reject an unregistered code at the sink (codes are public API; an
un-registered code is a bug, not a valid diagnostic).
"""

from __future__ import annotations

from typing import NamedTuple


class CodeEntry(NamedTuple):
    """One registry row: `(code, band, construct, message_intent, owning_task)`."""

    code: str
    band: str
    construct: str
    message_intent: str
    owning_task: str


# --- SPT1xxx: unsupported construct (dossier D.1; SS B REJECT column) ------
_SPT1XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT1001",
        "SPT1xxx",
        "nested FunctionDef / closures",
        "nested functions and closures are not supported; contracts are a flat set of "
        "methods and module-level helpers",
        "Task 6",
    ),
    CodeEntry(
        "SPT1002",
        "SPT1xxx",
        "AsyncFunctionDef / AsyncFor / AsyncWith / Await",
        "there is no event loop on chain; contract methods are synchronous",
        "Task 6",
    ),
    CodeEntry(
        "SPT1003",
        "SPT1xxx",
        "ListComp / SetComp / DictComp / GeneratorExp",
        "comprehensions are not supported; build the container with Vec(T, [...]) or fill "
        "it in a while loop",
        "Task 5",
    ),
    CodeEntry(
        "SPT1004",
        "SPT1xxx",
        "JoinedStr / FormattedValue (f-strings)",
        "f-strings are not supported: there is no runtime string formatting host function",
        "Task 5",
    ),
    CodeEntry(
        "SPT1005",
        "SPT1xxx",
        "Lambda",
        "lambdas and closures are not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT1006",
        "SPT1xxx",
        "NamedExpr (walrus `:=`)",
        "assign on its own line",
        "Task 5",
    ),
    CodeEntry(
        "SPT1007",
        "SPT1xxx",
        "Starred",
        "argument unpacking is not supported; a contract export has a fixed arity",
        "Task 5",
    ),
    CodeEntry(
        "SPT1008",
        "SPT1xxx",
        "Yield / YieldFrom",
        "generators are not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT1009",
        "SPT1xxx",
        "Slice node outside a supported subscript form",
        "slicing is only supported via the .slice(lo, hi) method",
        "Task 5",
    ),
    CodeEntry(
        "SPT1010",
        "SPT1xxx",
        "Compare -- chained (`a < b < c`)",
        "compare two values at a time",
        "Task 5",
    ),
    CodeEntry(
        "SPT1011",
        "SPT1xxx",
        "Compare -- `in` / `not in`",
        "use Map.has(k) or Vec.first_index_of(v) instead of `in`",
        "Task 5",
    ),
    CodeEntry(
        "SPT1012",
        "SPT1xxx",
        "Compare -- `is` / `is not`",
        "identity has no on-chain meaning; use ==",
        "Task 5",
    ),
    CodeEntry(
        "SPT1013",
        "SPT1xxx",
        "Subscript slice (`Bytes[a:b]`, `Vec[a:b]`)",
        "slicing via subscript is not supported; use .slice(lo, hi)",
        "Task 7b",
    ),
    CodeEntry(
        "SPT1014",
        "SPT1xxx",
        "Tuple outside event topics",
        "tuple structs are not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT1015",
        "SPT1xxx",
        "List / Dict / Set display outside Vec(...)/Map(...)",
        "there is no python list/dict/set on chain; build a Vec(T, [...]) or Map(K, V, [...])",
        "Task 5",
    ),
    CodeEntry(
        "SPT1016",
        "SPT1xxx",
        "Attribute -- chain-type introspection property (.value/.text/.data/.strkey/...)",
        "this property has no host equivalent",
        "Task 5",
    ),
    CodeEntry(
        "SPT1017",
        "SPT1xxx",
        "Call -- rejected builtin (sum/min/max/abs/int/str/print/isinstance/...)",
        "this builtin is not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT1018",
        "SPT1xxx",
        "For ... else",
        "the for ... else clause is not supported",
        "Task 6",
    ),
    CodeEntry(
        "SPT1019",
        "SPT1xxx",
        "For ... in map / bytes / tuple",
        "iterate a Map via map.keys()/map.values(); walk Bytes with a while loop indexed "
        "by bytes[i] up to len(b); tuples cannot be iterated",
        "Task 6",
    ),
    CodeEntry(
        "SPT1020",
        "SPT1xxx",
        "For i in range(...) -- 3-arg or negative-step form",
        "range() supports only range(stop) and range(start, stop) in M1",
        "Task 6",
    ),
    CodeEntry(
        "SPT1021",
        "SPT1xxx",
        "Raise -- non-error-enum form (raise X(...), bare raise, raise ... from ...)",
        "only raise <ErrorEnum>.<Member> is supported; contract errors are u32 codes, not "
        "exception instances",
        "Task 6",
    ),
    CodeEntry(
        "SPT1022",
        "SPT1xxx",
        "Try / TryStar / except / finally",
        "a contract cannot catch its own errors; validate before acting",
        "Task 6",
    ),
    CodeEntry(
        "SPT1023",
        "SPT1xxx",
        "With",
        "there is no context-manager protocol on chain",
        "Task 6",
    ),
    CodeEntry(
        "SPT1024",
        "SPT1xxx",
        "Match",
        "structural pattern matching is not supported",
        "Task 6",
    ),
    CodeEntry(
        "SPT1025",
        "SPT1xxx",
        "Assert",
        "assert has no on-chain meaning; raise <Error>.<Member> to fail with a code the "
        "caller can read",
        "Task 6",
    ),
    CodeEntry(
        "SPT1026",
        "SPT1xxx",
        "Delete (`del x`)",
        "use storage.del_(key), Vec.del_(i), or Map.del_(k)",
        "Task 6",
    ),
    CodeEntry(
        "SPT1027",
        "SPT1xxx",
        "Global / Nonlocal",
        "contract state lives in storage; module-level names are compile-time constants",
        "Task 6",
    ),
    CodeEntry(
        "SPT1028",
        "SPT1xxx",
        "Expr statement -- discarded non-void expression",
        "a non-void expression cannot be a statement on its own; assign it or discard it "
        "explicitly",
        "Task 6",
    ),
    CodeEntry(
        "SPT1029",
        "SPT1xxx",
        "Assign -- tuple/multi target",
        "assign one name at a time",
        "Task 6",
    ),
    CodeEntry(
        "SPT1030",
        "SPT1xxx",
        "Assign -- subscript target",
        "use Vec.put(i, v) or Map.set(k, v)",
        "Task 6/7b",
    ),
    CodeEntry(
        "SPT1031",
        "SPT1xxx",
        "Module body -- unsupported top-level statement",
        "a contract module's top level may only contain imports, module-level chain "
        "constants, and decorated classes/helpers",
        "Task 3",
    ),
    CodeEntry(
        "SPT1032",
        "SPT1xxx",
        "Call -- <Event instance>.publish(env)",
        "superseded by M1-E: `<Event instance>.publish(env)` is supported and lowers to "
        "the same contract_event call env.events().publish(topics, data) does",
        "Task 7a",
    ),
    CodeEntry(
        "SPT1033",
        "SPT1xxx",
        "Recognized Env/host surface not lowerable in M1 (logs, call/try_call, crypto, "
        "PRNG, current_contract_address, ledger_version, network_id, deployer)",
        "this Env surface is recognized but not yet supported; it lands in M2",
        "Task 7a",
    ),
    # Wording fixed in Task 7b's review fix round (sanctioned, wording only --
    # no renumber, no meaning change): the example used to read
    # `x = x.push_back(v)`, which reads as a VALUE-position mutation. That is
    # itself a reject under this same code (an expression has no binding to
    # rewrite, and tier 1's mutators return None), so the example contradicted
    # the rule. E11's real shape is the mutation as a STATEMENT, with C
    # supplying the rebind.
    CodeEntry(
        "SPT1034",
        "SPT1xxx",
        "Container mutation through an aliased binding or a temporary receiver",
        "host container operations are functional; mutate only a local this method owns, "
        "on a statement of its own -- `v.push_back(x)` -- and C rebinds it "
        "(v = vec_push_back(v, x))",
        "Task 7b",
    ),
    CodeEntry(
        "SPT1035",
        "SPT1xxx",
        "Call -- keyword argument outside the recognition table / @contracttype / event "
        "construction",
        "keyword arguments are only accepted where the recognized API names the parameter",
        "Task 7a/7b",
    ),
    CodeEntry(
        "SPT1036",
        "SPT1xxx",
        "AnnAssign in a function body with no value",
        "uninitialized locals are not supported; give x: T a value",
        "Task 6",
    ),
    CodeEntry(
        "SPT1037",
        "SPT1xxx",
        "Any AST node kind not covered by the rows above -- the NODE_KIND_CODES "
        "exhaustive-dispatch default branch (MJ-11)",
        "this construct is not supported by the serpent subset",
        "Task 5",
    ),
    # Added in Task 7a's review fix round (controller ruling). A recognized
    # Env-API attribute referenced without being called/chained at all
    # (`env.storage`, no `()`) and a structurally malformed recognized call
    # that is neither an arity mismatch (SPT3020) nor a type mismatch
    # (SPT3018) -- e.g. `env.events().publish((), data)`'s empty topics
    # tuple -- had no row; `recognize.py` was reusing SPT3018 ("declared-vs-
    # actual type mismatch"), which is the wrong KIND of error for either
    # shape (neither is a type disagreement). SPT1xxx, because the true
    # story is "this call shape is not part of the supported subset", the
    # same framing SPT1xxx already carries for every other REJECT row.
    CodeEntry(
        "SPT1038",
        "SPT1xxx",
        "Env API attribute referenced without being called/chained (`env.storage`, no "
        "`()`), or a structurally malformed recognized call that is not an arity or type "
        "error (e.g. an empty event-topics tuple)",
        "env API used with an unsupported call shape",
        "Task 7a",
    ),
    # Added in Task 7b's review fix round (controller ruling). A `Map(K, V,
    # [...])` literal with two keys that compare EQUAL under `val_cmp` had no
    # row: tier 1 silently keeps the last one, while the on-chain literal form
    # (`map_new_from_linear_memory`, `m.9`) requires strictly-ascending UNIQUE
    # keys and traps otherwise -- so an accept here would either trap on chain
    # or silently differ from the oracle. It is an authoring bug either way,
    # and "reject rather than approximate" (S1) makes the reject the honest
    # answer; a reject is allowed to be STRICTER than tier 1 (only ACCEPTS
    # must be oracle-runnable). SPT1xxx and not SPT3018/SPT3020: nothing about
    # it is a type disagreement or a call-arity mistake -- the map literal's
    # SHAPE is not one the subset supports.
    CodeEntry(
        "SPT1039",
        "SPT1xxx",
        "Map literal with duplicate keys (two literal keys equal under val_cmp, the Bytes "
        "family's payload equality included -- D5)",
        "a map literal may not repeat a key",
        "Task 7b",
    ),
)

# --- SPT2xxx: name resolution / imports / scope -----------------------------
_SPT2XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT2001",
        "SPT2xxx",
        "Name -- unresolved",
        "name is not defined",
        "Task 5",
    ),
    CodeEntry(
        "SPT2002",
        "SPT2xxx",
        "Name -- `self` used as a value",
        "contract state lives in storage, not on self",
        "Task 5",
    ),
    CodeEntry(
        "SPT2003",
        "SPT2xxx",
        "Annotation resolution -- unresolvable name (NameError)",
        "annotation refers to a name that is not defined",
        "Task 3",
    ),
    CodeEntry(
        "SPT2004",
        "SPT2xxx",
        "Local/param shadows a param, module constant, import, or type name",
        "name shadows an existing declaration",
        "Task 4",
    ),
    CodeEntry(
        "SPT2005",
        "SPT2xxx",
        "Import / ImportFrom -- non-serpent module",
        "a contract may only import from serpent",
        "Task 3",
    ),
    CodeEntry(
        "SPT2006",
        "SPT2xxx",
        "Attribute -- unknown name on Env",
        "unknown Env attribute",
        "Task 7a",
    ),
)

# --- SPT3xxx: types (dossier D.1) -------------------------------------------
_SPT3XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT3001",
        "SPT3xxx",
        "Return / annotation -- Error as a return type",
        "Error is never a returnable value (S8)",
        "Task 4/6",
    ),
    CodeEntry(
        "SPT3002",
        "SPT3xxx",
        "Attribute -- ErrorEnum.Member outside a raise statement",
        "an error case is not a value; it may only appear in raise <ErrorEnum>.<Member>",
        "Task 5",
    ),
    CodeEntry(
        "SPT3003",
        "SPT3xxx",
        "BinOp/Compare -- cross-width or cross-signedness operands (T1)",
        "operands must share the same chain-integer type",
        "Task 5",
    ),
    CodeEntry(
        "SPT3004",
        "SPT3xxx",
        "Constant -- out-of-range literal coercion",
        "literal is out of range for the target type",
        "Task 5",
    ),
    CodeEntry(
        "SPT3005",
        "SPT3xxx",
        "BinOp -- omitted operator (** @ & | ^ << >>); also covers the AugAssign forms "
        "(**=, &=, |=, ^=, <<=, >>=), which desugar to this BinOp before typing (A5/D2)",
        "this operator is not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT3006",
        "SPT3xxx",
        "BinOp -- true divide (/)",
        "there are no floats on chain; use // for truncating integer division",
        "Task 5",
    ),
    CodeEntry(
        "SPT3007",
        "SPT3xxx",
        "UnaryOp -- omitted operator (+ / ~)",
        "this unary operator is not supported",
        "Task 5",
    ),
    CodeEntry(
        "SPT3008",
        "SPT3xxx",
        "Constant -- bare literal with no chain type in scope",
        "wrap the literal in a chain type, e.g. U32(5)",
        "Task 5",
    ),
    CodeEntry(
        "SPT3009",
        "SPT3xxx",
        "Call -- len() on Symbol/String (MJ-1 ruling)",
        "len() is only supported on Vec, Map, and Bytes",
        "Task 5",
    ),
    CodeEntry(
        "SPT3010",
        "SPT3xxx",
        "IfExp -- arm type mismatch",
        "both branches of a conditional expression must have the same type",
        "Task 5",
    ),
    CodeEntry(
        "SPT3011",
        "SPT3xxx",
        "Subscript -- negative literal index (D6)",
        "negative indices are not representable on chain",
        "Task 7b",
    ),
    CodeEntry(
        "SPT3012",
        "SPT3xxx",
        "BoolOp -- non-Bool/non-comparison operand (E9)",
        "and/or are restricted to Bool-typed and comparison operands",
        "Task 5",
    ),
    CodeEntry(
        "SPT3013",
        "SPT3xxx",
        "Annotation -- unmappable to the contract spec (B7: U256/I256/MuxedAddress/Val/"
        "Result/Tuple, bare Vec/Map, non-Optional unions, plain int/str/bytes/bool, "
        "Env outside the leading param, None outside a return, Event/@contracterror/"
        "@contract used as a type, any other parameterized generic)",
        "this annotation cannot be expressed in the contract spec",
        "Task 4",
    ),
    CodeEntry(
        "SPT3014",
        "SPT3xxx",
        "Subscript -- annotation-only generic form (Vec[T]/Map[K,V]/Optional[X]) used in "
        "a value position",
        "this is an annotation-only form; it cannot appear as a value",
        "Task 4/5",
    ),
    CodeEntry(
        "SPT3015",
        "SPT3xxx",
        "If/While condition -- truthiness of a non-numeric chain value (E10)",
        "truthiness is only defined for numeric chain types and Bool; write the explicit "
        "test, e.g. len(v) > U32(0) or storage.has(k)",
        "Task 5/6",
    ),
    CodeEntry(
        "SPT3016",
        "SPT3xxx",
        "Compare -- chain value vs. raw str/bytes literal via == (E13/T4)",
        "compare against the chain type's constructor, e.g. Symbol('abc'), not the raw literal",
        "Task 5",
    ),
    CodeEntry(
        "SPT3017",
        "SPT3xxx",
        "Local rebound at a different type than its first binding (SS C.3 rule 1)",
        "a local's type is fixed by its first binding",
        "Task 4",
    ),
    CodeEntry(
        "SPT3018",
        "SPT3xxx",
        "Declared-vs-actual type mismatch: AnnAssign-with-value disagreeing with its "
        "annotation, Return disagreeing with a non-None return annotation, or a call "
        "argument disagreeing with an InternalCall/recognized-API parameter type",
        "value's type does not match the declared/expected type",
        "Task 4/6/7a/7b",
    ),
    CodeEntry(
        "SPT3019",
        "SPT3xxx",
        "events().publish(topics, data) -- topics[0] is not a short Symbol (S11)",
        "the first event topic must be a short Symbol naming the event",
        "Task 7a",
    ),
    # Added in Task 5's review fix round (controller ruling). A chain-type
    # constructor takes exactly one payload argument, and `U32()` / `U32(1, 2)`
    # had no row: Task 5 was reporting it under MJ-11's catch-all (SPT1037),
    # whose "not supported by the serpent subset" wording is wrong for a
    # construct that IS supported and merely miscalled -- the same mismatch
    # SPT4019/SPT4020 fixed for the loader. Banded SPT3xxx rather than
    # SPT1xxx/SPT4xxx: it is a call-signature disagreement between an argument
    # list and a known type, which is the same family as SPT3018's "call
    # argument disagreeing with a parameter type" (SPT1xxx would falsely claim
    # the construct is unsupported, and SPT4xxx is about DECLARATION shape,
    # not call sites).
    #
    # Widened in Task 7a's review fix round (controller ruling): the same
    # arity-shaped mistake -- too many/too few positional arguments, a
    # missing required argument, a duplicate keyword -- recurs at every
    # `recognize.py` call site (`env.storage().instance().set(k)`,
    # `addr.require_auth_for_args(a, b)`, ...), and it is the SAME kind of
    # disagreement a chain-type constructor's own arity check already
    # reports here: a call-signature mismatch against a KNOWN shape, never a
    # type disagreement (SPT3018) and never an unsupported construct
    # (SPT1xxx -- every one of these calls IS supported, just miscalled).
    # One code for the one rule, general to any recognized call, not just a
    # constructor.
    CodeEntry(
        "SPT3020",
        "SPT3xxx",
        "Call -- a chain-type constructor or a recognized API call with the wrong "
        "arguments (wrong arity, a missing required argument, or a duplicate keyword) -- "
        "`U32()`, `U32(1, 2)`, `<bucket>.set(k)`, `addr.require_auth_for_args(a, b)`",
        "call has the wrong arguments (missing, extra, or duplicate keyword)",
        "Task 5/7a",
    ),
)

# --- SPT4xxx: contract shape (dossier D.1; SS B.3) --------------------------
_SPT4XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT4001",
        "SPT4xxx",
        "FunctionDef -- self is not the first parameter",
        "contract methods must take self first",
        "Task 3",
    ),
    CodeEntry(
        "SPT4002",
        "SPT4xxx",
        "FunctionDef -- *args/**kwargs, or a keyword-only parameter, on a contract function",
        "contract functions have a fixed, positional arity; *args/**kwargs and "
        "keyword-only parameters are not supported",
        "Task 3",
    ),
    CodeEntry(
        "SPT4003",
        "SPT4xxx",
        "FunctionDef -- default parameter value",
        "default parameter values are not supported on a contract function",
        "Task 3",
    ),
    CodeEntry(
        "SPT4004",
        "SPT4xxx",
        "FunctionDef -- missing parameter annotation",
        "every parameter needs a chain-type annotation",
        "Task 3",
    ),
    CodeEntry(
        "SPT4005",
        "SPT4xxx",
        "FunctionDef -- missing return annotation",
        "a contract function needs a return annotation",
        "Task 3",
    ),
    CodeEntry(
        "SPT4006",
        "SPT4xxx",
        "__init__ -- not annotated -> None",
        "__init__ compiles to the constructor and must return None",
        "Task 3",
    ),
    CodeEntry(
        "SPT4007",
        "SPT4xxx",
        "FunctionDef -- staticmethod/classmethod (D7)",
        "@contract methods may not be static or class methods",
        "Task 3",
    ),
    CodeEntry(
        "SPT4008",
        "SPT4xxx",
        "@contracterror member -- bare int, not errorcode(N) (S10)",
        "error members must be declared NAME = errorcode(N)",
        "Task 3",
    ),
    CodeEntry(
        "SPT4009",
        "SPT4xxx",
        "@contracterror member -- code out of [0, 0xFFFFFF00) (A9)",
        "error code is out of the allowed range",
        "Task 3",
    ),
    CodeEntry(
        "SPT4010",
        "SPT4xxx",
        "@contracterror -- duplicate error code",
        "error codes must be unique within the enum",
        "Task 3",
    ),
    CodeEntry(
        "SPT4011",
        "SPT4xxx",
        "@contracterror -- empty error enum",
        "an error enum must declare at least one member",
        "Task 3",
    ),
    CodeEntry(
        "SPT4012",
        "SPT4xxx",
        "@contracttype -- non-chain field annotation",
        "struct fields need a chain-type annotation",
        "Task 3",
    ),
    CodeEntry(
        "SPT4013",
        "SPT4xxx",
        "ClassDef -- re-decoration (more than one of @contract/@contracttype/"
        "@contracterror/@contractevent)",
        "a class may carry exactly one serpent decorator",
        "Task 3",
    ),
    CodeEntry(
        "SPT4014",
        "SPT4xxx",
        "@contractevent -- class does not inherit serpent.Event (D8)",
        "event classes must inherit from serpent.Event",
        "Task 3",
    ),
    CodeEntry(
        "SPT4015",
        "SPT4xxx",
        "ClassDef -- undecorated class, or a class with no recognized serpent decorator",
        "every top-level class needs exactly one of @contract/@contracttype/"
        "@contracterror/@contractevent",
        "Task 3",
    ),
    CodeEntry(
        "SPT4016",
        "SPT4xxx",
        "Assign -- attribute target on a @contracttype value",
        "@contracttype values are immutable; build a new one instead",
        "Task 5/6",
    ),
    CodeEntry(
        "SPT4017",
        "SPT4xxx",
        "Return -- a value in a method annotated -> None",
        "a method returning None may not return a value",
        "Task 6",
    ),
    CodeEntry(
        "SPT4018",
        "SPT4xxx",
        "Call -- @contracttype construction with positional args "
        "(superseded by SPT3020; retained append-only, not emitted)",
        "struct construction takes keyword arguments only",
        "Task 7b",
    ),
    # Added in Task 3's review fix round (controller ruling): SS C.3 names
    # "expected exactly one @contract class" as the worked example of a
    # module-scope (WHOLE_FILE) fact, but the Task 1 derivation had no row
    # for it, so the loader was reusing SPT4015 with a message that did not
    # contain its own registry intent. This row fixes that mismatch.
    CodeEntry(
        "SPT4019",
        "SPT4xxx",
        "Module -- not exactly one @contract class (SS C.3's module-scope fact): zero "
        "(WHOLE_FILE) or more than one (each extra class's Loc)",
        "expected exactly one @contract class per module",
        "Task 3",
    ),
    # Added in the same round: a serpent-decorated class body admits only the
    # declaration form its KIND declares -- methods in a @contract class,
    # `NAME = errorcode(N)` in an error enum, `name: T` fields in a struct or
    # event -- per SS C.3 ("Contract class: method names only. There are no
    # class attributes"). Both the wrong-kind member and a class-body
    # statement that declares nothing at all land here. Without this row the
    # loader had to reuse MJ-11's catch-all (SPT1037), whose "not supported
    # by the serpent subset" wording is wrong for a construct that IS
    # supported, just not in that body.
    CodeEntry(
        "SPT4020",
        "SPT4xxx",
        "Decorated class body -- a member that is not valid for the class's kind (a field "
        "in a @contract class, a method in a struct/event/error enum, a bare `x: T` in an "
        "error enum, a plain `x = y` in a struct), or a statement that declares nothing "
        "at all (SS C.3)",
        "this member is not valid in this kind of serpent-decorated class body",
        "Task 3",
    ),
)

# --- SPT5xxx: spec/XDR limits (dossier D.1; SS B.3) -------------------------
_SPT5XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT5001",
        "SPT5xxx",
        "function/field/param name -- length > 30 or non-Symbol charset (S12/D10); also "
        "covers __constructor's name and every parameter name, which decorators.py never "
        "checks at all (B11)",
        "name is too long (> 30) or uses characters outside [a-zA-Z0-9_]",
        "Task 9",
    ),
    CodeEntry(
        "SPT5002",
        "SPT5xxx",
        "@contracttype/@contracterror type name -- length > 60 or non-Symbol charset (Task 9 "
        "fix round 1: a name outside [a-zA-Z0-9_] is representable as a Python identifier but "
        "not as the Rust identifier every Soroban tool renders it as)",
        "type name is too long (> 60) or uses characters outside [a-zA-Z0-9_]",
        "Task 9",
    ),
    CodeEntry(
        "SPT5003",
        "SPT5xxx",
        "@contracterror case name -- length > 60 or non-Symbol charset (Task 9 fix round 1, "
        "same reasoning as SPT5002)",
        "error case name is too long (> 60) or uses characters outside [a-zA-Z0-9_]",
        "Task 9",
    ),
    CodeEntry(
        "SPT5004",
        "SPT5xxx",
        "docstring -- encoded length > 1024 bytes (B12)",
        "docstring is too long (> 1024 encoded bytes)",
        "Task 9",
    ),
    CodeEntry(
        "SPT5005",
        "SPT5xxx",
        "exported method -- more than 32 parameters (S23)",
        "an exported method may have at most 32 parameters",
        "Task 9",
    ),
)

# --- SPT6xxx: protocol gating (dossier D.1; B4/B5/S18) ----------------------
_SPT6XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT6001",
        "SPT6xxx",
        "Host function used above the declared/target protocol (B4/B5/S18)",
        "function is gated above the build's target protocol",
        "Task 10",
    ),
)

# --- SPT7xxx: flow analysis (dossier D.1; SS C.3) ---------------------------
_SPT7XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT7001",
        "SPT7xxx",
        "Non-Void method with a path that does not return (SS C.3 rule 3; P6/S17)",
        "not every path returns a value",
        "Task 6",
    ),
    CodeEntry(
        "SPT7002",
        "SPT7xxx",
        "Local read before it is definitely assigned on every path (SS C.3 rule 2)",
        "local may be used before it is assigned",
        "Task 6",
    ),
    CodeEntry(
        "SPT7003",
        "SPT7xxx",
        "Break / Continue outside a loop",
        "break/continue must be inside a while or for loop",
        "Task 6",
    ),
    CodeEntry(
        "SPT7004",
        "SPT7xxx",
        "Statement unreachable after a terminal return/raise",
        "unreachable code after a return or raise",
        "Task 6",
    ),
    CodeEntry(
        "SPT7005",
        "SPT7xxx",
        "Call-graph cycle among module-level helpers / private methods (InternalCall) -- "
        "recursion is rejected (E8)",
        "recursive and mutually-recursive calls are not supported",
        "Task 8",
    ),
)

# --- SPT8xxx: emitter limits (M1-D; dossier S22/P12, ruling M10) ------------
#: The emitter band, appended by M1-D Task 10 under the sub-plan's
#: CONTROLLER-SANCTIONED enumeration (M1-D plan-review B3) and the append-only
#: rule (D15). The first three rows are exactly the user-visible discriminators
#: `serpent.emitter.frame.BUILD_LIMITS` carries -- `module_size`, `pool`,
#: `scratch` -- which is what lets `build_wasm` map a `BuildLimitError` to a
#: located diagnostic without a second table; the fourth is the emitter's
#: coverage backstop, reported when the frontend accepts a construct this
#: emitter version has no lowering for. Every row is emitted from the EMITTER,
#: never from the frontend's checks, so none of them has (or can have) a
#: `tests/must_reject/` fixture -- see `NO_FIXTURE_REASONS`.
_SPT8XXX: tuple[CodeEntry, ...] = (
    CodeEntry(
        "SPT8001",
        "SPT8xxx",
        "module size",
        "the compiled module exceeds the network's 131072-byte contract size limit",
        "M1-D Task 10",
    ),
    CodeEntry(
        "SPT8002",
        "SPT8xxx",
        "literal pool",
        "the literal pool overflows into the scratch region at 0x1000",
        "M1-D Task 10",
    ),
    CodeEntry(
        "SPT8003",
        "SPT8xxx",
        "scratch region",
        "the scratch region exceeds the module's single 64 KiB memory page",
        "M1-D Task 10",
    ),
    CodeEntry(
        "SPT8004",
        "SPT8xxx",
        "emitter coverage",
        "the construct compiles but this emitter version cannot lower it yet",
        "M1-D Task 10",
    ),
)

REGISTRY: tuple[CodeEntry, ...] = (
    _SPT1XXX + _SPT2XXX + _SPT3XXX + _SPT4XXX + _SPT5XXX + _SPT6XXX + _SPT7XXX + _SPT8XXX
)

#: The bare set of codes, for O(1) "is this code registered" checks (used by
#: `diagnostics.Diagnostics.error`, which rejects an unregistered code --
#: codes are public API, and an unregistered one is a compiler bug).
CODES: frozenset[str] = frozenset(entry.code for entry in REGISTRY)

#: Codes provably without a source-level fixture trigger (dossier BL-1c).
#: Meta-test B (Task 11b) consults this to know which registry codes are
#: exempt from the "every code has >= 1 must_reject/ fixture" completeness
#: rule. Seeded, per the Task 1 brief, with the Task 10 protocol-gate code:
#: today no C-emitted host function carries a protocol gate above the base
#: (the frontend only ever emits the ungated v1 TTL form), so no real
#: source can trip SPT6001 -- it is proven end-to-end by a synthetic-bindings
#: unit test (Task 10) instead of a fixture. (Rider, constructor-floor fix:
#: SPT6001 also gates any constructor-bearing contract below protocol 22
#: (CAP-0058) -- a real source-level trigger -- but only via an explicit
#: `target_protocol`, a `compile_module` keyword no `must_reject` fixture can
#: set, so the allowlisting still holds.) `SPT1009`/`SPT4018`/`SPT7003`
#: were added by controller ruling during Task 11b's fixture-completion
#: round: each is a dead dispatch/check branch an earlier, always-first check
#: already claims on every real-source path (see `NO_FIXTURE_REASONS`), kept
#: in its owning module as defense-in-depth rather than deleted. The four
#: `SPT8xxx` rows joined for a different reason: they are EMITTER-side limits,
#: raised from `serpent.emitter` over a module the frontend already accepted,
#: so no small source fixture can trip one (see `NO_FIXTURE_REASONS`).
#: `SPT1032` joined in M1-E for a THIRD reason, and it is the one this list was
#: always meant to absorb: the construct it rejected became SUPPORTED (sub-plan
#: E's `Event.publish(env)` desugar), so there is no longer any source that
#: trips it. The registry row survives un-renumbered (D9 is append-only, and
#: reversal is not a thing a published code may do); its `message_intent` now
#: says it is superseded, and its fixture was deleted in the same commit.
NO_FIXTURE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "SPT1009",
        "SPT1032",
        "SPT4018",
        "SPT6001",
        "SPT7003",
        "SPT8001",
        "SPT8002",
        "SPT8003",
        "SPT8004",
    }
)

#: One reason string per `NO_FIXTURE_ALLOWLIST` entry.
NO_FIXTURE_REASONS: dict[str, str] = {
    "SPT1009": (
        "dead dispatch branch by construction: a bare Slice is always intercepted by "
        "SPT1013 (direct slice) or SPT1014 (multi-dim tuple); branch retained as "
        "defense-in-depth"
    ),
    "SPT1032": (
        "retired by M1-E (sub-plan E): the form it rejected is now supported -- "
        "`<Event instance>.publish(env)` desugars into the canonical event lowering, so "
        "no source can trip it; the row stays under the append-only rule (D9), and it "
        "leaves this allowlist with a fixture if it ever becomes reachable again"
    ),
    "SPT4018": (
        "superseded by SPT3020 per the Task 7b review adjudication (struct positional "
        "args ARE a call-arity shape); row retained under the append-only rule, never "
        "emitted"
    ),
    "SPT6001": (
        "no fixture-reachable trigger: no host function the frontend emits is gated above "
        "the base protocol (that arm is wired end-to-end via a synthetic-bindings unit "
        "test, Task 10), and the one FEATURE gate -- a contract with a constructor needs "
        "protocol >= 22, CAP-0058 -- fires only against an explicit target_protocol, which "
        "is a compile_module keyword a must_reject fixture cannot set"
    ),
    "SPT7003": (
        "unreachable end-to-end: CPython's compile() rejects break/continue outside a "
        "loop as a SyntaxError (bridged to SPT1037) before the frontend's loop-depth "
        "check runs; the stmt-layer check is retained as defense-in-depth for "
        "AST-only entry"
    ),
    "SPT8001": (
        "emitter-side limit: reported by build_wasm over an already-accepted module, so "
        "no small source fixture can deterministically trigger it (a 131072-byte module "
        "would be a fixture nobody could read); proven by an emitter unit test instead"
    ),
    "SPT8002": (
        "emitter-side limit: reported by build_wasm over an already-accepted module, so "
        "no small source fixture can deterministically trigger it (the pool reaches "
        "0x1000 only for thousands of literals); proven by an emitter unit test instead"
    ),
    "SPT8003": (
        "emitter-side limit: reported by build_wasm over an already-accepted module, so "
        "no small source fixture can deterministically trigger it (scratch exceeds one "
        "64 KiB page only for thousands of call sites); proven by an emitter unit test"
    ),
    "SPT8004": (
        "emitter coverage backstop: it fires exactly when the frontend accepts a "
        "construct the emitter has no lowering for, which is a defect state rather than "
        "a language rule -- any fixture for it would be deleted by the lowering that "
        "fixes it"
    ),
}


def validate() -> None:
    """Validate the registry's internal invariants; raise `ValueError` listing
    every problem found (collect-all, matching the diagnostics sink's own
    philosophy) if any fail.
    """
    problems: list[str] = []

    seen: dict[str, int] = {}
    for entry in REGISTRY:
        seen[entry.code] = seen.get(entry.code, 0) + 1
    duplicates = sorted(code for code, count in seen.items() if count > 1)
    if duplicates:
        problems.append(f"duplicate code(s): {', '.join(duplicates)}")

    for entry in REGISTRY:
        if len(entry.code) != 7 or not entry.code.startswith("SPT"):
            problems.append(f"{entry.code!r} is not a well-formed SPT#### code")
            continue
        band_digit = entry.code[3]
        # Bands 1-8: 1-7 are the frontend's, 8 is M1-D's emitter band.
        if band_digit not in "12345678" or not entry.code[4:].isdigit():
            problems.append(f"{entry.code!r} is not a well-formed SPT#### code")
            continue
        expected_band = f"SPT{band_digit}xxx"
        if entry.band != expected_band:
            problems.append(
                f"{entry.code}: band {entry.band!r} does not match its code (expected "
                f"{expected_band!r})"
            )
        if not entry.owning_task.strip():
            problems.append(f"{entry.code}: no owning task")
        if not entry.construct.strip():
            problems.append(f"{entry.code}: no construct")
        if not entry.message_intent.strip():
            problems.append(f"{entry.code}: no message intent")

    registry_codes = {entry.code for entry in REGISTRY}
    missing_from_registry = sorted(NO_FIXTURE_ALLOWLIST - registry_codes)
    if missing_from_registry:
        problems.append(
            f"NO_FIXTURE_ALLOWLIST code(s) not in REGISTRY: {', '.join(missing_from_registry)}"
        )

    reason_keys = set(NO_FIXTURE_REASONS)
    if reason_keys != NO_FIXTURE_ALLOWLIST:
        extra = sorted(reason_keys - NO_FIXTURE_ALLOWLIST)
        missing = sorted(NO_FIXTURE_ALLOWLIST - reason_keys)
        if extra:
            problems.append(f"NO_FIXTURE_REASONS has unlisted code(s): {', '.join(extra)}")
        if missing:
            problems.append(f"NO_FIXTURE_ALLOWLIST code(s) missing a reason: {', '.join(missing)}")

    if problems:
        raise ValueError(
            "codes.validate() found problems:\n" + "\n".join(f"- {p}" for p in problems)
        )
