#!/usr/bin/env python3
"""RDAC codec support for SP-555 STANDARD and observed lo-fi sample data."""

from __future__ import annotations

import dataclasses
import collections


@dataclasses.dataclass(frozen=True)
class RdacControl:
    family: str
    exponent: int
    control_byte: int
    unit_bytes: int


@dataclasses.dataclass(frozen=True)
class Sp5EncoderSelection:
    family: str
    exponent: int
    candidate: int
    candidates: tuple[tuple[str, int], ...]
    metrics: tuple[tuple[str, int], ...]


def _u16(value: int) -> int:
    return value & 0xFFFF


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def _s32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _sar32(value: int, count: int) -> int:
    return _s32(value) >> count


def rdac_mask_bias(exponent: int, unit_bytes: int) -> tuple[int, int]:
    """Port of the mask/bias setup at `0x00467d20`."""

    shift = exponent + 4
    mask = _sar32(_s32(-1 << shift), unit_bytes)
    bias = _sar32(1 << shift, unit_bytes + 1)
    return mask, bias


def _word_from_bytes(hi: int, lo: int) -> int:
    return ((hi & 0xFF) << 8) | (lo & 0xFF)


def _apply_quant(value: int, mask: int, bias: int) -> int:
    return _s16((_s32(value) & mask) | bias)


def _apply_quant32(value: int, mask: int, bias: int) -> int:
    return _s32((_s32(value) & _u32(mask)) | _u32(bias))


def _avg16(left: int, right: int) -> int:
    return _s16((left + right) >> 1)


def _avg32(left: int, right: int) -> int:
    return _sar32(_u32(left + right), 1)


def _add_words(left: int, right: int) -> int:
    return _s16((left + right) & 0xFFFF)


def _sat_upper_s16(value: int) -> int:
    return min(value, 0x7FFF)


def decode_family_b_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 4,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-B window.

    This is a direct port of `fcn.00467570`, used by the family-B path in
    `fcn.004685e0`. The caller is responsible for passing the correctly
    aligned 14-byte RDAC code window.
    """

    if unit_bytes == 4:
        # The unit-4 shift cascade derives every field from `c`; the `a` word
        # (window bytes 8..13) is never read, so the 6-byte window suffices.
        _require_unit4_window(window, "B")
        a = 0
    elif len(window) < 14:
        raise ValueError("family-B decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    if unit_bytes != 4:
        a = _word_from_bytes(window[8], window[9])
        a = ((a << 8) | window[12]) & 0xFFFFFFFF
        a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c << 4) & 0xFFFFFFFF

    if unit_bytes == 8:
        ebp = (a * 4) & 0xFFFFFFFF
        esi = (c << 8) & 0xFFFFFFFF
        ebx = (esi << 7) & 0xFFFFFFFF
        edi = ((a >> 5) | ((ebx << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
        arg1 = ebp
        ebp = (ebp << 8) & 0xFFFFFFFF
        arg2 = (ebp << 7) & 0xFFFFFFFF
        var10 = (arg2 << 8) & 0xFFFFFFFF
    elif unit_bytes == 7:
        esi = (c << 7) & 0xFFFFFFFF
        ebx = (esi << 6) & 0xFFFFFFFF
        edi = (ebx << 7) & 0xFFFFFFFF
        ecx = ((a >> 2) | ((edi << 6) & 0xFFFFFFFF)) & 0xFFFFFFFF
        ebp = (a << 5) & 0xFFFFFFFF
        arg2 = (ebp << 6) & 0xFFFFFFFF
        arg1 = ecx
        var10 = (arg2 << 7) & 0xFFFFFFFF
    elif unit_bytes == 6:
        esi = (c << 6) & 0xFFFFFFFF
        ebx = (esi << 5) & 0xFFFFFFFF
        edi = (ebx << 6) & 0xFFFFFFFF
        ebp = a
        arg2 = (a << 5) & 0xFFFFFFFF
        arg1 = (edi << 5) & 0xFFFFFFFF
        var10 = (arg2 << 6) & 0xFFFFFFFF
    elif unit_bytes == 5:
        esi = (c << 5) & 0xFFFFFFFF
        ebx = (esi << 4) & 0xFFFFFFFF
        edi = (ebx << 5) & 0xFFFFFFFF
        ebp = (edi << 4) & 0xFFFFFFFF
        arg1 = ebp
        ebp = (ebp << 5) & 0xFFFFFFFF
        arg2 = ((a >> 1) | ((ebp << 4) & 0xFFFFFFFF)) & 0xFFFFFFFF
        var10 = (a << 4) & 0xFFFFFFFF
    else:
        esi = (c << 4) & 0xFFFFFFFF
        ebx = (esi * 8) & 0xFFFFFFFF
        edi = (ebx << 4) & 0xFFFFFFFF
        ebp = (edi * 8) & 0xFFFFFFFF
        arg1 = ebp
        ebp = (ebp << 4) & 0xFFFFFFFF
        arg2 = (ebp * 8) & 0xFFFFFFFF
        var10 = (arg2 << 4) & 0xFFFFFFFF

    q14 = _apply_quant(_sar32(c, 28 - exponent), mask, bias)
    q6 = _apply_quant(_sar32(arg1, 28 - exponent), mask, bias)
    q2 = _apply_quant(_sar32(arg2, 28 - exponent), mask, bias)
    q10 = _apply_quant(_sar32(ebx, 28 - exponent), mask, bias)

    q0 = _apply_quant(_sar32(var10, 29 - exponent), mask, bias) + _s16((q2 + previous) >> 1)
    q4 = _apply_quant(_sar32(ebp, 29 - exponent), mask, bias) + _s16((q6 + q2) >> 1)
    q8 = _apply_quant(_sar32(edi, 29 - exponent), mask, bias) + _s16((q10 + q6) >> 1)
    q12 = _apply_quant(_sar32(esi, 29 - exponent), mask, bias) + _s16((q10 + q14) >> 1)

    out_by_offset = {
        0: _s16(q0),
        2: q2,
        4: _s16(q4),
        6: q6,
        8: _s16(q8),
        10: q10,
        12: _s16(q12),
        14: q14,
    }
    samples = [out_by_offset[i] for i in range(0, 16, 2)]
    return samples, samples[-1]


def _read_c_nibble_word(window: bytes) -> int:
    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    return ((c << 8) | window[5]) & 0xFFFFFFFF


def _require_unit4_window(window: bytes, family: str) -> None:
    if len(window) < 6:
        raise ValueError(f"family-{family} unit_bytes=4 decode needs a 6-byte window")


def _decode_family_c_window_unit4(
    window: bytes,
    previous: int,
    exponent: int,
) -> tuple[list[int], int]:
    _require_unit4_window(window, "C")
    mask, bias = rdac_mask_bias(exponent, 4)
    c = (_read_c_nibble_word(window) * 4) & 0xFFFFFFFF

    var18_raw = c
    esi_raw = (c << 5) & 0xFFFFFFFF
    ebx_raw = (esi_raw * 8) & 0xFFFFFFFF
    edi_raw = (ebx_raw << 4) & 0xFFFFFFFF
    arg1_raw = (edi_raw * 8) & 0xFFFFFFFF
    var10_raw = (arg1_raw << 5) & 0xFFFFFFFF
    ebp_raw = (var10_raw * 8) & 0xFFFFFFFF
    arg2_raw = (ebp_raw << 4) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var18_raw, 27 - exponent), mask, bias)
    q6 = _apply_quant32(_sar32(arg1_raw, 27 - exponent), mask, bias)
    q2_base = _apply_quant32(_sar32(ebp_raw, 28 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(ebx_raw, 28 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant32(_sar32(arg2_raw, 29 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(q2, previous))
    q4_base = _apply_quant32(_sar32(var10_raw, 29 - exponent), mask, bias)
    q4 = _add_words(q4_base, _avg32(q2, q6))
    q8_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q8 = _add_words(q8_base, _avg32(q6, q10))
    q12_base = _apply_quant32(_sar32(esi_raw, 29 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), _s16(q4), _s16(q6), _s16(q8), _s16(q10), _s16(q12), _s16(q14)], q14


def _decode_family_d_window_unit4(
    window: bytes,
    previous: int,
    exponent: int,
) -> tuple[list[int], int]:
    _require_unit4_window(window, "D")
    mask, bias = rdac_mask_bias(exponent, 4)
    c = (_read_c_nibble_word(window) * 8) & 0xFFFFFFFF

    var14_raw = c
    var10 = (c << 7) & 0xFFFFFFFF
    esi = (var10 * 4) & 0xFFFFFFFF
    ebp = (esi << 4) & 0xFFFFFFFF
    ebx = (ebp * 4) & 0xFFFFFFFF
    arg1_raw = ebx
    ebx = (ebx << 6) & 0xFFFFFFFF
    edi = (ebx * 4) & 0xFFFFFFFF
    arg2_raw = (edi << 4) & 0xFFFFFFFF

    if exponent == 10:
        q14 = _apply_quant(_sar32((c + c) & 0xFFFFFFFF, 16), mask, bias)
        q6 = _apply_quant(_sar32(arg1_raw, 16), mask, bias)
    else:
        q14 = _apply_quant(_sar32(var14_raw, 25 - exponent), mask, bias)
        q6_base = _apply_quant(_sar32(arg1_raw, 26 - exponent), mask, bias)
        q6 = _s16(q6_base + _avg16(q14, previous))

    q2_base = _apply_quant(_sar32(edi, 28 - exponent), mask, bias)
    q2 = _s16(q2_base + _avg16(q6, previous))
    q10_base = _apply_quant(_sar32(esi, 28 - exponent), mask, bias)
    q10 = _s16(q10_base + _avg16(q6, q14))

    q0_base = _apply_quant(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q0 = _s16(q0_base + _avg16(previous, q2))
    q4_base = _apply_quant(_sar32(ebx, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg16(q2, q6)))
    q8_base = _apply_quant(_sar32(ebp, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg16(q10, q6)))
    q12_base = _apply_quant(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _s16(q12_base + _avg16(q10, q14))

    return [q0, q2, q4, q6, q8, q10, q12, q14], q14


def _decode_family_e_window_unit4(
    window: bytes,
    previous: int,
    exponent: int,
) -> tuple[list[int], int]:
    _require_unit4_window(window, "E")
    mask, bias = rdac_mask_bias(exponent, 4)
    c = (_read_c_nibble_word(window) * 4) & 0xFFFFFFFF

    var18_raw = c
    var10 = (c << 9) & 0xFFFFFFFF
    esi = (var10 * 4) & 0xFFFFFFFF
    arg2_raw = (esi << 4) & 0xFFFFFFFF
    ebp = (arg2_raw * 4) & 0xFFFFFFFF
    ebx = (ebp << 5) & 0xFFFFFFFF
    edi = (ebx * 4) & 0xFFFFFFFF
    arg1_raw = (edi << 4) & 0xFFFFFFFF

    if exponent == 8:
        q14 = _apply_quant(_sar32((c + c) & 0xFFFFFFFF, 16), mask, bias)
    else:
        q14 = _apply_quant(_sar32(var18_raw, 23 - exponent), mask, bias)

    q6_base = _apply_quant(_sar32(ebp, 27 - exponent), mask, bias)
    q6 = _s16(q6_base + _avg16(q14, previous))
    q2_base = _apply_quant(_sar32(edi, 28 - exponent), mask, bias)
    q2 = _s16(q2_base + _avg16(q6, previous))
    q10_base = _apply_quant(_sar32(esi, 28 - exponent), mask, bias)
    q10 = _s16(q10_base + _avg16(q6, q14))

    q0_base = _apply_quant(_sar32(arg1_raw, 30 - exponent), mask, bias)
    q0 = _s16(q0_base + _avg16(previous, q2))
    q4_base = _apply_quant(_sar32(ebx, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg16(q2, q6)))
    q8_base = _apply_quant(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg16(q6, q10)))
    q12_base = _apply_quant(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _s16(q12_base + _avg16(q10, q14))

    return [q0, q2, q4, q6, q8, q10, q12, q14], q14


def _decode_family_f_window_unit4(
    window: bytes,
    previous: int,
    exponent: int,
) -> tuple[list[int], int]:
    _require_unit4_window(window, "F")
    mask, bias = rdac_mask_bias(exponent, 4)
    c = (_read_c_nibble_word(window) * 8) & 0xFFFFFFFF

    var14_raw = c
    var10 = (c << 10) & 0xFFFFFFFF
    esi_raw = (var10 * 4) & 0xFFFFFFFF
    arg2_raw = (esi_raw * 8) & 0xFFFFFFFF
    ebx_raw = (arg2_raw * 4) & 0xFFFFFFFF
    arg1_raw = ebx_raw
    ebx_raw = (ebx_raw << 5) & 0xFFFFFFFF
    edi_raw = (ebx_raw * 4) & 0xFFFFFFFF
    ebp_raw = (edi_raw * 8) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var14_raw, 22 - exponent), mask, bias)
    q6_base = _apply_quant32(_sar32(arg1_raw, 27 - exponent), mask, bias)
    q6 = _s32(q6_base + _avg32(q14, previous))

    q2_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(esi_raw, 29 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant32(_sar32(ebp_raw, 30 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(previous, q2))
    q4_base = _apply_quant32(_sar32(ebx_raw, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg32(q2, q6)))
    q8_base = _apply_quant32(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg32(q6, q10)))
    q12_base = _apply_quant32(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), q4, _s16(q6), q8, _s16(q10), _s16(q12), _s16(q14)], q14


def _decode_family_g_window_unit4(
    window: bytes,
    previous: int,
    exponent: int,
) -> tuple[list[int], int]:
    _require_unit4_window(window, "G")
    mask, bias = rdac_mask_bias(exponent, 4)
    c = (_read_c_nibble_word(window) << 4) & 0xFFFFFFFF

    var18_raw = c
    esi_raw = (c << 7) & 0xFFFFFFFF
    edi_raw = (esi_raw * 8) & 0xFFFFFFFF
    ebx_raw = (edi_raw * 8) & 0xFFFFFFFF
    arg1_raw = (ebx_raw * 8) & 0xFFFFFFFF
    var10_raw = (arg1_raw * 8) & 0xFFFFFFFF
    ebp_raw = (var10_raw * 8) & 0xFFFFFFFF
    arg2_raw = (ebp_raw * 8) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var18_raw, 25 - exponent), mask, bias)
    q6_base = _apply_quant32(_sar32(arg1_raw, 29 - exponent), mask, bias)
    q6 = _s32(q6_base + _avg32(q14, previous))
    q2_base = _apply_quant32(_sar32(ebp_raw, 29 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant(_sar32(arg2_raw, 29 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(previous, q2))
    q4_base = _apply_quant(_sar32(var10_raw, 29 - exponent), mask, bias)
    q4 = _add_words(q4_base, _avg32(q2, q6))
    q8_base = _apply_quant(_sar32(ebx_raw, 29 - exponent), mask, bias)
    q8 = _add_words(q8_base, _avg32(q6, q10))
    q12_base = _apply_quant(_sar32(esi_raw, 29 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), q4, _s16(q6), q8, _s16(q10), q12, _s16(q14)], q14


def decode_family_a_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-A window for mono STANDARD fixtures."""

    del previous
    if unit_bytes != 8:
        raise NotImplementedError("family-A decoder supports unit_bytes=8 only")
    if len(window) < 14:
        raise ValueError("family-A decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    half_mask = _sar32(mask, 1)
    half_bias = _sar32(bias, 1)

    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF

    var18_raw = (c << 4) & 0xFFFFFFFF
    esi_raw = (var18_raw << 8) & 0xFFFFFFFF
    arg1_raw = (a * 4) & 0xFFFFFFFF
    var14_raw = (arg1_raw << 8) & 0xFFFFFFFF
    ebp_raw = (var14_raw << 7) & 0xFFFFFFFF
    ebx_raw = ((a >> 5) | (((esi_raw << 7) << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
    var10_raw = (ebp_raw << 8) & 0xFFFFFFFF

    shift = 29 - exponent
    q14 = _apply_quant32(_sar32(var18_raw, shift), half_mask, half_bias)
    q6 = _apply_quant32(_sar32(arg1_raw, shift), half_mask, half_bias)
    q2 = _apply_quant32(_sar32(ebp_raw, shift), half_mask, half_bias)
    q10 = _apply_quant32(_sar32((esi_raw << 7) & 0xFFFFFFFF, shift), half_mask, half_bias)
    q0 = _apply_quant(_sar32(var10_raw, shift), mask, bias)
    q4 = _apply_quant(_sar32(var14_raw, shift), mask, bias)
    q8 = _apply_quant(_sar32(ebx_raw, shift), mask, bias)
    q12 = _apply_quant(_sar32(esi_raw, shift), mask, bias)

    return [_s16(q0), _s16(q2), _s16(q4), _s16(q6), _s16(q8), _s16(q10), _s16(q12), _s16(q14)], q14


def decode_family_c_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-C window for observed unit-4 and unit-8 streams."""

    if unit_bytes == 4:
        return _decode_family_c_window_unit4(window, previous, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("family-C decoder supports unit_bytes=4 or 8 only")
    if len(window) < 14:
        raise ValueError("family-C decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c * 4) & 0xFFFFFFFF

    var18_raw = c
    arg1_raw = (a * 2) & 0xFFFFFFFF
    esi_raw = (c << 9) & 0xFFFFFFFF
    ebx_raw = (esi_raw << 7) & 0xFFFFFFFF
    var10_raw = (arg1_raw << 9) & 0xFFFFFFFF
    ebp_raw = (var10_raw << 7) & 0xFFFFFFFF
    edi_raw = ((a >> 6) | ((ebx_raw << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
    arg2_raw = (ebp_raw << 8) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var18_raw, 27 - exponent), mask, bias)
    q6 = _apply_quant32(_sar32(arg1_raw, 27 - exponent), mask, bias)
    q2_base = _apply_quant32(_sar32(ebp_raw, 28 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(ebx_raw, 28 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant32(_sar32(arg2_raw, 29 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(q2, previous))
    q4_base = _apply_quant32(_sar32(var10_raw, 29 - exponent), mask, bias)
    q4 = _add_words(q4_base, _avg32(q2, q6))
    q8_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q8 = _add_words(q8_base, _avg32(q6, q10))
    q12_base = _apply_quant32(_sar32(esi_raw, 29 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), _s16(q4), _s16(q6), _s16(q8), _s16(q10), _s16(q12), _s16(q14)], q14


def decode_family_d_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-D window for observed unit-4 and unit-8 streams."""

    if unit_bytes == 4:
        return _decode_family_d_window_unit4(window, previous, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("family-D decoder supports unit_bytes=4 or 8 only")
    if len(window) < 14:
        raise ValueError("family-D decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c * 8) & 0xFFFFFFFF

    var14_raw = c
    var10 = (c << 11) & 0xFFFFFFFF
    esi = (var10 << 6) & 0xFFFFFFFF
    ebx = (a * 4) & 0xFFFFFFFF
    ebp = ((a >> 4) | ((esi << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
    arg1_raw = ebx
    ebx = (ebx << 10) & 0xFFFFFFFF
    edi = (ebx << 6) & 0xFFFFFFFF
    arg2_raw = (edi << 8) & 0xFFFFFFFF

    if exponent == 10:
        q14 = _apply_quant(_sar32((c + c) & 0xFFFFFFFF, 16), mask, bias)
        q6_base = _apply_quant(_sar32(arg1_raw, 16), mask, bias)
        q6 = q6_base
    else:
        q14 = _apply_quant(_sar32(var14_raw, 25 - exponent), mask, bias)
        q6_base = _apply_quant(_sar32(arg1_raw, 26 - exponent), mask, bias)
        q6 = _s16(q6_base + _avg16(q14, previous))

    q2_base = _apply_quant(_sar32(edi, 28 - exponent), mask, bias)
    q2 = _s16(q2_base + _avg16(q6, previous))
    q10_base = _apply_quant(_sar32(esi, 28 - exponent), mask, bias)
    q10 = _s16(q10_base + _avg16(q6, q14))

    q0_base = _apply_quant(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q0 = _s16(q0_base + _avg16(previous, q2))
    q4_base = _apply_quant(_sar32(ebx, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg16(q2, q6)))
    q8_base = _apply_quant(_sar32(ebp, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg16(q10, q6)))
    q12_base = _apply_quant(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _s16(q12_base + _avg16(q10, q14))

    return [q0, q2, q4, q6, q8, q10, q12, q14], q14


def decode_family_e_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-E window for observed unit-4 and unit-8 streams."""

    if unit_bytes == 4:
        return _decode_family_e_window_unit4(window, previous, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("family-E decoder supports unit_bytes=4 or 8 only")
    if len(window) < 14:
        raise ValueError("family-E decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c * 4) & 0xFFFFFFFF

    var18_raw = c
    var10 = (c << 13) & 0xFFFFFFFF
    esi = (var10 << 6) & 0xFFFFFFFF
    edi = ((a >> 3) | ((esi << 8) & 0xFFFFFFFF)) & 0xFFFFFFFF
    ebp = (a * 8) & 0xFFFFFFFF
    ebx = (ebp << 9) & 0xFFFFFFFF
    arg2_raw = edi
    edi = (ebx << 6) & 0xFFFFFFFF
    arg1_raw = (edi << 8) & 0xFFFFFFFF

    if exponent == 8:
        q14 = _apply_quant(_sar32((c + c) & 0xFFFFFFFF, 16), mask, bias)
    else:
        q14 = _apply_quant(_sar32(var18_raw, 23 - exponent), mask, bias)

    q6_base = _apply_quant(_sar32(ebp, 27 - exponent), mask, bias)
    q6 = _s16(q6_base + _avg16(q14, previous))
    q2_base = _apply_quant(_sar32(edi, 28 - exponent), mask, bias)
    q2 = _s16(q2_base + _avg16(q6, previous))
    q10_base = _apply_quant(_sar32(esi, 28 - exponent), mask, bias)
    q10 = _s16(q10_base + _avg16(q6, q14))

    q0_base = _apply_quant(_sar32(arg1_raw, 30 - exponent), mask, bias)
    q0 = _s16(q0_base + _avg16(previous, q2))
    q4_base = _apply_quant(_sar32(ebx, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg16(q2, q6)))
    q8_base = _apply_quant(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg16(q6, q10)))
    q12_base = _apply_quant(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _s16(q12_base + _avg16(q10, q14))

    return [q0, q2, q4, q6, q8, q10, q12, q14], q14


def decode_family_f_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-F window for observed unit-4 and unit-8 streams."""

    if unit_bytes == 4:
        return _decode_family_f_window_unit4(window, previous, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("family-F decoder supports unit_bytes=4 or 8 only")
    if len(window) < 14:
        raise ValueError("family-F decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c * 8) & 0xFFFFFFFF

    var14_raw = c
    var10 = (c << 14) & 0xFFFFFFFF
    esi_raw = (var10 << 6) & 0xFFFFFFFF
    arg2_raw = ((a >> 2) | ((esi_raw << 7) & 0xFFFFFFFF)) & 0xFFFFFFFF
    arg1_raw = (a << 4) & 0xFFFFFFFF
    ebx_raw = (arg1_raw << 9) & 0xFFFFFFFF
    edi_raw = (ebx_raw << 6) & 0xFFFFFFFF
    ebp_raw = (edi_raw << 7) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var14_raw, 22 - exponent), mask, bias)
    q6_base = _apply_quant32(_sar32(arg1_raw, 27 - exponent), mask, bias)
    q6 = _s32(q6_base + _avg32(q14, previous))

    q2_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(esi_raw, 29 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant32(_sar32(ebp_raw, 30 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(previous, q2))
    q4_base = _apply_quant32(_sar32(ebx_raw, 30 - exponent), mask, bias)
    q4 = _s16(_sat_upper_s16(q4_base + _avg32(q2, q6)))
    q8_base = _apply_quant32(_sar32(arg2_raw, 30 - exponent), mask, bias)
    q8 = _s16(_sat_upper_s16(q8_base + _avg32(q6, q10)))
    q12_base = _apply_quant32(_sar32(var10, 30 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), q4, _s16(q6), q8, _s16(q10), _s16(q12), _s16(q14)], q14


def decode_family_g_window(
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    """Decode one 8-sample family-G window for observed unit-4 and unit-8 streams."""

    if unit_bytes == 4:
        return _decode_family_g_window_unit4(window, previous, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("family-G decoder supports unit_bytes=4 or 8 only")
    if len(window) < 14:
        raise ValueError("family-G decode needs a 14-byte window")

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    a = _word_from_bytes(window[8], window[9])
    a = ((a << 8) | window[12]) & 0xFFFFFFFF
    a = ((a << 8) | window[13]) & 0xFFFFFFFF

    c = _word_from_bytes(window[0], window[1])
    c = ((c << 8) | window[4]) & 0xFFFFFFFF
    c = ((c << 8) | window[5]) & 0xFFFFFFFF
    c = (c << 4) & 0xFFFFFFFF

    var18_raw = c
    arg1_raw = (a << 4) & 0xFFFFFFFF
    esi_raw = (c << 11) & 0xFFFFFFFF
    edi_raw = (esi_raw << 7) & 0xFFFFFFFF
    var10_raw = (arg1_raw << 7) & 0xFFFFFFFF
    ebp_raw = (var10_raw << 7) & 0xFFFFFFFF
    ebx_raw = ((a >> 3) | ((edi_raw << 7) & 0xFFFFFFFF)) & 0xFFFFFFFF
    arg2_raw = (ebp_raw << 7) & 0xFFFFFFFF

    q14 = _apply_quant32(_sar32(var18_raw, 25 - exponent), mask, bias)
    q6_base = _apply_quant32(_sar32(arg1_raw, 29 - exponent), mask, bias)
    q6 = _s32(q6_base + _avg32(q14, previous))
    q2_base = _apply_quant32(_sar32(ebp_raw, 29 - exponent), mask, bias)
    q2 = _s32(q2_base + _avg32(q6, previous))
    q10_base = _apply_quant32(_sar32(edi_raw, 29 - exponent), mask, bias)
    q10 = _s32(q10_base + _avg32(q6, q14))

    q0_base = _apply_quant(_sar32(arg2_raw, 29 - exponent), mask, bias)
    q0 = _add_words(q0_base, _avg32(previous, q2))
    q4_base = _apply_quant(_sar32(var10_raw, 29 - exponent), mask, bias)
    q4 = _add_words(q4_base, _avg32(q2, q6))
    q8_base = _apply_quant(_sar32(ebx_raw, 29 - exponent), mask, bias)
    q8 = _add_words(q8_base, _avg32(q6, q10))
    q12_base = _apply_quant(_sar32(esi_raw, 29 - exponent), mask, bias)
    q12 = _add_words(q12_base, _avg32(q10, q14))

    return [_s16(q0), _s16(q2), q4, _s16(q6), q8, _s16(q10), q12, _s16(q14)], q14


def decode_family_window(
    family: str,
    window: bytes,
    previous: int,
    exponent: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int]:
    if family == "A":
        return decode_family_a_window(window, previous, exponent, unit_bytes)
    if family == "B":
        return decode_family_b_window(window, previous, exponent, unit_bytes)
    if family == "C":
        return decode_family_c_window(window, previous, exponent, unit_bytes)
    if family == "D":
        return decode_family_d_window(window, previous, exponent, unit_bytes)
    if family == "E":
        return decode_family_e_window(window, previous, exponent, unit_bytes)
    if family == "F":
        return decode_family_f_window(window, previous, exponent, unit_bytes)
    if family == "G":
        return decode_family_g_window(window, previous, exponent, unit_bytes)
    raise NotImplementedError(f"family {family} decode is not ported yet")


def decode_rdac_block(
    block: bytes,
    previous: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int, RdacControl]:
    """Decode one RDAC block into 16 PCM samples.

    `fcn.004685e0` copies `unit_bytes * 2` bytes to a local buffer, then calls
    the family helper twice: first with the staged buffer base, then with the
    same buffer plus two bytes. This function models that call structure for
    ported family helpers.
    """

    staged = bytes(_prepare_control_bytes(block, unit_bytes))
    control = parse_control(block, unit_bytes)

    first, first_previous = decode_family_window(
        control.family,
        staged[0:],
        previous=previous,
        exponent=control.exponent,
        unit_bytes=unit_bytes,
    )
    second, second_previous = decode_family_window(
        control.family,
        staged[2:],
        previous=first_previous,
        exponent=control.exponent,
        unit_bytes=unit_bytes,
    )
    return first + second, second_previous, control


def decode_family_b_block(
    block: bytes,
    previous: int,
    unit_bytes: int = 8,
) -> tuple[list[int], int, RdacControl]:
    decoded, next_previous, control = decode_rdac_block(block, previous, unit_bytes)
    if control.family != "B":
        raise ValueError(f"family-B block required, got {control.family}")
    return decoded, next_previous, control


def _prepare_control_bytes(block: bytes, unit_bytes: int) -> bytearray:
    """Mirror the initial byte staging in `fcn.004685e0`.

    The legacy decoder copies `unit_bytes * 2` bytes to a stack buffer. For
    4-byte units it swaps byte positions 2/4 and 3/5 before constructing the
    control byte. For odd unit sizes it duplicates the final byte, matching the
    stack fixup at `0x0046863d`.
    """

    if unit_bytes < 1:
        raise ValueError("unit_bytes must be positive")
    needed = unit_bytes * 2
    if len(block) < needed:
        raise ValueError(f"need at least {needed} bytes for a control block")

    staged = bytearray(block[:needed])
    if unit_bytes == 4:
        staged[2], staged[4] = staged[4], staged[2]
        staged[3], staged[5] = staged[5], staged[3]
    elif unit_bytes & 1:
        staged[unit_bytes * 2 - 1] = staged[unit_bytes * 2 - 2]
    return staged


def parse_control(block: bytes, unit_bytes: int = 4) -> RdacControl:
    """Parse an RDAC block family/exponent selector.

    This is transcribed from `SP5.exe` around `0x00468651..0x004689e0`.
    
    """

    staged = _prepare_control_bytes(block, unit_bytes)
    control = (staged[0] & 0xF0) | (staged[2] >> 4)
    dl = control & 0xCC

    if dl < 0x48:
        if dl < 0x48:
            exponent = ((((control >> 2) & 0x30) | (control & 0x0C)) >> 2) + 2
        else:
            exponent = 8
        return RdacControl("E", exponent, control, unit_bytes)

    cl = control & 0xEE

    if cl == 0xCE and unit_bytes == 4:
        # In 4-byte mode this selector falls through to the E-family path.
        exponent = 8 if dl >= 0x48 else ((((control >> 2) & 0x30) | (control & 0x0C)) >> 2) + 2
        return RdacControl("E", exponent, control, unit_bytes)

    if cl == 0xCC and unit_bytes != 4:
        exponent = 8 if dl >= 0x48 else ((((control >> 2) & 0x30) | (control & 0x0C)) >> 2) + 2
        return RdacControl("E", exponent, control, unit_bytes)

    if dl < 0xC0:
        exponent = (((control >> 2) & 0x30) | (control & 0x0C)) >> 2
        return RdacControl("C", exponent, control, unit_bytes)

    if (cl < 0xCE and unit_bytes == 4) or (cl < 0xCC and unit_bytes != 4):
        exponent = ((((control >> 1) & 0x70) | (control & 0x0E)) >> 1) - 0x30
        if unit_bytes != 4:
            exponent += 1
        return RdacControl("F", exponent, control, unit_bytes)

    if (cl < 0xEC or control == 0xFE) and unit_bytes == 4:
        return _parse_family_d_control(control, cl, unit_bytes)

    if (cl < 0xEA or control == 0xFA) and unit_bytes != 4:
        return _parse_family_d_control(control, cl, unit_bytes)

    if control > 0xFA and unit_bytes != 4:
        exponent = control - 0xFB if control < 0xFD else control - 0x100
        return RdacControl("G", exponent, control, unit_bytes)

    if control < 0xFE and cl != 0xEC and unit_bytes == 4:
        exponent = control - 0xEE
        return RdacControl("G", exponent, control, unit_bytes)

    if control == 0xFF and unit_bytes == 4:
        return RdacControl("B", 0x0C, control, unit_bytes)

    if control in (0xEF, 0xFF) or unit_bytes == 4:
        return RdacControl("A", 0x0D, control, unit_bytes)

    exponent = control - 0xE2
    return RdacControl("B", exponent, control, unit_bytes)


def _parse_family_d_control(control: int, cl: int, unit_bytes: int) -> RdacControl:
    if unit_bytes == 4:
        if cl >= 0xEE:
            exponent = 0x0A
        else:
            exponent = ((((control >> 1) & 0x70) | (control & 0x0E)) >> 1) - 0x34
    elif cl >= 0xEA:
        exponent = 0x0A
    else:
        exponent = ((((control >> 1) & 0x70) | (control & 0x0E)) >> 1) - 0x33
    return RdacControl("D", exponent, control, unit_bytes)


def scan_controls(data: bytes, unit_bytes: int = 8) -> collections.Counter[tuple[str, int]]:
    """Classify a stream as fixed-size RDAC blocks."""

    step = unit_bytes * 2
    counts: collections.Counter[tuple[str, int]] = collections.Counter()
    for offset in range(0, len(data) - step + 1, step):
        control = parse_control(data[offset : offset + step], unit_bytes)
        counts[(control.family, control.exponent)] += 1
    return counts


def _clip_i16(value: int) -> int:
    return max(-32768, min(32767, value))


def decode_ported_stream(data: bytes, unit_bytes: int = 8) -> tuple[list[int], collections.Counter[str]]:
    step = unit_bytes * 2
    previous = 0
    decoded: list[int] = []
    families: collections.Counter[str] = collections.Counter()
    for offset in range(0, len(data) - step + 1, step):
        block = data[offset : offset + step]
        control = parse_control(block, unit_bytes)
        families[control.family] += 1
        if control.family not in ("A", "B", "C", "D", "E", "F", "G"):
            break
        samples, previous, _ = decode_rdac_block(block, previous, unit_bytes)
        decoded.extend(samples)
    return decoded, families


def _sp5_encoder_magnitude(value: int) -> int:
    return value if value >= 0 else ~value


def _sp5_collect_encoder_metrics(
    window: list[int],
    previous: int,
    metrics: dict[str, int],
) -> int:
    """Port of encoder metric collector `0x00466c20`.

    The helper scans one 8-sample half-block and ORs one's-complement
    magnitudes into the same metric slots that the selector later reduces.
    """

    if len(window) != 8:
        raise ValueError("SP5 encoder metric collection requires exactly 8 samples")

    previous = _clip_i16(previous)
    sample = [_clip_i16(value) for value in window]
    s0, s1, s2, s3, s4, s5, s6, s7 = sample

    metrics["0c"] |= _sp5_encoder_magnitude(s7)
    metrics["10"] |= _sp5_encoder_magnitude(s3)
    metrics["20"] |= _sp5_encoder_magnitude(s3 - ((s7 + previous) >> 1))

    metrics["14"] |= _sp5_encoder_magnitude(s1)
    metrics["24"] |= _sp5_encoder_magnitude(s1 - ((previous + s3) >> 1))

    metrics["14"] |= _sp5_encoder_magnitude(s5)
    metrics["24"] |= _sp5_encoder_magnitude(s5 - ((s3 + s7) >> 1))

    metrics["28"] |= _sp5_encoder_magnitude(s0 - ((s1 + previous) >> 1))
    metrics["28"] |= _sp5_encoder_magnitude(s2 - ((s1 + s3) >> 1))
    metrics["28"] |= _sp5_encoder_magnitude(s4 - ((s3 + s5) >> 1))
    metrics["28"] |= _sp5_encoder_magnitude(s6 - ((s5 + s7) >> 1))
    return s7


def _sp5_candidate_is_better(candidate: int, current: int) -> bool:
    """Mirror the selector's bit-mask comparison from `0x00466e42`.

    The binary chooses the candidate with the lower highest-set bit. Equal
    exponent ties keep the earlier family in B, C, D, E, F order.
    """

    current_only_bits = (candidate | current) ^ candidate
    return _s32(candidate) < _s32(current_only_bits)


def _sp5_exponent_for_candidate(candidate: int) -> int:
    exponent = 13
    if candidate >= 0x4000:
        return exponent

    value = candidate
    while value < 0x4000:
        value += value
        exponent -= 1
    return exponent


def _sp5_select_family(
    samples: list[int],
    previous: int,
    unit_bytes: int = 8,
) -> Sp5EncoderSelection:
    """Port of the SP5.exe encoder family/exponent selector at `0x00466d10`."""

    if len(samples) != 16:
        raise ValueError("SP5 encoder selection requires exactly 16 samples")
    if unit_bytes < 4:
        raise ValueError("SP5 encoder selection requires unit_bytes >= 4")

    metrics = {
        "0c": 0,
        "10": 0,
        "14": 0,
        "20": 0,
        "24": 0,
        "28": 0,
    }

    half_previous = _sp5_collect_encoder_metrics(samples[:8], previous, metrics)
    _sp5_collect_encoder_metrics(samples[8:], half_previous, metrics)

    metric_0c = metrics["0c"]
    metric_10 = metrics["10"]
    metric_14 = metrics["14"]
    metric_20 = metrics["20"]
    metric_24 = metrics["24"]
    metric_28 = metrics["28"]
    unit_floor = 3 << (unit_bytes - 4)
    metric_28_double = metric_28 + metric_28

    family_b = (metric_0c | metric_10 | metric_14 | 0x7FE) >> 1
    family_b |= metric_28
    family_b |= unit_floor

    family_c = (metric_0c | metric_10 | 0x3FC) >> 1
    family_c |= metric_24
    family_c >>= 1
    family_c |= metric_28
    family_c |= unit_floor

    family_d = (metric_0c | 0x3F0) >> 1
    family_d |= metric_20
    family_d >>= 2
    family_d |= metric_24
    family_d >>= 1
    family_d |= metric_28_double
    family_d |= unit_floor

    family_e = (metric_0c | 0x3C0) >> 4
    family_e |= metric_20
    family_e >>= 1
    family_e |= metric_24
    family_e >>= 1
    family_e |= metric_28_double
    family_e |= unit_floor

    family_f = (metric_0c | 0x180) >> 5
    family_f |= metric_20
    family_f >>= 2
    family_f |= metric_28_double
    family_f |= metric_24
    family_f |= unit_floor

    if unit_bytes == 4:
        family_b |= 0x3FFF
    else:
        family_f |= 7

    if family_d > 0x7FF:
        family_d = (metric_24 | 0x1FFE) >> 1
        family_d |= metric_28_double

    candidates = (
        ("B", family_b),
        ("C", family_c),
        ("D", family_d),
        ("E", family_e),
        ("F", family_f),
    )
    selected_family, selected_candidate = candidates[0]
    for candidate_family, candidate in candidates[1:]:
        if _sp5_candidate_is_better(candidate, selected_candidate):
            selected_family = candidate_family
            selected_candidate = candidate

    exponent = _sp5_exponent_for_candidate(selected_candidate)
    if exponent == 13:
        selected_family = "A"

    return Sp5EncoderSelection(
        selected_family,
        exponent,
        selected_candidate,
        candidates,
        tuple(sorted(metrics.items())),
    )


def encode_family_a_window(samples: list[int]) -> bytes:
    """Encode eight PCM samples as one unit-8 family-A RDAC window."""

    if len(samples) != 8:
        raise ValueError("family-A encoding requires exactly 8 samples")

    packed_byte_positions = (13, 12, 9, 8, 5, 4, 1, 0)
    packed = 0
    bit_cursor = 0
    for index, sample in enumerate(samples):
        code, _decoded = _family_a_code_and_decoded(sample, index)
        bits = 8 if index & 1 else 7
        packed |= code << bit_cursor
        bit_cursor += bits

    window = bytearray(14)
    for byte_index, byte_position in enumerate(packed_byte_positions):
        window[byte_position] = (packed >> (byte_index * 8)) & 0xFF
    return bytes(window)


def encode_family_a_block(samples: list[int]) -> bytes:
    """Encode sixteen PCM samples as one unit-8 family-A RDAC block."""

    if len(samples) != 16:
        raise ValueError("family-A block encoding requires exactly 16 samples")

    block, _, _ = _encode_family_a_block_scored(samples)
    return block


def _score_block(decoded: list[int], target: list[int]) -> int:
    return sum((got - want) * (got - want) for got, want in zip(decoded, target, strict=True))


_FAMILY_A_CODE_SHIFTS = (0, 7, 15, 22, 30, 37, 45, 52)
_FAMILY_A_TABLES: tuple[list[int], list[int], list[int], list[int]] | None = None
_PREDICTIVE_WIDTHS = {
    "B": (7, 8, 7, 8, 7, 8, 7, 8),
    "C": (7, 8, 7, 9, 7, 8, 7, 7),
    "D": (6, 8, 6, 10, 6, 8, 6, 10),
    "E": (6, 8, 6, 9, 6, 8, 6, 11),
    "F": (6, 7, 6, 9, 6, 7, 6, 13),
}
_PREDICTIVE_WIDTHS_UNIT4 = {
    family: tuple(width - 4 for width in widths)
    for family, widths in _PREDICTIVE_WIDTHS.items()
}
_SP5_Q6_WIDTH_PARAMETER = {
    "D": 6,
    "E": 5,
    "F": 5,
}
_SP5_MID_WIDTH_PARAMETER = {
    "C": 4,
    "D": 4,
    "E": 4,
    "F": 3,
}
_SP5_EVEN_WIDTH_PARAMETER = {
    "B": 3,
    "C": 3,
    "D": 2,
    "E": 2,
    "F": 2,
}


def _family_a_code_and_decoded(sample: int, index: int) -> tuple[int, int]:
    mask, bias = rdac_mask_bias(13, 8)
    if index & 1:
        half_mask = _sar32(mask, 1)
        half_bias = _sar32(bias, 1)
        decoded = _s16((_clip_i16(sample) & half_mask) | half_bias)
        code = ((decoded & half_mask) >> 8) & 0xFF
    else:
        decoded = _s16((_clip_i16(sample) & mask) | bias)
        code = ((decoded & mask) >> 9) & 0x7F
    return code, decoded


def _family_a_tables() -> tuple[list[int], list[int], list[int], list[int]]:
    global _FAMILY_A_TABLES
    if _FAMILY_A_TABLES is not None:
        return _FAMILY_A_TABLES

    even_codes: list[int] = []
    even_decoded: list[int] = []
    odd_codes: list[int] = []
    odd_decoded: list[int] = []
    for raw in range(0x10000):
        sample = _s16(raw)
        even_code, even_sample = _family_a_code_and_decoded(sample, 0)
        odd_code, odd_sample = _family_a_code_and_decoded(sample, 1)
        even_codes.append(even_code)
        even_decoded.append(even_sample)
        odd_codes.append(odd_code)
        odd_decoded.append(odd_sample)

    _FAMILY_A_TABLES = even_codes, even_decoded, odd_codes, odd_decoded
    return _FAMILY_A_TABLES


def _encode_family_a_block_scored(samples: list[int]) -> tuple[bytes, int, int]:
    """Return encoded family-A bytes, squared error, and decoded final sample."""

    if len(samples) != 16:
        raise ValueError("family-A block scoring requires exactly 16 samples")

    even_codes, even_decoded, odd_codes, odd_decoded = _family_a_tables()
    packed_first = 0
    packed_second = 0
    score = 0
    final_decoded = 0

    for index in range(8):
        sample = _clip_i16(samples[index])
        raw = sample & 0xFFFF
        if index & 1:
            code = odd_codes[raw]
            decoded = odd_decoded[raw]
        else:
            code = even_codes[raw]
            decoded = even_decoded[raw]
        packed_first |= code << _FAMILY_A_CODE_SHIFTS[index]
        delta = decoded - sample
        score += delta * delta

    for index in range(8):
        sample = _clip_i16(samples[index + 8])
        raw = sample & 0xFFFF
        if index & 1:
            code = odd_codes[raw]
            decoded = odd_decoded[raw]
        else:
            code = even_codes[raw]
            decoded = even_decoded[raw]
        packed_second |= code << _FAMILY_A_CODE_SHIFTS[index]
        delta = decoded - sample
        score += delta * delta
        final_decoded = decoded

    block = bytearray(16)
    block[13] = packed_first & 0xFF
    block[12] = (packed_first >> 8) & 0xFF
    block[9] = (packed_first >> 16) & 0xFF
    block[8] = (packed_first >> 24) & 0xFF
    block[5] = (packed_first >> 32) & 0xFF
    block[4] = (packed_first >> 40) & 0xFF
    block[1] = (packed_first >> 48) & 0xFF
    block[0] = ((packed_first >> 56) & 0x0F) | 0xE0
    block[15] = packed_second & 0xFF
    block[14] = (packed_second >> 8) & 0xFF
    block[11] = (packed_second >> 16) & 0xFF
    block[10] = (packed_second >> 24) & 0xFF
    block[7] = (packed_second >> 32) & 0xFF
    block[6] = (packed_second >> 40) & 0xFF
    block[3] = (packed_second >> 48) & 0xFF
    block[2] = ((packed_second >> 56) & 0x0F) | 0xF0
    return bytes(block), score, final_decoded


def _predictive_widths_for_unit(unit_bytes: int) -> dict[str, tuple[int, ...]]:
    return _PREDICTIVE_WIDTHS if unit_bytes == 8 else _PREDICTIVE_WIDTHS_UNIT4


def _pack_predictive_codes(codes: list[int], widths: tuple[int, ...]) -> int:
    packed = 0
    bit_cursor = 0
    for code, width in zip(codes, widths, strict=True):
        packed |= (code & ((1 << width) - 1)) << bit_cursor
        bit_cursor += width
    if bit_cursor not in (60, 28):
        raise AssertionError("predictive RDAC code widths must pack to 60 (unit-8) or 28 (unit-4) bits")
    return packed


def _predictive_window_from_packed(packed: int) -> bytes:
    window = bytearray(14)
    positions = (13, 12, 9, 8, 5, 4, 1, 0)
    for byte_index, byte_position in enumerate(positions):
        window[byte_position] = (packed >> (byte_index * 8)) & 0xFF
    return bytes(window)


def _sp5_quantize_residual(
    target: int,
    left: int,
    right: int,
    width_parameter: int,
    exponent: int,
    unit_bytes: int = 8,
) -> int:
    """Port of encoder quantizer `0x00465b60`.

    The width parameter is the helper's fourth argument in SP5.exe. It is not
    the packed field width; each family packer supplies its own constants.
    """

    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    shift = width_parameter + exponent - 1
    average = (left + right) >> 1
    residual = target - average
    shifted = residual >> shift

    if ((shifted >> 1) ^ shifted) != 0:
        if residual > 0:
            return _s32((((1 << shift) - 1) & mask) | bias)
        return _s32(((-1 << shift) & mask) | bias)

    value = _s32((residual & mask) | bias)
    reconstructed = value + average
    if (((reconstructed >> 1) ^ reconstructed) & 0xFFFF8000) != 0:
        return _s32(((mask & 0xFFFF) ^ (reconstructed & 0xFFFFFFFF)) - average)

    if reconstructed == -0x8000 and ((left & right) & 1):
        value = _s32(value + bias * 2)
    return value


def _sp5_direct_quantized_sample(sample: int, exponent: int, unit_bytes: int = 8) -> int:
    mask, bias = rdac_mask_bias(exponent, unit_bytes)
    return _s32((_clip_i16(sample) & mask) | bias)


def _sp5_predictive_average(family: str, left: int, right: int) -> int:
    if family in ("C", "F"):
        return _avg32(left, right)
    return _avg16(left, right)


def _sp5_predictive_add(family: str, base: int, average: int, index: int) -> int:
    if family == "C":
        return _add_words(base, average)
    if family == "F":
        if index in (0, 6):
            return _add_words(base, average)
        if index in (2, 4):
            return _s16(_sat_upper_s16(base + average))
        return _s16(base + average)
    if family in ("D", "E") and index in (2, 4):
        return _s16(_sat_upper_s16(base + average))
    return _s16(base + average)


def _sp5_code_from_quantized(value: int, width: int, exponent: int, unit_bytes: int) -> int:
    mask, _bias = rdac_mask_bias(exponent, unit_bytes)
    shift = exponent - unit_bytes + 4
    if shift < 0:
        raise ValueError("SP5 predictive encoding does not support this exponent/unit size")
    return ((value & mask) >> shift) & ((1 << width) - 1)


def _sp5_q14_control_bits(family: str, q14: int, exponent: int, unit_bytes: int) -> int:
    if family == "B":
        bit_count = 0
    elif family == "C":
        bit_count = 2
    elif family == "D":
        bit_count = 0 if exponent == 10 else 1
    elif family == "E":
        bit_count = 1 if exponent == 8 else 2
    elif family == "F":
        bit_count = 1
    else:
        raise NotImplementedError(f"SP5 family-{family} q14 control bits are not ported")

    if bit_count == 0:
        return 0

    width = _predictive_widths_for_unit(unit_bytes)[family][7]
    mask, _bias = rdac_mask_bias(exponent, unit_bytes)
    shift = exponent - unit_bytes + 4
    full_code = (q14 & mask) >> shift
    return (full_code >> width) & ((1 << bit_count) - 1)


def _sp5_encode_predictive_window(
    family: str,
    exponent: int,
    samples: list[int],
    previous: int,
    unit_bytes: int = 8,
) -> tuple[int, list[int], int]:
    if len(samples) != 8:
        raise ValueError("SP5 predictive window encoding requires exactly 8 samples")
    if unit_bytes not in (4, 8):
        raise NotImplementedError("SP5 predictive encoder supports unit_bytes=4 or 8 only")
    if family not in _PREDICTIVE_WIDTHS or family == "G":
        raise NotImplementedError(f"SP5 family-{family} predictive encoder is not ported")

    target = [_clip_i16(sample) for sample in samples]
    previous = _clip_i16(previous)
    bases = [0] * 8
    decoded = [0] * 8

    bases[7] = _sp5_direct_quantized_sample(target[7], exponent, unit_bytes)
    decoded[7] = _s16(bases[7])

    if family in ("B", "C") or (family == "D" and exponent == 10):
        bases[3] = _sp5_direct_quantized_sample(target[3], exponent, unit_bytes)
        decoded[3] = _s16(bases[3])
    else:
        bases[3] = _sp5_quantize_residual(
            target[3],
            previous,
            decoded[7],
            _SP5_Q6_WIDTH_PARAMETER[family],
            exponent,
            unit_bytes,
        )
        decoded[3] = _s16(bases[3] + _sp5_predictive_average(family, previous, decoded[7]))

    if family == "B":
        bases[1] = _sp5_direct_quantized_sample(target[1], exponent, unit_bytes)
        decoded[1] = _s16(bases[1])
        bases[5] = _sp5_direct_quantized_sample(target[5], exponent, unit_bytes)
        decoded[5] = _s16(bases[5])
    else:
        width_parameter = _SP5_MID_WIDTH_PARAMETER[family]
        bases[1] = _sp5_quantize_residual(
            target[1],
            previous,
            decoded[3],
            width_parameter,
            exponent,
            unit_bytes,
        )
        decoded[1] = _s16(bases[1] + _sp5_predictive_average(family, previous, decoded[3]))

        bases[5] = _sp5_quantize_residual(
            target[5],
            decoded[3],
            decoded[7],
            width_parameter,
            exponent,
            unit_bytes,
        )
        decoded[5] = _s16(bases[5] + _sp5_predictive_average(family, decoded[3], decoded[7]))

    even_width_parameter = _SP5_EVEN_WIDTH_PARAMETER[family]
    for index, left_index, right_index in (
        (0, -1, 1),
        (2, 1, 3),
        (4, 3, 5),
        (6, 5, 7),
    ):
        left = previous if left_index < 0 else decoded[left_index]
        right = decoded[right_index]
        bases[index] = _sp5_quantize_residual(
            target[index],
            left,
            right,
            even_width_parameter,
            exponent,
            unit_bytes,
        )
        decoded[index] = _sp5_predictive_add(
            family,
            bases[index],
            _sp5_predictive_average(family, left, right),
            index,
        )

    widths = _predictive_widths_for_unit(unit_bytes)[family]
    codes = [
        _sp5_code_from_quantized(base, width, exponent, unit_bytes)
        for base, width in zip(bases, widths, strict=True)
    ]
    packed = _pack_predictive_codes(codes, widths)
    control_shift = 60 if unit_bytes == 8 else 28
    packed |= _sp5_q14_control_bits(family, bases[7], exponent, unit_bytes) << control_shift
    return packed, decoded, decoded[7]


def _sp5_control_or_bytes(family: str, exponent: int, unit_bytes: int = 8) -> tuple[int, int]:
    if unit_bytes == 4:
        return _sp5_control_or_bytes_unit4(family, exponent)
    if unit_bytes != 8:
        raise NotImplementedError("SP5 control byte synthesis currently supports unit_bytes=4 or 8 only")

    if family == "A":
        value = 0xEC
        return value & 0xF0, (value << 4) & 0xF0
    if family == "B":
        value = exponent + 0xE2
        return value & 0xF0, (value << 4) & 0xF0
    if family == "C":
        value = exponent
        return ((value & 0xFC) << 4) & 0xF0, (value << 6) & 0xF0
    if family == "D":
        if exponent == 10:
            value = exponent + 0xF0
            return value & 0xF0, (value << 4) & 0xF0
        value = exponent + 0x33
        return ((value & 0xF8) << 2) & 0xF0, (value << 5) & 0xF0
    if family == "E":
        if exponent == 8:
            value = exponent + 0x2E
            return ((value & 0xF8) << 2) & 0xF0, (value << 5) & 0xF0
        value = exponent - 2
        return ((value & 0xFC) << 4) & 0xF0, (value << 6) & 0xF0
    if family == "F":
        value = exponent + 0x2F
        return ((value & 0xF8) << 2) & 0xF0, (value << 5) & 0xF0
    raise NotImplementedError(f"SP5 family-{family} control byte synthesis is not ported")


def _sp5_control_or_bytes_unit4(family: str, exponent: int) -> tuple[int, int]:
    """Unit-4 control nibble synthesis, the inverse of `parse_control`'s
    unit_bytes=4 family regions. Family boundaries differ from unit-8: B only
    exists at exponent 12 (the unit-4 saturating family, control 0xFF), D uses
    a +0x34 selector base and 0xFE for exponent 10, E's exponent-8 region is
    0xCE-based, and F's selector base is +0x30 with no exponent offset."""

    if family == "B":
        if exponent != 12:
            raise ValueError("unit-4 family-B encoding requires exponent 12")
        return 0xF0, 0xF0
    if family == "C":
        value = exponent
        return ((value & 0xFC) << 4) & 0xF0, (value << 6) & 0xF0
    if family == "D":
        if exponent == 10:
            return 0xF0, 0xE0
        value = exponent + 0x34
        return ((value & 0xF8) << 2) & 0xF0, (value << 5) & 0xF0
    if family == "E":
        if exponent == 8:
            return 0xC0, 0xE0
        value = exponent - 2
        return ((value & 0xFC) << 4) & 0xF0, (value << 6) & 0xF0
    if family == "F":
        value = exponent + 0x30
        return ((value & 0xF8) << 2) & 0xF0, (value << 5) & 0xF0
    raise NotImplementedError(f"SP5 unit-4 family-{family} control byte synthesis is not ported")


def _sp5_block_from_predictive_windows(first: int, second: int, family: str, exponent: int) -> bytes:
    first_window = _predictive_window_from_packed(first)
    second_window = _predictive_window_from_packed(second)
    block = bytearray(16)
    for position in (0, 1, 4, 5, 8, 9, 12, 13):
        block[position] = first_window[position]
        block[position + 2] = second_window[position]

    byte0_or, byte2_or = _sp5_control_or_bytes(family, exponent, unit_bytes=8)
    block[0] |= byte0_or
    block[2] |= byte2_or
    return bytes(block)


def _sp5_block_from_predictive_windows_unit4(
    first: int,
    second: int,
    family: str,
    exponent: int,
) -> bytes:
    """Assemble one 8-byte unit-4 block.

    Each window is one big-endian 32-bit word: 28 packed code bits plus the
    window's control nibble at the top. The block-level staging swap in
    `_prepare_control_bytes` maps window 1 to block bytes 0..3 and window 2 to
    block bytes 4..7, with the control byte split across both top nibbles."""

    block = bytearray(8)
    block[0:4] = (first & 0xFFFFFFFF).to_bytes(4, "big")
    block[4:8] = (second & 0xFFFFFFFF).to_bytes(4, "big")
    byte0_or, byte2_or = _sp5_control_or_bytes(family, exponent, unit_bytes=4)
    block[0] |= byte0_or
    block[4] |= byte2_or
    return bytes(block)


def _sp5_encode_predictive_block(
    family: str,
    exponent: int,
    samples: list[int],
    previous: int,
    unit_bytes: int = 8,
) -> tuple[bytes, int]:
    if len(samples) != 16:
        raise ValueError("SP5 predictive block encoding requires exactly 16 samples")

    first, _first_decoded, first_previous = _sp5_encode_predictive_window(
        family,
        exponent,
        samples[:8],
        previous,
        unit_bytes=unit_bytes,
    )
    second, _second_decoded, second_previous = _sp5_encode_predictive_window(
        family,
        exponent,
        samples[8:],
        first_previous,
        unit_bytes=unit_bytes,
    )
    if unit_bytes == 4:
        block = _sp5_block_from_predictive_windows_unit4(first, second, family, exponent)
    else:
        block = _sp5_block_from_predictive_windows(first, second, family, exponent)
    return block, second_previous


def encode_standard_stream(samples: list[int]) -> bytes:
    """Encode 44.1 kHz PCM samples as STANDARD unit-8 RDAC bytes.

    The stream is padded to a 16-frame boundary, matching the observed SP-555
    STANDARD sample-file shape where one encoded byte decodes to one frame.
    """

    padded = [_clip_i16(sample) for sample in samples]
    remainder = len(padded) % 16
    if remainder:
        padded.extend([0] * (16 - remainder))

    encoded = bytearray()
    previous = 0
    for offset in range(0, len(padded), 16):
        target = padded[offset : offset + 16]
        selection = _sp5_select_family(target, previous, unit_bytes=8)
        if selection.family in ("B", "C", "D", "E", "F"):
            block, previous = _sp5_encode_predictive_block(
                selection.family,
                selection.exponent,
                target,
                previous,
            )
        else:
            block, _score, previous = _encode_family_a_block_scored(target)

        encoded.extend(block)
    return bytes(encoded)


def encode_unit4_stream(samples: list[int]) -> bytes:
    """Encode 44.1 kHz PCM samples as lo-fi unit-4 RDAC bytes.

    The stream is padded to a 16-frame boundary; each block packs 16 frames
    into 8 encoded bytes. The input is expected to be the full-rate stream the
    decoder reproduces (hardware lo-fi captures are quarter-rate content
    linearly interpolated back to 44.1 kHz before encoding)."""

    padded = [_clip_i16(sample) for sample in samples]
    remainder = len(padded) % 16
    if remainder:
        padded.extend([0] * (16 - remainder))

    encoded = bytearray()
    previous = 0
    for offset in range(0, len(padded), 16):
        target = padded[offset : offset + 16]
        selection = _sp5_select_family(target, previous, unit_bytes=4)
        family, exponent = selection.family, selection.exponent
        if family == "A":
            # Unit-4 streams have no family A; B/exp-12 is the saturating
            # direct-coded family (control 0xFF).
            family, exponent = "B", 12
        block, previous = _sp5_encode_predictive_block(
            family,
            exponent,
            target,
            previous,
            unit_bytes=4,
        )
        encoded.extend(block)
    return bytes(encoded)
