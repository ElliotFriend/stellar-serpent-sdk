"""A union-and-int-enum-shaped contract, in serpent's authoring style (M1-E2).

`token_style.py`'s role (A23) for the two new declaration kinds: one real
contract spelling **every construction form and every read**, kept under the
tests-wide `uv run mypy --strict` run so that it is the executable POSITIVE
half of ruling E1's headline claim -- the descriptor surface catches an
author's mistakes with no plugin. There is not a single `# type: ignore` here,
and there must never be one: the negative half (the five mistakes that MUST
fail) lives in `tests/unit/test_authoring_types.py`, written to `tmp_path` and
checked in a subprocess, because a tracked file cannot be both clean and wrong
(review B5).

**Authoring forms demonstrated:**
* a unit variant (`Shape.Empty`), a one-payload variant (`Shape.Circle(U32)`)
  and a two-payload variant (`Shape.Rect(U32, U32)`) -- construction is a plain
  call on the case, and `mypy --strict` checks the payload types and the arity
  of each;
* an int enum with explicit discriminants (`Level.Low = enumvalue(0)`);
* every read: `tag()` compared against a `Symbol`, and `payload(index, ty)`
  with its **0-based-over-the-payload** index;
* both kinds as stored VALUES, and a union as a storage KEY (a union is an
  `ScVec` on chain, so it is a legitimate key -- and `storage_key` finds an
  equal-but-rebuilt one, which is the point of keying on a value at all);
* an int enum as a stored value read back under its own type, which is what
  makes the `get` tag check meaningful for it.

**One thing about this file's current state, deliberately visible.** Every
name now comes from the `serpent` root and both classes carry their decorator
(M1-E2 Task 2 landed the declaration layer), but the module does not COMPILE
yet: teaching the frontend to resolve `Shape`/`Level` in an annotation, and to
lower `tag()`/`payload()`/case construction, is Task 4's. Until then this
module is the typing fixture it is above, plus a member of the fuzz mangling
corpus (`tests/unit/test_frontend_fuzz.py`'s exact `fixtures/` inventory). Its
declaration spellings do not change when Task 4 lands.

Not collected by pytest (no `test_*` functions) and not imported at runtime by
anything; the gate that covers it is the config-driven `mypy --strict` run.
"""

from serpent import (
    U32,
    ContractEnum,
    ContractUnion,
    Env,
    Symbol,
    contract,
    contractenum,
    contractunion,
    enumvalue,
    variant,
)

SHAPE = Symbol("shape")
LEVEL = Symbol("level")


@contractunion
class Shape(ContractUnion):
    """A tagged union: one unit case and two tuple cases."""

    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Level(ContractEnum):
    """An int enum: each member IS the bare `u32` it names."""

    Low = enumvalue(0)
    High = enumvalue(1)


@contract
class UdtStyle:
    """A contract that stores, reads and branches on both new kinds."""

    def __init__(self, env: Env) -> None:
        env.storage().instance().set(SHAPE, Shape.Empty)
        env.storage().instance().set(LEVEL, Level.Low)

    # --- construction, one spelling per arity --------------------------------

    def clear(self, env: Env) -> None:
        env.storage().instance().set(SHAPE, Shape.Empty)

    def set_circle(self, env: Env, radius: U32) -> None:
        env.storage().instance().set(SHAPE, Shape.Circle(radius))

    def set_rect(self, env: Env, width: U32, height: U32) -> None:
        env.storage().instance().set(SHAPE, Shape.Rect(width, height))

    def promote(self, env: Env) -> None:
        env.storage().instance().set(LEVEL, Level.High)

    # --- reads ---------------------------------------------------------------

    def shape_name(self, env: Env) -> Symbol:
        """`tag()` is the variant name -- element 0 of the on-chain `ScVec`."""
        return env.storage().instance().get(SHAPE, Shape).tag()

    def current_shape(self, env: Env) -> Shape:
        """The stored union itself, not just its tag -- the same read
        `shape_name` makes, minus the `.tag()` narrowing."""
        return env.storage().instance().get(SHAPE, Shape)

    def radius(self, env: Env) -> U32:
        """A one-payload read, guarded by the tag the way a compiled contract
        must guard it: the payload of a `Rect` is not a `Circle`'s."""
        shape = env.storage().instance().get(SHAPE, Shape)
        if shape.tag() == Symbol("Circle"):
            return shape.payload(U32(0), U32)
        return U32(0)

    def area(self, env: Env) -> U32:
        """A two-payload read: index 0 is the FIRST payload value, not the
        variant name."""
        shape = env.storage().instance().get(SHAPE, Shape)
        if shape.tag() == Symbol("Rect"):
            return shape.payload(U32(0), U32) * shape.payload(U32(1), U32)
        return U32(0)

    def level(self, env: Env) -> Level:
        return env.storage().instance().get(LEVEL, Level)

    # --- a union as a storage KEY -------------------------------------------

    def record(self, env: Env, width: U32, height: U32, level: Level) -> None:
        """Key on the union itself: a rebuilt-but-equal `Rect` finds this
        entry, which is what keying by VALUE means (S13)."""
        env.storage().persistent().set(Shape.Rect(width, height), level)

    def recorded(self, env: Env, width: U32, height: U32) -> Level:
        return env.storage().persistent().get(Shape.Rect(width, height), Level)
