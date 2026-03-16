"""Shared UI constants extracted from the legacy monolith."""

PAD_ORDER = [
    0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,  # Bank A Pads 1-8
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F,  # Bank B Pads 1-8
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,  # Bank C Pads 1-8
    0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F   # Bank D Pads 1-8
]

PAD_NAMES = {
    0x00: "A1", 0x01: "A2", 0x02: "A3", 0x03: "A4",
    0x04: "A5", 0x05: "A6", 0x06: "A7", 0x07: "A8",
    0x08: "B1", 0x09: "B2", 0x0A: "B3", 0x0B: "B4",
    0x0C: "B5", 0x0D: "B6", 0x0E: "B7", 0x0F: "B8",
    0x10: "C1", 0x11: "C2", 0x12: "C3", 0x13: "C4",
    0x14: "C5", 0x15: "C6", 0x16: "C7", 0x17: "C8",
    0x18: "D1", 0x19: "D2", 0x1A: "D3", 0x1B: "D4",
    0x1C: "D5", 0x1D: "D6", 0x1E: "D7", 0x1F: "D8",
}

# Grid snap values (in ticks at 96 PPQN) - matching SP-303 device labels
GRID_SNAPS = {
    "Off": 0,     # Quantise Off
    "4": 96,      # Quarter note
    "8": 48,      # Eighth note
    "8-3": 32,    # Eighth note triplet (96/3)
    "16": 24,     # Sixteenth note
}

# Color palettes
COLOR_PALETTES = {
    "Dark": {
        "background": "#1a1a1a",
        "grid_major": "#333333",
        "grid_minor": "#222222",
        "ruler_bg": "#252525",
        "ruler_text": "#aaaaaa",
        "lane_separator": "#2a2a2a",
        "lane_label_bg": "#202020",
        "lane_label_text": "#cccccc",
        "selection_rect": "#60a5fa",
        "selection_fill": "#60a5fa33",
        "pattern_end": "#ff4444",
        # Bank A (red/orange shades)
        "pad_a": [
            "#EF4444", "#F87171", "#FCA5A5", "#FECACA",
            "#DC2626", "#B91C1C", "#991B1B", "#7F1D1D"
        ],
        # Bank B (purple shades)
        "pad_b": [
            "#A855F7", "#C084FC", "#D8B4FE", "#E9D5FF",
            "#9333EA", "#7E22CE", "#6B21A8", "#581C87"
        ],
        # Bank C (blue shades)
        "pad_c": [
            "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE",
            "#2563EB", "#1D4ED8", "#1E40AF", "#1E3A8A"
        ],
        # Bank D (green shades)
        "pad_d": [
            "#10B981", "#34D399", "#6EE7B7", "#A7F3D0",
            "#059669", "#047857", "#065F46", "#064E3B"
        ],
    },
    "High Contrast (White on Black)": {
        "background": "#000000",
        "grid_major": "#444444",
        "grid_minor": "#222222",
        "ruler_bg": "#0a0a0a",
        "ruler_text": "#ffffff",
        "lane_separator": "#333333",
        "lane_label_bg": "#000000",
        "lane_label_text": "#ffffff",
        "selection_rect": "#ffffff",
        "selection_fill": "#ffffff33",
        "pattern_end": "#ff0000",
        # Bank A (bright red/orange on black)
        "pad_a": [
            "#ff0000", "#ff2200", "#ff4400", "#ff6600",
            "#ff8800", "#ffaa00", "#ffcc00", "#ffee00"
        ],
        # Bank B (bright purple/magenta on black)
        "pad_b": [
            "#ff00ff", "#ee00ff", "#dd00ff", "#cc00ff",
            "#bb00ff", "#aa00ff", "#9900ff", "#8800ff"
        ],
        # Bank C (bright blue/cyan on black)
        "pad_c": [
            "#0088ff", "#00aaff", "#00ccff", "#00eeff",
            "#0066ff", "#0044ff", "#0022ff", "#0000ff"
        ],
        # Bank D (bright green on black)
        "pad_d": [
            "#00ff88", "#00ffaa", "#00ffcc", "#00ffee",
            "#00ff66", "#00ff44", "#00ff22", "#00ff00"
        ],
    },
    "High Contrast (Black on White)": {
        "background": "#ffffff",
        "grid_major": "#cccccc",
        "grid_minor": "#e8e8e8",
        "ruler_bg": "#f5f5f5",
        "ruler_text": "#000000",
        "lane_separator": "#dddddd",
        "lane_label_bg": "#ffffff",
        "lane_label_text": "#000000",
        "selection_rect": "#000000",
        "selection_fill": "#00000033",
        "pattern_end": "#cc0000",
        # Bank A (dark red/orange on white)
        "pad_a": [
            "#cc0000", "#aa0000", "#880000", "#660000",
            "#dd2200", "#ee4400", "#ff6600", "#ff8800"
        ],
        # Bank B (dark purple on white)
        "pad_b": [
            "#880088", "#770077", "#660066", "#550055",
            "#990099", "#aa00aa", "#bb00bb", "#cc00cc"
        ],
        # Bank C (dark blue on white)
        "pad_c": [
            "#0066cc", "#0055aa", "#004488", "#003366",
            "#0077dd", "#0088ee", "#0099ff", "#00aaff"
        ],
        # Bank D (dark green on white)
        "pad_d": [
            "#008844", "#007733", "#006622", "#005511",
            "#009955", "#00aa66", "#00bb77", "#00cc88"
        ],
    },
    "Apple Green": {
        "background": "#001100",
        "grid_major": "#003300",
        "grid_minor": "#002200",
        "ruler_bg": "#001a00",
        "ruler_text": "#00ff00",
        "lane_separator": "#002200",
        "lane_label_bg": "#001100",
        "lane_label_text": "#00ff00",
        "selection_rect": "#00ff00",
        "selection_fill": "#00ff0033",
        "pattern_end": "#00ff00",
        # Bank A (red-green variations)
        "pad_a": [
            "#ff4400", "#ee3300", "#dd2200", "#cc1100",
            "#ff5500", "#ff6600", "#ff7700", "#ff8800"
        ],
        # Bank B (purple-green variations)
        "pad_b": [
            "#cc00ff", "#bb00ee", "#aa00dd", "#9900cc",
            "#dd00ff", "#ee00ff", "#ff00ff", "#ff22ff"
        ],
        # Bank C (green variations)
        "pad_c": [
            "#00ff00", "#00ee00", "#00dd00", "#00cc00",
            "#00bb00", "#00aa00", "#009900", "#008800"
        ],
        # Bank D (yellow-green variations)
        "pad_d": [
            "#88ff00", "#99ff00", "#aaff00", "#bbff00",
            "#77ff00", "#66ff00", "#55ff00", "#44ff00"
        ],
    },
}

# Default color scheme
COLORS = COLOR_PALETTES["High Contrast (White on Black)"]
