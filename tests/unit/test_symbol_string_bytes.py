import copy
import pickle

import pytest
from hypothesis import given
from hypothesis import strategies as st

from serpent import val
from serpent.types import U32, U64, Bytes, Bytes32, Bytes64, String, Symbol, bytes_n

# --- Symbol ------------------------------------------------------------------


def test_symbol_validation_matrix() -> None:
    assert Symbol("COUNT").text == "COUNT"
    assert Symbol("_").text == "_"
    assert Symbol("a" * val.SCSYMBOL_LIMIT).text == "a" * 32
    for bad in ("a" * 33, "has-dash", "has space", "café", "dot.dot", "$"):
        with pytest.raises(ValueError):
            Symbol(bad)
    with pytest.raises(TypeError):
        Symbol(b"COUNT")  # type: ignore[arg-type]


def test_symbol_rejects_the_empty_string() -> None:
    # Carried ruling from Task 2: the empty symbol is representable on-chain but
    # is always an authoring error, so serpent rejects it uniformly.
    with pytest.raises(ValueError):
        Symbol("")
    assert val.is_valid_symbol("") is False


def test_symbol_val_forms() -> None:
    assert Symbol("COUNT").to_val() == val.symbol_small("COUNT")
    assert Symbol("nine_char").to_val() == val.symbol_small("nine_char")
    assert Symbol.from_val(val.symbol_small("COUNT")) == Symbol("COUNT")
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Symbol("ten_chars_").to_val()
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        Symbol.from_val(val.from_major_minor_tag(0, 0, val.TAG_SYMBOL_OBJECT))
    with pytest.raises(ValueError):
        Symbol.from_val(val.pack_u32val(1))


def test_symbol_equality_and_ordering() -> None:
    assert Symbol("a") == Symbol("a")
    assert (Symbol("a") == Symbol("b")) is False
    assert Symbol("a") < Symbol("b") and Symbol("b") >= Symbol("b")
    assert sorted([Symbol("c"), Symbol("a"), Symbol("b")]) == [
        Symbol("a"),
        Symbol("b"),
        Symbol("c"),
    ]
    # Phase 0 map golden: sorted order of two real contract keys.
    assert Symbol("counter_limit") < Symbol("display_name")
    assert hash(Symbol("a")) == hash("a")
    assert len({Symbol("a"), Symbol("a")}) == 1


# --- String ------------------------------------------------------------------


def test_string_accepts_arbitrary_text() -> None:
    assert String("").text == ""
    assert String("hello world").text == "hello world"
    assert String("héllo 🌍").text == "héllo 🌍"
    assert String("has-dash and.dot").text == "has-dash and.dot"
    assert String("x" * 1000).text == "x" * 1000
    with pytest.raises(TypeError):
        String(7)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        String("\ud800")  # lone surrogate: not UTF-8 encodable, not on-chain


def test_string_orders_by_utf8_bytes() -> None:
    assert String("a") < String("b")
    assert String("Z") < String("a")  # byte order, not case-insensitive
    assert String("é") > String("z")  # 0xC3.. sorts after 0x7A
    assert sorted([String("b"), String("a")]) == [String("a"), String("b")]
    assert String("héllo") == String("héllo")
    assert (String("a") == String("b")) is False


def test_string_has_no_val_form_yet() -> None:
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        String("hi").to_val()
    with pytest.raises(NotImplementedError, match="sub-plan B"):
        String.from_val(val.from_major_minor_tag(0, 0, val.TAG_STRING_OBJECT))


# --- Bytes -------------------------------------------------------------------


def test_bytes_basics() -> None:
    b = Bytes(b"ab")
    assert b.data == b"ab"
    assert len(b) == 2
    assert len(Bytes(b"")) == 0
    with pytest.raises(TypeError):
        Bytes("ab")  # type: ignore[arg-type]


def test_bytes_indexing_returns_u32() -> None:
    # The host's bytes_get returns a U32Val, so serpent indexes into chain types.
    assert Bytes(b"ab")[0] == U32(97)
    assert Bytes(b"ab")[1] == U32(98)
    assert isinstance(Bytes(b"ab")[0], U32)
    with pytest.raises(IndexError):
        Bytes(b"ab")[2]
    with pytest.raises(IndexError):
        Bytes(b"")[0]


def test_negative_indices_are_out_of_range() -> None:
    # Ruled: indexing is chain-faithful everywhere (the host's bytes_get takes a
    # u32, so data[-1] cannot be compiled), matching Vec.get. Slicing keeps
    # Python's semantics.
    with pytest.raises(IndexError):
        Bytes(b"ab")[-1]
    with pytest.raises(IndexError):
        Bytes(b"ab")[-3]
    assert Bytes(b"abcd")[-2:] == Bytes(b"cd")
    assert Bytes(b"abcd")[:-2] == Bytes(b"ab")


def test_bytes_slicing_returns_bytes() -> None:
    b = Bytes(b"abcd")
    assert b[1:3] == Bytes(b"bc")
    assert isinstance(b[1:3], Bytes)
    assert b[:] == b
    assert b[10:] == Bytes(b"")
    # A slice of a fixed-length type is a plain Bytes: the length no longer holds.
    sliced = Bytes32(b"\0" * 32)[0:4]
    assert type(sliced) is Bytes and sliced == Bytes(b"\0" * 4)


def test_bytes_is_immutable_and_copies_its_input() -> None:
    # The annotation is `bytes` (the only form a contract can compile), but an
    # untyped caller can still hand over a mutable buffer, so the constructor
    # copies bytes-like input rather than aliasing it.
    source = bytearray(b"ab")
    b = Bytes(source)  # type: ignore[arg-type]
    source[0] = ord("z")
    assert b.data == b"ab"  # not aliased to the caller's mutable buffer
    with pytest.raises(AttributeError):
        b._payload = b"zz"


def test_bytes_equality_and_ordering() -> None:
    assert Bytes(b"ab") == Bytes(b"ab")
    assert (Bytes(b"ab") == Bytes(b"ac")) is False
    assert Bytes(b"ab") < Bytes(b"ac") and Bytes(b"b") > Bytes(b"ab")
    assert sorted([Bytes(b"b"), Bytes(b"a")]) == [Bytes(b"a"), Bytes(b"b")]
    assert hash(Bytes(b"ab")) == hash(b"ab")


def test_fixed_length_bytes_compare_equal_to_plain_bytes_of_the_same_payload() -> None:
    # Bytes32 is an authoring-time refinement of one ScVal case (Bytes, rank 13),
    # not a distinct chain type, so equality and ordering are by payload across
    # the whole family -- consistent with what val_cmp will answer.
    payload = b"\x01" * 32
    assert Bytes32(payload) == Bytes(payload)
    assert Bytes(payload) == Bytes32(payload)
    assert hash(Bytes32(payload)) == hash(Bytes(payload))
    assert not (Bytes32(payload) < Bytes(payload))
    assert Bytes32(payload) <= Bytes(payload)
    assert Bytes32(b"\0" * 32) < Bytes(b"\x01")


# --- bytes_n -----------------------------------------------------------------


def test_bytes_n_factory_is_cached_and_length_checked() -> None:
    assert bytes_n(32) is Bytes32
    assert bytes_n(64) is Bytes64
    assert bytes_n(7) is bytes_n(7)
    assert issubclass(Bytes32, Bytes) and issubclass(bytes_n(7), Bytes)
    assert isinstance(Bytes32(b"\0" * 32), Bytes)
    assert Bytes32.__name__ == "Bytes32" and bytes_n(7).__name__ == "Bytes7"
    assert Bytes32._LENGTH == 32 and Bytes64._LENGTH == 64 and Bytes._LENGTH is None
    assert bytes_n(7)(b"1234567").data == b"1234567"
    with pytest.raises(ValueError):
        Bytes32(b"short")
    with pytest.raises(ValueError):
        Bytes32(b"\0" * 33)
    with pytest.raises(ValueError):
        Bytes64(b"\0" * 32)
    assert len(Bytes64(b"\0" * 64)) == 64
    with pytest.raises(ValueError):
        bytes_n(-1)
    with pytest.raises(TypeError):
        bytes_n("32")  # type: ignore[arg-type]


def test_bytes32_is_usable_as_an_annotation() -> None:
    # Must type-check under mypy --strict: there is no BytesN[32] subscript form.
    x: Bytes32 = Bytes32(b"\0" * 32)
    def take(b: Bytes32) -> int:
        return len(b)

    assert take(x) == 32


# --- cross-type comparison contract ------------------------------------------


def test_ranks_follow_scval_type_order() -> None:
    assert Bytes._SCVAL_RANK == 13
    assert String._SCVAL_RANK == 14
    assert Symbol._SCVAL_RANK == 15
    assert Bytes32._SCVAL_RANK == 13 and bytes_n(7)._SCVAL_RANK == 13


def test_cmp_payloads_are_utf8_bytes_or_raw_bytes() -> None:
    assert Symbol("abc")._cmp_payload() == b"abc"
    assert String("é")._cmp_payload() == "é".encode()
    assert Bytes(b"ab")._cmp_payload() == b"ab"
    assert Bytes32(b"\0" * 32)._cmp_payload() == b"\0" * 32


def test_equality_never_raises_across_types() -> None:
    not_a_chain_value: object = None
    # A Symbol is not its text, and Bytes are not their bytes: these types do not
    # coerce. Equality still answers False rather than raising.
    assert (Symbol("a") == "a") is False
    assert (String("a") == "a") is False
    assert (Bytes(b"a") == b"a") is False
    assert (Symbol("a") == String("a")) is False
    assert (String("a") == Symbol("a")) is False
    assert (Bytes(b"a") == Symbol("a")) is False
    assert (Symbol("a") == U32(1)) is False
    assert (U32(1) == Symbol("a")) is False
    assert (Bytes(b"a") == not_a_chain_value) is False
    assert (Symbol("a") != Symbol("b")) is True


def test_ordering_against_foreign_types_raises() -> None:
    with pytest.raises(TypeError):
        _ = Symbol("a") < String("a")  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = String("a") < Symbol("a")  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Bytes(b"a") < Symbol("a")  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Symbol("a") < U32(1)       # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Bytes(b"a") <= U64(1)      # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Symbol("a") < "b"          # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = Bytes(b"a") > b"a"         # type: ignore[operator]


def test_immutability_and_repr() -> None:
    s = Symbol("a")
    with pytest.raises(AttributeError):
        s._payload = "b"
    with pytest.raises(AttributeError):
        del s._payload
    assert repr(Symbol("COUNT")) == "Symbol('COUNT')"
    assert repr(String("hi")) == "String('hi')"
    assert repr(Bytes(b"ab")) == "Bytes(b'ab')"
    payload = b"\x01" * 32
    assert repr(Bytes32(payload)) == f"Bytes32({payload!r})"


def test_copy_deepcopy_and_pickle_round_trip() -> None:
    originals: list[object] = [
        Symbol("COUNT"),
        String("héllo 🌍"),
        Bytes(b"\x00\xff"),
        Bytes32(b"\x01" * 32),
        Bytes64(b"\x02" * 64),
    ]
    for original in originals:
        clones = [
            copy.copy(original),
            copy.deepcopy(original),
            pickle.loads(pickle.dumps(original)),
        ]
        for clone in clones:
            assert type(clone) is type(original)
            assert clone == original


# --- properties ---------------------------------------------------------------

symbols = st.text(alphabet=st.sampled_from(val.SYMBOL_CHARS), min_size=1, max_size=32)
small_symbols = st.text(alphabet=st.sampled_from(val.SYMBOL_CHARS), min_size=1, max_size=9)


@given(small_symbols)
def test_symbol_val_round_trips(text: str) -> None:
    assert Symbol.from_val(Symbol(text).to_val()) == Symbol(text)


@given(symbols, symbols)
def test_symbol_ordering_matches_text_ordering(a: str, b: str) -> None:
    assert (Symbol(a) < Symbol(b)) == (a < b)
    assert (Symbol(a) == Symbol(b)) == (a == b)


@given(st.binary(max_size=64), st.binary(max_size=64))
def test_bytes_ordering_matches_raw_byte_ordering(a: bytes, b: bytes) -> None:
    assert (Bytes(a) < Bytes(b)) == (a < b)
    assert (Bytes(a) == Bytes(b)) == (a == b)
    assert (Bytes(a) <= Bytes(b)) == (a <= b)


@given(st.text(), st.text())
def test_string_ordering_matches_utf8_ordering(a: str, b: str) -> None:
    try:
        raw_a, raw_b = a.encode(), b.encode()
    except UnicodeEncodeError:
        return  # lone surrogates are rejected at construction
    assert (String(a) < String(b)) == (raw_a < raw_b)
    assert (String(a) == String(b)) == (raw_a == raw_b)
