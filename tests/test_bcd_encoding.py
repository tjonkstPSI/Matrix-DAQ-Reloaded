# Author: T. Onkst | Date: 03092026

"""
BCD encode/decode behavior mirrors ``LoadBankPlugin._write_setpoint_bcd`` and
``LoadBankPlugin._decode_registers`` for ``bcd_double`` / ``bcd32`` dtypes.
"""

from __future__ import annotations

import struct


def _bcd_encode_to_regs(value: float) -> tuple[int, int]:
    """Match loadbank setpoint BCD packing (two big-endian uint16 registers)."""
    target = int(round(max(0, min(99_999_999, float(value)))))
    digits = f"{target:08d}"
    b = bytes(
        [
            (int(digits[0]) << 4) | int(digits[1]),
            (int(digits[2]) << 4) | int(digits[3]),
            (int(digits[4]) << 4) | int(digits[5]),
            (int(digits[6]) << 4) | int(digits[7]),
        ]
    )
    return ((b[0] << 8) | b[1]), ((b[2] << 8) | b[3])


def _bcd_decode_from_regs(w0: int, w1: int, word_order: str = "AB") -> float:
    """Match loadbank BCD decode path."""
    w0, w1 = int(w0) & 0xFFFF, int(w1) & 0xFFFF
    if word_order.upper() == "BA":
        w0, w1 = w1, w0
    digits = (
        f"{(w0 >> 12) & 0xF}{(w0 >> 8) & 0xF}{(w0 >> 4) & 0xF}{w0 & 0xF}"
        f"{(w1 >> 12) & 0xF}{(w1 >> 8) & 0xF}{(w1 >> 4) & 0xF}{w1 & 0xF}"
    )
    digits = "".join(ch if ch in "0123456789" else "0" for ch in digits)
    return float(int(digits))


def test_bcd_roundtrip_zero():
    w0, w1 = _bcd_encode_to_regs(0.0)
    assert _bcd_decode_from_regs(w0, w1) == 0.0


def test_bcd_roundtrip_max_digits():
    v = 12_345_678.0
    w0, w1 = _bcd_encode_to_regs(v)
    assert _bcd_decode_from_regs(w0, w1) == v


def test_bcd_roundtrip_word_order_ba():
    v = 999.0
    w0, w1 = _bcd_encode_to_regs(v)
    assert _bcd_decode_from_regs(w1, w0, word_order="BA") == v


def test_bcd_known_registers_decode():
    # 12345678 -> nibbles packed into two 16-bit words (big-endian bytes per word)
    w0, w1 = _bcd_encode_to_regs(12345678.0)
    assert w0 == struct.unpack(">H", bytes([0x12, 0x34]))[0]
    assert w1 == struct.unpack(">H", bytes([0x56, 0x78]))[0]
    assert _bcd_decode_from_regs(w0, w1) == 12345678.0


def test_bcd_clamp_to_8_digits_in_encode():
    w0, w1 = _bcd_encode_to_regs(100_000_000.0)
    assert _bcd_decode_from_regs(w0, w1) == 99_999_999.0
