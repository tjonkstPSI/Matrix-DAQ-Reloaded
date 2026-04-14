# Author: T. Onkst | Date: 03092026

from __future__ import annotations

import pytest

from src.plugins._ccp_protocol import (
    CcpProto,
    CanFrame,
    compute_key_from_seed_algo,
    _rotl32,
    _rotr32,
)


def test_compute_key_from_seed_algo_cal_big_endian_known_vector():
    seed = bytes([0x01, 0x02, 0x03, 0x04])
    key = compute_key_from_seed_algo(seed, 0, "big", "CAL")
    assert key == bytes.fromhex("09000702")


def test_compute_key_from_seed_algo_daq_little_endian_known_vector():
    seed = bytes([0x78, 0x56, 0x34, 0x12])
    key = compute_key_from_seed_algo(seed, 0xA5A5A5A5, "little", "DAQ")
    assert key == bytes.fromhex("0c56f98a")


def test_compute_key_from_seed_algo_reverse_outputs_reversed_bytes_vs_big():
    seed = bytes([0x01, 0x02, 0x03, 0x04])
    k_big = compute_key_from_seed_algo(seed, 0, "big", "CAL")
    k_rev = compute_key_from_seed_algo(bytes(reversed(seed)), 0, "reverse", "CAL")
    assert k_big == bytes(reversed(k_rev))


@pytest.mark.parametrize(
    "bad_seed",
    [b"", b"\x00\x00\x00", b"\x00" * 5],
)
def test_compute_key_from_seed_algo_rejects_bad_length(bad_seed: bytes):
    with pytest.raises(ValueError, match="Seed must be 4 bytes"):
        compute_key_from_seed_algo(bad_seed, 0, "big", "CAL")


def test_compute_key_from_seed_algo_rejects_bad_endian():
    with pytest.raises(ValueError, match="seed_endian"):
        compute_key_from_seed_algo(b"\x00" * 4, 0, "middle", "CAL")


def test_compute_key_from_seed_algo_rejects_bad_sec_type():
    with pytest.raises(ValueError, match="sec_type"):
        compute_key_from_seed_algo(b"\x00" * 4, 0, "big", "OTHER")


def test_rotr32_basic():
    assert _rotr32(0x80000000, 1) == 0x40000000
    assert _rotr32(0xFFFFFFFF, 5) == 0xFFFFFFFF


def test_rotl32_basic():
    assert _rotl32(0x00000001, 1) == 0x00000002
    assert _rotl32(0xFFFFFFFF, 3) == 0xFFFFFFFF


def test_rotr_rotl_inverse():
    v = 0xA5F0C3D2
    for s in range(0, 32):
        assert _rotr32(_rotl32(v, s), s) == (v & 0xFFFFFFFF)
        assert _rotl32(_rotr32(v, s), s) == (v & 0xFFFFFFFF)


def test_ccp_proto_build_connect():
    proto = CcpProto(0x18EFF9FD, False)
    fr = proto.build_connect(0xABCD, ctr_override=0x07)
    assert isinstance(fr, CanFrame)
    assert fr.arbitration_id == 0x18EFF9FD
    assert fr.data == bytes([0x01, 0x07, 0xCD, 0xAB, 0x00, 0x00, 0x00])


def test_ccp_proto_build_get_seed():
    proto = CcpProto(0x100, True)
    fr = proto.build_get_seed(0x02, ctr_override=0x10)
    assert fr.is_extended is True
    assert fr.data == bytes([0x12, 0x10, 0x02, 0, 0, 0, 0, 0])


def test_ccp_proto_build_unlock():
    proto = CcpProto(0x200, False)
    key = bytes([1, 2, 3, 4, 5, 6])
    fr = proto.build_unlock(key, ctr_override=3, pad=0xAA)
    assert fr.data[:2] == bytes([0x13, 0x03])
    assert fr.data[2:8] == key


def test_ccp_proto_build_short_up():
    proto = CcpProto(0x300, False)
    fr = proto.build_short_up(size=4, address=0x08001234, extension=0, byteorder="big")
    assert fr.data[0] == 0x0F
    assert fr.data[2] == 4
    assert fr.data[3] == 0
    assert fr.data[4:8] == bytes([0x08, 0x00, 0x12, 0x34])
