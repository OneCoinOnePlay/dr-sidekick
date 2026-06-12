# Code Review Actions — RDAC Codec Replacement

Review of commit `50a3fd1` ("Replace experimental RDAC decoder with solved codec") plus
working-tree changes, 2026-06-11. Findings ranked most severe first.

**The thread tying findings 1–4 together:** the new codec made SMPINFO metadata
authoritative (mode + length), but the three producers of that metadata —
`assign_archived_sp0`, `set_slot`/`prepare_card`, and the UI's `slot_records`
lifecycle — were never taught the new invariants. Fixing `set_slot` to accept
`sample_rate`, storing audio-byte lengths, and invalidating/swapping
`slot_records` alongside the other per-pad state closes all four.

---

## 1. Restored cards write trailer-inclusive lengths the decoder rejects

**File:** `dr_sidekick/engine/core.py:1627` (with `sp303_codec.py:424`)

`assign_archived_sp0` stores `sample_length = sp0_file.stat().st_size` (N×512,
page trailers included) and `prepare_card` writes it via `set_slot`. The new
`decode_sp0_file` requires audio-only bytes: after `strip_page_trailers` leaves
N×504 bytes, `encoded_length` (N×512) exceeds the stream and it raises
`ValueError`. **Reproduced** by the verifier on a synthetic 512-byte page.

**Effect:** every pad on a Dr. Sidekick–written card fails preview/convert after
reload. Hardware-written cards (audio-byte lengths) work fine.

**Action:** store audio-byte length (504 per 512-byte page for STANDARD/LONG;
raw size for LO-FI) when assigning archived SP0s, or convert at write time in
`prepare_card`.

## 2. Restore-to-card stamps every slot STANDARD

**File:** `dr_sidekick/engine/core.py:1767` (and `set_slot` at ~1378)

`set_slot` has no `sample_rate` parameter, so `SlotRecord` defaults to 44100 and
`to_bytes` writes rate field 0x113A for every restored slot. The UI's post-write
SMPINFO patching covers bytes 35/37–39 only, never bytes 32–33.

**Effect:** a restored LO-FI sample plays at 4× speed on hardware; reloading the
card decodes the raw 8-byte LO-FI stream as page-framed STANDARD → noise. Same
for LONG at 2×.

**Action:** add `sample_rate` to `set_slot` and propagate the loaded card's rate
through `prepare_card`.

## 3. swap_assignments doesn't swap slot_records

**File:** `dr_sidekick/ui/sample_manager.py:1143`

`swap_assignments` swaps `slot_metadata`, `level_state`, `gate_state`,
`loop_state`, `reverse_state` — but not the new `slot_records` dict that
preview/convert use to pick the lo_fi flag and `encoded_length`.

**Effect:** swap a STANDARD pad with a LO-FI pad, then preview: the LO-FI file is
decoded with the STANDARD record → garbage audio at the wrong rate, or
`ValueError` when the stale length exceeds the stripped stream.

**Action:** swap `slot_records` entries alongside the other per-pad dicts.

## 4. assign_sp0 / clear_pad leave stale slot_records

**File:** `dr_sidekick/ui/sample_manager.py:562` (and `clear_pad` at ~664)

`slot_records` is only written inside `load_smpinfo_from_path`; assigning a new
SP0 file to a pad or clearing a pad never updates or removes the slot's record.

**Effect:** assign a STANDARD file over a previously LO-FI pad and preview:
decoded with `lo_fi=True` framing and the old length → garbled audio or
`ValueError`.

**Action:** clear (or rebuild) `slot_records[slot]` in both paths.

## 5. Stereo R channel decoded with the L record's length

**File:** `dr_sidekick/ui/sample_manager.py:934`

Both channels are decoded with the same `SlotRecord`, so an R.SP0 shorter than
the SMPINFO length raises `ValueError` and aborts the whole preview — the
existing max/pad logic at lines 935–937 never runs. The old decoder padded the
shorter channel. (PLAUSIBLE — needs an odd/truncated file state.)

**Action:** clamp `encoded_length` to the R file's actual audio bytes, or catch
and fall back to length-agnostic decode for the R channel.

## 6. decode_rdac_block unit-5/6/7 paths always raise (latent)

**File:** `dr_sidekick/engine/rdac.py:777`

Only `unit_bytes*2` bytes are staged and `staged[2:]` is passed as the second
window; for unit 5/6/7 family B's 14-byte window check always raises, and other
families raise `NotImplementedError`. Unexercised today (the app only passes
`unit_bytes=4`; unit-6 STANDARD uses `sp303_codec._decode_window`), but the API
advertises these unit sizes.

**Action:** restrict the signature to `unit_bytes in (4, 8)` or fix the staging.

## 7. No-record fallback silently mis-decodes as LO-FI

**File:** `dr_sidekick/ui/sample_manager.py:923`

Any stream that isn't an exact multiple of 512 with zero trailers is treated as
LO-FI: a trailer-stripped or truncated STANDARD file converts to an 11.025 kHz
WAV of noise with a success dialog. Documented as a heuristic, but a warning for
ambiguous inputs would be cheap.

**Action:** warn (or refuse) when the heuristic input is neither cleanly
page-framed nor a multiple of the LO-FI block size.

## 8. Drive-by config change in working tree

**File:** `dr_sidekick_config.json:8`

`last_author` changed from "Immy" to "Pink Floyd" — app-written runtime state
(`library_window.py:892/1022`) riding along uncommitted with the codec work. The
file also carries machine-specific `recent_files` paths.

**Action:** don't commit; consider gitignoring the runtime config.

## 9. sp303_codec duplicates rdac's machinery verbatim

**File:** `dr_sidekick/engine/sp303_codec.py:62`

`_s16/_s32/_avg16/_add_words/_sat_upper_s16` (== `rdac.py:31–83`),
`_predictive_add` (== `rdac._sp5_predictive_add`), `_base_widths`
(== `rdac._PREDICTIVE_WIDTHS` minus 2 per width), `_q14_extra_bit_count`
(== the ladder in `rdac._sp5_q14_control_bits`). Encoder and decoder hold
separate copies of the same arithmetic and bit-width truth tables — a correction
to one silently breaks encode/decode round-trip.

**Action:** import the shared helpers from `rdac` and derive the unit-6 width
table as `width - 2` (rdac already derives unit-4 as `width - 4`).

## 10. ~760 lines of dead code in rdac.py

**File:** `dr_sidekick/engine/rdac.py:896`

Zero callers repo-wide (verified by grep incl. tests): the entire `_sp5_*`
encoder chain, `scan_controls`, `decode_ported_stream`, `encode_*_stream`,
`Sp5EncoderSelection`, plus orphan helpers `_u16`, `_score_block`,
`decode_family_b_block`. The only live entry points are `parse_control`,
`decode_rdac_block`, and `RdacControl`. Also: dead nested `if dl < 0x48` in
`parse_control` (line 831 — confirmed self-consistent, not a mistranscription),
a shebang/exec bit with no `__main__`, and a redundant eager import at
`engine/__init__.py:3` (`core.py:31` already pulls the chain in).

**Action:** delete the dead half (it lives in the upstream Roland SP RDAC
Toolkit), or keep it deliberately and say so in the module docstring.

---

## Lower-priority notes (confirmed, below the cut)

- `decode_unit4_block` parses the control byte twice and stages bytes three
  times per 8-byte block (`sp303_codec.py:355–359` + `rdac.py:765–766`).
- Family-A decode extracts bit-by-bit (~118k `_bit_at_msb_index` calls per 16 KB
  channel); a single `int.from_bytes` + shifts would do.
- `_family_a_tables` builds ~2 MB of lookup lists (131,072-call warm-up) to
  replace two integer ops per sample.
- `SlotRecord.to_bytes` inlines the inverse of `sample_rate_from_field` with
  magic numbers (`core.py:1229`); the 1102↔11025 quirk should live once in
  `sp303_codec` as a `rate_field_from_sample_rate()`.
- `decode_sp0_channel`'s fallback re-implements `decode_sp0_file`'s body instead
  of calling it with `lo_fi=not framed` (`sample_manager.py:922`).
- Preview then convert fully re-decodes the same SP0 bytes through the
  pure-Python decoder each time, on the Tk UI thread; memoize by
  `(path, mtime, record)` or reuse the preview's temp WAV.
- `SlotRecord.mode` conflates semantic mode and display text (UI string-compares
  `== "STANDARD"` while other sites compare raw rates); make mode a real
  constant with formatting in the UI.
