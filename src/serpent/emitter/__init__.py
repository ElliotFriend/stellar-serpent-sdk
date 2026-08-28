"""The WASM emitter (M1-D): lowers a compiled ``ContractIR`` to a Soroban module.

Empty for now -- the public API lands once the later tasks in this sub-plan
(frame/layout/module assembly) exist. Today this package holds only the
foundation: ``encode`` (LEB128 + section framing) and ``opcodes``
(provenance-pinned instruction/valtype/blocktype constants).
"""
