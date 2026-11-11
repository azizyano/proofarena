"""Bitcoin-alphabet base58 (the alphabet NEAR uses for keys, hashes and signatures)."""

from __future__ import annotations

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: value for value, char in enumerate(ALPHABET)}


class Base58Error(ValueError):
    pass


def b58encode(raw: bytes) -> str:
    if not raw:
        return ""
    number = int.from_bytes(raw, "big")
    encoded = []
    while number:
        number, remainder = divmod(number, 58)
        encoded.append(ALPHABET[remainder])
    padding = 0
    for byte in raw:
        if byte == 0:
            padding += 1
        else:
            break
    return "1" * padding + "".join(reversed(encoded))


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        try:
            number = number * 58 + _INDEX[char]
        except KeyError as exc:
            raise Base58Error(f"invalid base58 character {char!r}") from exc
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    padding = 0
    for char in text:
        if char == "1":
            padding += 1
        else:
            break
    return b"\x00" * padding + decoded
