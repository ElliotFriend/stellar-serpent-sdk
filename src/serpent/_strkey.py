"""Zero-dependency strkey codec (internal): version byte + 32-byte payload +
CRC16-XModem checksum, RFC 4648 base32 uppercase, no padding.

Stdlib only (`base64`, `binascii`).
"""

import base64
import binascii

VERSION_ACCOUNT = 48  # 'G'
VERSION_CONTRACT = 16  # 'C'

_PAYLOAD_LEN = 32
_DATA_LEN = 1 + _PAYLOAD_LEN  # version byte + payload
_TOTAL_LEN = _DATA_LEN + 2  # + little-endian CRC16
_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_CRC16_XMODEM_POLY = 0x1021


def _crc16_xmodem(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ _CRC16_XMODEM_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode(version_byte: int, payload: bytes) -> str:
    if not 0 <= version_byte <= 255:
        raise ValueError(f"version byte out of range 0..255: {version_byte}")
    if len(payload) != _PAYLOAD_LEN:
        raise ValueError(f"strkey payload must be {_PAYLOAD_LEN} bytes, got {len(payload)}")
    data = bytes([version_byte]) + payload
    crc = _crc16_xmodem(data)
    full = data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    return base64.b32encode(full).decode("ascii").rstrip("=")


def decode(expected_version: int, s: str) -> bytes:
    if not s or any(ch not in _B32_ALPHABET for ch in s):
        raise ValueError(f"invalid strkey (not RFC 4648 base32, uppercase, unpadded): {s!r}")
    if len(s) % 8 != 0:
        raise ValueError(f"invalid strkey length (not a multiple of 8 base32 chars): {s!r}")
    try:
        raw = base64.b32decode(s, casefold=False)
    except binascii.Error as exc:
        raise ValueError(f"invalid strkey base32: {s!r}") from exc
    if len(raw) != _TOTAL_LEN:
        raise ValueError(f"strkey decodes to {len(raw)} bytes, expected {_TOTAL_LEN}: {s!r}")
    data, crc_bytes = raw[:_DATA_LEN], raw[_DATA_LEN:]
    version_byte, payload = data[0], data[1:]
    if version_byte != expected_version:
        raise ValueError(f"expected strkey version {expected_version}, found {version_byte}: {s!r}")
    expected_crc = _crc16_xmodem(data)
    actual_crc = crc_bytes[0] | (crc_bytes[1] << 8)
    if actual_crc != expected_crc:
        raise ValueError(f"bad strkey checksum: {s!r}")
    return payload
