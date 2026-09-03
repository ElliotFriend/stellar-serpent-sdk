"""Tagged unions and int enums: one value that is one of several shapes.

`@contracttype` (see `examples/structs.py`) declares a record -- every field,
every time. `@contractunion` declares the other half of the on-chain value
model: a value that is EXACTLY ONE of a fixed set of cases, each carrying its
own payload. `@contractenum` is the degenerate case of that, a plain numbered
choice with no payload at all.

Three declaration forms and their on-chain shapes, which are facts about
Soroban rather than serpent's choices (byte-verified against soroban-sdk 22 and
27 builds):

* `Empty = variant()` -- a UNIT case. Its value is a one-element `ScVec`
  holding the case-name `Symbol`; not a bare `Symbol`, which is the detail
  everyone guesses wrong;
* `Circle = variant(U32)` / `Rect = variant(U32, U32)` -- TUPLE cases. The
  value is `ScVec[Symbol, *payload]`, the payload in DECLARATION order;
* `Red = enumvalue(0)` -- an int-enum member. Its value is a **bare `U32`**:
  no wrapper, no name, nothing to unwrap. That is why `paint(env, Color.Blue)`
  crosses the ABI as the number 2 and why an int enum costs nothing to store.

Construction is a call on the case (`Shape.Circle(radius)`), except for a unit
case, which is a bare attribute (`Shape.Empty` -- there is nothing to pass).
`mypy --strict` checks the payload types and the arity with no plugin, so
`Shape.Circle(width, height)` and `Shape.Rect(U32(1))` are static errors, and
the compiler refuses them too (`SPT3020` for the arity, `SPT3018` for the slot
type).

## Reading one back: `tag()`, then `payload()`

There is no `match` in the subset, and there is no attribute per case. A union
is read in two steps, both below in `area`:

    if shape.tag() == Symbol("Rect"):
        return shape.payload(U32(0), U32) * shape.payload(U32(1), U32)

`tag()` answers the case-name `Symbol` -- element 0 of the vec -- and
`payload(index, ty)` answers one payload slot, **0-based over the PAYLOAD**:
slot 0 of a `Rect` is its width, not its name. The `if`/`elif` chain is not a
workaround for a missing `match`; it is literally the rewrite `SPT1024`'s own
help text recommends. Three things are compile errors rather than runtime
surprises: a misspelled case name (`SPT3022`), an index at or above the case's
arity or a `ty` matching no slot (`SPT3021`), and a `payload()` index that is
not a literal (`SPT1037`).

An int enum is read by comparing it -- `color == Color.Green`, in `palette`
below. Ordering (`<`) and arithmetic on an int enum are compile-rejected: the
discriminants are an identity, not a scale, and Rust's own `#[contracttype]`
enums carry no such operators either.

## Storage: both kinds are chain values, and a union is also a KEY

`put`/`get` take either kind under any of the three durabilities, and this
contract deliberately uses all three for the three different jobs a real
contract has: the shape being drawn is INSTANCE state, the color is
PERSISTENT, and the pin set is TEMPORARY. `pin`/`is_pinned` key an entry ON
the union itself, which works for the same reason `examples/structs.py`'s
struct key works -- the host compares the VALUE, so a `Shape.Circle(U32(3))`
rebuilt in a later invocation finds the entry the first one wrote.

`get(SHAPE, Shape, default=Shape.Empty)` is worth copying: a unit variant is
the natural "nothing yet" value, so this contract needs neither an `__init__`
nor a `has` guard before its first read.

## Two boundaries this file is honest about

**A union is a hashable storage KEY, but it is not modelled as ORDERABLE at
tier 1.** Keying a storage entry on one is fine (that is `pin`). A
`Map[Shape, V]` is NOT, at any entry count: the map keeps its pairs sorted by
`val_cmp` of the key, comparing two unions needs the nested-container ordering
sub-plan B verifies, and the model raises `NotImplementedError` rather than
inventing an order. The first `set` does go in silently -- a one-element map
compares nothing -- but reading it back does not: `get` and `has` compare the
probe key against the stored one and raise, so a single-entry map keyed by a
union is not a workaround either. If you want a map keyed by shape, key it on a
`@contracttype` struct instead and let the struct's fields carry the shape's
data.

**`get(key, Shape)` accepts a stored plain `Vec`, and `get(key, Color)` a
stored plain `U32` -- and reads each one back AS the type you asked for.** The
check `get` makes is TAG-level ("is this an `ScVec`?", "is this a `u32`?"),
because that is the only check the host makes: it hands back a bare word and
looks up no spec, so the `ty` argument is what says how to read it. A vec that
was never built as a `Shape` therefore comes back as a `Shape`, and a stored
`Color.Blue` read as `get(key, U32)` comes back as the `U32(2)` it IS on chain.
That is the same latitude a `@contracttype` struct already has against a stored
`Map` (a union IS an `ScVec` on chain and a struct IS a `Map`, so there is
nothing finer to check without a spec lookup the host does not do). Read it as:
the type argument tells `get` what you MEANT, the host confirms only the shape,
and what you get back is the word, read your way.

## Every entry point here answers a `Symbol`, a `U32` or a `Bool`

A contract MAY return a union or an int enum, and this module's spec declares
both types so a client can decode one (`tests/unit/test_examples.py` asserts
the `UDT_UNION_V0` and `UDT_ENUM_V0` entries are really in the built module).
The methods below return scalars anyway, because that is what lets ONE test
compare the tier-1 answers against the WASM answers value for value: the mini
host in `tests/harness` has no spec decoder, so it hands a returned union back
as an opaque vec and a returned enum back as a bare `U32`. That is a
limitation of the mini host -- it cannot decode a union/enum return; the real
host leg does (`tests/real_host/test_examples_real.py`) -- and not a rule
about what you may write.

Run it two ways -- `tests/unit/test_examples.py` does both and asserts the two
legs agree:

    from serpent.env import Env, deploy

    env = Env()
    drawing = deploy(Drawing, env)
    with env.frame():
        drawing.draw_rect(env, U32(4), U32(5))
        drawing.kind(env)    # -> Symbol('Rect')
        drawing.area(env)    # -> U32(20)
"""

from serpent import (
    U32,
    Bool,
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

#: The shape being drawn: instance storage, because it is the contract's own
#: singleton state and shares the contract's TTL.
SHAPE = Symbol("SHAPE")
#: The color: persistent storage, with a lifetime of its own.
COLOR = Symbol("COLOR")


@contractunion
class Shape(ContractUnion):
    """One shape, in one of three cases -- a unit, a one-payload and a
    two-payload variant, which is every arity that behaves differently."""

    Empty = variant()
    Circle = variant(U32)
    Rect = variant(U32, U32)


@contractenum
class Color(ContractEnum):
    """A numbered choice with no payload. Each member IS the `u32` it names.

    The discriminants are explicit and must stay put: like an error code, a
    member's number is part of the interface, so add new members at the end
    and never renumber an existing one.
    """

    Red = enumvalue(0)
    Green = enumvalue(1)
    Blue = enumvalue(2)


@contract
class Drawing:
    """One shape, its color, and the shapes pinned for later."""

    def clear(self, env: Env) -> None:
        """The UNIT case: `Shape.Empty` is an attribute, not a call."""
        env.storage().instance().set(SHAPE, Shape.Empty)

    def draw_circle(self, env: Env, radius: U32) -> None:
        """A one-payload case. `Shape.Circle(radius)` types as `(U32) -> Shape`."""
        env.storage().instance().set(SHAPE, Shape.Circle(radius))

    def draw_rect(self, env: Env, width: U32, height: U32) -> None:
        """A two-payload case; the payload keeps its declaration order."""
        env.storage().instance().set(SHAPE, Shape.Rect(width, height))

    def kind(self, env: Env) -> Symbol:
        """The first read pattern: `tag()`, the case-name `Symbol`.

        `default=Shape.Empty` is what makes this answer before anything has
        been drawn -- the unit case as the empty value, so this contract needs
        no constructor.
        """
        return env.storage().instance().get(SHAPE, Shape, default=Shape.Empty).tag()

    def area(self, env: Env) -> U32:
        """The second read pattern: branch on `tag()`, then read `payload()`.

        Every arm reads only the slots ITS case declares, which is the whole
        discipline a tagged union asks for: a `Circle` has no slot 1, and
        asking for one is `SPT3021` at compile time rather than a trap on
        chain. `U32(3) * radius * radius` is a deliberately crude area -- the
        subset has no floats, and a real contract would carry a scale factor.
        """
        shape = env.storage().instance().get(SHAPE, Shape, default=Shape.Empty)
        if shape.tag() == Symbol("Rect"):
            return shape.payload(U32(0), U32) * shape.payload(U32(1), U32)
        elif shape.tag() == Symbol("Circle"):
            radius = shape.payload(U32(0), U32)
            return U32(3) * radius * radius
        else:
            return U32(0)

    def paint(self, env: Env, color: Color) -> None:
        """An int enum as a PARAMETER and as a stored value.

        It crosses the ABI as its bare discriminant, so a client passes the
        number and the contract's spec entry is what names it.
        """
        env.storage().persistent().set(COLOR, color)

    def palette(self, env: Env) -> Symbol:
        """The int-enum read pattern: an `if`/`elif` chain over `==`.

        The mirror of `area`'s chain over `tag()`, and the only thing an int
        enum can do besides being stored and passed around. `<` and `+` on a
        `Color` are compile errors, so a chain of equalities is the shape --
        deliberately exhaustive, with the last case as the `else`.
        """
        color = env.storage().persistent().get(COLOR, Color, default=Color.Red)
        if color == Color.Green:
            return Symbol("green")
        elif color == Color.Blue:
            return Symbol("blue")
        else:
            return Symbol("red")

    def pin(self, env: Env) -> None:
        """A union as a storage KEY: the pinned set is one entry per shape.

        Temporary storage, because a pin is cheap and expected to lapse.
        """
        shape = env.storage().instance().get(SHAPE, Shape, default=Shape.Empty)
        env.storage().temporary().set(shape, Bool(True))

    def is_pinned(self, env: Env) -> Bool:
        """The key is rebuilt from storage, not remembered.

        A separately built `Shape.Circle(U32(3))` finds the entry the earlier
        one wrote, because the host compares the value and not the handle --
        the same property `examples/structs.py`'s struct key relies on.
        """
        shape = env.storage().instance().get(SHAPE, Shape, default=Shape.Empty)
        return env.storage().temporary().has(shape)
