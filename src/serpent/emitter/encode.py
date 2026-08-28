"""LEB128 and WASM section framing (dossier P18, on-chain-verified).

Ported by copy from ``spikes/spike1/emitter.py:101-139`` (R5: spikes/ is
read-only evidence, never imported from). These six functions produced the
bytes of the deployed, on-chain-verified 877-byte artifact at
``spikes/spike1/spike.wasm``; semantics are preserved exactly, including
``sleb``'s termination rule.
"""

from collections.abc import Sequence


def uleb(n: int) -> bytes:
    """Unsigned LEB128.

    Callers must pass n >= 0 -- the WASM binary format has no signed use for
    uleb (section ids, counts, indices, string lengths).
    """
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def sleb(n: int) -> bytes:
    """Signed LEB128.

    Termination rule (P18, verbatim): a group is the last one iff
    ``(n == 0 and not b & 0x40) or (n == -1 and b & 0x40)`` -- i.e. the
    remaining value is fully absorbed by the sign-extension of the group just
    emitted, not merely zero/all-ones.
    """
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        done = (n == 0 and not b & 0x40) or (n == -1 and b & 0x40)
        out.append(b if done else b | 0x80)
        if done:
            return bytes(out)


def vec(items: Sequence[bytes]) -> bytes:
    """A WASM ``vec``: element count (uleb) followed by the elements' bytes."""
    return uleb(len(items)) + b"".join(items)


def section(sid: int, payload: bytes) -> bytes:
    """One module section: id byte, uleb payload length, then the payload."""
    return bytes([sid]) + uleb(len(payload)) + payload


def wasm_name(s: str) -> bytes:
    """A WASM ``name``: uleb byte length of the UTF-8 encoding, then the bytes."""
    return uleb(len(s.encode())) + s.encode()


def custom_section(name: str, payload: bytes) -> bytes:
    """A custom section (id 0): its name framed as a wasm_name, then payload."""
    return section(0, wasm_name(name) + payload)
