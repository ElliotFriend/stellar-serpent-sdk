"""Generate Soroban contract metadata custom sections in pure Python and append
them to a wasm module. Proof that the metadata half of a Python toolchain is trivial."""
import struct, sys

def uleb(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def custom_section(name, payload):
    body = uleb(len(name)) + name.encode() + payload
    return b"\x00" + uleb(len(body)) + body

# --- XDR helpers (big-endian, 4-byte aligned) ---
def xdr_u32(n): return struct.pack(">I", n)
def xdr_u64(n): return struct.pack(">Q", n)
def xdr_str(s):
    b = s.encode()
    pad = (-len(b)) % 4
    return struct.pack(">I", len(b)) + b + b"\x00" * pad

# SCSpecType codes (subset)
SC_SPEC_TYPE_VAL, SC_SPEC_TYPE_BOOL, SC_SPEC_TYPE_VOID = 0, 1, 2
SC_SPEC_TYPE_U32, SC_SPEC_TYPE_I32 = 4, 5
SC_SPEC_TYPE_U64, SC_SPEC_TYPE_I64 = 6, 7

def spec_fn(name, inputs, output):
    """inputs: list of (name, typecode); output: typecode or None for void"""
    out = xdr_u32(0)          # SCSpecEntryKind = FUNCTION_V0
    out += xdr_str("")        # doc
    out += xdr_str(name)      # name (SCSymbol)
    out += xdr_u32(len(inputs))
    for iname, itype in inputs:
        out += xdr_str("")     # input doc
        out += xdr_str(iname)  # input name
        out += xdr_u32(itype)  # input type
    if output is None:
        out += xdr_u32(0)
    else:
        out += xdr_u32(1) + xdr_u32(output)
    return out

def env_meta(protocol, pre_release=0):
    # SCEnvMetaEntry: kind 0 = INTERFACE_VERSION, then u64 (protocol<<32|pre_release)
    return xdr_u32(0) + xdr_u64((protocol << 32) | pre_release)

def meta_kv(pairs):
    out = b""
    for k, v in pairs:
        out += xdr_u32(0) + xdr_str(k) + xdr_str(v)  # kind 0 = SC_META_V0
    return out

if __name__ == "__main__":
    src, dst, protocol = sys.argv[1], sys.argv[2], int(sys.argv[3])
    wasm = open(src, "rb").read()

    spec = spec_fn("get", [], SC_SPEC_TYPE_U32) + spec_fn("increment", [], SC_SPEC_TYPE_U32)
    wasm += custom_section("contractspecv0", spec)
    wasm += custom_section("contractenvmetav0", env_meta(protocol))
    wasm += custom_section("contractmetav0", meta_kv([("pysoroban", "0.0.1-experiment")]))

    open(dst, "wb").write(wasm)
    print(f"wrote {dst}: {len(wasm)} bytes (spec={len(spec)}B)")
