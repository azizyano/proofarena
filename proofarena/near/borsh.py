"""Minimal Borsh encoder, sufficient for a NEAR ``Transaction``.

Layout (little-endian, as specified by the NEAR borsh schema):

    Transaction {
        signer_id: string,          // u32 len + utf8
        public_key: PublicKey,      // u8 enum tag (0 = ED25519) + 32 bytes
        nonce: u64,
        receiver_id: string,
        block_hash: [u8; 32],
        actions: Vec<Action>,       // u32 count + actions
    }

    Action::FunctionCall {          // enum tag 2
        method_name: string,
        args: Vec<u8>,
        gas: u64,
        deposit: u128,
    }

If this encoding is wrong the chain rejects the transaction *before* executing it
(no funds move), which makes it a safe failure mode. ``--dry-run`` prints the
signed bytes so a mismatch is debuggable.
"""

from __future__ import annotations

import struct


class BorshWriter:
    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def u8(self, value: int) -> "BorshWriter":
        self._parts.append(struct.pack("<B", value))
        return self

    def u32(self, value: int) -> "BorshWriter":
        self._parts.append(struct.pack("<I", value))
        return self

    def u64(self, value: int) -> "BorshWriter":
        self._parts.append(struct.pack("<Q", value))
        return self

    def u128(self, value: int) -> "BorshWriter":
        self._parts.append(struct.pack("<QQ", value & 0xFFFFFFFFFFFFFFFF, value >> 64))
        return self

    def bytes_fixed(self, value: bytes) -> "BorshWriter":
        self._parts.append(bytes(value))
        return self

    def string(self, value: str) -> "BorshWriter":
        encoded = value.encode("utf-8")
        self._parts.append(struct.pack("<I", len(encoded)))
        self._parts.append(encoded)
        return self

    def bytes_vec(self, value: bytes) -> "BorshWriter":
        self._parts.append(struct.pack("<I", len(value)))
        self._parts.append(bytes(value))
        return self

    def build(self) -> bytes:
        return b"".join(self._parts)


class BorshError(ValueError):
    pass


def _need(raw: bytes, offset: int, count: int) -> None:
    if offset + count > len(raw):
        raise BorshError(f"truncated borsh payload at {offset} (wanted {count} bytes)")


def read_u8(raw: bytes, offset: int) -> tuple[int, int]:
    _need(raw, offset, 1)
    return raw[offset], offset + 1


def read_u32(raw: bytes, offset: int) -> tuple[int, int]:
    _need(raw, offset, 4)
    return int.from_bytes(raw[offset : offset + 4], "little"), offset + 4


def read_u64(raw: bytes, offset: int) -> tuple[int, int]:
    _need(raw, offset, 8)
    return int.from_bytes(raw[offset : offset + 8], "little"), offset + 8


def read_u128(raw: bytes, offset: int) -> tuple[int, int]:
    _need(raw, offset, 16)
    return int.from_bytes(raw[offset : offset + 16], "little"), offset + 16


def read_fixed(raw: bytes, offset: int, count: int) -> tuple[bytes, int]:
    _need(raw, offset, count)
    return raw[offset : offset + count], offset + count


def read_vec(raw: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = read_u32(raw, offset)
    return read_fixed(raw, offset, length)


def read_string(raw: bytes, offset: int) -> tuple[str, int]:
    value, offset = read_vec(raw, offset)
    return value.decode("utf-8"), offset
