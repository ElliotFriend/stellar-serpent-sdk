"""The three Soroban custom-section payloads, built from decorator metadata.

One builder per section (spec Sec.7):

* `contractenvmetav0` -> `build_env_meta(protocol)`
* `contractspecv0`    -> `build_spec_entries(contract_cls, types=..., events=...)`
* `contractmetav0`    -> `build_meta(name, version, pairs)`

Each returns a **bare stream of XDR entries** with no outer length prefix: a
reader decodes entries until the payload is exhausted, so plain concatenation
is the correct framing (recorded in `spikes/spike1/sections.py`, and confirmed
by the deployed `spike.wasm`, whose sections `tests/unit/test_sections.py`
decodes and compares byte for byte). Every byte comes out of `stellar_sdk.xdr`;
nothing here hand-rolls XDR.

**Validation is source-located on purpose.** `stellar_sdk` enforces the XDR's
own length caps inside its constructors, which produces a `ValueError` that
names a field (`doc`, `name`) but not the method, struct or contract it came
from. serpent therefore pre-validates every name and doc and raises
`SpecNameError` / `SpecDocError` naming the declaration, so an author can find
the offending line. The caps:

| what | cap | why that number |
|---|---|---|
| function name, function input name, struct field name, event param name | 30 | `decorators.NAME_LIMIT`; the XDR caps inputs/fields/event params at 30 |
| type name (struct, error enum) | 60 | `SCSpecTypeUDT.name` is a `string<60>`, so a longer name could be declared but never *referenced* |
| error-enum case name | 60 | `SCSpecUDTErrorEnumCaseV0.name` is a `string<60>` |
| event name, prefix topic | 32 | both are an `SCSymbol` (`val.SCSYMBOL_LIMIT`), NOT a `string<60>` -- an event's name cap is the tighter one |
| event prefix-topic count | 2 | `SCSpecEventV0.prefix_topics` is an `SCSymbol<2>` |
| any doc | 1024 | `SC_SPEC_DOC_LIMIT` |
| `lib` | 80 | always `b""` here: serpent has no cross-crate type references |

**What the decorators do NOT check, and this module must:** `__init__`'s name
(it is skipped there, and is emitted here as `__constructor`), *parameter*
names, class names, and error-enum case names. Everything this module emits
goes through `_check_name` regardless of whether a decorator already saw it.

**Events are their own keyword, not a `types=` member.** `SC_SPEC_ENTRY_EVENT_V0`
is built from `@contractevent`'s topic convention (M1-E Task 5): the metadata
carries `prefix_topics`, a per-field `locations` map (topic vs data) and a
`data_format`, so nothing here has to guess. They arrive via `events=` and are
emitted AFTER the functions (ruling E2), which is what keeps every spec that
declares no event -- the on-chain-verified spike1 golden included -- byte for
byte where it was. An event handed to `types=` is still refused: a UDT
reference names a type, and an event is not one.

**Deferred, deliberately:**

* Per-field, per-input and per-event-param docs are `b""`: `_serpent_type_`
  records no per-field doc. A real gap, noted for sub-plan C. *Class* and
  *method* docstrings ARE emitted.
"""

import inspect
from collections.abc import Mapping, Sequence
from types import MappingProxyType, NoneType
from typing import Any, Final

from stellar_sdk import xdr

import serpent
from serpent import val
from serpent.decorators import (
    _METADATA_ATTR,
    DATA_LOCATION,
    NAME_LIMIT,
    PREFIX_TOPIC_LIMIT,
    TOPIC_LOCATION,
    _check_data_format,
)
from serpent.env import Env
from serpent.spec.typemap import SpecTypeError, to_spec_type

__all__ = [
    "SpecDocError",
    "SpecNameError",
    "build_env_meta",
    "build_meta",
    "build_spec_entries",
]

#: `SCSpecTypeUDT.name` is a `string<60>`: a type whose name is longer could be
#: declared but never referenced, so 60 is the real cap for both.
TYPE_NAME_LIMIT: Final = 60

#: `SCSpecUDTErrorEnumCaseV0.name` is a `string<60>`.
CASE_NAME_LIMIT: Final = 60

#: `stellar_sdk.xdr.constants.SC_SPEC_DOC_LIMIT`, restated so the pre-check
#: does not depend on a private-ish constant import.
DOC_LIMIT: Final = 1024

#: The host-reserved export an `__init__` compiles to. The Stellar CLI derives
#: deploy-time `--arg-name` flags from this entry, so it is always emitted --
#: even with no arguments -- or a parameterized contract cannot be deployed.
CONSTRUCTOR_NAME: Final = "__constructor"

#: `build_meta` writes these three first, in this order (spec Sec.7); a caller
#: pair colliding with one is an error rather than a silent duplicate. All
#: three stay reserved even when `version=None` omits its entry: the key is
#: serpent's to write, and a user pair claiming it would be indistinguishable
#: from a contract version this build never declared.
RESERVED_META_KEYS: Final = ("name", "version", "serpentver")

_U32_MAX: Final = 2**32 - 1

#: An immutable empty default (a `{}` default would be a mutable-argument bug).
_NO_PAIRS: Final[Mapping[str, str]] = MappingProxyType({})


class SpecNameError(ValueError):
    """A name the contract spec cannot carry, with the declaration named."""


class SpecDocError(ValueError):
    """A docstring too long for the contract spec, with the method named."""


# --- contractenvmetav0 ------------------------------------------------------


def build_env_meta(protocol: int) -> bytes:
    """The `contractenvmetav0` payload for a declared target protocol.

    `pre_release` is always 0: serpent targets released protocols, and a
    non-zero pre-release version would claim compatibility with an unreleased
    host build. Call this with `serpent._host.declared_protocol(...)`'s answer
    rather than a literal, so the declared protocol and the computed floor
    cannot drift.

    Follows serpent's error convention: a wrong *type* is a `TypeError` (`bool`
    included -- it is an `int` subclass, and `build_env_meta(True)` is never
    what an author meant), an out-of-range `int` a `ValueError`.
    """
    if not isinstance(protocol, int) or isinstance(protocol, bool):
        raise TypeError(f"protocol must be an int, not {type(protocol).__name__}")
    if not 0 <= protocol <= _U32_MAX:
        raise ValueError(f"protocol must fit in a u32, got {protocol}")
    return xdr.SCEnvMetaEntry(
        kind=xdr.SCEnvMetaKind.SC_ENV_META_KIND_INTERFACE_VERSION,
        interface_version=xdr.SCEnvMetaEntryInterfaceVersion(
            protocol=xdr.Uint32(protocol),
            pre_release=xdr.Uint32(0),
        ),
    ).to_xdr_bytes()


# --- contractspecv0 --------------------------------------------------------


def build_spec_entries(
    contract_cls: type, *, types: Sequence[type] = (), events: Sequence[type] = ()
) -> bytes:
    """The `contractspecv0` payload for one `@contract` class.

    **`types` is not discovered, it is declared** -- and so is `events`. This
    function cannot find the `@contracttype` structs and `@contracterror` enums
    a contract's signatures mention -- an annotation only yields a UDT
    *reference*, never the entry it points at -- and it cannot find the
    `@contractevent` classes a module declares either. Sub-plan D collects the
    module's decorated classes and passes them here; **a caller that omits
    `types` silently emits a spec whose UDT references have no matching
    entries**, which decodes fine and renders as an unknown type. When in
    doubt, pass every decorated class in the module: structs and error enums in
    `types`, events in `events`.

    Entry order is pinned (and tested independently of the golden bytes),
    matching `spikes/spike1/sections.py`'s recorded rationale -- a stable order
    keeps builds deterministic:

    1. `UDT_STRUCT_V0`, in `types` order,
    2. `UDT_ERROR_ENUM_V0`, in `types` order,
    3. `FUNCTION_V0`: `__constructor` first, then declaration order,
    4. `EVENT_V0`, in `events` order.

    **Events go LAST on purpose** (ruling E2): appending them cannot move a
    single byte of a spec that declares none, which is what lets the on-chain
    spike1 golden stay byte-identical now that this keyword exists.

    A leading `env: Env` parameter is dropped from every signature: the host
    passes the environment implicitly, so it is not a spec input. (The Stellar
    CLI re-inserts it when rendering a trait, which is why the recorded on-chain
    render shows `fn setup(env: soroban_sdk::Env, counter_limit: u32)` for a
    spec entry whose only input is `counter_limit`.) An `Env` anywhere else
    raises `SpecTypeError` from the type mapping.
    """
    metadata = _metadata_of(contract_cls)
    if metadata is None or metadata.get("kind") != "contract":
        raise SpecTypeError(
            f"{contract_cls.__name__} is not a @contract class, so it has no "
            "contractspecv0 entries -- pass the contract class itself, and its "
            "structs and error enums via `types=`"
        )

    structs: list[xdr.SCSpecEntry] = []
    enums: list[xdr.SCSpecEntry] = []
    seen: dict[str, type] = {}
    for declared in types:
        # Structs and error enums share one spec namespace, and a UDT reference
        # carries only a name -- so two entries under one name is a spec that
        # cannot be resolved, however it was reached (the same class passed
        # twice, or two same-named classes from different modules).
        previous = seen.get(declared.__name__)
        if previous is not None:
            raise SpecNameError(
                f"{declared.__name__}: declared twice in `types` "
                f"({previous!r} and {declared!r}) -- a UDT reference names a "
                "type, so one spec cannot carry two entries for one name"
            )
        seen[declared.__name__] = declared
        kind, entry = _declared_type_entry(declared)
        (structs if kind == "struct" else enums).append(entry)

    methods: list[tuple[str, list[tuple[str, object]], object]] = metadata["methods"]
    functions = [
        _function_entry(contract_cls, name, params, returns)
        for name, params, returns in _constructor_first(methods)
    ]

    event_entries = [_event_entry(declared) for declared in events]

    return b"".join(entry.to_xdr_bytes() for entry in structs + enums + functions + event_entries)


def _declared_type_entry(declared: type) -> tuple[str, xdr.SCSpecEntry]:
    """One declared type's `(kind, entry)`, or a refusal naming the class."""
    metadata = _metadata_of(declared)
    kind = metadata.get("kind") if metadata is not None else None
    if metadata is not None and kind == "struct":
        return "struct", _struct_entry(declared, metadata)
    if metadata is not None and kind == "error_enum":
        return "error_enum", _enum_entry(declared, metadata)
    if kind == "event":
        raise SpecTypeError(
            f"{declared.__name__} is a @contractevent class -- pass it in `events=`, "
            "not in `types=`. An event is its own entry kind (EVENT_V0) with its own "
            "prefix topics, per-parameter locations and data format; `types=` carries "
            "the structs and error enums a UDT reference can name, and an event is "
            "not one of those"
        )
    if kind == "contract":
        raise SpecTypeError(
            f"{declared.__name__} is a @contract class, not a type -- pass it as "
            "the first argument, not in `types`"
        )
    raise SpecTypeError(
        f"{declared.__name__} is not a @contracttype struct or @contracterror "
        "enum, so it has no contractspecv0 entry"
    )


def _constructor_first(
    methods: Sequence[tuple[str, list[tuple[str, object]], object]],
) -> list[tuple[str, list[tuple[str, object]], object]]:
    return [m for m in methods if m[0] == "__init__"] + [m for m in methods if m[0] != "__init__"]


def _function_entry(
    contract_cls: type,
    name: str,
    params: Sequence[tuple[str, object]],
    returns: object,
) -> xdr.SCSpecEntry:
    emitted = CONSTRUCTOR_NAME if name == "__init__" else name
    # `__init__` never went through the decorator's name check, and parameter
    # names are checked nowhere else at all.
    _check_name(emitted, contract_cls, "function", NAME_LIMIT)

    inputs: list[xdr.SCSpecFunctionInputV0] = []
    for index, (param_name, annotation) in enumerate(params):
        if index == 0 and annotation is Env:
            continue
        _check_name(param_name, contract_cls, f"{emitted} parameter", NAME_LIMIT)
        inputs.append(
            xdr.SCSpecFunctionInputV0(
                doc=b"",
                name=param_name.encode("utf-8"),
                type=to_spec_type(annotation),
            )
        )

    # `outputs` is an XDR array<1>: empty for a void return, one entry
    # otherwise -- what the Rust SDK emits for `-> ()`, and what the deployed
    # spike.wasm carries for `setup`.
    outputs = [] if returns is NoneType or returns is None else [to_spec_type(returns)]

    return xdr.SCSpecEntry(
        kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0,
        function_v0=xdr.SCSpecFunctionV0(
            doc=_doc_bytes(
                _own_doc(vars(contract_cls).get(name)), f"{contract_cls.__name__}.{name}"
            ),
            name=xdr.SCSymbol(emitted.encode("utf-8")),
            inputs=inputs,
            outputs=outputs,
        ),
    )


def _struct_entry(declared: type, metadata: Mapping[str, Any]) -> xdr.SCSpecEntry:
    name = _check_type_name(declared)
    fields: list[tuple[str, object]] = metadata["fields"]
    return xdr.SCSpecEntry(
        kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0,
        udt_struct_v0=xdr.SCSpecUDTStructV0(
            # NOT COVERED BY THE ON-CHAIN ANCHOR. spike1's `Settings` carries no
            # docstring and the Phase 0 reference hardcoded `doc=b""` for UDTs,
            # so the 348-byte byte-identity check passes whatever this line
            # does. Validating it needs a Rust-artifact comparison of a
            # DOCUMENTED struct -- banked for sub-plan D.
            doc=_doc_bytes(_class_doc(declared), declared.__name__),
            # `lib` names the foreign crate a type was imported from; serpent
            # has no cross-module type references, so it is always empty.
            lib=b"",
            name=name.encode("utf-8"),
            fields=[
                xdr.SCSpecUDTStructFieldV0(
                    doc=b"",
                    name=_check_name(field_name, declared, "field", NAME_LIMIT).encode("utf-8"),
                    type=to_spec_type(annotation),
                )
                for field_name, annotation in fields
            ],
        ),
    )


def _enum_entry(declared: type, metadata: Mapping[str, Any]) -> xdr.SCSpecEntry:
    name = _check_type_name(declared)
    cases: list[tuple[str, int]] = metadata["cases"]
    return xdr.SCSpecEntry(
        kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0,
        udt_error_enum_v0=xdr.SCSpecUDTErrorEnumV0(
            # NOT COVERED BY THE ON-CHAIN ANCHOR, exactly as for a struct's doc
            # above: spike1's `Error` enum has no docstring either, so
            # byte-identity says nothing about this choice. Needs a
            # Rust-artifact comparison of a documented enum -- sub-plan D.
            doc=_doc_bytes(_class_doc(declared), declared.__name__),
            lib=b"",
            name=name.encode("utf-8"),
            cases=[
                xdr.SCSpecUDTErrorEnumCaseV0(
                    doc=b"",
                    name=_check_name(case_name, declared, "error case", CASE_NAME_LIMIT).encode(
                        "utf-8"
                    ),
                    value=xdr.Uint32(code),
                )
                for case_name, code in cases
            ],
        ),
    )


#: `@contractevent`'s `data_format` string -> its `SCSpecEventDataFormat` case.
#: Its keys are exactly `decorators.DATA_FORMATS`, the strings an author writes.
#: A dict literal cannot express that tie, so `test_sections.py` pins it: a
#: format the decorator accepts with no case here would `KeyError` at emission.
_DATA_FORMATS: Final[dict[str, xdr.SCSpecEventDataFormat]] = {
    "map": xdr.SCSpecEventDataFormat.SC_SPEC_EVENT_DATA_FORMAT_MAP,
    "vec": xdr.SCSpecEventDataFormat.SC_SPEC_EVENT_DATA_FORMAT_VEC,
    "single-value": xdr.SCSpecEventDataFormat.SC_SPEC_EVENT_DATA_FORMAT_SINGLE_VALUE,
}

#: A field's recorded `location` -> its `SCSpecEventParamLocationV0` case. The
#: keys are the decorator's own constants, never restated string literals.
_PARAM_LOCATIONS: Final[dict[str, xdr.SCSpecEventParamLocationV0]] = {
    TOPIC_LOCATION: xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_TOPIC_LIST,
    DATA_LOCATION: xdr.SCSpecEventParamLocationV0.SC_SPEC_EVENT_PARAM_LOCATION_DATA,
}


def _event_entry(declared: type) -> xdr.SCSpecEntry:
    """One `@contractevent` class's `SC_SPEC_ENTRY_EVENT_V0` entry.

    Every cap and every rule is re-checked here, source-located, before
    `stellar_sdk` sees a byte (R5): the event's own name and its prefix topics
    against the SCSymbol cap of 32, the prefix-topic COUNT against the XDR's
    `SCSymbol<2>`, each param name against the 30-byte cap, the `fields` and
    `locations` views against each other, and the `data_format` -- including
    `"single-value"`'s field arity -- through the DECORATOR's own
    `_check_data_format`, so there is exactly one copy of that rule.

    **Belt and braces, not paranoia.** `@contractevent` refuses all of this at
    the declaration site, so a failure here means metadata that did not come
    from the decorator. But this is the function that turns metadata into a
    published spec, and every one of these rules is a case where the XDR would
    happily encode a LIE: `SINGLE_VALUE` over two data params, a param whose
    location was never recorded. Refusing here, naming the class, is what keeps
    "valid XDR" and "true" the same thing.
    """
    metadata = _metadata_of(declared)
    if metadata is None or metadata.get("kind") != "event":
        raise SpecTypeError(
            f"{declared.__name__} is not a @contractevent class, so it has no "
            "contractspecv0 event entry -- `events=` takes the module's "
            "@contractevent classes"
        )
    name = _check_name(declared.__name__, declared, "event", val.SCSYMBOL_LIMIT)

    prefix_topics: Sequence[str] = metadata["prefix_topics"]
    if len(prefix_topics) > PREFIX_TOPIC_LIMIT:
        raise SpecTypeError(
            f"{declared.__name__}: an event declares at most {PREFIX_TOPIC_LIMIT} "
            f"prefix topics (got {len(prefix_topics)}) -- "
            "`SCSpecEventV0.prefix_topics` is an XDR `SCSymbol<2>`"
        )

    fields: Sequence[tuple[str, object]] = metadata["fields"]
    locations: Mapping[str, str] = metadata["locations"]
    _check_locations(declared, fields, locations)

    data_format: str = metadata["data_format"]
    try:
        # ONE copy of the format/arity rule, the decorator's. Its `ValueError`
        # already names the class; re-raising as `SpecTypeError` puts it in the
        # family every other refusal in this module belongs to.
        _check_data_format(declared, data_format, locations)
    except ValueError as exc:
        raise SpecTypeError(str(exc)) from exc

    return xdr.SCSpecEntry(
        kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_EVENT_V0,
        event_v0=xdr.SCSpecEventV0(
            # As for a struct and an error enum: emitting the class docstring is
            # serpent's own choice, and NOT covered by the on-chain anchor
            # (spike1 declares no event at all).
            doc=_doc_bytes(_class_doc(declared), declared.__name__),
            lib=b"",
            name=xdr.SCSymbol(name.encode("utf-8")),
            prefix_topics=[
                xdr.SCSymbol(
                    _check_name(prefix_topic, declared, "prefix topic", val.SCSYMBOL_LIMIT).encode(
                        "utf-8"
                    )
                )
                for prefix_topic in prefix_topics
            ],
            params=[
                xdr.SCSpecEventParamV0(
                    doc=b"",
                    name=_check_name(field_name, declared, "event parameter", NAME_LIMIT).encode(
                        "utf-8"
                    ),
                    type=to_spec_type(annotation),
                    location=_PARAM_LOCATIONS[locations[field_name]],
                )
                for field_name, annotation in fields
            ],
            data_format=_DATA_FORMATS[data_format],
        ),
    )


def _check_locations(
    declared: type, fields: Sequence[tuple[str, object]], locations: Mapping[str, str]
) -> None:
    """Refuse a `fields` / `locations` skew, naming the class and the skew.

    The two views must describe exactly the same field set. Without this check a
    MISSING key is a bare `KeyError('amount')` that names neither the class nor
    what went wrong, and an EXTRA key is worse: silently ignored, so an event
    whose metadata says a field is a topic can publish a spec that says it is
    data. An unknown location value is caught here too, for the same reason.
    """
    named = [name for name, _annotation in fields]
    missing = [name for name in named if name not in locations]
    extra = sorted(set(locations) - set(named))
    if missing or extra:
        raise SpecTypeError(
            f"{declared.__name__}: the event's `locations` map does not describe its "
            f"fields -- {'missing ' + ', '.join(missing) if missing else ''}"
            f"{'; ' if missing and extra else ''}"
            f"{'unknown field(s) ' + ', '.join(extra) if extra else ''}. Every field "
            "carries exactly one location, and only the fields do"
        )
    unknown = sorted({locations[name] for name in named} - set(_PARAM_LOCATIONS))
    if unknown:
        raise SpecTypeError(
            f"{declared.__name__}: location {', '.join(repr(u) for u in unknown)} is "
            f"not one of {', '.join(repr(k) for k in _PARAM_LOCATIONS)}"
        )


# --- contractmetav0 --------------------------------------------------------


def build_meta(name: str, version: str | None, pairs: Mapping[str, str] = _NO_PAIRS) -> bytes:
    """The `contractmetav0` payload: a stream of key/value `SCMetaEntry`.

    `("name", name)`, `("version", version)` and `("serpentver",
    serpent.__version__)` are written first, in that order (spec Sec.7); the
    caller's `pairs` follow in their own iteration order, so a `dict` literal's
    order is preserved. A caller key colliding with a reserved one is a
    `ValueError` rather than a duplicate entry, since the reader takes the
    first.

    **`version=None` OMITS the entry entirely** (ruling E8, M1-D Task 10's
    sanctioned edit): most contracts carry no version of their own, and writing
    an invented one -- `"0.0.0"`, or serpent's version standing in for the
    contract's -- would publish a claim the author never made. An empty string
    is still a `ValueError`: a blank version reads as a version.

    `serpentver` always names the compiler that produced the artifact and is
    read straight off `serpent.__version__`; `tests/unit/test_sections.py`
    holds that string equal to `importlib.metadata.version("serpent")`, so the
    two cannot drift.
    """
    if not name:
        raise ValueError("meta `name` must be a non-empty string")
    if version is not None and not version:
        raise ValueError("meta `version` must be a non-empty string when it is given")

    entries: list[tuple[str, str]] = [("name", name)]
    if version is not None:
        entries.append(("version", version))
    entries.append(("serpentver", serpent.__version__))
    for key, value in pairs.items():
        if key in RESERVED_META_KEYS:
            raise ValueError(
                f"meta key {key!r} is reserved and written by serpent itself "
                f"(reserved: {', '.join(RESERVED_META_KEYS)})"
            )
        entries.append((key, value))

    return b"".join(
        xdr.SCMetaEntry(
            kind=xdr.SCMetaKind.SC_META_V0,
            v0=xdr.SCMetaV0(key=key.encode("utf-8"), val=value.encode("utf-8")),
        ).to_xdr_bytes()
        for key, value in entries
    )


# --- shared helpers --------------------------------------------------------


def _metadata_of(declared: type) -> dict[str, Any] | None:
    """The class's OWN `_serpent_type_`, or `None`.

    `vars(...)`, not `getattr`: an undecorated subclass inherits the attribute
    without having been declared, and emitting an entry for it would name a
    class the compiler never processed.
    """
    metadata = vars(declared).get(_METADATA_ATTR)
    return metadata if isinstance(metadata, dict) else None


def _check_name(name: str, owner: type, what: str, limit: int) -> str:
    """Enforce one name cap and the Symbol charset; return the name unchanged.

    Length first, then charset -- an over-long name is a length problem, and
    reporting it as a charset problem sends the author looking for the wrong
    thing.

    The charset is `val.SYMBOL_CHARS`, the on-chain Symbol charset, but the
    check is `all(c in ...)` rather than `val.is_valid_symbol`: that function
    also enforces `SCSYMBOL_LIMIT` (32), which is right for a name that really
    is an `SCSymbol` (a function name, a struct field) and WRONG for the
    60-byte `string<60>` fields (a type name, an error-enum case name) -- a
    valid 60-character type name is not a valid Symbol.

    Applying the Symbol charset to those 60-byte fields is stricter than the
    XDR, which would take any bytes. It is deliberate: every Soroban tool
    renders a type name as a Rust identifier (see the recorded CLI output in
    `spikes/spike1/DEPLOY_LOG.md`), so a name outside this charset would emit a
    spec that decodes but cannot be rendered.
    """
    encoded = len(name.encode("utf-8"))
    if encoded > limit:
        raise SpecNameError(
            f"{owner.__name__}.{name}: {what} names are capped at {limit} bytes "
            f"by the contract spec (got {encoded})"
        )
    if not name or any(char not in val.SYMBOL_CHARS for char in name):
        raise SpecNameError(
            f"{owner.__name__}.{name}: {what} names must use the Symbol charset (a-z, A-Z, 0-9, _)"
        )
    return name


def _check_type_name(declared: type) -> str:
    """The type's spec name: its class name, capped at 60 bytes.

    Checked here rather than in `typemap` because this is the *declaration*, so
    the error can name it; `typemap` re-checks the same cap when emitting a
    reference (`SCSpecTypeUDT` enforces it in its constructor), which is the
    error a caller sees for a struct that was never passed in `types`.
    """
    return _check_name(declared.__name__, declared, "type", TYPE_NAME_LIMIT)


def _own_doc(owner: object) -> str:
    """The object's own docstring, cleandoc'd -- never an inherited one.

    `inspect.getdoc` falls back to the base class's docstring, which for a
    contract `__init__` means `object.__init__`'s "Initialize self..." text
    would be emitted into the spec of every contract that documents nothing.
    Guarding on the own `__doc__` first keeps `getdoc`'s cleandoc behavior
    (full text, dedented -- NOT first-line-only) without that fallback.
    """
    if owner is None or getattr(owner, "__doc__", None) is None:
        return ""
    return inspect.getdoc(owner) or ""


def _class_doc(declared: type) -> str:
    """A decorated class's docstring, minus the one `dataclasses` synthesizes.

    `@contracttype` applies `dataclasses.dataclass`, which sets
    `cls.__doc__ = cls.__name__ + str(inspect.signature(cls)).replace(' -> None', '')`
    when the class has none of its own. That synthetic signature string is not
    documentation and must never reach the spec, so it is recomputed and
    filtered out. (An author docstring that happens to be exactly that string
    is also dropped -- an acceptable trade for never emitting the synthetic
    one; the deployed `spike.wasm` carries `doc=b""` for its `Settings` struct,
    which is the behavior pinned by test.)

    Emitting a class docstring at all is serpent's own choice: it is the one
    thing the on-chain anchor cannot validate, since spike1's types carry no
    docstrings (see the markers at the two emission sites).
    """
    doc = _own_doc(declared)
    return "" if doc and doc == _synthetic_dataclass_doc(declared) else doc


def _synthetic_dataclass_doc(declared: type) -> str:
    try:
        signature = str(inspect.signature(declared))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return ""
    return declared.__name__ + signature.replace(" -> None", "")


def _doc_bytes(text: str, label: str) -> bytes:
    """UTF-8 encode a doc, refusing one the spec cannot carry.

    Counted in ENCODED BYTES, not characters: the XDR cap is on the string's
    length, so a 600-character docstring of two-byte characters is over it.
    """
    encoded = text.encode("utf-8")
    if len(encoded) > DOC_LIMIT:
        raise SpecDocError(
            f"{label}: docstring is {len(encoded)} bytes, over the contract "
            f"spec's {DOC_LIMIT}-byte limit -- shorten it or move the detail "
            "into a comment"
        )
    return encoded
