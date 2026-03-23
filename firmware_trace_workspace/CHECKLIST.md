# Checklist

The tracer work is not done until all of these are satisfied for at least one real block.

- Exact 15 selector bits are shown in consumed order
- Five final selector values are shown
- Jump-table bytes used for each selector are shown
- Selector 6 runtime behavior is shown as actual fallthrough or call behavior
- Post-`movx.w` state is shown
- Post-`movy.w` state is shown
- 2-4 successive `pshl` steps are shown from a real loaded state
- Actual extracted bit values are shown for those `pshl` steps
- Observed X/Y cursor movement is shown
- Observed residual writes are shown

Stretch goals after first milestone:
- Standard `0x40` anchor block traced
- One non-standard anchor mode traced
- Any read/use of `Record[8]` in the active decode path traced
