"""The container chain types: `Vec` and `Map`.

Both are host-shaped rather than Python-shaped -- the method names and their
error behaviour mirror the host functions a contract compiles down to
(`vec_push_back`, `map_get`, ...), because these classes are the behavioral
oracle the compiler will be proven against:

* **Element types are explicit at construction**: `Vec(U32)`,
  `Vec(U32, [U32(1)])`, `Map(Symbol, U32)`. `Generic` cannot deliver the type
  argument at runtime (`__orig_class__` is only set *after* `__init__`), so the
  runtime needs it passed in. `Vec[U32]` and `Map[Symbol, U32]` remain the
  annotation forms.
* **Exception mapping**: host traps become builtin exceptions -- out-of-bounds
  indices and popping an empty `Vec` raise `IndexError`, a missing `Map` key
  raises `KeyError` (the host's `map_get`/`map_del` trap). Authoring-time
  misuse -- putting the wrong type into a `Vec` -- raises `TypeError`.
* **`Map` is always sorted by `val_cmp(key)`**, which is observable on-chain:
  iteration, `keys()`, `values()` and the positional accessors all follow the
  host's `ScValType`-rank order.

**Homogeneity asymmetry (deliberate).** `Vec` enforces its element type at
runtime, so a `Vec` is homogeneous by construction; that is serpent's authoring
constraint, not a host rule. `Map` does *not* enforce its declared key or value
type at runtime: the host allows heterogeneous keys and `val_cmp` totally orders
them (a `U32` key sorts before a `Bytes` key before a `Symbol` key, whatever
their payloads), so rejecting them here would be inventing a restriction the
chain does not have. `Map`'s declared types still drive `mypy --strict` and
supply the element types for `keys()`/`values()` whenever the contents actually
satisfy them.

What `Map` *does* enforce at runtime is **chain-ness**: a key or value with no
`_SCVAL_RANK`/`_cmp_payload` raises `TypeError` on the way in, whatever the map's
size. Without that, an empty map accepts a key its binary search never compared
(the search returns before calling `val_cmp`), and every later operation on that
map raises. Consequently `keys()`/`values()` can always fall back to the
permissive `ChainValue` element type for a heterogeneous map, so the returned
`Vec` satisfies its own invariant: every `Vec` operation works on its own
contents.

**Deferred:** `Vec` and `Map` carry their `ScValType` ranks (16 and 17), so they
order correctly against every scalar, but comparing two containers *of the same
rank* needs nested-container host semantics that have not been verified yet --
`_cmp_payload()` raises `NotImplementedError("container comparison; sub-plan
B")`. Containers are mutable, hence unhashable; `copy`/`deepcopy` produce
independent containers (pickling is not modelled in M1-A).
"""

import copy as _copy
from collections.abc import Iterable, Iterator
from typing import Any, ClassVar, Generic, TypeVar

from serpent.types._ordering import ChainValue, require_chain_value, val_cmp
from serpent.types.numeric import U32

T = TypeVar("T", bound=ChainValue)
K = TypeVar("K", bound=ChainValue)
V = TypeVar("V", bound=ChainValue)

_DEFERRED = "container comparison; sub-plan B"


class Vec(Generic[T]):
    """The chain `Vec`: an ordered, homogeneous sequence of chain values.

    `Vec(U32)` builds an empty one, `Vec(U32, [U32(1), U32(2)])` a populated
    one. Note `append(other)` follows the host (`vec_append` concatenates
    another `Vec`), NOT `list.append`; the single-element operation is
    `push_back`.
    """

    __slots__ = ("_element_type", "_items")

    _SCVAL_RANK: ClassVar[int] = 16

    _element_type: type[T]
    _items: list[T]

    def __init__(self, element_type: type[T], items: Iterable[T] | None = None) -> None:
        if not isinstance(element_type, type):
            raise TypeError(
                f"Vec() takes the element type as its first argument, "
                f"not {element_type!r}"
            )
        self._element_type = element_type
        self._items = []
        if items is not None:
            for item in items:
                self.push_back(item)

    @property
    def element_type(self) -> type[T]:
        return self._element_type

    # --- internals -----------------------------------------------------------

    def _check(self, value: T) -> T:
        if not isinstance(value, self._element_type):
            raise TypeError(
                f"Vec element must be {self._element_type.__name__}, "
                f"not {type(value).__name__}"
            )
        return value

    def _require_index(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            raise IndexError(
                f"index {index} out of range for Vec of length {len(self._items)}"
            )

    # --- host-shaped API -----------------------------------------------------

    def push_back(self, value: T) -> None:
        self._items.append(self._check(value))

    def push_front(self, value: T) -> None:
        self._items.insert(0, self._check(value))

    def pop_back(self) -> T:
        if not self._items:
            raise IndexError("pop_back from an empty Vec")
        return self._items.pop()

    def pop_front(self) -> T:
        if not self._items:
            raise IndexError("pop_front from an empty Vec")
        return self._items.pop(0)

    def get(self, index: int) -> T:
        self._require_index(index)
        return self._items[index]

    def put(self, index: int, value: T) -> None:
        self._require_index(index)
        self._items[index] = self._check(value)

    def del_(self, index: int) -> None:
        self._require_index(index)
        del self._items[index]

    def insert(self, index: int, value: T) -> None:
        """Insert before `index`; `index == len(self)` appends (host `vec_insert`)."""
        if not 0 <= index <= len(self._items):
            raise IndexError(
                f"index {index} out of range for inserting into a Vec of "
                f"length {len(self._items)}"
            )
        self._items.insert(index, self._check(value))

    def append(self, other: "Vec[T]") -> None:
        """Concatenate another `Vec` onto this one (host `vec_append`)."""
        if not isinstance(other, Vec):
            raise TypeError(f"Vec.append() takes a Vec, not {type(other).__name__}")
        items: list[T] = list(other._items)
        for item in items:  # validate everything before mutating anything
            self._check(item)
        self._items.extend(items)

    def slice(self, lo: int, hi: int) -> "Vec[T]":
        """`self[lo:hi]` as a new `Vec`; the host traps rather than clamping."""
        if not 0 <= lo <= hi <= len(self._items):
            raise IndexError(
                f"slice({lo}, {hi}) out of range for Vec of length {len(self._items)}"
            )
        return Vec(self._element_type, self._items[lo:hi])

    def first_index_of(self, value: T) -> U32 | None:
        """The index of the first equal element, or `None` (the host's `Option<u32>`)."""
        self._check(value)
        for index, item in enumerate(self._items):
            if item == value:
                return U32(index)
        return None

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    # --- value semantics -----------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Element-wise; never raises. Two empty `Vec`s are equal whatever their
        element types -- on-chain they are the same empty `ScVec`."""
        if isinstance(other, Vec):
            other_items: list[Any] = other._items
            return self._items == other_items
        return NotImplemented

    # No __hash__: defining __eq__ leaves it None, and a mutable container must
    # not be hashable.

    def _cmp_payload(self) -> object:
        raise NotImplementedError(_DEFERRED)

    def __repr__(self) -> str:
        return f"Vec({self._element_type.__name__}, {self._items!r})"

    def __copy__(self) -> "Vec[T]":
        return Vec(self._element_type, self._items)

    def __deepcopy__(self, memo: dict[int, Any]) -> "Vec[T]":
        return Vec(self._element_type, [_copy.deepcopy(item, memo) for item in self._items])


def _element_type_for(declared: type[Any], items: list[Any]) -> type[Any]:
    """The most specific element type that every item actually satisfies.

    `Map.keys()`/`values()` normally hand back the declared type, but a
    legitimately heterogeneous `Map` must not produce a `Vec` that lies about
    its own contents: a `Vec` claiming `element_type is U32` while holding a
    `Symbol` would fail its own `slice`/`append`/`first_index_of`. When the
    entries do not all satisfy the declared type, the honest answer is the
    permissive `ChainValue` protocol, which every chain value satisfies (`Map`
    validates chain-ness on the way in), so every `Vec` operation keeps working
    on the result.
    """
    if all(isinstance(item, declared) for item in items):
        return declared
    return ChainValue


class Map(Generic[K, V]):
    """The chain `Map`: key/value pairs kept sorted by `val_cmp(key)`.

    `Map(Symbol, U32)` builds an empty one; the optional third argument takes
    initial `(key, value)` pairs. `get`/`del_` raise `KeyError` for a missing
    key because the host's `map_get`/`map_del` trap -- this is not `dict.get`.
    """

    __slots__ = ("_key_type", "_pairs", "_value_type")

    _SCVAL_RANK: ClassVar[int] = 17

    _key_type: type[K]
    _value_type: type[V]
    _pairs: list[tuple[K, V]]

    def __init__(
        self,
        key_type: type[K],
        value_type: type[V],
        entries: Iterable[tuple[K, V]] | None = None,
    ) -> None:
        if not isinstance(key_type, type) or not isinstance(value_type, type):
            raise TypeError(
                "Map() takes the key and value types as its first two arguments"
            )
        self._key_type = key_type
        self._value_type = value_type
        self._pairs = []
        if entries is not None:
            for key, value in entries:
                self.set(key, value)

    @property
    def key_type(self) -> type[K]:
        return self._key_type

    @property
    def value_type(self) -> type[V]:
        return self._value_type

    # --- internals -----------------------------------------------------------

    def _search(self, key: K) -> tuple[int, bool]:
        """Binary search by `val_cmp`: `(insertion point, found)`.

        The key is validated first: on an empty (or single-branch) map the
        search can return without ever calling `val_cmp`, and a key that slipped
        in uncompared would make every later operation raise.
        """
        require_chain_value(key)
        lo, hi = 0, len(self._pairs)
        while lo < hi:
            mid = (lo + hi) // 2
            order = val_cmp(self._pairs[mid][0], key)
            if order == 0:
                return mid, True
            if order < 0:
                lo = mid + 1
            else:
                hi = mid
        return lo, False

    def _require_position(self, position: int) -> None:
        if not 0 <= position < len(self._pairs):
            raise IndexError(
                f"position {position} out of range for Map of size {len(self._pairs)}"
            )

    # --- host-shaped API -----------------------------------------------------

    def set(self, key: K, value: V) -> None:
        require_chain_value(value)
        index, found = self._search(key)
        if found:
            self._pairs[index] = (key, value)
        else:
            self._pairs.insert(index, (key, value))

    def get(self, key: K) -> V:
        index, found = self._search(key)
        if not found:
            raise KeyError(key)
        return self._pairs[index][1]

    def has(self, key: K) -> bool:
        _, found = self._search(key)
        return found

    def del_(self, key: K) -> None:
        index, found = self._search(key)
        if not found:
            raise KeyError(key)
        del self._pairs[index]

    def keys(self) -> Vec[K]:
        """The keys in `val_cmp` order.

        Built through the validating `Vec` constructor, with an element type
        that the contents actually satisfy -- see `_element_type_for`.
        """
        items: list[K] = [key for key, _ in self._pairs]
        keys_vec: Vec[K] = Vec(_element_type_for(self._key_type, items), items)
        return keys_vec

    def values(self) -> Vec[V]:
        """The values, in key order (same element-type rule as `keys()`)."""
        items: list[V] = [value for _, value in self._pairs]
        values_vec: Vec[V] = Vec(_element_type_for(self._value_type, items), items)
        return values_vec

    def key_by_pos(self, position: int) -> K:
        self._require_position(position)
        return self._pairs[position][0]

    def val_by_pos(self, position: int) -> V:
        self._require_position(position)
        return self._pairs[position][1]

    def __len__(self) -> int:
        return len(self._pairs)

    def __iter__(self) -> Iterator[K]:
        """Keys in `val_cmp` order -- the order the host stores them in."""
        return iter([key for key, _ in self._pairs])

    # --- value semantics -----------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """Pairwise over the sorted entries; never raises."""
        if isinstance(other, Map):
            other_pairs: list[tuple[Any, Any]] = other._pairs
            return self._pairs == other_pairs
        return NotImplemented

    # No __hash__ -- see Vec.

    def _cmp_payload(self) -> object:
        raise NotImplementedError(_DEFERRED)

    def __repr__(self) -> str:
        return (
            f"Map({self._key_type.__name__}, {self._value_type.__name__}, "
            f"{self._pairs!r})"
        )

    def __copy__(self) -> "Map[K, V]":
        return Map(self._key_type, self._value_type, self._pairs)

    def __deepcopy__(self, memo: dict[int, Any]) -> "Map[K, V]":
        return Map(
            self._key_type,
            self._value_type,
            [
                (_copy.deepcopy(key, memo), _copy.deepcopy(value, memo))
                for key, value in self._pairs
            ],
        )
