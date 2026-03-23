# Trace Schema

The tracer output must be machine-checkable.

Minimum event fields:
- `pc`
- `mnemonic`
- `cpu_regs_changed`
- `dsp_regs_changed`
- `memory_read`
- `memory_write`
- `semantic_tag`

Suggested semantic tags:
- `anchor_bit`
- `selector_bit`
- `selector_finalized`
- `jump_table_lookup`
- `null_selector_fallthrough`
- `x_word_load`
- `y_word_load`
- `pshl_pop`
- `residual_write`
- `anchor_mode_branch`

Per-block summary fields:
- `block_index`
- `block_hex`
- `selector_bits`
- `selectors`
- `jump_table_entries_used`
- `x_cursor_start`
- `x_cursor_end`
- `y_cursor_start`
- `y_cursor_end`
- `anchor_mode`
- `notes`

Acceptance rule:
- A claim is only usable by the decoder if it can be pointed to in trace output.
