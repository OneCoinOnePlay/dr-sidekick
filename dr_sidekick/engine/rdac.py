"""RDAC decode support for SP-555 STANDARD and observed lo-fi sample data.

Decode-side port from the Roland SP RDAC Toolkit. The toolkit's encoder
chain and stream scanners were removed here as dead code — they live in the
upstream toolkit. The live entry points are `parse_control`,
`decode_rdac_block`, and `RdacControl`, plus the shared truth tables at the
bottom of the module that `sp303_codec` builds on.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class RdacControl:
    family: str
    exponent: int
    control_byte: int
    unit_bytes: int


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
    control: RdacControl | None = None,
) -> tuple[list[int], int, RdacControl]:
    """Decode one RDAC block into 16 PCM samples.

    `fcn.004685e0` copies `unit_bytes * 2` bytes to a local buffer, then calls
    the family helper twice: first with the staged buffer base, then with the
    same buffer plus two bytes. Only unit sizes 4 and 8 stage enough bytes for
    both family windows, so other unit sizes are rejected up front. A caller
    that already parsed the control byte can pass it to skip the re-parse.
    """

    if unit_bytes not in (4, 8):
        raise ValueError("decode_rdac_block supports unit_bytes=4 or 8 only")
    staged = bytes(_prepare_control_bytes(block, unit_bytes))
    if control is None:
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
        exponent = ((((control >> 2) & 0x30) | (control & 0x0C)) >> 2) + 2
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


# ---------------------------------------------------------------------------
# Shared RDAC truth tables and reconstruction rules
#
# The decode cascades above inline their own shift arithmetic; this section is
# the single home for the per-family code bit-widths and predictive
# reconstruction rules that `sp303_codec` builds on. Unit-4 widths derive from
# the unit-8 base table as `width - 4`; the SP-303 unit-6 codec derives its
# table as `width - 2`. The SP5 encoder these were extracted from lives in the
# upstream Roland SP RDAC Toolkit and was removed here as dead code.

PREDICTIVE_WIDTHS = {
    "B": (7, 8, 7, 8, 7, 8, 7, 8),
    "C": (7, 8, 7, 9, 7, 8, 7, 7),
    "D": (6, 8, 6, 10, 6, 8, 6, 10),
    "E": (6, 8, 6, 9, 6, 8, 6, 11),
    "F": (6, 7, 6, 9, 6, 7, 6, 13),
}
PREDICTIVE_WIDTHS_UNIT4 = {
    family: tuple(width - 4 for width in widths)
    for family, widths in PREDICTIVE_WIDTHS.items()
}


def predictive_add(family: str, base: int, average: int, index: int) -> int:
    """Per-family reconstruction add for the even-position predictive samples."""
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


def q14_extra_bit_count(family: str, exponent: int) -> int:
    """Number of control-nibble bits that extend the q14 code for a family."""
    if family == "B":
        return 0
    if family == "C":
        return 2
    if family == "D":
        return 0 if exponent == 10 else 1
    if family == "E":
        return 1 if exponent == 8 else 2
    if family == "F":
        return 1
    return 0
