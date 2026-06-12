"""Solved SP-303 sample codec (decode side).

Ported from the Roland SP RDAC Toolkit (`sp303_probe.py` / `sp303.py`), which
validated these decoders against hardware captures: STANDARD/LONG re-encode
byte-exact on the fixture cards and LO-FI predictive blocks re-encode
byte-exact. See the toolkit's `docs/SP-303.md` for the format reference.

The SP-303 stores samples in three modes; the mode lives in the SMPINFO
record's playback-rate field (offset 0x20, big-endian, tens of Hz):

- STANDARD (44.1 kHz): 12-byte unit-6 blocks in 512-byte pages (504 audio
  bytes = 42 blocks, then an 8-byte zero trailer); `strip_page_trailers()`
  removes the trailers before decoding.
- LONG (22.05 kHz): byte-identical STANDARD codec and page framing, just
  played back at half rate.
- LO-FI (11.025 kHz): SP-555 lo-fi unit-4 codec, 8-byte blocks, **no** page
  trailers (the SMPINFO length counts raw block bytes), plus the SP-303-only
  unit-4 family A.
"""

from __future__ import annotations

from pathlib import Path

from . import rdac
from .rdac import _avg16, _s16, _s32


BLOCK_BYTES = 12
SAMPLES_PER_BLOCK = 16
LOFI_BLOCK_BYTES = 8

# SP-303 STANDARD/LONG sample data is stored in fixed 512-byte pages. Each page
# holds 504 audio bytes (42 * 12-byte blocks) followed by an 8-byte zero
# trailer. The `length` field in SMPINFO counts only the audio bytes, so the
# trailers must be removed before block decoding or the 12-byte grid slips
# 8 bytes per page.
PAGE_BYTES = 512
PAGE_AUDIO_BYTES = 504
PAGE_TRAILER_BYTES = PAGE_BYTES - PAGE_AUDIO_BYTES

STANDARD_SAMPLE_RATE = 44100
LONG_SAMPLE_RATE = 22050
LOFI_SAMPLE_RATE = 11025
MODE_BY_RATE = {
    STANDARD_SAMPLE_RATE: "STANDARD",
    LONG_SAMPLE_RATE: "LONG",
    LOFI_SAMPLE_RATE: "LO-FI",
}


def sample_rate_from_field(rate_field: int) -> int:
    """Map the SMPINFO playback-rate field (tens of Hz) to the published rate.

    4410 STANDARD, 2205 LONG, 1102 LO-FI (the hardware floors 1102.5; map it
    back to the published rate).
    """
    if rate_field == 1102:
        return LOFI_SAMPLE_RATE
    return rate_field * 10


def rate_field_from_sample_rate(sample_rate: int) -> int:
    """Inverse of `sample_rate_from_field`: published rate to SMPINFO field.

    The hardware floors LO-FI's 1102.5 tens of Hz to 1102.
    """
    if sample_rate == LOFI_SAMPLE_RATE:
        return 1102
    return sample_rate // 10


def _sign_extend(value: int, width: int) -> int:
    sign = 1 << (width - 1)
    return (value ^ sign) - sign


def _predictive_average(family: str, left: int, right: int) -> int:
    del family
    # SP-555's C/F helpers use an unsigned intermediate average. On SP-303
    # size-6 blocks that wraps across zero and corrupts negative half-cycles.
    return _avg16(left, right)


_predictive_add = rdac.predictive_add
_q14_extra_bit_count = rdac.q14_extra_bit_count

# SP-303 STANDARD keeps the same family structure as SP-555 predictive blocks,
# but with 6-bit-scale widths: unit-6 derives from the shared unit-8 base
# table as `width - 2`, the same way rdac derives unit-4 as `width - 4`.
_UNIT6_WIDTHS = {
    family: tuple(width - 2 for width in widths)
    for family, widths in rdac.PREDICTIVE_WIDTHS.items()
}


def _base_widths(family: str) -> tuple[int, ...]:
    return _UNIT6_WIDTHS[family]


def _dequant(code: int, width: int, exponent: int, unit_bytes: int) -> int:
    """Scale one SP-303 residual code to a signed sample-domain delta."""
    shift = exponent - unit_bytes + 4
    value = _sign_extend(code, width)
    return _scale_signed_code(value, shift)


def _scale_signed_code(value: int, shift: int) -> int:
    if shift >= 0:
        midpoint = 1 << (shift - 1) if shift else 0
        return _s32((value << shift) + midpoint)
    return _s32(value >> (-shift))


def _window_payload(staged: bytes, start: int) -> tuple[int, int]:
    # SP-555 predictive windows pack low-order code bits at byte positions
    # 13,12,9,8,5,4,1,0.  A 12-byte SP-303 block keeps the first six bytes
    # of that per-window order: 9,8,5,4,1,0 for the first half and
    # 11,10,7,6,3,2 for the second half.  The high nibble of byte 0/2 is the
    # control selector, so only the low nibble participates in the 44-bit
    # size-6 code payload.
    if start == 0:
        packed_bytes = (
            staged[9],
            staged[8],
            staged[5],
            staged[4],
            staged[1],
            staged[0] & 0x0F,
        )
        control_nibble = staged[0] >> 4
    else:
        packed_bytes = (
            staged[11],
            staged[10],
            staged[7],
            staged[6],
            staged[3],
            staged[2] & 0x0F,
        )
        control_nibble = staged[2] >> 4

    packed = 0
    for byte_index, byte_value in enumerate(packed_bytes):
        packed |= byte_value << (byte_index * 8)
    return packed, control_nibble


def _decode_window(
    staged: bytes,
    start: int,
    previous: int,
    family: str,
    exponent: int,
    unit_bytes: int,
) -> tuple[list[int], int]:
    widths = _base_widths(family)
    packed, control_nibble = _window_payload(staged, start)

    bit_cursor = 0
    codes = []
    for width in widths:
        codes.append((packed >> bit_cursor) & ((1 << width) - 1))
        bit_cursor += width

    effective_widths = list(widths)
    extra_bit_count = _q14_extra_bit_count(family, exponent)
    if extra_bit_count:
        extra = control_nibble & ((1 << extra_bit_count) - 1)
        codes[7] |= extra << widths[7]
        effective_widths[7] += extra_bit_count

    bases = [_dequant(code, width, exponent, unit_bytes) for code, width in zip(codes, effective_widths)]
    decoded = [0] * 8
    decoded[7] = _s16(bases[7])

    if family in ("B", "C") or (family == "D" and exponent == 10):
        decoded[3] = _s16(bases[3])
    else:
        decoded[3] = _s16(bases[3] + _predictive_average(family, previous, decoded[7]))

    if family == "B":
        decoded[1] = _s16(bases[1])
        decoded[5] = _s16(bases[5])
    else:
        decoded[1] = _s16(bases[1] + _predictive_average(family, previous, decoded[3]))
        decoded[5] = _s16(bases[5] + _predictive_average(family, decoded[3], decoded[7]))

    for index, left_index, right_index in ((0, -1, 1), (2, 1, 3), (4, 3, 5), (6, 5, 7)):
        left = previous if left_index < 0 else decoded[left_index]
        decoded[index] = _predictive_add(
            family,
            bases[index],
            _predictive_average(family, left, decoded[right_index]),
            index,
        )
    return decoded, decoded[7]


# Family A is the non-predictive "verbatim" STANDARD family the SP-303 selects
# for loud, high-entropy material (full-scale noise, sawtooth edges) that the
# B-F predictors cannot track. Unlike B-F it carries no inter-sample prediction:
# every one of the 16 samples is an independent linear-PCM code that spans the
# full 16-bit range, so step = 2**(16-width) and the reconstruction adds the
# half-step midpoint.
#
# The bit grid was recovered empirically from the family-A blocks in the eight
# Test Signals (corr ~1.000 per sample against the reference WAVs). Each entry
# is the MSB-first list of 0-95 bit indices for that decoded sample. The two
# 8-sample windows are the usual 2-byte (16-bit) stride apart; widths alternate
# 5,6 and the wide (6-bit) codes for samples 5/13 borrow their high bit from a
# bit just below the control nibble, mirroring the predictive q14 extra bit.
FAMILY_A_BIT_LAYOUT: tuple[tuple[int, ...], ...] = (
    (75, 76, 77, 78, 79),              # s0  w5
    (69, 70, 71, 72, 73, 74),          # s1  w6
    (64, 65, 66, 67, 68),              # s2  w5
    (42, 43, 44, 45, 46, 47),          # s3  w6
    (37, 38, 39, 40, 41),              # s4  w5
    (15, 32, 33, 34, 35, 36),          # s5  w6
    (10, 11, 12, 13, 14),              # s6  w5
    (4, 5, 6, 7, 8, 9),                # s7  w6
    (91, 92, 93, 94, 95),              # s8  w5
    (85, 86, 87, 88, 89, 90),          # s9  w6
    (80, 81, 82, 83, 84),              # s10 w5
    (58, 59, 60, 61, 62, 63),          # s11 w6
    (53, 54, 55, 56, 57),              # s12 w5
    (31, 48, 49, 50, 51, 52),          # s13 w6
    (26, 27, 28, 29, 30),              # s14 w5
    (20, 21, 22, 23, 24, 25),          # s15 w6
)


def decode_family_a(block: bytes, previous: int) -> tuple[list[int], int]:
    """Decode one 12-byte unit-6 family-A block into 16 PCM samples.

    Family A is non-predictive, so `previous` is unused for reconstruction; the
    returned next-previous is simply the last decoded sample so a following
    predictive block keeps a valid seed.
    """
    del previous
    bits = int.from_bytes(block[:BLOCK_BYTES], "big")
    decoded: list[int] = []
    for positions in FAMILY_A_BIT_LAYOUT:
        width = len(positions)
        code = 0
        for bit_index in positions:
            code = (code << 1) | ((bits >> (95 - bit_index)) & 1)
        signed = _sign_extend(code, width)
        shift = 16 - width
        decoded.append(_s16((signed << shift) + (1 << (shift - 1))))
    return decoded, decoded[-1]


def decode_block_candidate(block: bytes, previous: int) -> tuple[list[int], int, rdac.RdacControl]:
    """Decode one 12-byte SP-303 STANDARD/LONG block into 16 PCM samples."""
    control = rdac.parse_control(block, unit_bytes=6)
    staged = bytearray(block[:BLOCK_BYTES])
    if control.family == "A":
        decoded, next_previous = decode_family_a(block, previous)
        return decoded, next_previous, control
    if control.family not in {"B", "C", "D", "E", "F"}:
        return [0] * SAMPLES_PER_BLOCK, previous, control
    first, first_previous = _decode_window(staged, 0, previous, control.family, control.exponent, 6)
    second, second_previous = _decode_window(staged, 2, first_previous, control.family, control.exponent, 6)
    return first + second, second_previous, control


# SP-303 LO-FI (unit-4, 8-byte block) family A. Unlike SP-555 lo-fi streams,
# which never use family A (controls 0xEC/0xED/0xFC/0xFD), the SP-303 emits it
# for the same loud, high-entropy material STANDARD family A covers. Each
# 32-bit window word carries 8 verbatim full-range "knot" codes x0..x7 packed
# LSB-first at widths 4,3,4,3,4,3,4,3; x7's missing high bit is the control
# nibble's low bit (which is why the A controls come in the pairs 0xEC/0xED and
# 0xFC/0xFD - window nibbles 0xE|bit and 0xC|bit). Playback output is the
# half-step smoother s[j] = avg(x[j], x[j+1]) for j < 7 and s[7] = x7.
UNIT4_FAMILY_A_WIDTHS: tuple[int, ...] = (4, 3, 4, 3, 4, 3, 4, 3)


def unit4_family_a_knots(block: bytes) -> list[int]:
    """Extract the 16 quantized knot values from one 8-byte unit-4 A block."""
    knots: list[int] = []
    for word_start in (0, 4):
        word = int.from_bytes(block[word_start : word_start + 4], "big")
        payload = word & 0x0FFFFFFF
        control_nibble = word >> 28
        cursor = 0
        for index, width in enumerate(UNIT4_FAMILY_A_WIDTHS):
            code = (payload >> cursor) & ((1 << width) - 1)
            cursor += width
            if index == 7:
                code |= (control_nibble & 1) << 3
                width = 4
            shift = 16 - width
            knots.append(_s16((_sign_extend(code, width) << shift) + (1 << (shift - 1))))
    return knots


def decode_unit4_family_a(block: bytes, previous: int) -> tuple[list[int], int]:
    """Decode one 8-byte unit-4 family-A block into 16 PCM samples.

    Family A is non-predictive; `previous` is unused. The last output sample
    equals the last knot, so it seeds a following predictive block correctly.
    """
    del previous
    knots = unit4_family_a_knots(block)
    decoded: list[int] = []
    for window in (knots[:8], knots[8:]):
        decoded.extend(_s16((window[j] + window[j + 1]) >> 1) for j in range(7))
        decoded.append(window[7])
    return decoded, decoded[-1]


def decode_unit4_block(block: bytes, previous: int) -> tuple[list[int], int, rdac.RdacControl]:
    """Decode one 8-byte SP-303 LO-FI block (SP-555 unit-4 + SP-303 family A)."""
    control = rdac.parse_control(block, unit_bytes=4)
    if control.family == "A":
        decoded, next_previous = decode_unit4_family_a(block, previous)
        return decoded, next_previous, control
    decoded, next_previous, _ = rdac.decode_rdac_block(
        block, previous, unit_bytes=4, control=control
    )
    return decoded, next_previous, control


def page_framed_audio_bytes(file_size: int) -> int:
    """Audio bytes in a page-framed STANDARD/LONG file of `file_size` bytes."""
    full_pages, remainder = divmod(file_size, PAGE_BYTES)
    return full_pages * PAGE_AUDIO_BYTES + min(remainder, PAGE_AUDIO_BYTES)


def strip_page_trailers(data: bytes) -> bytes:
    """Return the audio byte stream with the 8-byte page trailers removed."""
    audio = bytearray()
    for page_start in range(0, len(data), PAGE_BYTES):
        audio += data[page_start : page_start + PAGE_AUDIO_BYTES]
    return bytes(audio)


def decode_standard_stream(data: bytes) -> list[int]:
    """Decode a trailer-free STANDARD/LONG block stream to PCM samples."""
    previous = 0
    samples: list[int] = []
    for offset in range(0, len(data) - BLOCK_BYTES + 1, BLOCK_BYTES):
        decoded, previous, _control = decode_block_candidate(
            data[offset : offset + BLOCK_BYTES], previous
        )
        samples.extend(decoded)
    return samples


def decode_lofi_stream(data: bytes) -> list[int]:
    """Decode a LO-FI 8-byte block stream to PCM samples."""
    previous = 0
    samples: list[int] = []
    for offset in range(0, len(data) - LOFI_BLOCK_BYTES + 1, LOFI_BLOCK_BYTES):
        decoded, previous, _control = decode_unit4_block(
            data[offset : offset + LOFI_BLOCK_BYTES], previous
        )
        samples.extend(decoded)
    return samples


def looks_page_framed(data: bytes) -> bool:
    """Heuristic: does this look like a page-framed STANDARD/LONG stream?

    STANDARD/LONG files are whole 512-byte pages ending in 8-byte zero
    trailers; LO-FI files are raw 8-byte blocks with no framing. Used only
    when no SMPINFO record is available to state the mode.
    """
    if not data or len(data) % PAGE_BYTES:
        return False
    return all(
        data[page_end - PAGE_TRAILER_BYTES : page_end] == bytes(PAGE_TRAILER_BYTES)
        for page_end in range(PAGE_BYTES, len(data) + 1, PAGE_BYTES)
    )


def decode_sp0_stream(
    raw: bytes,
    encoded_length: int | None = None,
    lo_fi: bool = False,
    clamp_length: bool = False,
    name: str = "SP0 stream",
) -> list[int]:
    """Decode a raw SP0 audio stream to a flat list of PCM samples.

    `encoded_length` is the SMPINFO length field (audio bytes, trailer-free);
    without it the whole stream is decoded, which may include padding noise at
    the tail of the final page. When the metadata length exceeds the stream
    (truncated file, or a legacy trailer-inclusive length), `clamp_length`
    decodes the available audio instead of raising.
    """
    audio = raw if lo_fi else strip_page_trailers(raw)
    if encoded_length is not None:
        if encoded_length > len(audio):
            if not clamp_length:
                raise ValueError(
                    f"{name} has {len(audio)} audio bytes, metadata expects {encoded_length}"
                )
            encoded_length = len(audio)
        audio = audio[:encoded_length]
    if lo_fi:
        return decode_lofi_stream(audio)
    return decode_standard_stream(audio)


def decode_sp0_file(
    path: Path | str,
    encoded_length: int | None = None,
    lo_fi: bool = False,
    clamp_length: bool = False,
) -> list[int]:
    """Decode one `SMPxxxx[LR].SP0` audio file to a flat list of PCM samples."""
    path = Path(path)
    return decode_sp0_stream(
        path.read_bytes(),
        encoded_length=encoded_length,
        lo_fi=lo_fi,
        clamp_length=clamp_length,
        name=path.name,
    )
