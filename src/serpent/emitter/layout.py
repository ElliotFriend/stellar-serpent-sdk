"""The literal pool + scratch layout: one linear-memory bump allocator (P12).

Ported **by copy** from ``spikes/spike1/emitter.py:317-353`` (``_Memory``; R5:
``spikes/`` is read-only evidence, never imported from). The on-chain-verified
layout facts (dossier P12):

* The pool lives at address ``0x0000`` as the module's one active data
  segment. It is INTERNED: equal bytes are stored once, and every caller of
  ``intern`` for the same blob gets back the same offset.
* Scratch starts at ``SCRATCH_BASE = 0x1000`` and is a compile-time bump
  allocator -- each call site reserves ``nbytes`` rounded up to an 8-byte
  boundary (``(nbytes + 7) & ~7``) and that reservation is never freed or
  reused.
* The pool/scratch split is a FIXED constant (not sized to the pool after the
  fact), so a scratch address handed out while a function body is still
  compiling is already final -- nothing later shrinks or grows the pool out
  from under it.
* Two guards: the pool must not grow into scratch (``SCRATCH_BASE``), and
  scratch must fit in one 64 KiB wasm page (``PAGE = 65536``). Both raise
  ``BuildLimitError`` (frame.py) rather than bare ``EmitError`` -- a contract
  with too many literals or too much scratch is a USER-visible build limit,
  not a compiler bug (frame.py's exception taxonomy, review m10); Task 10
  re-reports ``limit="pool"``/``limit="scratch"`` as SPT8002/SPT8003.

Two blob families are deliberately NOT seeded and are interned lazily as
lowering reaches them -- a map literal's `Symbol` key-descriptor blob and a
static `Vec`/topic tuple's packed `Val` array (E12/C4) -- because the frontend's
`LiteralInventory` does not inventory either; determinism is preserved because
lowering order is source order, so those blobs are appended in a fixed order
after every seeded one.

``seed`` (added for D, not in the spike) pre-interns an entire
``LiteralInventory`` (SS C.2 output 2) in inventory order, so pool offsets are
a pure function of the inventory rather than of emission order (§C.1, E7):
running ``seed`` twice over two structurally-equal inventories, into two fresh
``Memory`` instances, produces byte-identical ``pool_bytes()``.
"""

import struct

from serpent.compiler.frontend import LiteralInventory
from serpent.emitter.frame import BuildLimitError

__all__ = ["Memory"]


class Memory:
    """The literal pool plus a compile-time bump allocator for scratch (P12)."""

    #: Where scratch begins; also the pool's hard ceiling (P12).
    SCRATCH_BASE = 0x1000
    #: One wasm page -- scratch must fit inside it entirely (P12).
    PAGE = 65536

    def __init__(self) -> None:
        self.pool = bytearray()
        self._interned: dict[bytes, int] = {}
        self._scratch = self.SCRATCH_BASE

    def intern(self, blob: bytes, align: int = 1) -> int:
        """Offset of ``blob`` in the data segment, storing it if new.

        Equal bytes are stored once: a repeat call with the same ``blob``
        returns the offset already assigned, padding or no. ``align`` pads the
        pool with zero bytes before a new blob only, never before a hit.
        """
        if blob not in self._interned:
            pad = (-len(self.pool)) % align
            self.pool += b"\x00" * pad
            self._interned[blob] = len(self.pool)
            self.pool += blob
        return self._interned[blob]

    def scratch(self, nbytes: int) -> int:
        """Reserve 8-byte-aligned scratch for one call site, forever.

        Never reused: two calls -- even requesting the same size -- get
        distinct, non-overlapping offsets. The bump is monotonic.
        """
        off = self._scratch
        self._scratch += (nbytes + 7) & ~7
        return off

    def pool_bytes(self) -> bytes:
        """The pool's current contents, as the bytes a data segment carries."""
        return bytes(self.pool)

    @property
    def is_empty(self) -> bool:
        """True iff nothing has been interned and no scratch has been reserved."""
        return not self.pool and self._scratch == self.SCRATCH_BASE

    def check(self) -> None:
        """Raise ``BuildLimitError`` if the pool or scratch has outgrown its budget."""
        if len(self.pool) > self.SCRATCH_BASE:
            raise BuildLimitError(
                limit="pool",
                message=(
                    f"the literal pool ({len(self.pool)}B) has grown past scratch's "
                    f"start at {self.SCRATCH_BASE:#x}"
                ),
            )
        if self._scratch > self.PAGE:
            raise BuildLimitError(
                limit="scratch",
                message=f"scratch ({self._scratch}B) does not fit in one {self.PAGE}B page",
            )

    def seed(self, literals: LiteralInventory) -> None:
        """Pre-intern every literal in ``literals``, IN INVENTORY ORDER (E7).

        Order: ``symbols_over_9`` (UTF-8), then ``strings`` (UTF-8), then
        ``bytes_literals``, then ``address_strkeys`` (UTF-8 -- a strkey is
        ASCII, and it is the STRING that ``string_new_from_linear_memory``
        reads before ``strkey_to_address`` converts it, review B6), then --
        for each entry of
        ``struct_key_descriptor_sets`` in turn -- its descriptor blob: for
        every field name in that set's own (already-P7-sorted, C9) order,
        ``struct.pack("<II", intern(name_bytes), len(name_bytes))``,
        concatenated and interned with ``align=8``. Interning the field-name
        bytes happens first, so a name shared across two descriptor sets (or
        with a plain string/bytes literal) is stored once regardless.

        Fixing this order makes every pool offset -- and therefore every
        scratch address computed once a body starts compiling -- a pure
        function of the inventory (§C.1, E7): re-running ``seed`` over an
        equal inventory, into a fresh ``Memory``, reproduces the same
        ``pool_bytes()`` byte for byte.
        """
        for name in literals.symbols_over_9:
            self.intern(name.encode("utf-8"))
        for s in literals.strings:
            self.intern(s.encode("utf-8"))
        for b in literals.bytes_literals:
            self.intern(b)
        for strkey in literals.address_strkeys:
            self.intern(strkey.encode("utf-8"))
        for key_set in literals.struct_key_descriptor_sets:
            descriptor = bytearray()
            for field_name in key_set:
                name_bytes = field_name.encode("utf-8")
                descriptor += struct.pack("<II", self.intern(name_bytes), len(name_bytes))
            self.intern(bytes(descriptor), align=8)
