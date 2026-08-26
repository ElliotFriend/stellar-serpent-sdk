"""Spike 1: Soroban custom-section payloads, built with ``stellar_sdk.xdr``.

This is the spike that proves spec §7's switch away from hand-rolled XDR (see
``reference/mkmeta.py`` for the hand-rolled version this replaces): every byte
below comes out of the SDK's generated XDR classes, so the encoding is the
protocol's, not ours.

Three payloads, one per Soroban custom section:

* ``contractenvmetav0``  -> :func:`env_meta`
* ``contractspecv0``     -> :func:`spec_entries`
* ``contractmetav0``     -> :func:`meta`

Each is a *stream* of XDR entries (no outer length prefix): the reader decodes
entries until the payload is exhausted, so plain concatenation is the correct
framing.
"""

from __future__ import annotations

from frontend import ContractIR
from stellar_sdk import xdr

# Authoring-surface type name -> SCSpecType. Anything not in here is assumed to
# name a @contracttype struct and is emitted as a UDT reference.
_SCALARS = {
    "U32": xdr.SCSpecType.SC_SPEC_TYPE_U32,
    "I32": xdr.SCSpecType.SC_SPEC_TYPE_I32,
    "U64": xdr.SCSpecType.SC_SPEC_TYPE_U64,
    "I64": xdr.SCSpecType.SC_SPEC_TYPE_I64,
    "Bool": xdr.SCSpecType.SC_SPEC_TYPE_BOOL,
    "String": xdr.SCSpecType.SC_SPEC_TYPE_STRING,
    "Symbol": xdr.SCSpecType.SC_SPEC_TYPE_SYMBOL,
    "Bytes": xdr.SCSpecType.SC_SPEC_TYPE_BYTES,
    "Address": xdr.SCSpecType.SC_SPEC_TYPE_ADDRESS,
    "Val": xdr.SCSpecType.SC_SPEC_TYPE_VAL,
}


def env_meta(protocol: int) -> bytes:
    """The ``contractenvmetav0`` payload for a declared target protocol."""
    return xdr.SCEnvMetaEntry(
        kind=xdr.SCEnvMetaKind.SC_ENV_META_KIND_INTERFACE_VERSION,
        interface_version=xdr.SCEnvMetaEntryInterfaceVersion(
            protocol=xdr.Uint32(protocol), pre_release=xdr.Uint32(0)
        ),
    ).to_xdr_bytes()


def spec_type(name: str) -> xdr.SCSpecTypeDef:
    """Map an authoring-surface type name onto an ``SCSpecTypeDef``."""
    if name in _SCALARS:
        return xdr.SCSpecTypeDef(type=_SCALARS[name])
    if name == "None":
        return xdr.SCSpecTypeDef(type=xdr.SCSpecType.SC_SPEC_TYPE_VOID)
    return xdr.SCSpecTypeDef(
        type=xdr.SCSpecType.SC_SPEC_TYPE_UDT,
        udt=xdr.SCSpecTypeUDT(name=name.encode()),
    )


def spec_entries(ir: ContractIR) -> bytes:
    """The ``contractspecv0`` payload: one ``SCSpecEntry`` per user-visible item.

    Order is structs, then error enums, then functions -- the CLI's interface
    renderer does not care, but a stable order keeps the build deterministic.
    """
    entries: list[xdr.SCSpecEntry] = []

    for struct_name, fields in ir.structs.items():
        entries.append(
            xdr.SCSpecEntry(
                kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_STRUCT_V0,
                udt_struct_v0=xdr.SCSpecUDTStructV0(
                    doc=b"",
                    lib=b"",
                    name=struct_name.encode(),
                    fields=[
                        xdr.SCSpecUDTStructFieldV0(
                            doc=b"", name=fname.encode(), type=spec_type(ftype)
                        )
                        for fname, ftype in fields
                    ],
                ),
            )
        )

    for enum_name, members in ir.errors.items():
        entries.append(
            xdr.SCSpecEntry(
                kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_UDT_ERROR_ENUM_V0,
                udt_error_enum_v0=xdr.SCSpecUDTErrorEnumV0(
                    doc=b"",
                    lib=b"",
                    name=enum_name.encode(),
                    cases=[
                        xdr.SCSpecUDTErrorEnumCaseV0(
                            doc=b"", name=case.encode(), value=xdr.Uint32(code)
                        )
                        for case, code in members.items()
                    ],
                ),
            )
        )

    for fn in ir.functions:
        # `outputs` is an XDR array<1>: empty for a void return, one entry
        # otherwise. This matches what the Rust SDK emits for `-> ()`.
        outputs = [] if fn.ret == "None" else [spec_type(fn.ret)]
        entries.append(
            xdr.SCSpecEntry(
                kind=xdr.SCSpecEntryKind.SC_SPEC_ENTRY_FUNCTION_V0,
                function_v0=xdr.SCSpecFunctionV0(
                    doc=fn.doc.encode(),
                    name=xdr.SCSymbol(fn.name.encode()),
                    inputs=[
                        xdr.SCSpecFunctionInputV0(
                            doc=b"", name=pname.encode(), type=spec_type(ptype)
                        )
                        for pname, ptype in fn.params
                    ],
                    outputs=outputs,
                ),
            )
        )

    return b"".join(e.to_xdr_bytes() for e in entries)


def meta(pairs: dict[str, str]) -> bytes:
    """The ``contractmetav0`` payload: a stream of key/value ``SCMetaEntry``."""
    return b"".join(
        xdr.SCMetaEntry(
            kind=xdr.SCMetaKind.SC_META_V0,
            v0=xdr.SCMetaV0(key=k.encode(), val=v.encode()),
        ).to_xdr_bytes()
        for k, v in pairs.items()
    )
