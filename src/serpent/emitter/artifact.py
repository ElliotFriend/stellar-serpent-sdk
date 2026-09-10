"""`inspect_artifact`: what a built module says about itself (ruling E3; G dossier D.1).

The facts `stellar contract info` cannot know, because they are serpent's:
the host-function IMPORTS resolved to their pinned names and protocol gates,
the DECLARED protocol (out of `contractenvmetav0`) beside the floor RECOMPUTED
from those imports plus the constructor gate (rulings D6/D9) -- the
honest-declaration check -- and the spec/meta entries by name. The interface
itself is deliberately NOT rendered here; the stock CLI already does that
from the same section (spec §10's drift rule).

Everything is read with the decoders this package already has
(`validate.iter_sections`, `printer._decode_imports`/`_decode_exports`) and
`serpent.spec.decode` for the XDR. Nothing decodes a `Val` word.

This is the SECOND module in `serpent/emitter/` allowed to import
`serpent.spec` -- `sections.py`, the write side, is the first. The boundary is
about the build path: nothing in this package imports this module, so `build`
never reaches `stellar_sdk` through it and `stellar-serpent inspect` imports it
lazily (`tests/unit/test_emitter_module.py` asserts both halves).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from serpent._host import CONSTRUCTOR_MIN_PROTOCOL, HOST_FUNCTIONS, compute_protocol_floor
from serpent.emitter import printer, validate
from serpent.emitter.frame import EmitError
from serpent.emitter.sections import ENV_META_SECTION_NAME, META_SECTION_NAME, SPEC_SECTION_NAME
from serpent.spec import decode

_SECTION_NAMES = {
    1: "type",
    2: "import",
    3: "function",
    4: "table",
    5: "memory",
    6: "global",
    7: "export",
    8: "start",
    9: "element",
    10: "code",
    11: "data",
    12: "data count",
}
_BY_IMPORT = {(fn.module, fn.export): fn for fn in HOST_FUNCTIONS}


class MalformedArtifact(ValueError):
    """Not a wasm module we can read: bad magic, truncated, or an unreadable section."""


@dataclass(frozen=True)
class Section:
    id: int
    name: str | None  # the custom section's name for id 0, else the standard name
    size: int


@dataclass(frozen=True)
class Import:
    module: str
    field: str
    host_fn: str | None  # the pinned name, or None for an import the pin does not know
    min_protocol: int | None


@dataclass(frozen=True)
class Artifact:
    sha256: str
    size: int
    sections: tuple[Section, ...]
    imports: tuple[Import, ...]
    exports: tuple[str, ...]
    has_constructor: bool
    declared_protocol: int | None
    recomputed_protocol: int
    spec: dict[str, list[str]] | None
    meta: list[tuple[str, str]] | None

    @property
    def protocol_mismatch(self) -> bool:
        return (
            self.declared_protocol is not None
            and self.declared_protocol != self.recomputed_protocol
        )

    @property
    def unknown_imports(self) -> tuple[Import, ...]:
        return tuple(i for i in self.imports if i.host_fn is None)


def inspect_artifact(wasm: bytes) -> Artifact:
    try:
        raw = list(validate.iter_sections(wasm))
    except EmitError as exc:
        raise MalformedArtifact(str(exc)) from exc

    sections: list[Section] = []
    imports: list[Import] = []
    exports: list[str] = []
    declared: int | None = None
    spec: dict[str, list[str]] | None = None
    meta: list[tuple[str, str]] | None = None
    try:
        for sid, payload in raw:
            if sid == 0:
                name, start = validate.read_name(payload, 0)
                data = payload[start:]
                sections.append(Section(0, name, len(payload)))
                if name == ENV_META_SECTION_NAME:
                    declared = decode.decode_env_meta(data)
                elif name == SPEC_SECTION_NAME:
                    spec = decode.spec_entry_names_by_kind(decode.decode_spec_entries(data))
                elif name == META_SECTION_NAME:
                    meta = decode.decode_meta(data)
                continue
            sections.append(Section(sid, _SECTION_NAMES.get(sid), len(payload)))
            if sid == 2:
                # `_decode_imports` raises EmitError on a non-function import
                # (memory/table/global), which serpent never emits; a Rust
                # artifact with one is "not serpent's", not malformed [m15].
                try:
                    decoded = printer._decode_imports(payload)
                except EmitError as exc:
                    raise MalformedArtifact(
                        f"not a serpent artifact: {exc} "
                        "(serpent modules import host functions only)"
                    ) from exc
                for module, field, _typeidx in decoded:
                    fn = _BY_IMPORT.get((module, field))
                    imports.append(
                        Import(
                            module,
                            field,
                            fn.name if fn else None,
                            fn.min_protocol if fn else None,
                        )
                    )
            elif sid == 7:
                exports.extend(
                    name for name, kind, _idx in printer._decode_exports(payload) if kind == 0
                )
    except MalformedArtifact:
        # `MalformedArtifact` IS a `ValueError`, so the broad clause below would
        # otherwise re-wrap the import-kind message above and bury it.
        raise
    except (EmitError, ValueError, IndexError) as exc:
        raise MalformedArtifact(f"unreadable section: {exc}") from exc

    known = [i.host_fn for i in imports if i.host_fn is not None]
    floor = compute_protocol_floor(known)
    has_constructor = "__constructor" in exports
    if has_constructor:
        floor = max(floor, CONSTRUCTOR_MIN_PROTOCOL)
    return Artifact(
        sha256=hashlib.sha256(wasm).hexdigest(),
        size=len(wasm),
        sections=tuple(sections),
        imports=tuple(imports),
        exports=tuple(exports),
        has_constructor=has_constructor,
        declared_protocol=declared,
        recomputed_protocol=floor,
        spec=spec,
        meta=meta,
    )
